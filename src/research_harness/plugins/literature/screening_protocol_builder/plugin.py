"""Screening protocol builder — model-assisted, approval-gated."""

from __future__ import annotations

import logging
from typing import Any

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.screening_protocol import (
    ProtocolStatus,
    ScreeningCriterion,
    ScreeningProtocol,
)

logger = logging.getLogger(__name__)


class ScreeningProtocolBuilderService:
    def __init__(
        self,
        model_router: Any,
        artifact_store: Any,
        autonomy_policy: Any,
        events: Any | None = None,
        model_role: str = "reasoning",
        max_inclusion: int = 12,
        max_exclusion: int = 12,
    ) -> None:
        self._router = model_router
        self._store = artifact_store
        self._autonomy = autonomy_policy
        self._events = events
        self._model_role = model_role
        self._max_inc = max_inclusion
        self._max_exc = max_exclusion

    async def build(self, research_question_id: str, research_plan_id: str | None = None) -> str:
        # Load RQ (and optional RP/Strategy)
        try:
            rq_env = await self._store.get(research_question_id)
        except Exception as e:
            raise ValueError(f"ResearchQuestion {research_question_id!r} not found: {e}") from e

        # Try to parse payload
        try:
            rq = rq_env.parse_payload(  # type: ignore[attr-defined]
                __import__(
                    "research_harness.research.schemas.project", fromlist=["ResearchQuestion"]
                ).ResearchQuestion
            )
        except Exception:
            from research_harness.research.schemas.project import ResearchQuestion

            if isinstance(rq_env.payload, dict):
                rq = ResearchQuestion.model_validate(rq_env.payload)
            else:
                rq = rq_env.payload  # type: ignore[assignment]

        rp = None
        if research_plan_id:
            try:
                rp_env = await self._store.get(research_plan_id)
                from research_harness.research.schemas.project import ResearchPlan

                if isinstance(rp_env.payload, dict):
                    rp = ResearchPlan.model_validate(rp_env.payload)
                else:
                    rp = rp_env.parse_payload(ResearchPlan)  # type: ignore[attr-defined]
            except Exception as e:
                raise ValueError(f"ResearchPlan {research_plan_id!r} not found: {e}") from e

        # Build prompt
        context_parts = [f"Research Question: {rq.question}"]
        if rq.motivation:
            context_parts.append(f"Motivation: {rq.motivation}")
        if rq.scope:
            context_parts.append(f"Scope: {rq.scope}")
        if rp:
            context_parts.append(f"Plan Objective: {rp.objective}")
            if rp.steps:
                context_parts.append(f"Plan Steps: {', '.join(rp.steps)}")
        context = "\n".join(context_parts)

        # Structured output schema — strict mode requires every property in `required`
        response_schema = {
            "type": "object",
            "properties": {
                "objective": {"type": "string"},
                "inclusion_criteria": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "criterion_id": {"type": "string"},
                            "description": {"type": "string"},
                            "rationale": {"type": "string"},
                            "required": {"type": "boolean"},
                        },
                        "required": ["criterion_id", "description", "rationale", "required"],
                        "additionalProperties": False,
                    },
                },
                "exclusion_criteria": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "criterion_id": {"type": "string"},
                            "description": {"type": "string"},
                            "rationale": {"type": "string"},
                            "required": {"type": "boolean"},
                        },
                        "required": ["criterion_id", "description", "rationale", "required"],
                        "additionalProperties": False,
                    },
                },
                "decision_rules": {"type": "string"},
            },
            "required": ["objective", "inclusion_criteria", "exclusion_criteria", "decision_rules"],
            "additionalProperties": False,
        }

        prompt = f"""You are a research assistant designing a title/abstract screening protocol.

Context:
{context}

Task: Propose inclusion and exclusion criteria for screening candidate papers.
- Inclusion: studies that should be included (e.g., algorithmic pricing, platform competition)
- Exclusion: studies that should be excluded (e.g., purely technical, non-scholarly)
- Each criterion must have a stable ID (I1, I2 for inclusion, E1, E2 for exclusion), description, rationale
- Provide decision_rules summarizing how to apply criteria (e.g., need all required inclusion, no exclusion)
- Keep criteria concise, max {self._max_inc} inclusion and {self._max_exc} exclusion

Return JSON matching the schema.
"""

        from research_harness.contracts.model import Message, ModelRequest

        messages = [
            Message(
                role="system",
                content="You are a helpful research protocol assistant. Return valid JSON.",
            ),
            Message(role="user", content=prompt),
        ]
        request = ModelRequest(
            messages=messages, response_schema=response_schema, temperature=0.2, metadata={}
        )

        try:
            response = await self._router.complete(self._model_role, request)
        except Exception as e:
            raise RuntimeError(
                f"protocol builder model call failed (role {self._model_role!r}): {e}"
            ) from e

        content = response.message.content or ""
        import json

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"protocol model returned invalid JSON: {content[:500]!r}: {e}") from e

        # Validate via temporary model
        from pydantic import BaseModel

        class CriterionProposal(BaseModel):
            criterion_id: str
            description: str
            rationale: str | None = None
            required: bool = True

            model_config = {"extra": "forbid"}

        class ProtocolProposal(BaseModel):
            objective: str
            inclusion_criteria: list[CriterionProposal]
            exclusion_criteria: list[CriterionProposal]
            decision_rules: str | None = None

            model_config = {"extra": "forbid"}

        try:
            proposal = ProtocolProposal.model_validate(data)
        except Exception as e:
            raise ValueError(f"protocol proposal validation failed: {e}, data: {data}") from e

        # Enforce bounds
        if len(proposal.inclusion_criteria) == 0:
            raise ValueError("at least one inclusion criterion required")
        if len(proposal.inclusion_criteria) > self._max_inc:
            raise ValueError(
                f"too many inclusion criteria {len(proposal.inclusion_criteria)} > {self._max_inc}"
            )
        if len(proposal.exclusion_criteria) > self._max_exc:
            raise ValueError(
                f"too many exclusion criteria {len(proposal.exclusion_criteria)} > {self._max_exc}"
            )
        # Check duplicate ids
        all_ids = [
            c.criterion_id for c in proposal.inclusion_criteria + proposal.exclusion_criteria
        ]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError(f"duplicate criterion ids: {all_ids}")
        for crit in proposal.inclusion_criteria + proposal.exclusion_criteria:
            if not crit.description.strip():
                raise ValueError(f"criterion {crit.criterion_id} has empty description")
            if len(crit.description) > 1000:
                raise ValueError(f"criterion {crit.criterion_id} description too long")

        # Build ScreeningProtocol
        inclusion = [
            ScreeningCriterion(
                criterion_id=c.criterion_id,
                kind="inclusion",  # type: ignore[arg-type]
                description=c.description,
                rationale=c.rationale,
                required=c.required,
            )
            for c in proposal.inclusion_criteria
        ]
        exclusion = [
            ScreeningCriterion(
                criterion_id=c.criterion_id,
                kind="exclusion",  # type: ignore[arg-type]
                description=c.description,
                rationale=c.rationale,
                required=c.required,
            )
            for c in proposal.exclusion_criteria
        ]

        protocol = ScreeningProtocol(
            research_question_id=research_question_id,
            research_plan_id=research_plan_id,
            objective=proposal.objective.strip(),
            inclusion_criteria=inclusion,
            exclusion_criteria=exclusion,
            decision_rules=proposal.decision_rules,
            model_role=self._model_role,
            status=ProtocolStatus.draft,
        )

        # Persist as draft
        env = ArtifactEnvelope.create(
            payload=protocol,
            artifact_type="screening_protocol",
            producer=f"literature.screening_protocol_builder:{self._model_role}",
        )
        await self._store.put(env)
        # Provenance
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=research_question_id,
                target_artifact_id=env.artifact_id,
                producer="literature.screening_protocol_builder",
            )
        )
        if research_plan_id:
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=research_plan_id,
                    target_artifact_id=env.artifact_id,
                    producer="literature.screening_protocol_builder",
                )
            )

        # Emit started
        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="screening.protocol.started",
                        source="literature.screening_protocol_builder",
                        payload={"protocol_id": env.artifact_id, "objective": protocol.objective},
                    )
                )
            except Exception:
                pass

        # Request approval via autonomy policy
        try:
            from research_harness.contracts.autonomy import ApprovalRequest

            approval_req = ApprovalRequest(
                request_id=env.artifact_id,
                checkpoint="screening_protocol",
                description=f"Approve screening protocol {env.artifact_id} with {len(inclusion)} inclusion and {len(exclusion)} exclusion criteria",
                payload={"protocol_id": env.artifact_id, "objective": protocol.objective},
            )
            decision = await self._autonomy.request_approval(approval_req)
            # If approved, update status to approved (create new artifact that supersedes draft)
            if decision.approved:
                approved_protocol = protocol.model_copy(update={"status": ProtocolStatus.approved})
                approved_env = ArtifactEnvelope.create(
                    payload=approved_protocol,
                    artifact_type="screening_protocol",
                    producer=f"literature.screening_protocol_builder:{self._model_role}",
                )
                await self._store.put(approved_env)
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.supersedes,
                        source_artifact_id=env.artifact_id,
                        target_artifact_id=approved_env.artifact_id,
                        producer="literature.screening_protocol_builder",
                    )
                )
                # Also link approved to RQ
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=research_question_id,
                        target_artifact_id=approved_env.artifact_id,
                        producer="literature.screening_protocol_builder",
                    )
                )
                if self._events is not None:
                    try:
                        from research_harness.kernel.events import Event

                        await self._events.publish(
                            Event.create(
                                event_type="screening.protocol.completed",
                                source="literature.screening_protocol_builder",
                                payload={
                                    "protocol_id": approved_env.artifact_id,
                                    "status": "approved",
                                },
                            )
                        )
                    except Exception:
                        pass
                return approved_env.artifact_id
            else:
                # Rejected — keep draft but mark as rejected via new artifact superseding
                rejected_protocol = protocol.model_copy(update={"status": ProtocolStatus.rejected})
                rejected_env = ArtifactEnvelope.create(
                    payload=rejected_protocol,
                    artifact_type="screening_protocol",
                    producer=f"literature.screening_protocol_builder:{self._model_role}",
                )
                await self._store.put(rejected_env)
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.supersedes,
                        source_artifact_id=env.artifact_id,
                        target_artifact_id=rejected_env.artifact_id,
                        producer="literature.screening_protocol_builder",
                    )
                )
                if self._events is not None:
                    try:
                        from research_harness.kernel.events import Event

                        await self._events.publish(
                            Event.create(
                                event_type="screening.protocol.failed",
                                source="literature.screening_protocol_builder",
                                payload={
                                    "protocol_id": rejected_env.artifact_id,
                                    "status": "rejected",
                                },
                            )
                        )
                    except Exception:
                        pass
                raise ValueError(
                    f"Screening protocol {env.artifact_id} rejected by autonomy policy: {decision.reason}"
                )
        except ValueError:
            raise
        except Exception as e:
            logger.exception("Approval failed for protocol %s", env.artifact_id)
            raise RuntimeError(f"approval failed for protocol {env.artifact_id}: {e}") from e


