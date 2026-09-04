"""Screening orchestrator — PaperIdentity level, budgets, idempotency."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.screening_execution import (
    ScreenedLiteratureSet,
    ScreeningExecution,
)

logger = logging.getLogger(__name__)


class ScreeningOrchestratorService:
    def __init__(
        self,
        artifact_store: Any,
        view_builder: Any,
        screener: Any,
        autonomy_policy: Any | None = None,
        events: Any | None = None,
        max_candidates: int = 500,
        max_model_calls: int = 500,
        review_uncertain: bool = True,
        review_low_confidence_below: float | None = 0.65,
    ) -> None:
        self._store = artifact_store
        self._view_builder = view_builder
        self._screener = screener
        self._autonomy = autonomy_policy
        self._events = events
        self._max_candidates = max_candidates
        self._max_model_calls = max_model_calls
        self._review_uncertain = review_uncertain
        self._review_low_confidence = review_low_confidence_below

    async def screen(self, search_execution_id: str, protocol_id: str) -> str:
        # Load protocol and check approved
        try:
            proto_env = await self._store.get(protocol_id)
        except Exception as e:
            raise ValueError(f"ScreeningProtocol {protocol_id!r} not found: {e}") from e

        from research_harness.research.schemas.screening_protocol import (
            ProtocolStatus,
            ScreeningProtocol,
        )

        if isinstance(proto_env.payload, dict):
            protocol = ScreeningProtocol.model_validate(proto_env.payload)
        else:
            protocol = proto_env.parse_payload(ScreeningProtocol)  # type: ignore[attr-defined]

        if protocol.status != ProtocolStatus.approved:
            raise ValueError(
                f"ScreeningProtocol {protocol_id!r} status is {protocol.status.value!r}, must be 'approved'"
            )

        # Load search execution to get candidate paper identities
        try:
            search_exec_env = await self._store.get(search_execution_id)
        except Exception as e:
            raise ValueError(
                f"LiteratureSearchExecution {search_execution_id!r} not found: {e}"
            ) from e

        from research_harness.research.schemas.execution import LiteratureSearchExecution

        if isinstance(search_exec_env.payload, dict):
            search_exec = LiteratureSearchExecution.model_validate(search_exec_env.payload)
        else:
            search_exec = search_exec_env.parse_payload(LiteratureSearchExecution)  # type: ignore[attr-defined]

        # Get candidate PaperIdentities: from search execution's paper_identity_artifact_ids, but also consider current non-superseded identities
        # For now, use the execution's paper identities directly, but filter to current non-superseded
        candidate_ids = list(search_exec.paper_identity_artifact_ids)
        # Also consider if execution has no identities (maybe it had paper_artifact_ids but not yet resolved?) - for now use those
        if not candidate_ids:
            # H22: this used to fall back to every non-superseded paper_identity
            # in the store, including identities from unrelated prior runs, and
            # then spend up to the full model-call budget screening them. An
            # empty candidate set is a failure signal (the search found nothing,
            # or identity resolution failed -- see H23), not a request to screen
            # the whole database.
            resolution_failed = bool(
                (search_exec.metadata or {}).get("identity_resolution_failed")
            )
            detail = (
                "identity resolution failed upstream"
                if resolution_failed
                else "the search produced no candidate identities"
            )
            raise ValueError(
                f"no candidate identities to screen: {detail} "
                f"(search execution {search_exec_env.artifact_id!r} reported "
                f"{len(search_exec.paper_artifact_ids)} raw paper record(s) and "
                f"{len(search_exec.provider_failures)} provider failure(s))"
            )

        # Filter to current non-superseded identities
        current_candidates: list[str] = []
        for pid in candidate_ids:
            # Check if this identity is superseded
            try:
                children = await self._store.get_children(pid)
                is_superseded = any(c.relation == ProvenanceRelation.supersedes for c in children)
                if not is_superseded:
                    current_candidates.append(pid)
                else:
                    logger.debug("Skipping superseded identity %s", pid)
            except Exception:
                current_candidates.append(pid)

        # Enforce max_candidates budget
        total_candidates = len(current_candidates)
        if total_candidates > self._max_candidates:
            logger.warning(
                "Too many candidates %d > max %d, truncating",
                total_candidates,
                self._max_candidates,
            )
            budget_stop_reason = f"max_candidates {self._max_candidates} reached"
            current_candidates = current_candidates[: self._max_candidates]
        else:
            budget_stop_reason = None

        started_at = datetime.now(UTC)
        decision_ids: list[str] = []
        view_ids: list[str] = []
        review_ids: list[str] = []
        included: list[str] = []
        excluded: list[str] = []
        uncertain: list[str] = []
        failed: list[dict[str, Any]] = []
        model_calls = 0
        reused = 0

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="screening.started",
                        source="literature.screening_orchestrator",
                        payload={
                            "protocol_id": protocol_id,
                            "search_execution_id": search_execution_id,
                            "candidate_count": len(current_candidates),
                            "total_candidates": total_candidates,
                        },
                    )
                )
            except Exception:
                pass

        # Sequential screening
        for pi_id in current_candidates:
            if model_calls >= self._max_model_calls:
                budget_stop_reason = f"max_model_calls {self._max_model_calls} reached"
                logger.warning("Budget stop: %s", budget_stop_reason)
                break

            # Idempotency: check if a decision already exists for this pi + protocol
            existing = await self._find_existing_decision(pi_id, protocol_id)
            if existing:
                decision_ids.append(existing)
                # Need to determine final disposition for counts
                try:
                    dec_env = await self._store.get(existing)
                    from research_harness.research.schemas.screening_decision import (
                        ScreeningDecision,
                    )

                    dec = (
                        dec_env.parse_payload(ScreeningDecision)
                        if hasattr(dec_env, "parse_payload")
                        else ScreeningDecision.model_validate(dec_env.payload)
                    )  # type: ignore[attr-defined]
                    # Check for review
                    final_dec = dec.decision.value
                    # Look for review
                    reviews = await self._store.list(artifact_type="screening_review")
                    for r_env in reviews:
                        from research_harness.research.schemas.screening_review import (
                            ScreeningReview,
                        )

                        try:
                            rev = (
                                r_env.parse_payload(ScreeningReview)
                                if hasattr(r_env, "parse_payload")
                                else ScreeningReview.model_validate(r_env.payload)
                            )  # type: ignore[attr-defined]
                            if rev.screening_decision_id == existing:
                                final_dec = rev.final_decision
                                break
                        except Exception:
                            continue
                    if final_dec == "include":
                        included.append(pi_id)
                    elif final_dec == "exclude":
                        excluded.append(pi_id)
                    else:
                        uncertain.append(pi_id)
                except Exception:
                    uncertain.append(pi_id)
                reused += 1
                continue

            # Build view
            try:
                view_id = await self._view_builder.build(pi_id)
                view_ids.append(view_id)
            except Exception as e:
                logger.exception("Failed to build view for %s", pi_id)
                failed.append({"paper_identity_id": pi_id, "error": str(e), "stage": "view"})
                continue

            # Screen
            try:
                if self._events is not None:
                    try:
                        from research_harness.kernel.events import Event

                        await self._events.publish(
                            Event.create(
                                event_type="screening.candidate.started",
                                source="literature.screening_orchestrator",
                                payload={"paper_identity_id": pi_id, "view_id": view_id},
                            )
                        )
                    except Exception:
                        pass
                decision_id = await self._screener.screen(view_id, protocol_id)
                model_calls += 1
                decision_ids.append(decision_id)

                # Fetch decision to determine outcome and handle review
                dec_env = await self._store.get(decision_id)
                from research_harness.research.schemas.screening_decision import ScreeningDecision

                dec = (
                    dec_env.parse_payload(ScreeningDecision)
                    if hasattr(dec_env, "parse_payload")
                    else ScreeningDecision.model_validate(dec_env.payload)
                )  # type: ignore[attr-defined]

                # Handle uncertain / low confidence via autonomy
                final_decision = dec.decision.value
                needs_review = False
                review_reason = None
                if dec.decision.value == "uncertain" and self._review_uncertain:
                    needs_review = True
                    review_reason = "uncertain"
                elif (
                    self._review_low_confidence is not None
                    and dec.confidence is not None
                    and dec.confidence < self._review_low_confidence
                ):
                    needs_review = True
                    review_reason = (
                        f"low_confidence {dec.confidence} < {self._review_low_confidence}"
                    )

                if needs_review and self._autonomy is not None:
                    # Request approval/review
                    try:
                        from research_harness.contracts.autonomy import ApprovalRequest
                        from research_harness.research.schemas.screening_review import (
                            ReviewerType,
                            ScreeningReview,
                        )

                        approval_req = ApprovalRequest(
                            request_id=decision_id,
                            checkpoint="screening_review",
                            description=f"Review screening decision {decision_id} for {pi_id}: {dec.decision.value} confidence {dec.confidence}",
                            payload={
                                "paper_identity_id": pi_id,
                                "decision_id": decision_id,
                                "reason": review_reason,
                            },
                        )
                        # Emit review requested
                        if self._events is not None:
                            try:
                                from research_harness.kernel.events import Event

                                await self._events.publish(
                                    Event.create(
                                        event_type="screening.review.requested",
                                        source="literature.screening_orchestrator",
                                        payload={
                                            "decision_id": decision_id,
                                            "reason": review_reason,
                                        },
                                    )
                                )
                            except Exception:
                                pass
                        decision = await self._autonomy.request_approval(approval_req)
                        # Create ScreeningReview artifact
                        # Map approval decision to final disposition
                        # For now, if approved, keep original; if not, use overridden? But autonomy in high mode auto-approves with original?
                        # We will create a review that records original and final
                        # If autonomy approved with reason auto, we keep original
                        # If we want to simulate human override, we would need to handle
                        # For now, create review with final = original (no override) but record review
                        review = ScreeningReview(
                            screening_decision_id=decision_id,
                            review_reason=review_reason or "review",
                            original_decision=dec.decision.value,
                            final_decision=dec.decision.value,  # no override in high autonomy
                            reviewer_type=ReviewerType.autonomy_policy,
                            approval_decision_id=decision.request_id,
                            notes=decision.reason,
                        )
                        review_env = ArtifactEnvelope.create(
                            payload=review,
                            artifact_type="screening_review",
                            producer="literature.screening_orchestrator",
                        )
                        await self._store.put(review_env)
                        await self._store.add_provenance(
                            ProvenanceLink(
                                relation=ProvenanceRelation.derived_from,
                                source_artifact_id=decision_id,
                                target_artifact_id=review_env.artifact_id,
                                producer="literature.screening_orchestrator",
                            )
                        )
                        review_ids.append(review_env.artifact_id)
                        if self._events is not None:
                            try:
                                from research_harness.kernel.events import Event

                                await self._events.publish(
                                    Event.create(
                                        event_type="screening.review.completed",
                                        source="literature.screening_orchestrator",
                                        payload={
                                            "review_id": review_env.artifact_id,
                                            "decision_id": decision_id,
                                            "final": final_decision,
                                        },
                                    )
                                )
                            except Exception:
                                pass
                    except Exception as e:
                        logger.warning("Review failed for %s: %s", pi_id, e)

                # Determine final disposition for counts (if review exists, final is review's final)
                # For now without override, final == dec.decision
                # Check if there's a review for this decision
                final_for_counts = dec.decision.value
                # Look for review just created
                # If review exists, it would have final same as original, so no change
                if final_for_counts == "include":
                    included.append(pi_id)
                elif final_for_counts == "exclude":
                    excluded.append(pi_id)
                else:
                    uncertain.append(pi_id)

                if self._events is not None:
                    try:
                        from research_harness.kernel.events import Event

                        await self._events.publish(
                            Event.create(
                                event_type="screening.candidate.completed",
                                source="literature.screening_orchestrator",
                                payload={
                                    "paper_identity_id": pi_id,
                                    "decision": final_for_counts,
                                    "confidence": dec.confidence,
                                },
                            )
                        )
                    except Exception:
                        pass

            except Exception as e:
                logger.exception("Screening failed for %s", pi_id)
                failed.append({"paper_identity_id": pi_id, "error": str(e)})
                if self._events is not None:
                    try:
                        from research_harness.kernel.events import Event

                        await self._events.publish(
                            Event.create(
                                event_type="screening.candidate.failed",
                                source="literature.screening_orchestrator",
                                payload={"paper_identity_id": pi_id, "error": str(e)},
                            )
                        )
                    except Exception:
                        pass
                continue

        completed_at = datetime.now(UTC)

        # Create ScreeningExecution

        execution = ScreeningExecution(
            protocol_artifact_id=protocol_id,
            search_execution_artifact_id=search_execution_id,
            candidate_identity_ids=current_candidates,
            screening_view_ids=view_ids,
            decision_artifact_ids=decision_ids,
            review_artifact_ids=review_ids,
            started_at=started_at,
            completed_at=completed_at,
            counts={
                "total_candidates": total_candidates,
                "processed": len(decision_ids),
                "included": len(included),
                "excluded": len(excluded),
                "uncertain": len(uncertain),
                "failed": len(failed),
                "reused": reused,
                "missing_abstract": 0,
            },
            failures=failed,
            budget_stop_reason=budget_stop_reason,
        )

        exec_env = ArtifactEnvelope.create(
            payload=execution,
            artifact_type="screening_execution",
            producer="literature.screening_orchestrator",
        )
        await self._store.put(exec_env)
        # Provenance
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=protocol_id,
                target_artifact_id=exec_env.artifact_id,
                producer="literature.screening_orchestrator",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=search_execution_id,
                target_artifact_id=exec_env.artifact_id,
                producer="literature.screening_orchestrator",
            )
        )

        # Create ScreenedLiteratureSet

        screened_set = ScreenedLiteratureSet(
            screening_execution_id=exec_env.artifact_id,
            screening_protocol_id=protocol_id,
            included_identity_ids=included,
            excluded_identity_ids=excluded,
            uncertain_identity_ids=uncertain,
            decision_artifact_ids=decision_ids,
        )
        set_env = ArtifactEnvelope.create(
            payload=screened_set,
            artifact_type="screened_literature_set",
            producer="literature.screening_orchestrator",
        )
        await self._store.put(set_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=exec_env.artifact_id,
                target_artifact_id=set_env.artifact_id,
                producer="literature.screening_orchestrator",
            )
        )

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="screening.completed",
                        source="literature.screening_orchestrator",
                        payload={
                            "execution_id": exec_env.artifact_id,
                            "screened_set_id": set_env.artifact_id,
                            "counts": execution.counts,
                        },
                    )
                )
            except Exception:
                pass

        return exec_env.artifact_id

    async def _find_existing_decision(self, paper_identity_id: str, protocol_id: str) -> str | None:
        # Search for existing decision with same inputs
        decisions = await self._store.list(artifact_type="screening_decision")
        for env in decisions:
            try:
                from research_harness.research.schemas.screening_decision import ScreeningDecision

                if isinstance(env.payload, dict):
                    dec = ScreeningDecision.model_validate(env.payload)
                else:
                    dec = env.parse_payload(ScreeningDecision)  # type: ignore[attr-defined]
                if (
                    dec.paper_identity_id == paper_identity_id
                    and dec.screening_protocol_id == protocol_id
                ):
                    return env.artifact_id
            except Exception:
                continue
        return None


class ScreeningOrchestratorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="literature.screening_orchestrator",
            version="0.1.0",
            plugin_type="literature",
            description="Screening orchestrator",
            provides=["screening_orchestrator.default"],
            requires=[
                "artifact_store.default",
                "screening_view_builder.default",
                "title_abstract_screener.default",
            ],
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
        max_candidates = lit_cfg.get("max_candidates", 500)
        max_calls = lit_cfg.get("max_model_calls", 500)
        review_uncertain = lit_cfg.get("review_uncertain", True)
        review_low = lit_cfg.get("review_low_confidence_below", 0.65)

        store = ctx.require("artifact_store.default")
        view_builder = ctx.require("screening_view_builder.default")
        screener = ctx.require("title_abstract_screener.default")

        autonomy = ctx.try_get("autonomy_policy.default")

        service = ScreeningOrchestratorService(
            artifact_store=store,
            view_builder=view_builder,
            screener=screener,
            autonomy_policy=autonomy,
            events=ctx.events,
            max_candidates=int(max_candidates),
            max_model_calls=int(max_calls),
            review_uncertain=bool(review_uncertain),
            review_low_confidence_below=float(review_low) if review_low is not None else None,
        )
        ctx.register("screening_orchestrator.default", service)
