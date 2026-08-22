"""Phase 3B model specification critic — independent critique of a
FormalAnalyticalModel plus immutable revision.

Critique (role `critic`) covers: mechanism/model mismatch, undefined
concepts, inconsistent timing, impossible information assumptions, redundant
assumptions, missing strategic actor, payoff inconsistency, poor
tractability, unjustified restrictions. Revision (role `revision_role`)
produces FormalAnalyticalModel V2 via `supersedes`; V1 is never mutated.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.plugins.research.model_builder.plugin import (
    ModelBuilderService,
    ModelSpecificationResponse,
)
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.mechanism import SelectedMechanism
from research_harness.research.schemas.model import (
    FormalAnalyticalModel,
    ModelCritiqueCategory,
    ModelCritiqueIssue,
    ModelSpecificationCritique,
    ModelStatus,
)

logger = logging.getLogger(__name__)


class _IssueItem(BaseModel):
    category: str
    description: str
    severity: str = "medium"
    location: str | None = None

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        if v not in ModelCritiqueCategory.values():
            raise ValueError(f"invalid critique category {v!r}")
        return v

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        if v not in ("high", "medium", "low"):
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
        if v not in ("keep", "revise", "reject"):
            raise ValueError(f"invalid verdict {v!r}")
        return v

    model_config = {"extra": "forbid"}


class _RevisionResponse(ModelSpecificationResponse):
    revision_notes: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class ModelSpecificationCriticService:
    def __init__(
        self,
        model_router: Any,
        artifact_store: Any,
        builder: ModelBuilderService,
        critic_role: str = "critic",
        revision_role: str = "reasoning",
    ) -> None:
        self._router = model_router
        self._store = artifact_store
        self._builder = builder
        self._critic_role = critic_role
        self._revision_role = revision_role

    @property
    def critic_id(self) -> str:
        return "research.model_specification_critic"

    # ------------------------------------------------------------------
    # Critique
    # ------------------------------------------------------------------

    async def critique(self, model_id: str) -> str:
        """Critique a FormalAnalyticalModel. Returns the critique artifact id."""
        existing = await self._store.list(artifact_type="model_specification_critique")
        for env in existing:
            try:
                crit = ModelSpecificationCritique.model_validate(env.payload)
                if crit.model_id == model_id and crit.model_role == self._critic_role:
                    return env.artifact_id
            except Exception:
                continue

        m_env = await self._store.get(model_id)
        model = m_env.parse_payload(FormalAnalyticalModel)
        mechanism = None
        try:
            mechanism = (await self._store.get(model.selected_mechanism_id)).parse_payload(
                SelectedMechanism
            )
        except Exception:
            pass

        prompt = self._build_critique_prompt(model, mechanism)
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You are an independent, skeptical critic of formal analytical "
                        "models. Return valid JSON matching the schema. Never include "
                        "chain-of-thought."
                    ),
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self._build_critique_schema(),
            temperature=0.0,
            metadata={"model_id": model_id},
        )
        try:
            response = await self._router.complete(self._critic_role, request)
            data = json.loads(response.message.content or "")
            parsed = _CritiqueResponse.model_validate(data)
        except Exception as e:
            raise ValueError(f"model critique call failed: {e}") from e

        critique = ModelSpecificationCritique(
            model_id=model_id,
            selected_mechanism_id=model.selected_mechanism_id,
            issues=[
                ModelCritiqueIssue(
                    category=ModelCritiqueCategory(i.category),
                    description=i.description,
                    severity=i.severity,
                    location=i.location,
                )
                for i in parsed.issues
            ],
            overall_assessment=parsed.overall_assessment,
            verdict=parsed.verdict,
            revision_recommendations=list(parsed.revision_recommendations),
            model_role=self._critic_role,
        )
        crit_env = ArtifactEnvelope.create(
            payload=critique,
            artifact_type="model_specification_critique",
            producer=f"research.model_specification_critic:{self._critic_role}",
        )
        await self._store.put(crit_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=model_id,
                target_artifact_id=crit_env.artifact_id,
                producer="research.model_specification_critic",
            )
        )
        return crit_env.artifact_id

    # ------------------------------------------------------------------
    # Revision (V1 -> V2, supersedes, V1 never mutated)
    # ------------------------------------------------------------------

    async def revise(self, model_id: str) -> str:
        """Revise a model in response to critiques. Returns the new model id."""
        # Idempotency: existing revised model superseding this one for same roles
        for env in await self._store.list(artifact_type="formal_analytical_model"):
            try:
                m = FormalAnalyticalModel.model_validate(env.payload)
            except Exception:
                continue
            if m.status == ModelStatus.revised and m.model_role == self._revision_role:
                children = await self._store.get_children(model_id)
                if any(
                    c.target_artifact_id == env.artifact_id and c.relation.value == "supersedes"
                    for c in children
                ):
                    return env.artifact_id

        m_env = await self._store.get(model_id)
        v1 = m_env.parse_payload(FormalAnalyticalModel)
        mechanism = (await self._store.get(v1.selected_mechanism_id)).parse_payload(
            SelectedMechanism
        )

        critiques = await self._critiques_for(model_id)
        if not critiques:
            critiques = [await self.critique(model_id)]
        critique_payloads = [
            (await self._store.get(cid)).parse_payload(ModelSpecificationCritique)
            for cid in critiques
        ]

        prompt = self._build_revision_prompt(v1, mechanism, critique_payloads)
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You revise a formal analytical model specification in response "
                        "to an independent critique. Return valid JSON matching the "
                        "schema. Never include chain-of-thought."
                    ),
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self._builder.build_schema(),
            temperature=0.0,
            metadata={"model_id": model_id},
        )
        try:
            response = await self._router.complete(self._revision_role, request)
            data = json.loads(response.message.content or "")
            parsed = _RevisionResponse.model_validate(data)
        except Exception as e:
            raise ValueError(f"model revision call failed: {e}") from e

        context = await self._builder.load_context(mechanism)
        revision_notes = list(parsed.revision_notes)
        del parsed.revision_notes  # not part of _ModelResponse
        try:
            v2 = self._builder.build_model(parsed, mechanism, v1.selected_mechanism_id, context)
        except ValueError as e:
            raise ValueError(f"revised model failed structural validation: {e}") from e
        v2 = v2.model_copy(
            update={
                "status": ModelStatus.revised,
                "revision_notes": revision_notes,
                "model_role": self._revision_role,
                "metadata": {"supersedes": model_id},
            }
        )
        v2_env = ArtifactEnvelope.create(
            payload=v2,
            artifact_type="formal_analytical_model",
            producer=f"research.model_specification_critic:{self._revision_role}",
        )
        await self._store.put(v2_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.supersedes,
                source_artifact_id=model_id,
                target_artifact_id=v2_env.artifact_id,
                producer="research.model_specification_critic",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=model_id,
                target_artifact_id=v2_env.artifact_id,
                producer="research.model_specification_critic",
            )
        )
        for cid in critiques:
            try:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=cid,
                        target_artifact_id=v2_env.artifact_id,
                        producer="research.model_specification_critic",
                    )
                )
            except Exception:
                pass
        # Literature-backed assumptions keep their individual grounding edges
        for a in v2.assumptions:
            if a.knowledge_basis.value == "literature_supported":
                for sid in a.source_ids:
                    try:
                        await self._store.add_provenance(
                            ProvenanceLink(
                                relation=ProvenanceRelation.derived_from,
                                source_artifact_id=sid,
                                target_artifact_id=v2_env.artifact_id,
                                producer="research.model_specification_critic",
                            )
                        )
                    except Exception:
                        pass
        return v2_env.artifact_id

    async def _critiques_for(self, model_id: str) -> list[str]:
        out = []
        for env in await self._store.list(artifact_type="model_specification_critique"):
            try:
                crit = ModelSpecificationCritique.model_validate(env.payload)
                if crit.model_id == model_id:
                    out.append(env.artifact_id)
            except Exception:
                continue
        return out

    def _build_critique_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "overall_assessment": {"type": "string"},
                "verdict": {"type": "string", "enum": ["keep", "revise", "reject"]},
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
                                "enum": ModelCritiqueCategory.values(),
                            },
                            "description": {"type": "string"},
                            "severity": {"type": "string", "enum": ["high", "medium", "low"]},
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

    def _build_critique_prompt(
        self, model: FormalAnalyticalModel, mechanism: SelectedMechanism | None
    ) -> str:
        mech_text = (
            f"Mechanism: {mechanism.name}\n{mechanism.description[:300]}"
            if mechanism
            else f"Mechanism id: {model.selected_mechanism_id}"
        )
        actors = "; ".join(f"{a.actor_id} ({a.role or a.name})" for a in model.actors)
        vars_lines = "\n".join(
            f"  {v.symbol}: {v.kind.value}, owner={v.owner_actor_id or '-'}, domain={v.domain}, {v.meaning[:80]}"
            for v in model.variables
        )
        param_lines = "\n".join(
            f"  {p.symbol}: {p.domain}, {p.meaning[:80]}" for p in model.parameters
        )
        timing_lines = "\n".join(
            f"  Stage {t.stage_number} ({t.name}): {t.description[:100]} actors={t.actor_ids}"
            for t in model.timing
        )
        info_lines = (
            "\n".join(
                f"  {i.actor_id} observes {i.variable_symbols} at stage {i.available_at_stage} [{i.visibility.value}]"
                for i in model.information_structure.items
            )
            or "  (none)"
        )
        unc_lines = (
            "\n".join(
                f"  {u.variable_symbol} ~ {u.distribution}"
                for u in model.information_structure.uncertainty
            )
            or "  (none)"
        )
        payoff_lines = "\n".join(
            f"  {p.actor_id} ({p.objective_type}): {p.expression.expression} | "
            f"decisions {p.decision_variables} | params {p.parameters}"
            for p in model.payoffs
        )
        asm_lines = (
            "\n".join(
                f"  - {a.statement[:120]} [{a.knowledge_basis.value}]"
                f"{' sources ' + str(a.source_ids[:4]) if a.source_ids else ''}"
                for a in model.assumptions
            )
            or "  (none)"
        )
        return f"""Critique the following formal analytical model specification.

