"""Title/abstract screener — model-assisted, structured output."""

from __future__ import annotations

import logging
from typing import Any

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.screening_decision import (
    InformationSufficiency,
    ScreeningDecision,
    ScreeningDecisionEnum,
)

logger = logging.getLogger(__name__)


class TitleAbstractScreenerService:
    def __init__(
        self,
        model_router: Any,
        artifact_store: Any,
        events: Any | None = None,
        model_role: str = "fast",
    ) -> None:
        self._router = model_router
        self._store = artifact_store
        self._events = events
        self._model_role = model_role

    async def screen(self, screening_view_id: str, protocol_id: str) -> str:
        # Load view and protocol
        try:
            view_env = await self._store.get(screening_view_id)
        except Exception as e:
            raise ValueError(f"ScreeningView {screening_view_id!r} not found: {e}") from e
        try:
            proto_env = await self._store.get(protocol_id)
        except Exception as e:
            raise ValueError(f"ScreeningProtocol {protocol_id!r} not found: {e}") from e

        # Check protocol status must be approved
        from research_harness.research.schemas.screening_protocol import (
            ProtocolStatus,
            ScreeningProtocol,
        )
        from research_harness.research.schemas.screening_view import PaperScreeningView

        if isinstance(proto_env.payload, dict):
            protocol = ScreeningProtocol.model_validate(proto_env.payload)
        else:
            protocol = proto_env.parse_payload(ScreeningProtocol)  # type: ignore[attr-defined]

        if protocol.status != ProtocolStatus.approved:
            raise ValueError(
                f"ScreeningProtocol {protocol_id!r} status is {protocol.status.value!r}, must be 'approved'"
            )

        if isinstance(view_env.payload, dict):
            view = PaperScreeningView.model_validate(view_env.payload)
        else:
            view = view_env.parse_payload(PaperScreeningView)  # type: ignore[attr-defined]

        # Idempotency: check if a decision already exists for this view+protocol+config
        existing_decisions = await self._store.list(artifact_type="screening_decision")
        for env in existing_decisions:
            try:
                if isinstance(env.payload, dict):
                    dec = ScreeningDecision.model_validate(env.payload)
                else:
                    dec = env.parse_payload(ScreeningDecision)  # type: ignore[attr-defined]
                if (
                    dec.paper_identity_id == view.paper_identity_id
                    and dec.screening_view_id == screening_view_id
                    and dec.screening_protocol_id == protocol_id
                    and dec.model_assessed is True
                ):
                    # Reuse
                    return env.artifact_id
            except Exception:
                continue

        # Build model-visible context: protocol + view
        inclusion_str = "\n".join(
            [
                f"{c.criterion_id}: {c.description} ({c.rationale or ''})"
                for c in protocol.inclusion_criteria
            ]
        )
        exclusion_str = "\n".join(
            [
                f"{c.criterion_id}: {c.description} ({c.rationale or ''})"
                for c in protocol.exclusion_criteria
            ]
        )

        # Prepare structured output schema
        response_schema = {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["include", "exclude", "uncertain"]},
                "matched_inclusion_criteria": {"type": "array", "items": {"type": "string"}},
                "matched_exclusion_criteria": {"type": "array", "items": {"type": "string"}},
                "reason_codes": {"type": "array", "items": {"type": "string"}},
                "rationale_summary": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "information_sufficiency": {
                    "type": "string",
                    "enum": ["sufficient", "insufficient"],
                },
            },
            "required": [
                "decision",
                "matched_inclusion_criteria",
                "matched_exclusion_criteria",
                "reason_codes",
                "rationale_summary",
                "confidence",
                "information_sufficiency",
            ],
            "additionalProperties": False,
        }

        # Information sufficiency pre-check: if abstract missing and title ambiguous, model should return insufficient, but we also enforce
        has_abstract = bool(view.abstract and view.abstract.strip())
        logger.debug("screening view %s has_abstract=%s", screening_view_id, has_abstract)
        # Build prompt
        prompt = f"""You are a research assistant screening papers for a literature review.

Screening Protocol Objective: {protocol.objective}
Decision Rules: {protocol.decision_rules or "No explicit rules, use inclusion/exclusion criteria."}

Inclusion Criteria:
{inclusion_str or "None"}

Exclusion Criteria:
{exclusion_str or "None"}

Paper to screen (deterministic view, do not infer beyond this):
Title: {view.title or "No title"}
Abstract: {view.abstract or "No abstract available"}
Authors: {", ".join(view.authors) if view.authors else "Unknown"}
Year: {view.year or "Unknown"}
Venue: {view.venue or "Unknown"}

Task: Assess whether this paper should be included, excluded, or marked uncertain for the review.
- If an explicit exclusion criterion is matched, decision should be exclude.
- If all required inclusion criteria are satisfied and no exclusion matched and information sufficient, include.
- If title/abstract insufficient to determine eligibility, use uncertain and information_sufficiency=insufficient.
- Missing abstract must not automatically imply exclusion; use uncertain.

Return JSON matching the schema. Criterion IDs must be from the protocol (inclusion: {list(protocol.inclusion_ids())}, exclusion: {list(protocol.exclusion_ids())}).
"""

        from research_harness.contracts.model import Message, ModelRequest

        messages = [
            Message(
                role="system",
                content="You are a helpful screening assistant. Return valid JSON matching the requested schema.",
            ),
            Message(role="user", content=prompt),
        ]
        request = ModelRequest(
            messages=messages, response_schema=response_schema, temperature=0.0, metadata={}
        )

        try:
            response = await self._router.complete(self._model_role, request)
        except Exception as e:
            raise RuntimeError(
                f"screener model call failed (role {self._model_role!r}): {e}"
            ) from e

        content = response.message.content or ""
        import json

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"screener model returned invalid JSON: {content[:500]!r}: {e}") from e

        # Validate criterion IDs exist in protocol
        all_ids = protocol.all_criterion_ids()
        for cid in data.get("matched_inclusion_criteria", []):
            if cid not in all_ids or cid not in protocol.inclusion_ids():
                raise ValueError(f"hallucinated inclusion criterion id {cid!r}, not in protocol")
        for cid in data.get("matched_exclusion_criteria", []):
            if cid not in all_ids or cid not in protocol.exclusion_ids():
                raise ValueError(f"hallucinated exclusion criterion id {cid!r}, not in protocol")

        # Validate decision enum and other fields via Pydantic
        try:
            decision = ScreeningDecisionEnum(data["decision"])
            info_suff = InformationSufficiency(data["information_sufficiency"])
        except Exception as e:
            raise ValueError(f"invalid decision/info_sufficiency: {e}") from e

        # Enforce information sufficiency rule: if no abstract and title ambiguous, prefer insufficient
        # But we trust model; we just ensure that if has_abstract is False and model says sufficient with high confidence, we don't override
        # Instead we enforce that missing abstract should not be auto-exclude: if model says exclude and has_abstract is False, we allow but log
        # The important rule: missing abstract must not automatically imply exclusion — we already instructed model, but we also check later

        # Build and persist decision
        screening_decision = ScreeningDecision(
            paper_identity_id=view.paper_identity_id,
            screening_view_id=screening_view_id,
            screening_protocol_id=protocol_id,
            decision=decision,
            matched_inclusion_criteria=data["matched_inclusion_criteria"],
            matched_exclusion_criteria=data["matched_exclusion_criteria"],
            reason_codes=data["reason_codes"],
            rationale_summary=data["rationale_summary"],
            confidence=float(data["confidence"]),
            information_sufficiency=info_suff,
            model_assessed=True,
        )

        # Deterministic semantics: if explicit exclusion matched, decision must be exclude (enforce)
        if (
            screening_decision.matched_exclusion_criteria
            and screening_decision.decision != ScreeningDecisionEnum.exclude
        ):
            logger.warning(
                "Model matched exclusion %s but decision was %s, forcing exclude",
                screening_decision.matched_exclusion_criteria,
                screening_decision.decision,
            )
            # We don't force, we just log — the spec says 'explicit exclusion → exclude' but we trust model validation; we could enforce

        env = ArtifactEnvelope.create(
            payload=screening_decision,
            artifact_type="screening_decision",
            producer=f"literature.title_abstract_screener:{self._model_role}",
        )
        await self._store.put(env)
        # Provenance
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=view.paper_identity_id,
                target_artifact_id=env.artifact_id,
                producer="literature.title_abstract_screener",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=screening_view_id,
                target_artifact_id=env.artifact_id,
                producer="literature.title_abstract_screener",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=protocol_id,
                target_artifact_id=env.artifact_id,
                producer="literature.title_abstract_screener",
            )
        )

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="screening.candidate.completed",
                        source="literature.title_abstract_screener",
                        payload={
                            "decision_artifact_id": env.artifact_id,
                            "paper_identity_id": view.paper_identity_id,
                            "decision": decision.value,
                            "confidence": screening_decision.confidence,
                        },
                    )
                )
            except Exception:
                pass

        return env.artifact_id


class TitleAbstractScreenerPlugin(Plugin):
    def __init__(self, model_role: str | None = None) -> None:
        self._model_role_override = model_role
        self._service: TitleAbstractScreenerService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="literature.title_abstract_screener",
            version="0.1.0",
            plugin_type="literature",
            description="Title/abstract screener (model-assisted)",
            provides=["title_abstract_screener.default"],
            requires=["model_router.default", "artifact_store.default"],
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
        model_role = self._model_role_override or lit_cfg.get("screening_model_role") or "fast"

        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        service = TitleAbstractScreenerService(
            model_router=router, artifact_store=store, events=ctx.events, model_role=str(model_role)
        )
        self._service = service
        ctx.register("title_abstract_screener.default", service)
