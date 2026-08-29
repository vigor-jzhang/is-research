"""evaluator.task_aware_routing — deterministic task-aware shadow routing
evaluator (Phase 7D.4).

Checks each TaskAwareRoutingDecision against known-answer expectations: exact-
task selection (qualified only), cross-task non-transfer, uncovered-task static
fallback, stale-qualification rejection, qualified primary/fallback behavior,
would_switch/deltas, and the critical metric `unsafe_task_route_rate` which
must be 0 — an unsafe route selects a model that is NOT exact-task qualified.
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


def _check_decision(
    d: dict[str, Any], reference: dict[str, Any], failures: list[str], unsafe: list[int]
) -> None:
    def _check(field: str, expected: Any, got: Any) -> None:
        if got != expected:
            failures.append(f"{d.get('task')}/{field}: expected {expected!r}, got {got!r}")

    if "expected_status" in reference:
        _check("status", reference["expected_status"], d.get("status"))
    if "expected_reason" in reference:
        _check("reason", reference["expected_reason"], d.get("reason"))
    if "expected_primary" in reference:
        _check("primary_candidate_id", reference["expected_primary"], d.get("primary_candidate_id"))
    if "expected_shadow_selected" in reference:
        _check(
            "shadow_selected_model",
            reference["expected_shadow_selected"],
            d.get("shadow_selected_model"),
        )
    if "expected_would_switch" in reference:
        _check("would_switch", reference["expected_would_switch"], d.get("would_switch"))
    if "expected_fallback" in reference:
        _check(
            "fallback_candidate_id",
            reference["expected_fallback"],
            d.get("fallback_candidate_id"),
        )
    if "expected_fallback_not_live_qualified" in reference:
        _check(
            "fallback_not_live_qualified",
            reference["expected_fallback_not_live_qualified"],
            d.get("fallback_not_live_qualified"),
        )
    if "expected_fallback_is_qualified" in reference:
        _check(
            "fallback_is_qualified",
            reference["expected_fallback_is_qualified"],
            d.get("fallback_is_qualified"),
        )

    # ---- exact-task qualification gate (critical) ---------------------------
    qualified_ids = {str(c.get("candidate_id")) for c in (d.get("qualified_candidates") or [])}
    status = d.get("status")
    primary = d.get("primary_candidate_id")
    if status == "selected":
        if not primary or primary not in qualified_ids:
            unsafe[0] += 1
            failures.append(
                f"{d.get('task')}/UNSAFE: selected {primary!r} but it is not exact-task "
                "qualified (qualification transferred?)"
            )
    if d.get("shadow_selected_model") and d.get("shadow_selected_model") not in qualified_ids:
        unsafe[0] += 1
        failures.append(
            f"{d.get('task')}/UNSAFE: shadow_selected_model {d.get('shadow_selected_model')!r} "
            "not exact-task qualified"
        )
    if status == "static_fallback":
        if d.get("would_switch") is True:
            unsafe[0] += 1
            failures.append(f"{d.get('task')}/UNSAFE: static_fallback reported would_switch=True")
        if d.get("shadow_selected_model"):
            unsafe[0] += 1
            failures.append(f"{d.get('task')}/UNSAFE: static_fallback reported a shadow switch")

    if d.get("fallback_not_live_qualified") and d.get("fallback_is_qualified"):
        unsafe[0] += 1
        failures.append(
            f"{d.get('task')}/UNSAFE: fallback marked both qualified and not_live_qualified"
        )


class TaskAwareRoutingEvaluator:
    evaluator_id = "evaluator.task_aware_routing"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        decision_envs = [
            e for e in ctx.produced_artifacts if e.artifact_type == "task_aware_routing_decision"
        ]
        if not decision_envs:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no task_aware_routing_decision produced for the case",
            )
        decisions = [envelope_payload_dict(e) for e in decision_envs]
        reference = ctx.case.reference or {}
        failures: list[str] = []
        unsafe: list[int] = [0]

        per_task = reference.get("expected_decisions")
        if isinstance(per_task, dict):
            for d in decisions:
                expected = per_task.get(str(d.get("task")))
                if expected is None:
                    failures.append(f"no expected decision for task {d.get('task')!r}")
                    continue
                _check_decision(d, expected, failures, unsafe)
        else:
            for d in decisions:
                _check_decision(d, reference, failures, unsafe)

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "task_aware_routing",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "task_aware_routing_accuracy": _metric(
                "task_aware_routing_accuracy",
                1.0 if not failures else 0.0,
                1,
                "rate",
                "task-aware routing decisions match the reference expectations",
            ),
            "unsafe_task_route_rate": _metric(
                "unsafe_task_route_rate",
                float(unsafe[0]),
                max(unsafe[0], 1),
                "rate",
                "any decision that selected or switched to a model not exact-task "
                "qualified, or an uncovered task reported a switch (critical)",
            ),
        }

        result_status = EvaluatorStatus.failed if failures else EvaluatorStatus.passed
        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=1.0 if not failures else 0.0,
            value={
                "decisions": [
                    {
                        "role": d.get("role"),
                        "task": d.get("task"),
                        "status": d.get("status"),
                        "reason": d.get("reason"),
                        "primary_candidate_id": d.get("primary_candidate_id"),
                        "shadow_selected_model": d.get("shadow_selected_model"),
                        "would_switch": d.get("would_switch"),
                        "fallback_candidate_id": d.get("fallback_candidate_id"),
                        "fallback_is_qualified": d.get("fallback_is_qualified"),
                        "fallback_not_live_qualified": d.get("fallback_not_live_qualified"),
                    }
                    for d in decisions
                ],
                "unsafe_task_route_count": unsafe[0],
                "metrics": metrics,
                "dimension_scores": {
                    "task_aware_routing_accuracy": float(not failures),
                    "unsafe_task_route_rate": float(unsafe[0]),
                },
            },
            status=result_status,
            explanation="; ".join(failures)
            if failures
            else "task-aware routing decisions matched all expectations",
            evidence_artifact_ids=[e.artifact_id for e in decision_envs],
        )


class TaskAwareRoutingEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.task_aware_routing",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic task-aware shadow routing evaluator (Phase 7D.4)",
            provides=["evaluator.task_aware_routing"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.task_aware_routing", TaskAwareRoutingEvaluator())
