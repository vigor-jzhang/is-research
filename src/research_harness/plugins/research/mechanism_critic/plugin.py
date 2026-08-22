"""Phase 3A mechanism critic — independent critique of mechanism candidates
plus selection/revision into a durable SelectedMechanism.

The critic (role `critic`) identifies logical inconsistencies, unsupported
assumptions, mechanisms already explained by the reviewed literature, unclear
causal direction, unmodelable concepts, missing actors/incentives, and
alternative explanations. Selection (role `revision_role`) revises the
candidate in response to the critique; the original candidate is never
mutated (candidate -> critique -> revision -> SelectedMechanism).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.gap import AnalyticalModelOpportunity, ResearchGap
from research_harness.research.schemas.mechanism import (
    CritiqueCategory,
    CritiqueIssue,
    CritiqueSeverity,
    CritiqueVerdict,
    GroundingElement,
    KnowledgeBasis,
    MechanismAnalysis,
    MechanismAnalysisStatus,
    MechanismCandidate,
    MechanismCritique,
    MechanismEvaluation,
    SelectedMechanism,
)
from research_harness.research.schemas.synthesis import SynthesisStatement

logger = logging.getLogger(__name__)


class _IssueItem(BaseModel):
    category: str
    description: str
    severity: str = "medium"
    location: str | None = None

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        if v not in CritiqueCategory.values():
            raise ValueError(f"invalid critique category {v!r}")
        return v

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        if v not in CritiqueSeverity.values():
            raise ValueError(f"invalid severity {v!r}")
        return v

    model_config = {"extra": "forbid"}


class _CritiqueResponse(BaseModel):
    overall_assessment: str
    verdict: str
    revision_recommendations: list[str] = Field(default_factory=list)
    issues: list[_IssueItem] = Field(default_factory=list)

    @field_validator("overall_assessment")
    @classmethod
    def validate_assessment(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must be non-empty")
        return v

    @field_validator("verdict")
    @classmethod
    def validate_verdict(cls, v: str) -> str:
        if v not in CritiqueVerdict.values():
            raise ValueError(f"invalid verdict {v!r}")
        return v

    model_config = {"extra": "forbid"}


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


class _RevisionResponse(BaseModel):
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
    grounding: list[_GroundingItem] = Field(default_factory=list)
    revision_notes: list[str] = Field(default_factory=list)
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


class MechanismCriticService:
    def __init__(
        self,
        model_router: Any,
        artifact_store: Any,
        critic_role: str = "critic",
        revision_role: str = "reasoning",
        events: Any | None = None,
    ) -> None:
        self._router = model_router
        self._store = artifact_store
        self._critic_role = critic_role
        self._revision_role = revision_role
        self._events = events

    @property
    def critic_id(self) -> str:
        return "research.mechanism_critic"

    # ------------------------------------------------------------------
    # Critique
    # ------------------------------------------------------------------

    async def critique(self, candidate_id: str) -> str:
        """Critique a mechanism candidate. Returns the MechanismCritique id."""
        # Idempotency: reuse an existing critique of this candidate with same role
        existing = await self._store.list(artifact_type="mechanism_critique")
        for env in existing:
            try:
                crit = MechanismCritique.model_validate(env.payload)
                if (
                    crit.mechanism_candidate_id == candidate_id
                    and crit.model_role == self._critic_role
                ):
                    return env.artifact_id
            except Exception:
                continue

        c_env = await self._store.get(candidate_id)
        candidate = c_env.parse_payload(MechanismCandidate)
        context = await self._load_candidate_context(candidate)

        prompt = self._build_critique_prompt(candidate, context)
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You are an independent, skeptical critic of theoretical mechanisms. "
                        "Return valid JSON matching the schema. Never include chain-of-thought."
                    ),
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self._build_critique_schema(),
            temperature=0.0,
            metadata={"candidate_id": candidate_id},
        )
        try:
            response = await self._router.complete(self._critic_role, request)
            data = json.loads(response.message.content or "")
            parsed = _CritiqueResponse.model_validate(data)
        except Exception as e:
            raise ValueError(f"critique model call failed: {e}") from e

        critique = MechanismCritique(
            mechanism_candidate_id=candidate_id,
            gap_id=candidate.gap_id,
            issues=[
                CritiqueIssue(
                    category=CritiqueCategory(i.category),
                    description=i.description,
                    severity=CritiqueSeverity(i.severity),
                    location=i.location,
                )
                for i in parsed.issues
            ],
            overall_assessment=parsed.overall_assessment,
            verdict=CritiqueVerdict(parsed.verdict),
            revision_recommendations=list(parsed.revision_recommendations),
            model_role=self._critic_role,
        )
        crit_env = ArtifactEnvelope.create(
            payload=critique,
            artifact_type="mechanism_critique",
            producer=f"research.mechanism_critic:{self._critic_role}",
        )
        await self._store.put(crit_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=candidate_id,
                target_artifact_id=crit_env.artifact_id,
                producer="research.mechanism_critic",
            )
        )
        await self._update_analysis(candidate, crit_env.artifact_id, critiqued=True)
        return crit_env.artifact_id

    # ------------------------------------------------------------------
    # Selection / revision
    # ------------------------------------------------------------------

    async def select(self, candidate_id: str) -> str:
        """Revise the candidate in response to critiques; persist SelectedMechanism.

        Returns the SelectedMechanism artifact id.
        """
        # Idempotency: reuse an existing SelectedMechanism for this candidate + roles
        existing = await self._store.list(artifact_type="selected_mechanism")
        for env in existing:
            try:
                sm = SelectedMechanism.model_validate(env.payload)
                if (
                    sm.mechanism_candidate_id == candidate_id
                    and sm.model_role == self._revision_role
                ):
                    return env.artifact_id
            except Exception:
                continue

        c_env = await self._store.get(candidate_id)
        candidate = c_env.parse_payload(MechanismCandidate)

        # Critiques must exist before selection; run them if missing
        critique_ids = await self._critiques_for(candidate_id)
        if not critique_ids:
            critique_ids = [await self.critique(candidate_id)]
        critiques = [
            (await self._store.get(cid)).parse_payload(MechanismCritique) for cid in critique_ids
        ]

        context = await self._load_candidate_context(candidate)
        revision = await self._revise(candidate, candidate_id, critiques, context)

        selected = SelectedMechanism(
            gap_id=candidate.gap_id,
            gap_selection_id=candidate.gap_selection_id or "",
            mechanism_candidate_id=candidate_id,
            critique_ids=critique_ids,
            name=revision["name"],
            description=revision["description"],
            actors=revision["actors"],
            strategic_interactions=revision["strategic_interactions"],
            information_structure=revision["information_structure"],
            incentives=revision["incentives"],
            causal_logic=revision["causal_logic"],
            key_assumptions=revision["key_assumptions"],
            expected_outcomes=revision["expected_outcomes"],
            boundary_conditions=revision["boundary_conditions"],
            grounding=revision["grounding"],
            revision_notes=revision["revision_notes"],
            analytical_model_potential=revision["analytical_model_potential"],
            evaluation=revision["evaluation"],
            model_role=self._revision_role,
        )
        sm_env = ArtifactEnvelope.create(
            payload=selected,
            artifact_type="selected_mechanism",
            producer=f"research.mechanism_critic:{self._revision_role}",
        )
        await self._store.put(sm_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=candidate_id,
                target_artifact_id=sm_env.artifact_id,
                producer="research.mechanism_critic",
            )
        )
        for cid in critique_ids:
            try:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=cid,
                        target_artifact_id=sm_env.artifact_id,
                        producer="research.mechanism_critic",
                    )
                )
            except Exception:
                pass
        await self._update_analysis(candidate, selected=sm_env.artifact_id)
        return sm_env.artifact_id

    async def _revise(
        self,
        candidate: MechanismCandidate,
        candidate_id: str,
        critiques: list[MechanismCritique],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = self._build_revision_prompt(candidate, critiques, context)
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You revise a mechanism candidate in response to an independent "
                        "critique. Return valid JSON matching the schema. Never include "
                        "chain-of-thought."
                    ),
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self._build_revision_schema(),
            temperature=0.0,
            metadata={"candidate_id": candidate_id},
        )
        try:
            response = await self._router.complete(self._revision_role, request)
            data = json.loads(response.message.content or "")
            parsed = _RevisionResponse.model_validate(data)
        except Exception as e:
            # Fall back to the original candidate unchanged; the critique is
            # preserved and the selection is documented as unrevisioned
            logger.warning("revision model call failed (%s); selecting candidate unchanged", e)
            return {
                "name": candidate.name,
                "description": candidate.description,
                "actors": list(candidate.actors),
                "strategic_interactions": list(candidate.strategic_interactions),
                "information_structure": candidate.information_structure,
                "incentives": list(candidate.incentives),
                "causal_logic": candidate.causal_logic,
                "key_assumptions": list(candidate.key_assumptions),
                "expected_outcomes": list(candidate.expected_outcomes),
                "boundary_conditions": list(candidate.boundary_conditions),
                "grounding": list(candidate.grounding),
                "revision_notes": [
                    "revision model unavailable; original candidate selected unchanged"
                ],
                "analytical_model_potential": candidate.analytical_model_potential,
                "evaluation": candidate.evaluation,
            }

        # Rebuild grounding with validated bases
        grounding: list[GroundingElement] = []
        for gi in parsed.grounding:
            basis = (
                KnowledgeBasis(gi.basis)
                if gi.basis in KnowledgeBasis.values()
                else KnowledgeBasis.new_hypothesis
            )
            grounding.append(
                GroundingElement(element=gi.element, basis=basis, source_ids=list(gi.source_ids))
            )

        opportunity = None
        if parsed.analytical_model_potential is not None:
            opportunity = AnalyticalModelOpportunity(
                suitable=parsed.analytical_model_potential.suitable,
                domains=parsed.analytical_model_potential.domains,
                rationale=parsed.analytical_model_potential.rationale,
            )
        evaluation = None
        if parsed.evaluation is not None:
            evaluation = MechanismEvaluation(
                gap_alignment=parsed.evaluation.gap_alignment,
                theoretical_coherence=parsed.evaluation.theoretical_coherence,
                novelty_within_reviewed_corpus=parsed.evaluation.novelty_within_reviewed_corpus,
                analytical_tractability=parsed.evaluation.analytical_tractability,
                managerial_economic_relevance=parsed.evaluation.managerial_economic_relevance,
                is_relevance=parsed.evaluation.is_relevance,
            )
        return {
            "name": parsed.name,
            "description": parsed.description,
            "actors": list(parsed.actors),
            "strategic_interactions": list(parsed.strategic_interactions),
            "information_structure": parsed.information_structure,
            "incentives": list(parsed.incentives),
            "causal_logic": parsed.causal_logic,
            "key_assumptions": list(parsed.key_assumptions),
            "expected_outcomes": list(parsed.expected_outcomes),
            "boundary_conditions": list(parsed.boundary_conditions),
            "grounding": grounding,
            "revision_notes": list(parsed.revision_notes),
            "analytical_model_potential": opportunity,
            "evaluation": evaluation,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _critiques_for(self, candidate_id: str) -> list[str]:
        out = []
        for env in await self._store.list(artifact_type="mechanism_critique"):
            try:
                crit = MechanismCritique.model_validate(env.payload)
                if crit.mechanism_candidate_id == candidate_id:
                    out.append(env.artifact_id)
            except Exception:
                continue
        return out

    async def _load_candidate_context(self, candidate: MechanismCandidate) -> dict[str, Any]:
        stmts: dict[str, SynthesisStatement] = {}
        for sid in candidate.literature_support_ids:
            try:
                env = await self._store.get(sid)
                stmt = env.parse_payload(SynthesisStatement)
                stmts[sid] = stmt
            except Exception:
                continue
        gap = None
        try:
            gap = (await self._store.get(candidate.gap_id)).parse_payload(ResearchGap)
        except Exception:
            pass
        return {"stmts": stmts, "gap": gap}

    async def _update_analysis(
        self,
        candidate: MechanismCandidate,
        crit_id: str | None = None,
        critiqued: bool = False,
        selected: str | None = None,
    ) -> None:
        """Emit a superseding MechanismAnalysis reflecting critique/selection."""
        selection_id = candidate.gap_selection_id
        if not selection_id:
            return
        analyses = await self._store.list(artifact_type="mechanism_analysis")
        matches: list[Any] = []
        for env in analyses:
            try:
                a = MechanismAnalysis.model_validate(env.payload)
            except Exception:
                continue
            if a.gap_selection_id == selection_id:
                matches.append((env, a))
        if not matches:
            return
        # The latest analysis is the leaf of the supersedes chain
        leaves = []
        for env, a in matches:
            children = await self._store.get_children(env.artifact_id)
            if not any(c.relation.value == "supersedes" for c in children):
                leaves.append((env, a))
        prev_env, prev = max(leaves or matches, key=lambda t: t[0].created_at)
        critique_ids = list(prev.critique_ids)
        if crit_id and crit_id not in critique_ids:
            critique_ids.append(crit_id)
        status = prev.status
        if critiqued and status == MechanismAnalysisStatus.generated:
            status = MechanismAnalysisStatus.critiqued
        if selected is not None:
            status = MechanismAnalysisStatus.selected
        updated = prev.model_copy(
            update={
                "critique_ids": critique_ids,
                "selected_mechanism_id": selected or prev.selected_mechanism_id,
                "status": status,
            }
        )
        new_env = ArtifactEnvelope.create(
            payload=updated,
            artifact_type="mechanism_analysis",
            producer="research.mechanism_critic",
        )
        await self._store.put(new_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.supersedes,
                source_artifact_id=prev_env.artifact_id,
                target_artifact_id=new_env.artifact_id,
                producer="research.mechanism_critic",
            )
        )

    def _build_critique_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "overall_assessment": {"type": "string"},
                "verdict": {"type": "string", "enum": CritiqueVerdict.values()},
                "revision_recommendations": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": CritiqueCategory.values(),
                            },
                            "description": {"type": "string"},
                            "severity": {
                                "type": "string",
                                "enum": CritiqueSeverity.values(),
                            },
                            "location": {"type": "string"},
                        },
                        "required": ["category", "description"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["overall_assessment", "verdict"],
            "additionalProperties": False,
        }

    def _build_revision_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "description": {"type": "string"},
                "actors": {"type": "array", "items": {"type": "string"}},
                "strategic_interactions": {"type": "array", "items": {"type": "string"}},
                "information_structure": {"type": "string"},
                "incentives": {"type": "array", "items": {"type": "string"}},
                "causal_logic": {"type": "string"},
                "key_assumptions": {"type": "array", "items": {"type": "string"}},
                "expected_outcomes": {"type": "array", "items": {"type": "string"}},
                "boundary_conditions": {"type": "array", "items": {"type": "string"}},
                "grounding": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "element": {"type": "string"},
                            "basis": {"type": "string", "enum": KnowledgeBasis.values()},
                            "source_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["element", "basis"],
                        "additionalProperties": False,
                    },
                },
                "revision_notes": {"type": "array", "items": {"type": "string"}},
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
                        "gap_alignment": {"type": "number", "minimum": 0, "maximum": 1},
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
                        "is_relevance": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["name", "description", "causal_logic"],
            "additionalProperties": False,
        }

    def _build_critique_prompt(self, candidate: MechanismCandidate, context: dict[str, Any]) -> str:
        gap = context.get("gap")
        gap_text = (
            f"Gap: {gap.title} ({gap.gap_type.value})\n{gap.description[:300]}"
            if gap
            else f"Gap id: {candidate.gap_id}"
        )
        grounding_lines = (
            "\n".join(
                f"  - {g.element} [basis: {g.basis.value}]{' (sources: ' + ', '.join(g.source_ids[:5]) + ')' if g.source_ids else ''}"
                for g in candidate.grounding
            )
            or "  (none)"
        )
        stmt_lines = (
            "\n".join(f"  [{sid}] {s.statement[:180]}" for sid, s in context["stmts"].items())
            or "  (no statements loaded)"
        )
        eval_text = (
            f"evaluation composite {candidate.evaluation.composite}"
            if candidate.evaluation
            else "evaluation not provided"
        )
        return f"""Critique the following mechanism candidate.

