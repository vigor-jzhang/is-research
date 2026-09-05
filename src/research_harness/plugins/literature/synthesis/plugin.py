"""Phase 2G literature synthesizer — evidence-only cross-paper synthesis.

Hierarchical bounded synthesis:
    paper profiles -> small profile/evidence batches -> candidate themes
    -> cross-batch consolidation -> LiteratureSynthesis

Strict structured output; hallucinated evidence IDs rejected; deterministic
support metrics computed by the orchestrator (never invented by the model);
consensus vs contradiction preserved with evidence from both sides.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.evidence import EvidenceItem
from research_harness.research.schemas.evidence_extraction import EvidenceCorpus
from research_harness.research.schemas.research_profile import PaperResearchProfile
from research_harness.research.schemas.synthesis import (
    LiteratureSynthesis,
    SupportType,
    SynthesisExecution,
    SynthesisStatement,
    SynthesisStatementType,
    SynthesisTheme,
)

logger = logging.getLogger(__name__)

_MULTI_PAPER_TYPES = {
    SynthesisStatementType.consensus,
    SynthesisStatementType.pattern,
    SynthesisStatementType.mixed,
    SynthesisStatementType.boundary_condition,
    SynthesisStatementType.methodological_pattern,
    SynthesisStatementType.theoretical_pattern,
    SynthesisStatementType.limitation_pattern,
    SynthesisStatementType.future_research_pattern,
}


class _StatementCandidate(BaseModel):
    statement: str
    type: str
    supporting_evidence_ids: list[str] = Field(min_length=1)
    conflicting_evidence_ids: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("statement must be non-empty")
        return v

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in SynthesisStatementType.values():
            raise ValueError(f"invalid synthesis statement type {v!r}")
        return v

    model_config = {"extra": "forbid"}


class _ThemeCandidate(BaseModel):
    title: str
    dimension: str | None = None
    statements: list[_StatementCandidate] = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("theme title must be non-empty")
        return v

    model_config = {"extra": "forbid"}


class _SynthesisResponse(BaseModel):
    themes: list[_ThemeCandidate]

    model_config = {"extra": "forbid"}


def _normalize_theme_title(title: str) -> str:
    return " ".join(title.strip().lower().split()).rstrip(".,;:!?")


class LiteratureSynthesizerService:
    def __init__(
        self,
        model_router: Any,
        artifact_store: Any,
        model_role: str = "reasoning",
        batch_profiles: int = 3,
        max_evidence_per_profile: int = 12,
        max_batches: int = 20,
        max_model_calls: int = 100,
        events: Any | None = None,
    ) -> None:
        self._router = model_router
        self._store = artifact_store
        self._model_role = model_role
        self._batch_profiles = batch_profiles
        self._max_evidence_per_profile = max_evidence_per_profile
        self._max_batches = max_batches
        self._max_model_calls = max_model_calls
        self._events = events

    @property
    def synthesizer_id(self) -> str:
        return "literature.synthesis"

    def _build_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "themes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "dimension": {"type": "string"},
                            "statements": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "statement": {"type": "string"},
                                        "type": {
                                            "type": "string",
                                            "enum": SynthesisStatementType.values(),
                                        },
                                        "supporting_evidence_ids": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "conflicting_evidence_ids": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "confidence": {
                                            "type": "number",
                                            "minimum": 0,
                                            "maximum": 1,
                                        },
                                    },
                                    "required": [
                                        "statement",
                                        "type",
                                        "supporting_evidence_ids",
                                    ],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["title", "statements"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["themes"],
            "additionalProperties": False,
        }

    async def run(self, evidence_corpus_id: str) -> str:
        """Synthesize an EvidenceCorpus. Returns SynthesisExecution artifact id."""
        # Idempotency: reuse only a completed successful execution with matching config
        existing = await self._store.list(artifact_type="synthesis_execution")
        for env in existing:
            try:
                ex = SynthesisExecution.model_validate(env.payload)
                if (
                    ex.evidence_corpus_id == evidence_corpus_id
                    and ex.model_role == self._model_role
                    and ex.counts.get("batch_profiles") == self._batch_profiles
                    and ex.counts.get("max_batches") == self._max_batches
                    and ex.completed_at is not None
                    and (ex.themes_created > 0 or ex.statements_created > 0)
                ):
                    return env.artifact_id
            except Exception:
                continue

        corp_env = await self._store.get(evidence_corpus_id)
        corpus = corp_env.parse_payload(EvidenceCorpus)

        started = datetime.now(UTC)
        exec_record = SynthesisExecution(
            evidence_corpus_id=evidence_corpus_id,
            profiles_processed=0,
            evidence_items_processed=0,
            batches_processed=0,
            batches_failed=0,
            themes_created=0,
            statements_created=0,
            statements_rejected=0,
            model_role=self._model_role,
            failures=[],
            counts={
                "batch_profiles": self._batch_profiles,
                "max_batches": self._max_batches,
                "model_calls": 0,
            },
            started_at=started,
        )

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="synthesis.started",
                        source="literature.synthesis",
                        payload={
                            "corpus_id": evidence_corpus_id,
                            "profiles": len(corpus.paper_profile_ids),
                        },
                    )
                )
            except Exception:
                pass

        # Load profiles + evidence, build evidence_id -> paper_identity_id map
        profiles: list[PaperResearchProfile] = []
        profile_envs: list[Any] = []
        ev_by_id: dict[str, EvidenceItem] = {}
        paper_by_evidence: dict[str, str] = {}
        for p_env in await self._load_profiles(corpus.paper_profile_ids):
            profile_envs.append(p_env)
            prof = p_env.parse_payload(PaperResearchProfile)
            profiles.append(prof)
            for eid in prof.evidence_item_ids:
                paper_by_evidence[eid] = prof.paper_identity_id

        # Also ensure corpus-wide evidence items are mapped (fallback to FullTextDocument)
        for eid in corpus.evidence_item_ids:
            if eid not in paper_by_evidence:
                try:
                    e_env = await self._store.get(eid)
                    ev = e_env.parse_payload(EvidenceItem)
                    ev_by_id[eid] = ev
                    # Map via FullTextDocument.paper_identity_id
                    doc_env = await self._store.get(ev.source_artifact_id)
                    from research_harness.research.schemas.full_text import FullTextDocument

                    doc = doc_env.parse_payload(FullTextDocument)
                    paper_by_evidence[eid] = doc.paper_identity_id
                except Exception:
                    continue
        for prof in profiles:
            for eid in prof.evidence_item_ids:
                try:
                    e_env = await self._store.get(eid)
                    ev_by_id[eid] = e_env.parse_payload(EvidenceItem)
                except Exception:
                    continue

        exec_record.profiles_processed = len(profiles)
        exec_record.evidence_items_processed = len(paper_by_evidence)

        # Bounded batches of profiles
        batches = [
            profiles[i : i + self._batch_profiles]
            for i in range(0, len(profiles), self._batch_profiles)
        ]
        batches = batches[: self._max_batches]

        theme_candidates: dict[str, _ThemeCandidate] = {}
        statement_candidates: list[tuple[str, _StatementCandidate]] = []  # (theme_title, stmt)
        model_calls = 0

        for batch_idx, batch in enumerate(batches):
            if model_calls >= self._max_model_calls:
                exec_record.failures.append(
                    {"batch_index": batch_idx, "error": "max_model_calls budget reached"}
                )
                break
            try:
                themes = await self._extract_batch(batch, ev_by_id)
                model_calls += 1
                exec_record.batches_processed += 1
            except Exception as e:
                logger.warning("synthesis batch %s failed: %s", batch_idx, e)
                model_calls += 1
                exec_record.batches_failed += 1
                exec_record.failures.append({"batch_index": batch_idx, "error": str(e)})
                continue

            for theme in themes:
                key = _normalize_theme_title(theme.title)
                if key not in theme_candidates:
                    theme_candidates[key] = theme
                else:
                    # Merge statements into existing theme (cross-batch consolidation)
                    theme_candidates[key].statements.extend(theme.statements)
                for stmt in theme.statements:
                    statement_candidates.append((key, stmt))

        exec_record.counts["model_calls"] = model_calls

        # Validate + compute deterministic support metrics, then persist
        all_statement_ids: list[str] = []
        theme_artifacts: list[Any] = []
        rejected = 0
        for _key, theme in sorted(theme_candidates.items()):
            # Consolidate statements: normalize + dedup by statement text
            seen_statements: set[str] = set()
            consolidated: list[SynthesisStatement] = []
            for cand in theme.statements:
                norm = " ".join(cand.statement.strip().lower().split())
                if norm in seen_statements:
                    continue
                seen_statements.add(norm)
                try:
                    stmt = self._build_statement(cand, ev_by_id, paper_by_evidence)
                except ValueError as e:
                    rejected += 1
                    exec_record.failures.append(
                        {"theme": theme.title, "statement": cand.statement[:80], "error": str(e)}
                    )
                    continue
                consolidated.append(stmt)

            if not consolidated:
                continue

            statement_ids: list[str] = []
            for stmt in consolidated:
                s_env = ArtifactEnvelope.create(
                    payload=stmt,
                    artifact_type="synthesis_statement",
                    producer=f"literature.synthesis:{self._model_role}",
                )
                await self._store.put(s_env)
                for eid in stmt.supporting_evidence_ids + stmt.conflicting_evidence_ids:
                    try:
                        await self._store.add_provenance(
                            ProvenanceLink(
                                relation=ProvenanceRelation.derived_from,
                                source_artifact_id=eid,
                                target_artifact_id=s_env.artifact_id,
                                producer="literature.synthesis",
                            )
                        )
                    except Exception:
                        pass
                statement_ids.append(s_env.artifact_id)
                all_statement_ids.append(s_env.artifact_id)

            evidence_ids = sorted(
                {
                    e
                    for stmt in consolidated
                    for e in stmt.supporting_evidence_ids + stmt.conflicting_evidence_ids
                }
            )
            paper_ids = sorted(
                {paper_by_evidence[e] for e in evidence_ids if e in paper_by_evidence}
            )
            theme_artifact = SynthesisTheme(
                title=theme.title,
                dimension=theme.dimension,
                statements=consolidated,
                evidence_item_ids=evidence_ids,
                paper_identity_ids=paper_ids,
                metadata={"statement_ids": statement_ids},
            )
            t_env = ArtifactEnvelope.create(
                payload=theme_artifact,
                artifact_type="synthesis_theme",
                producer=f"literature.synthesis:{self._model_role}",
            )
            await self._store.put(t_env)
            for sid in statement_ids:
                try:
                    await self._store.add_provenance(
                        ProvenanceLink(
                            relation=ProvenanceRelation.derived_from,
                            source_artifact_id=sid,
                            target_artifact_id=t_env.artifact_id,
                            producer="literature.synthesis",
                        )
                    )
                except Exception:
                    pass
            theme_artifacts.append(t_env)

        exec_record.statements_rejected = rejected
        exec_record.themes_created = len(theme_artifacts)
        exec_record.statements_created = len(all_statement_ids)
        exec_record.completed_at = datetime.now(UTC)

        exec_env = ArtifactEnvelope.create(
            payload=exec_record,
            artifact_type="synthesis_execution",
            producer="literature.synthesis",
        )
        await self._store.put(exec_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=evidence_corpus_id,
                target_artifact_id=exec_env.artifact_id,
                producer="literature.synthesis",
            )
        )

        # LiteratureSynthesis artifact
        synthesis = LiteratureSynthesis(
            evidence_corpus_id=evidence_corpus_id,
            theme_ids=[t_env.artifact_id for t_env in theme_artifacts],
            statement_ids=all_statement_ids,
            counts={
                "themes": len(theme_artifacts),
                "statements": len(all_statement_ids),
                "statements_rejected": rejected,
                "profiles_processed": exec_record.profiles_processed,
                "evidence_items_processed": exec_record.evidence_items_processed,
            },
            metadata={"model_role": self._model_role},
        )
        # Only persist a LiteratureSynthesis if themes were actually produced;
        # a fully-failed run leaves the execution (with failures) as the record.
        if not theme_artifacts:
            return exec_env.artifact_id
        syn_env = ArtifactEnvelope.create(
            payload=synthesis,
            artifact_type="literature_synthesis",
            producer="literature.synthesis",
        )
        await self._store.put(syn_env)
        for tid in synthesis.theme_ids:
            try:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=tid,
                        target_artifact_id=syn_env.artifact_id,
                        producer="literature.synthesis",
                    )
                )
            except Exception:
                pass
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=exec_env.artifact_id,
                target_artifact_id=syn_env.artifact_id,
                producer="literature.synthesis",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=evidence_corpus_id,
                target_artifact_id=syn_env.artifact_id,
                producer="literature.synthesis",
            )
        )

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="synthesis.completed",
                        source="literature.synthesis",
                        payload={
                            "synthesis_id": syn_env.artifact_id,
                            "execution_id": exec_env.artifact_id,
                            "themes": len(theme_artifacts),
                            "statements": len(all_statement_ids),
                        },
                    )
                )
            except Exception:
                pass

        return exec_env.artifact_id

    async def _load_profiles(self, profile_ids: list[str]) -> list[Any]:
        envs: list[Any] = []
        for pid in profile_ids:
            try:
                envs.append(await self._store.get(pid))
            except Exception:
                continue
        return envs

    async def _extract_batch(
        self, profiles: list[PaperResearchProfile], ev_by_id: dict[str, EvidenceItem]
    ) -> list[_ThemeCandidate]:
        """Run structured synthesis for one batch of profiles."""
        batch_text = []
        for prof in profiles:
            ev_lines = []
            for eid in prof.evidence_item_ids[: self._max_evidence_per_profile]:
                ev = ev_by_id.get(eid)
                if ev is None:
                    continue
                stmt = ev.statement[:300]
                cat = ev.category.value if ev.category else "?"
                pages = ",".join(
                    str(p) for p in (ev.locator.pages if ev.locator and ev.locator.pages else [])
                )
                ev_lines.append(f"  - [{eid}] ({cat}) pages[{pages}] conf {ev.confidence}: {stmt}")
            batch_text.append(
                f"Paper profile {prof.paper_identity_id} (evidence ids: {', '.join(prof.evidence_item_ids[: self._max_evidence_per_profile])}):\n"
                + ("\n".join(ev_lines) if ev_lines else "  (no evidence)")
            )

        # H21: the evidence text is extracted from arbitrary OA documents.
        from research_harness.research.prompt_safety import (
            DATA_ONLY_INSTRUCTION,
            fence_untrusted,
        )

        fenced_evidence = fence_untrusted("\n".join(batch_text), label="evidence items")

        prompt = f"""You are a literature synthesizer comparing research papers within a corpus.

