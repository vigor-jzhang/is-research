"""Phase 2H gap analyzer — evidence-grounded research gap analysis.

Consumes ResearchQuestion + EvidenceCorpus + LiteratureSynthesis, produces
ResearchGap candidates with strict structured output, grounding validation,
deterministic support counts, transparent ranking, and analytical-model
opportunity assessment. No theory/mechanism generation.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.evidence_extraction import EvidenceCorpus
from research_harness.research.schemas.gap import (
    AnalyticalModelOpportunity,
    GapAnalysis,
    GapAnalysisExecution,
    GapRankDimension,
    GapStatus,
    GapStrength,
    GapType,
    ResearchGap,
)
from research_harness.research.schemas.synthesis import (
    LiteratureSynthesis,
    SynthesisStatement,
    SynthesisTheme,
)

logger = logging.getLogger(__name__)

# Strong-claim phrases that are NOT corpus-bounded; flagged for normalization
_SWEEPING_PHRASES = [
    "no research has studied",
    "no studies have",
    "no paper has",
    "nothing is known",
    "no one has examined",
    "no studies exist",
    "has never been studied",
    "no literature",
    "no evidence exists",
    "nothing exists",
]


class _GapCandidate(BaseModel):
    title: str
    gap_type: str
    description: str
    why_it_matters: str | None = None
    supporting_synthesis_statement_ids: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradiction_statement_ids: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    scope: str | None = None
    limitations: list[str] = Field(default_factory=list)
    evidence_strength: float = Field(default=0.5, ge=0.0, le=1.0)
    research_importance: float = Field(default=0.5, ge=0.0, le=1.0)
    theoretical_relevance: float = Field(default=0.5, ge=0.0, le=1.0)
    analytical_model_potential: float = Field(default=0.5, ge=0.0, le=1.0)
    tractability: float = Field(default=0.5, ge=0.0, le=1.0)
    model_domains: list[str] = Field(default_factory=list)
    model_opportunity_rationale: str | None = None

    @field_validator("title", "description")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must be non-empty")
        return v

    @field_validator("gap_type")
    @classmethod
    def validate_gap_type(cls, v: str) -> str:
        if v not in GapType.values():
            raise ValueError(f"invalid gap type {v!r}")
        return v

    model_config = {"extra": "forbid"}


class _GapResponse(BaseModel):
    gaps: list[_GapCandidate]

    model_config = {"extra": "forbid"}


def _has_sweeping_claim(text: str) -> bool:
    low = text.lower()
    return any(phrase in low for phrase in _SWEEPING_PHRASES)


def _normalize_sweeping_claim(text: str) -> str:
    """Rewrite absolute absence claims into corpus-bounded language."""
    out = text
    replacements = [
        ("no research has studied", "the reviewed corpus provides no evidence on"),
        ("no studies have", "few included studies have"),
        ("no paper has", "no included paper has"),
        ("nothing is known", "little is known within the reviewed corpus"),
        ("no one has examined", "the reviewed corpus does not examine"),
        ("no studies exist", "few included studies exist"),
        ("has never been studied", "has received limited attention in the reviewed corpus"),
        ("no literature", "limited literature within the reviewed corpus"),
        ("no evidence exists", "the reviewed corpus provides limited evidence"),
        ("nothing exists", "little exists within the reviewed corpus"),
    ]
    for phrase, repl in replacements:
        out = re.sub(re.escape(phrase), repl, out, flags=re.IGNORECASE)
    return out


class GapAnalyzerService:
    def __init__(
        self,
        model_router: Any,
        artifact_store: Any,
        model_role: str = "reasoning",
        max_statements: int = 200,
        max_gaps: int = 50,
        max_model_calls: int = 20,
        events: Any | None = None,
    ) -> None:
        self._router = model_router
        self._store = artifact_store
        self._model_role = model_role
        self._max_statements = max_statements
        self._max_gaps = max_gaps
        self._max_model_calls = max_model_calls
        self._events = events

    @property
    def analyzer_id(self) -> str:
        return "literature.gap_analyzer"

    def _build_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "gaps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "gap_type": {"type": "string", "enum": GapType.values()},
                            "description": {"type": "string"},
                            "why_it_matters": {"type": "string"},
                            "supporting_synthesis_statement_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "supporting_evidence_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "contradiction_statement_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "scope": {"type": "string"},
                            "limitations": {"type": "array", "items": {"type": "string"}},
                            "evidence_strength": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "research_importance": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "theoretical_relevance": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "analytical_model_potential": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "tractability": {"type": "number", "minimum": 0, "maximum": 1},
                            "model_domains": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "model_opportunity_rationale": {"type": "string"},
                        },
                        "required": [
                            "title",
                            "gap_type",
                            "description",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["gaps"],
            "additionalProperties": False,
        }

    async def run(
        self,
        literature_synthesis_id: str,
        evidence_corpus_id: str,
        research_question_id: str | None = None,
    ) -> str:
        """Analyze a LiteratureSynthesis for research gaps. Returns GapAnalysisExecution id."""
        # Idempotency: reuse only a completed successful analysis with matching config
        existing = await self._store.list(artifact_type="gap_analysis_execution")
        for env in existing:
            try:
                ex = GapAnalysisExecution.model_validate(env.payload)
                if (
                    ex.literature_synthesis_id == literature_synthesis_id
                    and ex.evidence_corpus_id == evidence_corpus_id
                    and ex.model_role == self._model_role
                    and ex.completed_at is not None
                    and ex.gaps_created > 0
                ):
                    return env.artifact_id
            except Exception:
                continue

        syn_env = await self._store.get(literature_synthesis_id)
        synthesis = syn_env.parse_payload(LiteratureSynthesis)
        corp_env = await self._store.get(evidence_corpus_id)
        corpus = corp_env.parse_payload(EvidenceCorpus)

        started = datetime.now(UTC)
        exec_record = GapAnalysisExecution(
            literature_synthesis_id=literature_synthesis_id,
            evidence_corpus_id=evidence_corpus_id,
            research_question_id=research_question_id,
            statements_processed=0,
            themes_processed=0,
            gaps_created=0,
            gaps_rejected=0,
            model_role=self._model_role,
            failures=[],
            counts={"model_calls": 0, "max_gaps": self._max_gaps},
            started_at=started,
        )

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="gap_analysis.started",
                        source="literature.gap_analyzer",
                        payload={
                            "synthesis_id": literature_synthesis_id,
                            "corpus_id": evidence_corpus_id,
                        },
                    )
                )
            except Exception:
                pass

        # Load themes + statements
        themes: list[SynthesisTheme] = []
        stmt_by_id: dict[str, SynthesisStatement] = {}
        for tid in synthesis.theme_ids[: self._max_statements // 10 + 10]:
            try:
                t_env = await self._store.get(tid)
                theme = t_env.parse_payload(SynthesisTheme)
                themes.append(theme)
                for stmt in theme.statements:
                    sid = theme.metadata.get("statement_ids", [])
                    # statements are also persisted individually; map by content
                    stmt_by_id.setdefault(stmt.statement[:120], stmt)
            except Exception:
                continue

        # Map persisted synthesis_statement artifacts to their ids
        stmt_artifacts = await self._store.list(artifact_type="synthesis_statement")
        stmt_id_by_text: dict[str, str] = {}
        stmt_map: dict[str, SynthesisStatement] = {}
        for s_env in stmt_artifacts:
            s = s_env.parse_payload(SynthesisStatement)
            key = s.statement[:120]
            stmt_id_by_text[key] = s_env.artifact_id
            stmt_map[s_env.artifact_id] = s

        exec_record.themes_processed = len(themes)
        exec_record.statements_processed = len(stmt_map)

        # Evidence mapping: evidence id -> paper id via corpus profiles
        ev_id_to_paper: dict[str, str] = {}
        for pid in corpus.paper_profile_ids:
            try:
                from research_harness.research.schemas.research_profile import PaperResearchProfile

                p_env = await self._store.get(pid)
                prof = p_env.parse_payload(PaperResearchProfile)
                for eid in prof.evidence_item_ids:
                    ev_id_to_paper.setdefault(eid, prof.paper_identity_id)
            except Exception:
                continue
        # Resolve any evidence referenced by synthesis statements (via FullTextDocument)
        from research_harness.research.schemas.evidence import EvidenceItem
        from research_harness.research.schemas.full_text import FullTextDocument

        referenced_evidence = {
            eid
            for s in stmt_map.values()
            for eid in s.supporting_evidence_ids + s.conflicting_evidence_ids
        }
        referenced_evidence.update(corpus.evidence_item_ids)
        for eid in referenced_evidence:
            if eid in ev_id_to_paper:
                continue
            try:
                e_env = await self._store.get(eid)
                ev = e_env.parse_payload(EvidenceItem)
                d_env = await self._store.get(ev.source_artifact_id)
                doc = d_env.parse_payload(FullTextDocument)
                ev_id_to_paper[eid] = doc.paper_identity_id
            except Exception:
                continue

        # Coverage limitation from corpus (NOT a gap)
        coverage_limitations = list(corpus.documents_without_evidence)

        # Analyze in a single bounded structured call (statements are already synthesized)
        prompt = self._build_prompt(
            synthesis,
            themes,
            stmt_map,
            stmt_id_by_text,
            ev_id_to_paper,
            corpus,
            research_question_id,
        )
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content="You are a careful research gap analyst. Return valid JSON matching the schema. Never include chain-of-thought. Use corpus-bounded language only.",
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self._build_schema(),
            temperature=0.0,
            metadata={"synthesis_id": literature_synthesis_id},
        )

        try:
            response = await self._router.complete(self._model_role, request)
            exec_record.counts["model_calls"] = 1
        except Exception as e:
            exec_record.failures.append({"error": f"model call failed: {e}"})
            exec_record.completed_at = datetime.now(UTC)
            exec_env = ArtifactEnvelope.create(
                payload=exec_record,
                artifact_type="gap_analysis_execution",
                producer="literature.gap_analyzer",
            )
            await self._store.put(exec_env)
            return exec_env.artifact_id

        content = response.message.content or ""
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            exec_record.failures.append({"error": f"invalid JSON: {content[:200]!r}"})
            exec_record.completed_at = datetime.now(UTC)
            exec_env = ArtifactEnvelope.create(
                payload=exec_record,
                artifact_type="gap_analysis_execution",
                producer="literature.gap_analyzer",
            )
            await self._store.put(exec_env)
            return exec_env.artifact_id

        try:
            parsed = _GapResponse.model_validate(data)
        except Exception as e:
            exec_record.failures.append({"error": f"invalid gap response: {e}"})
            exec_record.completed_at = datetime.now(UTC)
            exec_env = ArtifactEnvelope.create(
                payload=exec_record,
                artifact_type="gap_analysis_execution",
                producer="literature.gap_analyzer",
            )
            await self._store.put(exec_env)
            return exec_env.artifact_id

        gap_artifacts: list[Any] = []
        gap_ids: list[str] = []
        rejected = 0
        for cand in parsed.gaps[: self._max_gaps]:
            try:
                gap = self._build_gap(cand, stmt_map, ev_id_to_paper)
            except ValueError as e:
                rejected += 1
                exec_record.failures.append({"gap": cand.title[:60], "error": str(e)})
                continue
            g_env = ArtifactEnvelope.create(
                payload=gap,
                artifact_type="research_gap",
                producer=f"literature.gap_analyzer:{self._model_role}",
            )
            await self._store.put(g_env)
            # Provenance: gap derived_from synthesis statements + evidence
            for sid in gap.supporting_synthesis_statement_ids + gap.contradiction_statement_ids:
                try:
                    await self._store.add_provenance(
                        ProvenanceLink(
                            relation=ProvenanceRelation.derived_from,
                            source_artifact_id=sid,
                            target_artifact_id=g_env.artifact_id,
                            producer="literature.gap_analyzer",
                        )
                    )
                except Exception:
                    pass
            for eid in gap.supporting_evidence_ids:
                try:
                    await self._store.add_provenance(
                        ProvenanceLink(
                            relation=ProvenanceRelation.derived_from,
                            source_artifact_id=eid,
                            target_artifact_id=g_env.artifact_id,
                            producer="literature.gap_analyzer",
                        )
                    )
                except Exception:
                    pass
            gap_artifacts.append(g_env)
            gap_ids.append(g_env.artifact_id)

        exec_record.gaps_rejected = rejected
        exec_record.gaps_created = len(gap_artifacts)
        exec_record.completed_at = datetime.now(UTC)

        exec_env = ArtifactEnvelope.create(
            payload=exec_record,
            artifact_type="gap_analysis_execution",
            producer="literature.gap_analyzer",
        )
        await self._store.put(exec_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=literature_synthesis_id,
                target_artifact_id=exec_env.artifact_id,
                producer="literature.gap_analyzer",
            )
        )

        # Rank deterministically: composite score descending, tie-break by gap id
        ranked = sorted(
            gap_ids,
            key=lambda gid: _composite_of(gap_artifacts, gid),
            reverse=True,
        )

        analysis = GapAnalysis(
            literature_synthesis_id=literature_synthesis_id,
            evidence_corpus_id=evidence_corpus_id,
            research_question_id=research_question_id,
            gap_ids=gap_ids,
            ranked_gap_ids=ranked,
            coverage_limitations=coverage_limitations,
            summary=(
                f"Identified {len(gap_ids)} research gap candidate(s) from the reviewed corpus "
                f"({exec_record.themes_processed} themes, {exec_record.statements_processed} statements); "
                f"{rejected} rejected for grounding failures. "
                f"Corpus coverage limitation: {len(coverage_limitations)} document(s) without extractable evidence."
            ),
            metadata={"model_role": self._model_role},
        )
        a_env = ArtifactEnvelope.create(
            payload=analysis,
            artifact_type="gap_analysis",
            producer="literature.gap_analyzer",
        )
        await self._store.put(a_env)
        for gid in gap_ids:
            try:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=gid,
                        target_artifact_id=a_env.artifact_id,
                        producer="literature.gap_analyzer",
                    )
                )
            except Exception:
                pass
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=exec_env.artifact_id,
                target_artifact_id=a_env.artifact_id,
                producer="literature.gap_analyzer",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=literature_synthesis_id,
                target_artifact_id=a_env.artifact_id,
                producer="literature.gap_analyzer",
            )
        )

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="gap_analysis.completed",
                        source="literature.gap_analyzer",
                        payload={
                            "analysis_id": a_env.artifact_id,
                            "execution_id": exec_env.artifact_id,
                            "gaps": len(gap_ids),
                            "rejected": rejected,
                        },
                    )
                )
            except Exception:
                pass

        return exec_env.artifact_id

    def _build_prompt(
        self,
        synthesis: LiteratureSynthesis,
        themes: list[SynthesisTheme],
        stmt_map: dict[str, SynthesisStatement],
        stmt_id_by_text: dict[str, str],
        ev_id_to_paper: dict[str, str],
        corpus: EvidenceCorpus,
        research_question_id: str | None,
    ) -> str:
        theme_lines = []
        for theme in themes:
            theme_lines.append(f"Theme: {theme.title} (dimension: {theme.dimension or '?'})")
            for stmt in theme.statements:
                sid = stmt_id_by_text.get(stmt.statement[:120], "?")
                theme_lines.append(
                    f"  [{sid}] ({stmt.type.value}, support {stmt.support_type.value}, "
                    f"papers {stmt.papers_supporting}, evidence {stmt.evidence_items_supporting}, "
                    f"conflicting papers {stmt.papers_conflicting}): {stmt.statement[:250]}"
                )
                if stmt.supporting_evidence_ids:
                    theme_lines.append(f"      evidence: {', '.join(stmt.supporting_evidence_ids)}")
                if stmt.conflicting_evidence_ids:
                    theme_lines.append(
                        f"      conflict: {', '.join(stmt.conflicting_evidence_ids)}"
                    )

        coverage = (
            f"{len(corpus.documents_without_evidence)} document(s) in the corpus have no extractable "
            f"text/evidence (insufficient/encrypted/failed extraction) — a COVERAGE LIMITATION, "
            f"not itself a research gap."
            if corpus.documents_without_evidence
            else "All corpus documents yielded extractable evidence."
        )

        rq = (
            f"Research question context: {research_question_id}"
            if research_question_id
            else "No research question id provided."
        )

        return f"""You are analyzing a literature synthesis to identify candidate research gaps.

