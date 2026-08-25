"""evaluator.model_routing — deterministic model-routing evaluator (Phase 7C).

Checks the real policy router's decisions against known-answer expectations:
- routing_decision_accuracy: status + selected candidate match
- eligibility_filter_accuracy: eligible/rejected partitions + reasons match
- constraint_satisfaction_accuracy: cost/latency/allowed-model constraints honored
- fallback_accuracy: approved fallback matches
- role_isolation_accuracy: only the requested role's leaderboard was used
- stale_evidence_handling_accuracy: stale/insufficient evidence returns the
  right status (never a silent unqualified choice)
- deterministic_tiebreak_accuracy: tie-break selects the expected candidate
- unsafe_selection_rate: ANY selection of a deterministically ineligible model
  is a critical failure
"""

from __future__ import annotations

from typing import Any

from research_harness.contracts.evaluator import EvaluatorContext, envelope_payload_dict
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.schemas.evaluation import (
    EvaluatorCategory,
    EvaluatorResult,
    EvaluatorStatus,
)


class ModelRoutingEvaluator:
    evaluator_id = "evaluator.model_routing"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        decisions = [e for e in ctx.produced_artifacts if e.artifact_type == "routing_decision"]
        if not decisions:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no routing_decision produced for the case",
            )
        decision = envelope_payload_dict(max(decisions, key=lambda e: e.created_at))
        status = str(decision.get("status") or "")
        selected_id = decision.get("selected_candidate_id")
        fallback_id = decision.get("fallback_candidate_id")
        decision_role = str(decision.get("role") or "")
        eligible = list(decision.get("eligible_candidates") or [])
        rejected = list(decision.get("rejected_candidates") or [])
        shadow = decision.get("shadow") or {}
        rationale = decision.get("rationale") or {}

        reference = ctx.case.reference or {}
        expected_status = reference.get("expected_status")
        expected_selected = reference.get("expected_selected")
        expected_fallback = reference.get("expected_fallback")
        expected_role = reference.get("expected_role")
        expected_rejected = {str(x) for x in (reference.get("expected_rejected") or [])}
        expected_policy = reference.get("expected_policy")
        expected_would_switch = reference.get("expected_would_switch")
        expected_eligible_count = reference.get("expected_eligible_count")

        failures: list[str] = []

        # ---- decision status + selection ----------------------------------
        decision_ok = True
        if expected_status is not None and status != expected_status:
            failures.append(f"STATUS: got {status!r}, expected {expected_status!r}")
            decision_ok = False
        if expected_selected is not None and selected_id != expected_selected:
            failures.append(f"SELECTED: got {selected_id!r}, expected {expected_selected!r}")
            decision_ok = False
        if expected_policy is not None and decision.get("policy_id") != expected_policy:
            failures.append(
                f"POLICY: got {decision.get('policy_id')!r}, expected {expected_policy!r}"
            )

        # ---- role isolation -----------------------------------------------
        role_ok = True
        if expected_role is not None and decision_role != expected_role:
            failures.append(f"ROLE: decision role {decision_role!r} != expected {expected_role!r}")
            role_ok = False

        # ---- eligibility partition ----------------------------------------
        eligible_ids = {str(a.get("candidate_id")) for a in eligible}
        rejected_ids = {str(a.get("candidate_id")) for a in rejected}
        eligibility_ok = True
        if expected_eligible_count is not None and len(eligible_ids) != expected_eligible_count:
            failures.append(
                f"ELIGIBLE COUNT: got {len(eligible_ids)}, expected {expected_eligible_count}"
            )
            eligibility_ok = False
        if expected_rejected:
            missing = expected_rejected - rejected_ids
            if missing:
                failures.append(f"REJECTED: candidates {sorted(missing)} were not rejected")
                eligibility_ok = False
        for a in rejected:
            reason = a.get("rejection_reason")
            if not reason:
                failures.append(f"REJECTION REASON missing for candidate {a.get('candidate_id')!r}")
                eligibility_ok = False

        # ---- unsafe selection (critical) ----------------------------------
        unsafe = 0
        if selected_id is not None and selected_id not in eligible_ids:
            unsafe += 1
            failures.append(
                f"UNSAFE SELECTION: selected candidate {selected_id!r} was filtered as "
                "deterministically ineligible"
            )
        if (
            expected_status in ("insufficient_evidence", "no_eligible_model")
            and selected_id is not None
        ):
            unsafe += 1
            failures.append(f"UNSAFE SELECTION: status {status!r} but a model was selected")

        # ---- constraints --------------------------------------------------
        constraints_ok = True
        if expected_status == "selected":
            selected = next((a for a in eligible if a.get("candidate_id") == selected_id), None)
            if selected is not None:
                request = decision.get("request") or {}
                max_cost = request.get("max_estimated_cost")
                latency_limit = request.get("latency_limit_ms")
                allowed_models = request.get("allowed_models")
                if max_cost is not None:
                    cost = selected.get("estimated_cost")
                    if cost is None or cost > max_cost:
                        failures.append(
                            f"CONSTRAINT: selected cost {cost!r} violates max_estimated_cost {max_cost}"
                        )
                        constraints_ok = False
                if latency_limit is not None:
                    lat = selected.get("latency_ms_p50")
                    if lat is None or lat > latency_limit:
                        failures.append(
                            f"CONSTRAINT: selected latency {lat!r} violates limit {latency_limit}"
                        )
                        constraints_ok = False
                if allowed_models:
                    if selected.get("requested_model") not in allowed_models:
                        failures.append(
                            f"CONSTRAINT: selected model {selected.get('requested_model')!r} "
                            "not in allowed_models"
                        )
                        constraints_ok = False

        # ---- fallback ------------------------------------------------------
        fallback_ok = True
        if expected_fallback is not None:
            if fallback_id != expected_fallback:
                failures.append(f"FALLBACK: got {fallback_id!r}, expected {expected_fallback!r}")
                fallback_ok = False
        elif fallback_id is not None and fallback_id not in eligible_ids:
            failures.append(f"FALLBACK: {fallback_id!r} is not an eligible candidate")
            fallback_ok = False

        # ---- deterministic tie-break ----------------------------------------
        tiebreak_ok = True
        if reference.get("expected_tiebreak") is not None:
            if selected_id != reference.get("expected_tiebreak"):
                failures.append(
                    f"TIE-BREAK: expected {reference.get('expected_tiebreak')!r}, got {selected_id!r}"
                )
                tiebreak_ok = False

        # ---- shadow ----------------------------------------------------------
        if reference.get("expected_shadow") is True:
            if not shadow:
                failures.append("SHADOW: decision carries no shadow comparison")
            if expected_would_switch is not None:
                actual = shadow.get("would_switch")
                if actual != expected_would_switch:
                    failures.append(
                        f"SHADOW: would_switch {actual!r} != expected {expected_would_switch!r}"
                    )

        # ---- stale evidence --------------------------------------------------
        stale_ok = True
        if expected_status == "insufficient_evidence":
            reason = rationale.get("reason") or ""
            if (
                "stale" in reason.lower()
                or "repetitions" in reason.lower()
                or "no role" in reason.lower()
            ):
                stale_ok = True
            else:
                stale_ok = False
                failures.append(
                    f"STALE EVIDENCE: insufficient_evidence without a stale/insufficient rationale ({reason!r})"
                )

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "model_routing",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "routing_decision_accuracy": _metric(
                "routing_decision_accuracy",
                1.0 if decision_ok else 0.0,
                1,
                "rate",
                "decision status and selected candidate match expectations",
            ),
            "eligibility_filter_accuracy": _metric(
                "eligibility_filter_accuracy",
                1.0 if eligibility_ok else 0.0,
                1,
                "rate",
                "eligible/rejected partition and rejection reasons match expectations",
            ),
            "constraint_satisfaction_accuracy": _metric(
                "constraint_satisfaction_accuracy",
                1.0 if constraints_ok else 0.0,
                1,
                "rate",
                "cost/latency/allowed-model constraints honored for the selected model",
            ),
            "fallback_accuracy": _metric(
                "fallback_accuracy",
                1.0 if fallback_ok else 0.0,
                1,
                "rate",
                "approved fallback matches expectations and is eligible",
            ),
            "role_isolation_accuracy": _metric(
                "role_isolation_accuracy",
                1.0 if role_ok else 0.0,
                1,
                "rate",
                "only the requested role's evidence was used",
            ),
            "stale_evidence_handling_accuracy": _metric(
                "stale_evidence_handling_accuracy",
                1.0 if stale_ok else 0.0,
                1,
                "rate",
                "stale/insufficient evidence returns the right status without a silent choice",
            ),
            "deterministic_tiebreak_accuracy": _metric(
                "deterministic_tiebreak_accuracy",
                float(tiebreak_ok) if reference.get("expected_tiebreak") is not None else 0.0,
                1 if reference.get("expected_tiebreak") is not None else 0,
                "rate",
                "ties break deterministically to the expected candidate",
            ),
            "unsafe_selection_rate": _metric(
                "unsafe_selection_rate",
                float(unsafe),
                max(unsafe, 1),
                "rate",
                "selections of deterministically ineligible models (critical)",
            ),
        }

        status_result = EvaluatorStatus.failed if failures else EvaluatorStatus.passed
        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=1.0 if not failures else 0.0,
            value={
                "status": status,
                "selected_candidate_id": selected_id,
                "fallback_candidate_id": fallback_id,
                "eligible_candidate_ids": sorted(eligible_ids),
                "rejected_candidate_ids": sorted(rejected_ids),
                "unsafe_selection_count": unsafe,
                "metrics": metrics,
                "dimension_scores": {
                    "routing_decision_accuracy": float(decision_ok),
                    "eligibility_filter_accuracy": float(eligibility_ok),
                    "constraint_satisfaction_accuracy": float(constraints_ok),
                    "fallback_accuracy": float(fallback_ok),
                    "role_isolation_accuracy": float(role_ok),
                    "stale_evidence_handling_accuracy": float(stale_ok),
                    "deterministic_tiebreak_accuracy": float(tiebreak_ok),
                    "unsafe_selection_rate": float(unsafe),
                },
            },
            status=status_result,
            explanation="; ".join(failures) if failures else "all routing checks matched",
            evidence_artifact_ids=[e.artifact_id for e in decisions],
        )


class ModelRoutingEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.model_routing",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic model-routing evaluator (Phase 7C)",
            provides=["evaluator.model_routing"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.model_routing", ModelRoutingEvaluator())
