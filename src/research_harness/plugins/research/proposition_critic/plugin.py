"""Phase 3D proposition critic — independent critique of propositions plus
structured economic interpretation generation.

The critic (role `critic`) checks overclaiming, interpretation beyond the
mathematical support, missing conditions, trivial propositions, contradictions
with assumptions/mechanism, and weak IS relevance. Interpretations (role
`interpretation_role`) separate the mathematical result, economic
interpretation, managerial implication, and IS/theoretical implication, and
remain consistent with the verified result.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.mechanism import SelectedMechanism
from research_harness.research.schemas.model import FormalAnalyticalModel
from research_harness.research.schemas.proposition import (
    EconomicInterpretation,
    Proposition,
    PropositionCritique,
    PropositionCritiqueCategory,
    PropositionCritiqueIssue,
    PropositionCritiqueVerdict,
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
        if v not in PropositionCritiqueCategory.values():
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
    recommendations: list[str] = Field(default_factory=list)
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
        if v not in PropositionCritiqueVerdict.values():
            raise ValueError(f"invalid verdict {v!r}")
        return v

    model_config = {"extra": "forbid"}


class _InterpretationResponse(BaseModel):
    mathematical_result: str
    economic_interpretation: str
    managerial_implication: str
    is_theoretical_implication: str
    consistency_note: str | None = None

    @field_validator("mathematical_result", "economic_interpretation")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must be non-empty")
        return v

    model_config = {"extra": "forbid"}


class PropositionCriticService:
    def __init__(
        self,
        model_router: Any,
        artifact_store: Any,
        critic_role: str = "critic",
        interpretation_role: str = "reasoning",
    ) -> None:
        self._router = model_router
        self._store = artifact_store
        self._critic_role = critic_role
        self._interpretation_role = interpretation_role

    @property
    def service_id(self) -> str:
        return "research.proposition_critic"

    async def critique(self, proposition_id: str) -> str:
        """Critique a proposition. Returns the PropositionCritique id."""
        existing = await self._store.list(artifact_type="proposition_critique")
        for env in existing:
            try:
                c = PropositionCritique.model_validate(env.payload)
                if c.proposition_id == proposition_id and c.model_role == self._critic_role:
                    return env.artifact_id
            except Exception:
                continue

        p_env = await self._store.get(proposition_id)
        prop = p_env.parse_payload(Proposition)
        model = None
        mechanism = None
        try:
            model = (await self._store.get(prop.model_id)).parse_payload(FormalAnalyticalModel)
        except Exception:
            pass
        try:
            if model is not None:
                mechanism = (await self._store.get(model.selected_mechanism_id)).parse_payload(
                    SelectedMechanism
                )
        except Exception:
            pass

        prompt = self._build_critique_prompt(prop, model, mechanism)
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You are an independent, skeptical critic of research "
                        "propositions. Return valid JSON matching the schema. "
                        "Never include chain-of-thought."
                    ),
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self._build_critique_schema(),
            temperature=0.0,
            metadata={"proposition_id": proposition_id},
        )
        try:
            response = await self._router.complete(self._critic_role, request)
            data = json.loads(response.message.content or "")
            parsed = _CritiqueResponse.model_validate(data)
        except Exception as e:
            raise ValueError(f"proposition critique call failed: {e}") from e

        critique = PropositionCritique(
            proposition_id=proposition_id,
            issues=[
                PropositionCritiqueIssue(
                    category=PropositionCritiqueCategory(i.category),
                    description=i.description,
                    severity=i.severity,
                    location=i.location,
                )
                for i in parsed.issues
            ],
            overall_assessment=parsed.overall_assessment,
            verdict=PropositionCritiqueVerdict(parsed.verdict),
            recommendations=list(parsed.recommendations),
            model_role=self._critic_role,
        )
        c_env = ArtifactEnvelope.create(
            payload=critique,
            artifact_type="proposition_critique",
            producer=f"research.proposition_critic:{self._critic_role}",
        )
        await self._store.put(c_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=proposition_id,
                target_artifact_id=c_env.artifact_id,
                producer="research.proposition_critic",
            )
        )
        return c_env.artifact_id

    async def interpret(self, proposition_id: str) -> str:
        """Generate the EconomicInterpretation of a proposition.

        Returns the EconomicInterpretation artifact id.
        """
        existing = await self._store.list(artifact_type="economic_interpretation")
        for env in existing:
            try:
                i = EconomicInterpretation.model_validate(env.payload)
                if i.proposition_id == proposition_id and i.model_role == self._interpretation_role:
                    return env.artifact_id
            except Exception:
                continue

        p_env = await self._store.get(proposition_id)
        prop = p_env.parse_payload(Proposition)
        model = (await self._store.get(prop.model_id)).parse_payload(FormalAnalyticalModel)
        mechanism = None
        gap_id = None
        try:
            mechanism = (await self._store.get(model.selected_mechanism_id)).parse_payload(
                SelectedMechanism
            )
            gap_id = mechanism.gap_id
        except Exception:
            pass

        prompt = self._build_interpretation_prompt(prop, model, mechanism)
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You write structured economic interpretations of verified "
                        "propositions. Separate the mathematical result, the economic "
                        "interpretation, the managerial implication, and the "
                        "IS/theoretical implication. Return valid JSON matching the "
                        "schema. Never include chain-of-thought."
                    ),
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self._build_interpretation_schema(),
            temperature=0.0,
            metadata={"proposition_id": proposition_id},
        )
        try:
            response = await self._router.complete(self._interpretation_role, request)
            data = json.loads(response.message.content or "")
            parsed = _InterpretationResponse.model_validate(data)
        except Exception as e:
            raise ValueError(f"interpretation model call failed: {e}") from e

        interpretation = EconomicInterpretation(
            proposition_id=proposition_id,
            model_id=prop.model_id,
            selected_mechanism_id=model.selected_mechanism_id,
            gap_id=gap_id,
            mathematical_result=parsed.mathematical_result,
            economic_interpretation=parsed.economic_interpretation,
            managerial_implication=parsed.managerial_implication,
            is_theoretical_implication=parsed.is_theoretical_implication,
            consistency_note=parsed.consistency_note,
            model_role=self._interpretation_role,
        )
        i_env = ArtifactEnvelope.create(
            payload=interpretation,
            artifact_type="economic_interpretation",
            producer=f"research.proposition_critic:{self._interpretation_role}",
        )
        await self._store.put(i_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=proposition_id,
                target_artifact_id=i_env.artifact_id,
                producer="research.proposition_critic",
            )
        )
        return i_env.artifact_id

    # ------------------------------------------------------------------
    # Schemas + prompts
    # ------------------------------------------------------------------

    def _build_critique_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "overall_assessment": {"type": "string"},
                "verdict": {"type": "string", "enum": PropositionCritiqueVerdict.values()},
                "recommendations": {"type": "array", "items": {"type": "string"}},
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": PropositionCritiqueCategory.values(),
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

    def _build_interpretation_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "mathematical_result": {"type": "string"},
                "economic_interpretation": {"type": "string"},
                "managerial_implication": {"type": "string"},
                "is_theoretical_implication": {"type": "string"},
                "consistency_note": {"type": "string"},
            },
            "required": [
                "mathematical_result",
                "economic_interpretation",
                "managerial_implication",
                "is_theoretical_implication",
            ],
            "additionalProperties": False,
        }

    def _build_critique_prompt(
        self,
        prop: Proposition,
        model: FormalAnalyticalModel | None,
        mechanism: SelectedMechanism | None,
    ) -> str:
        mech_text = (
            f"Mechanism: {mechanism.name} — {mechanism.description[:200]}"
            if mechanism
            else f"Mechanism id: {model.selected_mechanism_id if model else '?'}"
        )
        return f"""Critique the following research proposition.

