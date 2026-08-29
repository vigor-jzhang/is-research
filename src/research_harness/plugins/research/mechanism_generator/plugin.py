"""Phase 3A mechanism generation — produces structured MechanismCandidate
artifacts for a selected gap.

Mechanisms are candidate hypotheses. Element-level grounding distinguishes
literature-supported elements (must cite existing synthesis/evidence artifact
ids) from research inferences, new hypotheses, and modeling assumptions.
Deterministic support counts; raw evaluation dimensions kept separate.
No equations/propositions (Phase 3B).
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
from research_harness.research.schemas.gap import AnalyticalModelOpportunity, ResearchGap
from research_harness.research.schemas.mechanism import (
    GapSelection,
    GroundingElement,
    KnowledgeBasis,
    MechanismAnalysis,
    MechanismAnalysisExecution,
    MechanismAnalysisStatus,
    MechanismCandidate,
    MechanismEvaluation,
    MechanismStatus,
    SelectionStatus,
)
from research_harness.research.schemas.synthesis import SynthesisStatement

logger = logging.getLogger(__name__)

_MECH_DOMAINS = [
    "strategic interaction",
    "information asymmetry",
    "platform behavior",
    "pricing",
    "technology adoption",
    "incentives",
    "competition",
    "mechanism design",
    "industrial organization",
    "entry deterrence",
    "market structure",
    "market design",
    "network effects",
    "information economics",
    "contract theory",
    "game theory",
    "auction theory",
    "price discrimination",
    "signaling",
    "screening",
    "two-sided markets",
    "oligopoly",
    "behavioral economics",
    "digital platforms",
    "economic regulation",
    "welfare economics",
    "competition policy",
]


class _GroundingItem(BaseModel):
    element: str
    basis: str
    source_ids: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class _EvalItem(BaseModel):
    gap_alignment: float = Field(default=0.5, ge=0.0, le=1.0)
    theoretical_coherence: float = Field(default=0.5, ge=0.0, le=1.0)
    novelty_within_reviewed_corpus: float = Field(default=0.5, ge=0.0, le=1.0)
    analytical_tractability: float = Field(default=0.5, ge=0.0, le=1.0)
    managerial_economic_relevance: float = Field(default=0.5, ge=0.0, le=1.0)
    is_relevance: float = Field(default=0.5, ge=0.0, le=1.0)

    model_config = {"extra": "forbid"}


class _OpportunityItem(BaseModel):
    suitable: bool = False
    domains: list[str] = Field(default_factory=list)
    rationale: str | None = None

    model_config = {"extra": "forbid"}


class _CandidateItem(BaseModel):
    name: str
    description: str
    actors: list[str] = Field(default_factory=list)
    strategic_interactions: list[str] = Field(default_factory=list)
    information_structure: str | None = None
    incentives: list[str] = Field(default_factory=list)
    causal_logic: str
    key_assumptions: list[str] = Field(default_factory=list)
    expected_outcomes: list[str] = Field(default_factory=list)
    boundary_conditions: list[str] = Field(default_factory=list)
    literature_support_ids: list[str] = Field(default_factory=list)
    grounding: list[_GroundingItem] = Field(default_factory=list)
    analytical_model_potential: _OpportunityItem | None = None
    evaluation: _EvalItem | None = None

    @field_validator("name", "description", "causal_logic")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must be non-empty")
        return v

    model_config = {"extra": "forbid"}


class _CandidatesResponse(BaseModel):
    candidates: list[_CandidateItem]

    model_config = {"extra": "forbid"}


class MechanismGeneratorService:
    def __init__(
        self,
        model_router: Any,
        artifact_store: Any,
        model_role: str = "reasoning",
        max_candidates: int = 5,
        max_model_calls: int = 20,
        events: Any | None = None,
    ) -> None:
        self._router = model_router
        self._store = artifact_store
        self._model_role = model_role
        self._max_candidates = max_candidates
        self._max_model_calls = max_model_calls
        self._events = events

    @property
    def generator_id(self) -> str:
        return "research.mechanism_generator"

    async def generate(self, gap_selection_id: str) -> str:
        """Generate mechanism candidates for a selected gap.

        Returns the MechanismAnalysisExecution artifact id.
        """
        # Idempotency: reuse only a completed successful generation for the
        # same selection + generator role
        existing = await self._store.list(artifact_type="mechanism_analysis_execution")
        for env in existing:
            try:
                ex = MechanismAnalysisExecution.model_validate(env.payload)
                if (
                    ex.gap_selection_id == gap_selection_id
                    and ex.generator_role == self._model_role
                    and ex.completed_at is not None
                    and ex.candidates_created > 0
                ):
                    return env.artifact_id
            except Exception:
                continue

        sel_env = await self._store.get(gap_selection_id)
        selection = sel_env.parse_payload(GapSelection)
        if selection.status != SelectionStatus.approved:
            raise ValueError(
                f"GapSelection {gap_selection_id} is {selection.status.value}; "
                "gap selection must be approved before mechanism generation"
            )
        gap_env = await self._store.get(selection.selected_gap_id)
        gap = gap_env.parse_payload(ResearchGap)
        # Selected gap may have been superseded by an approved artifact; follow
        # the supersedes chain to the latest research_gap for this selection
        gap = await self._resolve_selected_gap(selection)

        started = datetime.now(UTC)
        exec_record = MechanismAnalysisExecution(
            gap_selection_id=gap_selection_id,
            gap_id=gap_env.artifact_id,
            candidates_created=0,
            candidates_rejected=0,
            generator_role=self._model_role,
            failures=[],
            counts={"model_calls": 0, "max_candidates": self._max_candidates},
            started_at=started,
        )

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="mechanism.generation.started",
                        source="research.mechanism_generator",
                        payload={
                            "gap_selection_id": gap_selection_id,
                            "gap_id": gap_env.artifact_id,
                        },
                    )
                )
            except Exception:
                pass

        context = await self._load_context(gap)
        prompt = self._build_prompt(selection, gap, context)
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You are a theoretical IS researcher developing candidate mechanisms "
                        "for a selected research gap. Return valid JSON matching the schema. "
                        "Never include chain-of-thought."
                    ),
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self._build_schema(),
            temperature=0.0,
            metadata={"gap_selection_id": gap_selection_id},
        )

        try:
            response = await self._router.complete(self._model_role, request)
            exec_record.counts["model_calls"] = 1
        except Exception as e:
            exec_record.failures.append({"error": f"model call failed: {e}"})
            exec_record.completed_at = datetime.now(UTC)
            exec_env = ArtifactEnvelope.create(
                payload=exec_record,
                artifact_type="mechanism_analysis_execution",
                producer="research.mechanism_generator",
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
                artifact_type="mechanism_analysis_execution",
                producer="research.mechanism_generator",
            )
            await self._store.put(exec_env)
            return exec_env.artifact_id

        try:
            parsed = _CandidatesResponse.model_validate(data)
        except Exception as e:
            exec_record.failures.append({"error": f"invalid candidate response: {e}"})
            exec_record.completed_at = datetime.now(UTC)
            exec_env = ArtifactEnvelope.create(
                payload=exec_record,
                artifact_type="mechanism_analysis_execution",
                producer="research.mechanism_generator",
            )
            await self._store.put(exec_env)
            return exec_env.artifact_id

        candidate_ids: list[str] = []
        rejected = 0
        for item in parsed.candidates[: self._max_candidates]:
            try:
                candidate = self._build_candidate(
                    item, selection, gap, context, gap_env.artifact_id, gap_selection_id
                )
            except ValueError as e:
                rejected += 1
                exec_record.failures.append({"candidate": item.name[:60], "error": str(e)})
                continue
            c_env = ArtifactEnvelope.create(
                payload=candidate,
                artifact_type="mechanism_candidate",
                producer=f"research.mechanism_generator:{self._model_role}",
            )
            await self._store.put(c_env)
            # Provenance: candidate derived_from the selected gap + statements + evidence
            try:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=gap_env.artifact_id,
                        target_artifact_id=c_env.artifact_id,
                        producer="research.mechanism_generator",
                    )
                )
            except Exception:
                pass
            # Provenance: candidate derived_from referenced statements + evidence
            # (literature_support_ids + grounding source_ids of literature-backed elements)
            referenced = set(candidate.literature_support_ids)
            for g in candidate.grounding:
                if g.basis == KnowledgeBasis.literature_supported:
                    referenced.update(g.source_ids)
            for sid in context["stmt_ids"]:
                if sid in referenced:
                    try:
                        await self._store.add_provenance(
                            ProvenanceLink(
                                relation=ProvenanceRelation.derived_from,
                                source_artifact_id=sid,
                                target_artifact_id=c_env.artifact_id,
                                producer="research.mechanism_generator",
                            )
                        )
                    except Exception:
                        pass
            for eid in context["ev_ids"]:
                if eid in referenced:
                    try:
                        await self._store.add_provenance(
                            ProvenanceLink(
                                relation=ProvenanceRelation.derived_from,
                                source_artifact_id=eid,
                                target_artifact_id=c_env.artifact_id,
                                producer="research.mechanism_generator",
                            )
                        )
                    except Exception:
                        pass
            candidate_ids.append(c_env.artifact_id)

        exec_record.candidates_rejected = rejected
        exec_record.candidates_created = len(candidate_ids)
        exec_record.completed_at = datetime.now(UTC)

        exec_env = ArtifactEnvelope.create(
            payload=exec_record,
            artifact_type="mechanism_analysis_execution",
            producer="research.mechanism_generator",
        )
        await self._store.put(exec_env)

        analysis = MechanismAnalysis(
            gap_selection_id=gap_selection_id,
            gap_id=gap_env.artifact_id,
            candidate_ids=candidate_ids,
            status=MechanismAnalysisStatus.generated,
            generator_role=self._model_role,
            summary=(
                f"Generated {len(candidate_ids)} mechanism candidate(s) for selected gap; "
                f"{rejected} rejected for grounding failures."
            ),
        )
        a_env = ArtifactEnvelope.create(
            payload=analysis,
            artifact_type="mechanism_analysis",
            producer="research.mechanism_generator",
        )
        await self._store.put(a_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=gap_selection_id,
                target_artifact_id=a_env.artifact_id,
                producer="research.mechanism_generator",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=exec_env.artifact_id,
                target_artifact_id=a_env.artifact_id,
                producer="research.mechanism_generator",
            )
        )
        for cid in candidate_ids:
            try:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=cid,
                        target_artifact_id=a_env.artifact_id,
                        producer="research.mechanism_generator",
                    )
                )
            except Exception:
                pass

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="mechanism.generation.completed",
                        source="research.mechanism_generator",
                        payload={
                            "analysis_id": a_env.artifact_id,
                            "execution_id": exec_env.artifact_id,
                            "candidates": len(candidate_ids),
                            "rejected": rejected,
                        },
                    )
                )
            except Exception:
                pass

        return exec_env.artifact_id

    async def _resolve_selected_gap(self, selection: GapSelection) -> ResearchGap:
        """Follow supersedes chain to the latest research_gap for the selection."""
        current_id = selection.selected_gap_id
        seen: set[str] = set()
        gap = (await self._store.get(current_id)).parse_payload(ResearchGap)
        while current_id not in seen:
            seen.add(current_id)
            children = await self._store.get_children(current_id)
            superseded_by = [
                c.target_artifact_id for c in children if c.relation.value == "supersedes"
            ]
            if not superseded_by:
                return gap
            current_id = superseded_by[0]
            gap = (await self._store.get(current_id)).parse_payload(ResearchGap)
        return gap

    async def _load_context(self, gap: ResearchGap) -> dict[str, Any]:
        """Load statements + evidence referenced by the selected gap."""
        stmt_ids: set[str] = set()
        ev_ids: set[str] = set()
        stmts: dict[str, SynthesisStatement] = {}
        for sid in gap.supporting_synthesis_statement_ids + gap.contradiction_statement_ids:
            try:
                env = await self._store.get(sid)
                stmt = env.parse_payload(SynthesisStatement)
                stmts[sid] = stmt
                stmt_ids.add(sid)
                ev_ids.update(stmt.supporting_evidence_ids)
                ev_ids.update(stmt.conflicting_evidence_ids)
            except Exception:
                continue
        ev_ids.update(gap.supporting_evidence_ids)
        return {"stmt_ids": stmt_ids, "ev_ids": ev_ids, "stmts": stmts}

    def _build_candidate(
        self,
        item: _CandidateItem,
        selection: GapSelection,
        gap: ResearchGap,
        context: dict[str, Any],
        gap_artifact_id: str,
        gap_selection_id: str,
    ) -> MechanismCandidate:
        valid_stmt = context["stmt_ids"]
        valid_ev = context["ev_ids"]
        valid_ids = valid_stmt | valid_ev

        # Grounding validation: literature_supported elements must cite existing ids
        grounding: list[GroundingElement] = []
        for gi in item.grounding:
            if gi.basis not in KnowledgeBasis.values():
                raise ValueError(f"invalid knowledge basis {gi.basis!r}")
            basis = KnowledgeBasis(gi.basis)
            if basis == KnowledgeBasis.literature_supported:
                if not gi.source_ids:
                    raise ValueError(
                        f"literature_supported element {gi.element[:60]!r} has no source_ids"
                    )
                bad = [sid for sid in gi.source_ids if sid not in valid_ids]
                if bad:
                    raise ValueError(
                        f"literature_supported element {gi.element[:60]!r} cites "
                        f"unknown artifact ids: {bad[:3]}"
                    )
            grounding.append(
                GroundingElement(element=gi.element, basis=basis, source_ids=list(gi.source_ids))
            )

        # Literature support ids must all resolve to the gap's context artifacts
        bad = [sid for sid in item.literature_support_ids if sid not in valid_ids]
        if bad:
            raise ValueError(
                f"unsupported literature ids {bad[:3]!r} (not in gap's synthesis/evidence)"
            )

        # Deterministic support counts
        support_ids = set(item.literature_support_ids)
        papers: set[str] = set()
        for sid in support_ids:
            stmt = context["stmts"].get(sid)
            if stmt is not None:
                papers.update(stmt.supporting_paper_identity_ids)
        ev_count = len(support_ids & valid_ev)
        for eid in support_ids & valid_ev:
            for stmt in context["stmts"].values():
                if eid in stmt.supporting_evidence_ids:
                    papers.update(stmt.supporting_paper_identity_ids)

        opportunity = None
        if item.analytical_model_potential is not None:
            opportunity = AnalyticalModelOpportunity(
                suitable=item.analytical_model_potential.suitable,
                domains=item.analytical_model_potential.domains,
                rationale=item.analytical_model_potential.rationale,
            )
        evaluation = None
        if item.evaluation is not None:
            evaluation = MechanismEvaluation(
                gap_alignment=item.evaluation.gap_alignment,
                theoretical_coherence=item.evaluation.theoretical_coherence,
                novelty_within_reviewed_corpus=item.evaluation.novelty_within_reviewed_corpus,
                analytical_tractability=item.evaluation.analytical_tractability,
                managerial_economic_relevance=item.evaluation.managerial_economic_relevance,
                is_relevance=item.evaluation.is_relevance,
            )

        return MechanismCandidate(
            gap_id=gap_artifact_id,
            gap_selection_id=gap_selection_id,
            name=item.name,
            description=item.description,
            actors=list(item.actors),
            strategic_interactions=list(item.strategic_interactions),
            information_structure=item.information_structure,
            incentives=list(item.incentives),
            causal_logic=item.causal_logic,
            key_assumptions=list(item.key_assumptions),
            expected_outcomes=list(item.expected_outcomes),
            boundary_conditions=list(item.boundary_conditions),
            literature_support_ids=sorted(support_ids),
            grounding=grounding,
            literature_support_papers=len(papers),
            literature_support_evidence_items=ev_count,
            analytical_model_potential=opportunity,
            evaluation=evaluation,
            status=MechanismStatus.candidate,
            model_role=self._model_role,
        )

    def _build_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "actors": {"type": "array", "items": {"type": "string"}},
                            "strategic_interactions": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "information_structure": {"type": "string"},
                            "incentives": {"type": "array", "items": {"type": "string"}},
                            "causal_logic": {"type": "string"},
                            "key_assumptions": {"type": "array", "items": {"type": "string"}},
                            "expected_outcomes": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "boundary_conditions": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "literature_support_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "grounding": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "element": {"type": "string"},
                                        "basis": {
                                            "type": "string",
                                            "enum": KnowledgeBasis.values(),
                                        },
                                        "source_ids": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                    },
                                    "required": ["element", "basis"],
                                    "additionalProperties": False,
                                },
                            },
                            "analytical_model_potential": {
                                "type": "object",
                                "properties": {
                                    "suitable": {"type": "boolean"},
                                    "domains": {"type": "array", "items": {"type": "string"}},
                                    "rationale": {"type": "string"},
                                },
                                "required": ["suitable"],
                                "additionalProperties": False,
                            },
                            "evaluation": {
                                "type": "object",
                                "properties": {
                                    "gap_alignment": {
                                        "type": "number",
                                        "minimum": 0,
                                        "maximum": 1,
                                    },
                                    "theoretical_coherence": {
                                        "type": "number",
                                        "minimum": 0,
                                        "maximum": 1,
                                    },
                                    "novelty_within_reviewed_corpus": {
                                        "type": "number",
                                        "minimum": 0,
                                        "maximum": 1,
                                    },
                                    "analytical_tractability": {
                                        "type": "number",
                                        "minimum": 0,
                                        "maximum": 1,
                                    },
                                    "managerial_economic_relevance": {
                                        "type": "number",
                                        "minimum": 0,
                                        "maximum": 1,
                                    },
                                    "is_relevance": {
                                        "type": "number",
                                        "minimum": 0,
                                        "maximum": 1,
                                    },
                                },
                                "additionalProperties": False,
                            },
                        },
                        "required": ["name", "description", "causal_logic"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["candidates"],
            "additionalProperties": False,
        }

    def _build_prompt(
        self,
        selection: GapSelection,
        gap: ResearchGap,
        context: dict[str, Any],
    ) -> str:
        stmt_lines = []
        for sid in sorted(context["stmt_ids"]):
            stmt = context["stmts"].get(sid)
            if stmt is None:
                continue
            stmt_lines.append(
                f"[{sid}] ({stmt.type.value}, support {stmt.support_type.value}, "
                f"papers {stmt.papers_supporting}, evidence {stmt.evidence_items_supporting}): "
                f"{stmt.statement[:240]}"
            )
            if stmt.supporting_evidence_ids:
                stmt_lines.append(f"    evidence: {', '.join(stmt.supporting_evidence_ids[:8])}")
        ev_list = sorted(context["ev_ids"])
        opportunity = gap.analytical_model_opportunity
        opp_text = (
            f"suitable={opportunity.suitable}; domains={', '.join(opportunity.domains)}; "
            f"{opportunity.rationale or ''}"
            if opportunity
            else "not assessed"
        )

        return f"""Develop structured candidate mechanisms for the selected research gap.