{mech_text}

Model: {model.title}
Description: {model.description[:300]}
Game type: {model.game_type or "not specified"}

Actors: {actors}

Variables:
{vars_lines}

Parameters:
{param_lines}

Timing:
{timing_lines}

Information structure:
{info_lines}

Uncertainty:
{unc_lines}

Payoffs:
{payoff_lines}

Assumptions:
{asm_lines}

Critique dimensions (identify issues in ANY):
- mechanism/model mismatch
- undefined concepts (symbols without meaning or missing from the symbol table)
- inconsistent timing
- impossible information assumptions (observing what cannot be known when)
- redundant assumptions
- missing strategic actor
- payoff inconsistency (payoffs not matching the mechanism/incentives)
- poor tractability (hard-to-handle functional forms)
- unjustified restrictions

Return: overall_assessment, verdict (keep|revise|reject),
revision_recommendations, and issues with category (one of
{", ".join(ModelCritiqueCategory.values())}), severity, location.
Valid JSON only, no chain-of-thought.
"""

    def _build_revision_prompt(
        self,
        model: FormalAnalyticalModel,
        mechanism: SelectedMechanism,
        critiques: list[ModelSpecificationCritique],
    ) -> str:
        crit_lines = []
        for c in critiques:
            crit_lines.append(f"Verdict: {c.verdict}; {c.overall_assessment}")
            for i in c.issues:
                crit_lines.append(f"  [{i.severity}] {i.category.value}: {i.description}")
            if c.revision_recommendations:
                crit_lines.append("  Recommendations: " + "; ".join(c.revision_recommendations))
        return f"""Revise the following formal analytical model in response to its critique.

