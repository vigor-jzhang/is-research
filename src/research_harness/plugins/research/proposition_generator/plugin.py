"""Phase 3D proposition generator — candidate propositions from a verified
equilibrium + comparative statics + model assumptions + mechanism.

The LLM (`reasoning` role) proposes structured propositions; every proposition
is then verified deterministically (research.proposition_verifier), critiqued
(research.proposition_critic), and — for non-rejected, at least conditionally
verified propositions — given a structured economic interpretation. No
numerical experiments.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.equilibrium import (
    EquilibriumCandidate,
)
from research_harness.research.schemas.mechanism import SelectedMechanism
from research_harness.research.schemas.model import Expression, FormalAnalyticalModel
from research_harness.research.schemas.proposition import (
    ComparativeStatic,
    ComparativeStaticsAnalysis,
    Proposition,
    PropositionClaimType,
    PropositionStatus,
    PropositionVerification,
    PropositionVerificationStatus,
)

logger = logging.getLogger(__name__)


class _PropItem(BaseModel):
    statement: str
    claim_type: str = PropositionClaimType.monotonicity.value
    outcome_variable: str | None = None
    parameter: str | None = None
    expected_sign: str | None = None
    mathematical_form: str | None = None
    conditions: list[str] = Field(default_factory=list)
    supporting_static_ids: list[str] = Field(default_factory=list)

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("statement must be non-empty")
        return v

    @field_validator("claim_type")
    @classmethod
    def validate_claim_type(cls, v: str) -> str:
        if v not in PropositionClaimType.values():
            raise ValueError(f"invalid claim type {v!r}")
        return v

    model_config = {"extra": "forbid"}


class _PropositionsResponse(BaseModel):
    propositions: list[_PropItem]

    model_config = {"extra": "forbid"}


class PropositionGeneratorService:
    def __init__(
        self,
        model_router: Any,
        artifact_store: Any,
        verifier: Any,
        critic: Any,
        generator_role: str = "reasoning",
        max_propositions: int = 8,
        max_llm_calls: int = 20,
    ) -> None:
        self._router = model_router
        self._store = artifact_store
        self._verifier = verifier
        self._critic = critic
        self._generator_role = generator_role
        self._max_propositions = max_propositions
        self._max_llm_calls = max_llm_calls

    @property
    def service_id(self) -> str:
        return "research.proposition_generator"

    async def generate(self, comparative_statics_analysis_id: str) -> list[str]:
        """Generate, verify, critique, and interpret propositions.

        Returns the proposition artifact ids (existing ones on idempotent rerun).
        """
        # Idempotency: reuse existing propositions for the same analysis + role
        existing = await self._store.list(artifact_type="proposition")
        reused: list[str] = []
        for env in existing:
            try:
                p = Proposition.model_validate(env.payload)
            except Exception:
                continue
            if (
                p.comparative_statics_analysis_id == comparative_statics_analysis_id
                and p.model_role == self._generator_role
            ):
                reused.append(env.artifact_id)
        if reused:
            return reused

        cs_env = await self._store.get(comparative_statics_analysis_id)
        cs_analysis = cs_env.parse_payload(ComparativeStaticsAnalysis)
        model = (await self._store.get(cs_analysis.model_id)).parse_payload(FormalAnalyticalModel)
        candidate = (await self._store.get(cs_analysis.equilibrium_candidate_id)).parse_payload(
            EquilibriumCandidate
        )
        mechanism = None
        try:
            mechanism = (await self._store.get(model.selected_mechanism_id)).parse_payload(
                SelectedMechanism
            )
        except Exception:
            pass

        statics = [
            (await self._store.get(sid)).parse_payload(ComparativeStatic)
            for sid in cs_analysis.static_ids
        ]
        self._cs_ids = {
            (s.outcome_variable, s.parameter): sid
            for sid, s in zip(cs_analysis.static_ids, statics, strict=True)
        }

        prompt = self._build_prompt(model, candidate, statics, mechanism)
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You are a theoretical IS researcher proposing testable "
                        "propositions from verified equilibrium results. Return "
                        "valid JSON matching the schema. Never include chain-of-thought."
                    ),
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self._build_schema(),
            temperature=0.0,
            metadata={"comparative_statics_analysis_id": comparative_statics_analysis_id},
        )
        try:
            response = await self._router.complete(self._generator_role, request)
            data = json.loads(response.message.content or "")
            parsed = _PropositionsResponse.model_validate(data)
        except Exception as e:
            raise ValueError(f"proposition generation call failed: {e}") from e

        out: list[str] = []
        for item in parsed.propositions[: self._max_propositions]:
            prop = Proposition(
                model_id=cs_analysis.model_id,
                equilibrium_candidate_id=cs_analysis.equilibrium_candidate_id,
                comparative_statics_analysis_id=comparative_statics_analysis_id,
                statement=item.statement,
                claim_type=PropositionClaimType(item.claim_type),
                outcome_variable=item.outcome_variable,
                parameter=item.parameter,
                expected_sign=item.expected_sign,
                mathematical_form=(
                    Expression(expression=item.mathematical_form, symbols_used=[])
                    if item.mathematical_form
                    else None
                ),
                conditions=list(item.conditions),
                supporting_static_ids=list(item.supporting_static_ids),
                status=PropositionStatus.candidate,
                proposed_by="llm",
                model_role=self._generator_role,
            )
            p_env = ArtifactEnvelope.create(
                payload=prop,
                artifact_type="proposition",
                producer=f"research.proposition_generator:{self._generator_role}",
            )
            await self._store.put(p_env)
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=comparative_statics_analysis_id,
                    target_artifact_id=p_env.artifact_id,
                    producer="research.proposition_generator",
                )
            )
            for sid in item.supporting_static_ids:
                try:
                    await self._store.add_provenance(
                        ProvenanceLink(
                            relation=ProvenanceRelation.derived_from,
                            source_artifact_id=sid,
                            target_artifact_id=p_env.artifact_id,
                            producer="research.proposition_generator",
                        )
                    )
                except Exception:
                    pass
            out.append(p_env.artifact_id)

        # Verify, critique, interpret
        for pid in out:
            await self._verifier.verify(pid)
            v = await self._latest_verification(pid)
            if v is not None and v.status != PropositionVerificationStatus.failed:
                await self._critic.critique(pid)
                await self._critic.interpret(pid)
        return out

    async def _latest_verification(self, proposition_id: str) -> PropositionVerification | None:
        for env in await self._store.list(artifact_type="proposition_verification"):
            try:
                v = env.parse_payload(PropositionVerification)
                if v.proposition_id == proposition_id:
                    return v
            except Exception:
                continue
        return None

    def _build_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "propositions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "statement": {"type": "string"},
                            "claim_type": {
                                "type": "string",
                                "enum": PropositionClaimType.values(),
                            },
                            "outcome_variable": {"type": "string"},
                            "parameter": {"type": "string"},
                            "expected_sign": {
                                "type": "string",
                                "enum": ["positive", "negative", "zero"],
                            },
                            "mathematical_form": {"type": "string"},
                            "conditions": {"type": "array", "items": {"type": "string"}},
                            "supporting_static_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["statement"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["propositions"],
            "additionalProperties": False,
        }

    def _build_prompt(
        self,
        model: FormalAnalyticalModel,
        candidate: EquilibriumCandidate,
        statics: list[ComparativeStatic],
        mechanism: SelectedMechanism | None,
    ) -> str:
        cand_lines = "\n".join(
            f"  {e.variable} = {e.expression.expression}"
            + (f"  (conditions: {'; '.join(e.conditions) or '-'})" if e.conditions else "")
            for e in candidate.expressions
        )
        static_lines = "\n".join(
            f"  [{self._cs_ids.get((s.outcome_variable, s.parameter), '?')}] "
            f"{s.outcome_variable} | {s.parameter} | d/d = {s.derivative_expression.expression} "
            f"| sign {s.sign.value}"
            + (f" | conditions: {'; '.join(s.conditions)}" if s.conditions else "")
            for s in statics
        )
        mech_text = (
            f"Mechanism: {mechanism.name} — {mechanism.description[:200]}" if mechanism else ""
        )
        asm_lines = (
            "\n".join(
                f"  - {a.statement[:120]} [{a.knowledge_basis.value}]" for a in model.assumptions
            )
            or "  (none)"
        )
        return f"""Propose testable propositions grounded in the verified equilibrium.