Selected gap: {gap.title} ({gap.gap_type.value})
Description: {gap.description[:400]}
Support: {gap.supporting_papers} papers, {gap.supporting_evidence_items} evidence items.
Analytical model opportunity: {opp_text}

Grounded synthesis statements of this gap (IDs are authoritative; cite ONLY these IDs
for literature_supported elements and literature_support_ids):

{chr(10).join(stmt_lines) if stmt_lines else "(no statements loaded)"}

Available evidence artifact ids: {", ".join(ev_list[:30]) if ev_list else "(none)"}

Produce {self._max_candidates} distinct candidate mechanisms. Each candidate must have:
- name, description, actors, strategic_interactions, information_structure,
  incentives, causal_logic, key_assumptions, expected_outcomes, boundary_conditions
- literature_support_ids: subset of the IDs above that the mechanism builds on
- grounding: element-level knowledge basis. Use basis values:
  literature_supported (cite source_ids from the IDs above), research_inference
  (inferred from the reviewed corpus), new_hypothesis (novel hypothesis),
  modeling_assumption (assumed for future modeling). Do NOT present novel
  hypotheses as established facts.
- analytical_model_potential (suitable + domains from: {", ".join(_MECH_DOMAINS)})
- evaluation: raw 0..1 scores on gap_alignment, theoretical_coherence,
  novelty_within_reviewed_corpus, analytical_tractability,
  managerial_economic_relevance, is_relevance