{gap_text}

Candidate: {candidate.name}
Description: {candidate.description[:400]}
Actors: {", ".join(candidate.actors)}
Strategic interactions: {", ".join(candidate.strategic_interactions)}
Information structure: {candidate.information_structure or "not specified"}
Incentives: {", ".join(candidate.incentives)}
Causal logic: {candidate.causal_logic[:400]}
Key assumptions: {", ".join(candidate.key_assumptions)}
Expected outcomes: {", ".join(candidate.expected_outcomes)}
Boundary conditions: {", ".join(candidate.boundary_conditions)}
{eval_text}

Element-level knowledge basis:
{grounding_lines}

Grounded literature support (statements):
{stmt_lines}

Critique dimensions (identify issues in ANY of these):
- logical inconsistencies
- unsupported assumptions
- mechanisms already explained by the reviewed literature
- unclear causal direction
- unmodelable concepts
- missing actors or incentives
- alternative explanations

Return: overall_assessment, verdict (keep|revise|reject), revision_recommendations,
and issues with category (one of {", ".join(CritiqueCategory.values())}), severity
(high|medium|low), and location. Valid JSON only, no chain-of-thought.
"""

    def _build_revision_prompt(
        self,
        candidate: MechanismCandidate,
        critiques: list[MechanismCritique],
        context: dict[str, Any],
    ) -> str:
        crit_lines = []
        for c in critiques:
            crit_lines.append(f"Verdict: {c.verdict.value}; {c.overall_assessment}")
            for i in c.issues:
                crit_lines.append(f"  [{i.severity.value}] {i.category.value}: {i.description}")
            if c.revision_recommendations:
                crit_lines.append("  Recommendations: " + "; ".join(c.revision_recommendations))
        stmt_lines = (
            "\n".join(f"  [{sid}] {s.statement[:180]}" for sid, s in context["stmts"].items())
            or "  (no statements loaded)"
        )
        return f"""Revise the following mechanism candidate in response to its critique.