Current model V1: {model.title}
Description: {model.description[:300]}
Actors: {", ".join(a.actor_id for a in model.actors)}
Variables: {", ".join(v.symbol for v in model.variables)}
Parameters: {", ".join(p.symbol for p in model.parameters)}
Timing stages: {[t.stage_number for t in model.timing]}
Payoffs: {", ".join(f"{p.actor_id}: {p.expression.expression}" for p in model.payoffs)}
Assumptions: {"; ".join(a.statement[:100] for a in model.assumptions)}

Mechanism context: {mechanism.name} — {mechanism.description[:200]}

Critique:
{chr(10).join(crit_lines)}

Produce the full revised model specification (same schema as V1):
- keep the grounding discipline: literature_supported assumptions must retain
  their valid source ids; new assumptions labeled new_hypothesis; modeling
  choices labeled modeling_assumption.
- every symbol used in expressions must be declared and listed in symbols_used.
- no duplicate symbols; decision variables owned by an actor.
- timing sequential stages 0..N.
- list what changed in revision_notes.
- Do NOT solve anything (no best responses, equilibrium, propositions).
Return valid JSON only, no chain-of-thought.
"""


class ModelSpecificationCriticPlugin(Plugin):
    def __init__(self, critic_role: str | None = None) -> None:
        self._critic_role_override = critic_role
        self._service: ModelSpecificationCriticService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.model_specification_critic",
            version="0.1.0",
            plugin_type="research",
            description="Independent model specification critique + revision (Phase 3B)",
            provides=["model_specification_critic.default"],
            requires=[
                "model_router.default",
                "artifact_store.default",
                "analytical_model_builder.default",
            ],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        research_cfg: dict[str, Any] = {}
        if "research" in cfg and isinstance(cfg["research"], dict):
            research_cfg = (
                cfg["research"].get("model", {})
                if isinstance(cfg["research"].get("model"), dict)
                else {}
            )
        critic_role = self._critic_role_override or research_cfg.get("critic_role") or "critic"
        revision_role = research_cfg.get("revision_role") or "reasoning"

        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        builder = ctx.require("analytical_model_builder.default")
        self._service = ModelSpecificationCriticService(
            model_router=router,
            artifact_store=store,
            builder=builder,
            critic_role=str(critic_role),
            revision_role=str(revision_role),
        )
        ctx.register("model_specification_critic.default", self._service)