Rules:
- NEVER cite an ID not shown above.
- No equations, optimization problems, or propositions (later phase).
- Return valid JSON only, no chain-of-thought.
"""


class MechanismGeneratorPlugin(Plugin):
    def __init__(self, model_role: str | None = None) -> None:
        self._model_role_override = model_role
        self._service: MechanismGeneratorService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.mechanism_generator",
            version="0.1.0",
            plugin_type="research",
            description="Evidence-grounded mechanism candidate generation (Phase 3A)",
            provides=["mechanism_generator.default"],
            requires=["model_router.default", "artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        research_cfg: dict[str, Any] = {}
        if "research" in cfg and isinstance(cfg["research"], dict):
            research_cfg = (
                cfg["research"].get("mechanism", {})
                if isinstance(cfg["research"].get("mechanism"), dict)
                else {}
            )
        model_role = (
            self._model_role_override
            or research_cfg.get("generator_role")
            or research_cfg.get("model_role")
            or "reasoning"
        )
        max_candidates = int(research_cfg.get("max_candidates", 5))
        max_model_calls = int(research_cfg.get("max_model_calls", 20))

        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        self._service = MechanismGeneratorService(
            model_router=router,
            artifact_store=store,
            model_role=str(model_role),
            max_candidates=max_candidates,
            max_model_calls=max_model_calls,
            events=ctx.events,
        )
        ctx.register("mechanism_generator.default", self._service)