class ScreeningProtocolBuilderPlugin(Plugin):
    def __init__(self, model_role: str | None = None) -> None:
        self._model_role_override = model_role
        self._service: ScreeningProtocolBuilderService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="literature.screening_protocol_builder",
            version="0.1.0",
            plugin_type="literature",
            description="Screening protocol builder (model-assisted)",
            provides=["screening_protocol_builder.default"],
            requires=["model_router.default", "artifact_store.default", "autonomy_policy.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        lit_cfg: dict[str, Any] = {}
        if "literature" in cfg and isinstance(cfg["literature"], dict):
            lit_cfg = (
                cfg["literature"].get("screening", {})
                if isinstance(cfg["literature"].get("screening"), dict)
                else {}
            )
        model_role = self._model_role_override or lit_cfg.get("protocol_model_role") or "reasoning"
        max_inc = lit_cfg.get("max_inclusion_criteria", 12)
        max_exc = lit_cfg.get("max_exclusion_criteria", 12)

        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        autonomy = ctx.require("autonomy_policy.default")
        service = ScreeningProtocolBuilderService(
            model_router=router,
            artifact_store=store,
            autonomy_policy=autonomy,
            events=ctx.events,
            model_role=str(model_role),
            max_inclusion=int(max_inc),
            max_exclusion=int(max_exc),
        )
        self._service = service
        ctx.register("screening_protocol_builder.default", service)