Evidence items from the following paper profiles are provided (IDs are authoritative; you may ONLY cite IDs present below):

{fenced_evidence}

{DATA_ONLY_INSTRUCTION}

Task: produce cross-paper synthesis themes and statements grounded ONLY in the provided evidence.
- Every supporting_evidence_ids / conflicting_evidence_ids entry MUST be an evidence ID listed above.
- For each statement choose one type: {", ".join(SynthesisStatementType.values())}.
- 'consensus' = most papers agree; 'contradiction' = papers disagree (must cite conflicting evidence IDs from BOTH sides); 'mixed' = mixed evidence; 'pattern' = recurring pattern; others are dimension-specific patterns.
- Do not invent evidence, do not fabricate papers, do not claim literature-wide consensus from one paper.
- Return JSON only, matching the schema. No chain-of-thought.
"""
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content="You are a careful literature synthesizer. Return valid JSON matching the schema. Never include chain-of-thought.",
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self._build_schema(),
            temperature=0.0,
            metadata={"profiles": [p.paper_identity_id for p in profiles]},
        )

        try:
            response = await self._router.complete(self._model_role, request)
        except Exception as e:
            raise RuntimeError(
                f"synthesis model call failed (role {self._model_role!r}): {e}"
            ) from e

        content = response.message.content or ""
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"synthesis model returned invalid JSON: {content[:500]!r}: {e}"
            ) from e

        try:
            parsed = _SynthesisResponse.model_validate(data)
        except Exception as e:
            raise ValueError(f"invalid synthesis response: {e}") from e
        return parsed.themes

    def _build_statement(
        self,
        cand: _StatementCandidate,
        ev_by_id: dict[str, EvidenceItem],
        paper_by_evidence: dict[str, str],
    ) -> SynthesisStatement:
        """Validate grounding and compute deterministic support metrics."""
        # Grounding: every supporting evidence ID must exist in the corpus
        for eid in cand.supporting_evidence_ids:
            if eid not in ev_by_id:
                raise ValueError(f"hallucinated supporting evidence id {eid!r} (not in corpus)")
            if eid not in paper_by_evidence:
                raise ValueError(f"evidence id {eid!r} has no paper mapping")
        for eid in cand.conflicting_evidence_ids:
            if eid not in ev_by_id:
                raise ValueError(f"hallucinated conflicting evidence id {eid!r} (not in corpus)")
            # M32: the supporting loop checked the paper mapping, this one did
            # not. An unmapped conflicting id therefore raised KeyError six
            # lines later, after the statements had already been persisted,
            # orphaning artifacts and forcing a re-billed re-run. The call site
            # catches ValueError, not KeyError.
            if eid not in paper_by_evidence:
                raise ValueError(f"conflicting evidence id {eid!r} has no paper mapping")

        if cand.type not in SynthesisStatementType.values():
            raise ValueError(f"invalid synthesis statement type {cand.type!r}")
        stype = SynthesisStatementType(cand.type)
        supporting_papers = sorted({paper_by_evidence[e] for e in cand.supporting_evidence_ids})
        conflicting_papers = sorted({paper_by_evidence[e] for e in cand.conflicting_evidence_ids})

        if stype == SynthesisStatementType.contradiction and not cand.conflicting_evidence_ids:
            raise ValueError("contradiction statement requires conflicting_evidence_ids")

        papers_supporting = len(supporting_papers)
        papers_conflicting = len(conflicting_papers)
        evidence_items_supporting = len(cand.supporting_evidence_ids)
        evidence_items_conflicting = len(cand.conflicting_evidence_ids)

        # Deterministic support type: multi-paper only if >= 2 distinct papers
        support_type = (
            SupportType.multi_paper if papers_supporting >= 2 else SupportType.single_paper
        )

        return SynthesisStatement(
            statement=cand.statement,
            type=stype,
            supporting_evidence_ids=cand.supporting_evidence_ids,
            conflicting_evidence_ids=cand.conflicting_evidence_ids,
            supporting_paper_identity_ids=supporting_papers,
            conflicting_paper_identity_ids=conflicting_papers,
            papers_supporting=papers_supporting,
            evidence_items_supporting=evidence_items_supporting,
            papers_conflicting=papers_conflicting,
            evidence_items_conflicting=evidence_items_conflicting,
            support_type=support_type,
            confidence=cand.confidence,
            metadata={},
        )


class LiteratureSynthesisPlugin(Plugin):
    def __init__(self, model_role: str | None = None) -> None:
        self._model_role_override = model_role
        self._service: LiteratureSynthesizerService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="literature.synthesis",
            version="0.1.0",
            plugin_type="literature",
            description="Cross-paper evidence synthesis (Phase 2G)",
            provides=["literature_synthesizer.default"],
            requires=["model_router.default", "artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        lit_cfg: dict[str, Any] = {}
        if "literature" in cfg and isinstance(cfg["literature"], dict):
            lit_cfg = (
                cfg["literature"].get("synthesis", {})
                if isinstance(cfg["literature"].get("synthesis"), dict)
                else {}
            )
        model_role = self._model_role_override or lit_cfg.get("model_role") or "reasoning"
        batch_profiles = int(lit_cfg.get("batch_profiles", 3))
        max_evidence_per_profile = int(lit_cfg.get("max_evidence_per_profile", 12))
        max_batches = int(lit_cfg.get("max_batches", 20))
        max_model_calls = int(lit_cfg.get("max_model_calls", 100))

        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        self._service = LiteratureSynthesizerService(
            model_router=router,
            artifact_store=store,
            model_role=str(model_role),
            batch_profiles=batch_profiles,
            max_evidence_per_profile=max_evidence_per_profile,
            max_batches=max_batches,
            max_model_calls=max_model_calls,
            events=ctx.events,
        )
        ctx.register("literature_synthesizer.default", self._service)