Original candidate: {candidate.name}
Description: {candidate.description[:400]}
Causal logic: {candidate.causal_logic[:400]}
Key assumptions: {", ".join(candidate.key_assumptions)}
Grounding (element -> basis):
{chr(10).join(f"  - {g.element} [{g.basis.value}]" for g in candidate.grounding) or "  (none)"}

Critique:
{chr(10).join(crit_lines)}

Grounded literature support (statements):
{stmt_lines}

Produce a revised mechanism that addresses the critique. Keep the grounding
discipline: literature_supported elements must keep source_ids; new hypotheses
stay labeled as new_hypothesis; assumptions as modeling_assumption.
List what changed in revision_notes. Return valid JSON only, no chain-of-thought.
"""


class MechanismCriticPlugin(Plugin):
    def __init__(self, critic_role: str | None = None) -> None:
        self._critic_role_override = critic_role
        self._service: MechanismCriticService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.mechanism_critic",
            version="0.1.0",
            plugin_type="research",
            description="Independent mechanism critique + revision/selection (Phase 3A)",
            provides=["mechanism_critic.default"],
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
        critic_role = (
            self._critic_role_override
            or research_cfg.get("critic_role")
            or research_cfg.get("model_role")
            or "critic"
        )
        revision_role = research_cfg.get("revision_role") or "reasoning"

        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        self._service = MechanismCriticService(
            model_router=router,
            artifact_store=store,
            critic_role=str(critic_role),
            revision_role=str(revision_role),
            events=ctx.events,
        )
        ctx.register("mechanism_critic.default", self._service)