{mech_text}

Proposition: {prop.statement}
Claim type: {prop.claim_type.value}
Outcome: {prop.outcome_variable or "-"}  Parameter: {prop.parameter or "-"}
Expected sign: {prop.expected_sign or "-"}
Conditions: {prop.conditions or "-"}
Mathematical form: {prop.mathematical_form.expression if prop.mathematical_form else "-"}
Supporting comparative statics: {prop.supporting_static_ids}

Critique dimensions:
- overclaiming (claims beyond the verified result)
- interpretation beyond mathematical support
- missing conditions (sign depends on parameter restrictions)
- trivial propositions
- contradiction with assumptions or the mechanism
- weak IS relevance

Return: overall_assessment, verdict (keep|revise|reject), recommendations,
and issues with category (one of {", ".join(PropositionCritiqueCategory.values())}),
severity, location. Valid JSON only, no chain-of-thought.
"""

    def _build_interpretation_prompt(
        self,
        prop: Proposition,
        model: FormalAnalyticalModel,
        mechanism: SelectedMechanism | None,
    ) -> str:
        mech_text = (
            f"Mechanism: {mechanism.name} — {mechanism.description[:250]}"
            if mechanism
            else "Mechanism context unavailable"
        )
        return f"""Write the economic/IS interpretation of the following verified proposition.

{mech_text}

Model: {model.title} (game_type: {model.game_type or "?"})
Actors: {", ".join(a.actor_id for a in model.actors)}
Payoffs: {"; ".join(f"{p.actor_id}: {p.expression.expression}" for p in model.payoffs)}

Proposition: {prop.statement}
Claim type: {prop.claim_type.value}
Outcome: {prop.outcome_variable or "-"}  Parameter: {prop.parameter or "-"}
Expected sign: {prop.expected_sign or "-"}
Conditions: {prop.conditions or "-"}

Produce:
- mathematical_result: restate the verified result precisely (with its conditions)
- economic_interpretation: what the result means economically, WITHOUT going
  beyond the mathematical support
- managerial_implication: practical implication for managers/designers
- is_theoretical_implication: implication for IS theory
- consistency_note: how this interpretation stays within the verified result

Never claim effects that the conditions do not guarantee. Valid JSON only,
no chain-of-thought.
"""


class PropositionCriticPlugin(Plugin):
    def __init__(self, critic_role: str | None = None) -> None:
        self._critic_role_override = critic_role
        self._service: PropositionCriticService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.proposition_critic",
            version="0.1.0",
            plugin_type="research",
            description="Proposition critique + economic interpretation (Phase 3D)",
            provides=["proposition_critic.default"],
            requires=["model_router.default", "artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        research_cfg: dict[str, Any] = {}
        if "research" in cfg and isinstance(cfg["research"], dict):
            research_cfg = (
                cfg["research"].get("proposition", {})
                if isinstance(cfg["research"].get("proposition"), dict)
                else {}
            )
        critic_role = self._critic_role_override or research_cfg.get("critic_role") or "critic"
        interpretation_role = research_cfg.get("interpretation_role") or "reasoning"
        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        self._service = PropositionCriticService(
            model_router=router,
            artifact_store=store,
            critic_role=str(critic_role),
            interpretation_role=str(interpretation_role),
        )
        ctx.register("proposition_critic.default", self._service)