{rq}

Synthesis summary counts: {synthesis.counts}

Evidence corpus: {len(corpus.paper_profile_ids)} paper profiles, {len(corpus.evidence_item_ids)} evidence items.
Coverage: {coverage}

Themes and statements (IDs are authoritative; cite ONLY these IDs):

{chr(10).join(theme_lines)}

Task: identify candidate research gaps grounded in this reviewed corpus.
Consider at least: contradictory findings, weakly supported themes, missing mechanisms, unexplored
boundary conditions, limited contexts, repeated limitations, future-research recommendations,
methodological concentration, theoretical fragmentation.

Rules:
- Every supporting_synthesis_statement_ids / supporting_evidence_ids / contradiction_statement_ids entry MUST be an ID shown above.
- gap_type must be one of: {", ".join(GapType.values())}.
- Use corpus-bounded language ('within the reviewed corpus...', 'the reviewed literature provides limited evidence on...', 'few included studies examine...').
- NEVER write 'No research has studied X' or other absolute absence claims.
- Do NOT invent support counts; the orchestrator computes them deterministically.
- Rank dimensions (0..1): evidence_strength, research_importance, theoretical_relevance,
  analytical_model_potential, tractability.
- model_domains (if any) from: strategic interaction, information asymmetry, platform behavior,
  pricing, technology adoption, incentives, competition, mechanism design.