{mech_text}

Model: {model.title} (game_type: {model.game_type or "?"})
Actors: {", ".join(a.actor_id for a in model.actors)}
Payoffs: {"; ".join(f"{p.actor_id}: {p.expression.expression}" for p in model.payoffs)}

Verified equilibrium (candidate {candidate.model_id}):
{cand_lines}

Verified comparative statics (IDs authoritative; cite ONLY these IDs in
supporting_static_ids):
{static_lines}

Model assumptions:
{asm_lines}

For each proposition:
- statement: a precise conditional claim, e.g. 'Increasing the demand
  parameter a raises each platform's equilibrium quantity.'
- claim_type: monotonicity (sign of a comparative static) or equality
  (algebraic identity among equilibrium outcomes, with mathematical_form
  like 'q1 = q2')
- outcome_variable / parameter / expected_sign: the comparative static this
  proposition claims; expected_sign must match the verified static's sign
- conditions: parameter restrictions under which the claim holds; REQUIRED
  when the comparative static is ambiguous
- supporting_static_ids: the comparative statics the proposition builds on

Rules:
- Every proposition must be backed by at least one supporting comparative
  static; never claim a sign the statics do not support.
- Never simplify conditional results: keep the conditions explicit.
- Return valid JSON only, no chain-of-thought.
"""


class PropositionGeneratorPlugin(Plugin):
    def __init__(self, generator_role: str | None = None) -> None:
        self._generator_role_override = generator_role
        self._service: PropositionGeneratorService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.proposition_generator",
            version="0.1.0",
            plugin_type="research",
            description="Proposition generation + verification + critique + interpretation (Phase 3D)",
            provides=["proposition_generator.default"],
            requires=[
                "model_router.default",
                "artifact_store.default",
                "proposition_verifier.default",
                "proposition_critic.default",
            ],
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
        generator_role = (
            self._generator_role_override or research_cfg.get("generator_role") or "reasoning"
        )
        max_propositions = int(research_cfg.get("max_propositions", 8))
        max_llm_calls = int(research_cfg.get("max_llm_calls", 20))

        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        verifier = ctx.require("proposition_verifier.default")
        critic = ctx.require("proposition_critic.default")
        self._service = PropositionGeneratorService(
            model_router=router,
            artifact_store=store,
            verifier=verifier,
            critic=critic,
            generator_role=str(generator_role),
            max_propositions=max_propositions,
            max_llm_calls=max_llm_calls,
        )
        ctx.register("proposition_generator.default", self._service)