- Return JSON only. No chain-of-thought.
"""

    def _build_gap(
        self,
        cand: _GapCandidate,
        stmt_map: dict[str, SynthesisStatement],
        ev_id_to_paper: dict[str, str],
    ) -> ResearchGap:
        # Grounding: all referenced ids must exist
        for sid in cand.supporting_synthesis_statement_ids + cand.contradiction_statement_ids:
            if sid not in stmt_map:
                raise ValueError(f"hallucinated synthesis statement id {sid!r}")
        for eid in cand.supporting_evidence_ids:
            if eid not in ev_id_to_paper:
                raise ValueError(f"hallucinated evidence id {eid!r}")

        # Corpus-bounded language enforcement
        description = cand.description
        if _has_sweeping_claim(description):
            description = _normalize_sweeping_claim(description)

        # Deterministic support counts
        supporting_stmts = [
            stmt_map[sid] for sid in cand.supporting_synthesis_statement_ids if sid in stmt_map
        ]
        supporting_papers: set[str] = set()
        supporting_evidence: set[str] = set()
        for s in supporting_stmts:
            supporting_papers.update(s.supporting_paper_identity_ids)
            supporting_evidence.update(s.supporting_evidence_ids)
        supporting_evidence.update(cand.supporting_evidence_ids)
        for eid in cand.supporting_evidence_ids:
            if eid in ev_id_to_paper:
                supporting_papers.add(ev_id_to_paper[eid])

        contradicting_papers: set[str] = set()
        for sid in cand.contradiction_statement_ids:
            s = stmt_map.get(sid)
            if s:
                # Papers on the conflicting side of the contradiction
                contradicting_papers.update(s.conflicting_paper_identity_ids)
                # If the statement itself is a contradiction, its supporting side is
                # the disagreeing paper; include it too when conflicting side is empty
                if not s.conflicting_paper_identity_ids:
                    contradicting_papers.update(s.supporting_paper_identity_ids)

        relevant_papers = sorted(supporting_papers | contradicting_papers)
        supporting_papers_count = len(supporting_papers)
        supporting_evidence_count = len(supporting_evidence)
        contradicting_papers_count = len(contradicting_papers)

        # Deterministic strength: strongly supported if >= 2 papers or >= 3 evidence items
        strength = (
            GapStrength.strongly_supported
            if supporting_papers_count >= 2 or supporting_evidence_count >= 3
            else GapStrength.tentative
        )

        ranking = GapRankDimension(
            evidence_strength=min(1.0, cand.evidence_strength),
            research_importance=min(1.0, cand.research_importance),
            theoretical_relevance=min(1.0, cand.theoretical_relevance),
            analytical_model_potential=min(1.0, cand.analytical_model_potential),
            tractability=min(1.0, cand.tractability),
        )
        opportunity = AnalyticalModelOpportunity(
            suitable=bool(cand.model_domains),
            domains=cand.model_domains,
            rationale=cand.model_opportunity_rationale,
        )

        return ResearchGap(
            title=cand.title,
            gap_type=GapType(cand.gap_type),
            description=description,
            why_it_matters=cand.why_it_matters,
            supporting_synthesis_statement_ids=cand.supporting_synthesis_statement_ids,
            supporting_evidence_ids=sorted(supporting_evidence),
            contradiction_statement_ids=cand.contradiction_statement_ids,
            relevant_paper_identity_ids=relevant_papers,
            supporting_papers=supporting_papers_count,
            supporting_evidence_items=supporting_evidence_count,
            contradicting_papers=contradicting_papers_count,
            strength=strength,
            confidence=cand.confidence,
            scope=cand.scope,
            limitations=cand.limitations,
            status=GapStatus.candidate,
            ranking=ranking,
            analytical_model_opportunity=opportunity,
            metadata={},
        )


def _composite_of(gap_artifacts: list[Any], gid: str) -> float:
    for env in gap_artifacts:
        if env.artifact_id == gid:
            gap = env.parse_payload(ResearchGap)
            return gap.ranking.composite if gap.ranking else 0.0
    return 0.0


class GapAnalyzerPlugin(Plugin):
    def __init__(self, model_role: str | None = None) -> None:
        self._model_role_override = model_role
        self._service: GapAnalyzerService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="literature.gap_analyzer",
            version="0.1.0",
            plugin_type="literature",
            description="Evidence-grounded research gap analysis (Phase 2H)",
            provides=["gap_analyzer.default"],
            requires=["model_router.default", "artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        lit_cfg: dict[str, Any] = {}
        if "literature" in cfg and isinstance(cfg["literature"], dict):
            lit_cfg = (
                cfg["literature"].get("gap", {})
                if isinstance(cfg["literature"].get("gap"), dict)
                else {}
            )
        model_role = self._model_role_override or lit_cfg.get("model_role") or "reasoning"
        max_statements = int(lit_cfg.get("max_statements", 200))
        max_gaps = int(lit_cfg.get("max_gaps", 50))
        max_model_calls = int(lit_cfg.get("max_model_calls", 20))

        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        self._service = GapAnalyzerService(
            model_router=router,
            artifact_store=store,
            model_role=str(model_role),
            max_statements=max_statements,
            max_gaps=max_gaps,
            max_model_calls=max_model_calls,
            events=ctx.events,
        )
        ctx.register("gap_analyzer.default", self._service)
