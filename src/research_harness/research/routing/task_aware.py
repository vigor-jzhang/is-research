"""Task-aware shadow routing (Phase 7D.4) — pure, deterministic, no LLM.

Selects an advisory model per (role, task) from TaskQualificationMatrix
evidence instead of a single role-level leaderboard. Shadow mode only: the
decision is recorded and production keeps executing the configured static role
model.

Selection rules (documented, never opaque):
1. Exact-task qualification gate: a model is considered ONLY when its
   TaskQualificationResult.qualified is true for the EXACT requested task.
   Qualification is never transferred across tasks.
2. Staleness gate: qualification evidence older than
   max_qualification_age_seconds is rejected (static_fallback).
3. Covered tasks (>=1 qualified model): rank qualified candidates by
   correctness/reliability first (task_rank_key: mean, worst, structured
   output), then latency, then cost, deterministic candidate_id tie-break.
   primary = top-ranked; fallback = next qualified model when one exists.
   When only a primary exists the fallback is the static configured model,
   explicitly marked fallback_not_live_qualified=true (never claimed
   qualified). An unqualified model (however cheap) is never selected.
4. Uncovered tasks (no qualified model): static_fallback with reason
   no_qualified_task_model; no dynamic switch.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from research_harness.research.routing.qualification import task_rank_key
from research_harness.research.routing.tasks import tasks_for_role
from research_harness.research.schemas.qualification import TaskQualificationMatrix
from research_harness.research.schemas.routing import (
    TaskAwareQualifiedCandidate,
    TaskAwareRoutingDecision,
    TaskAwareRoutingStatus,
)

TASK_AWARE_POLICY_ID = "task_aware_shadow_v1"
TASK_AWARE_POLICY_VERSION = "1"


def _qualified_rows(matrix: TaskQualificationMatrix, task: str) -> list[Any]:
    return [r for r in matrix.rows if r.task == task and r.qualified]


def _candidate(row: Any) -> TaskAwareQualifiedCandidate:
    return TaskAwareQualifiedCandidate(
        candidate_id=row.candidate_id,
        resolved_model=row.resolved_model,
        qualified=row.qualified,
        deterministic_pass_rate_mean=row.deterministic_pass_rate_mean,
        deterministic_pass_rate_worst=row.deterministic_pass_rate_worst,
        structured_output_success_rate=row.structured_output_success_rate,
        provider_error_frequency=row.provider_error_frequency,
        critical_grounding_failures=row.critical_grounding_failures,
        latency_ms_p50=row.latency_ms_p50,
        estimated_cost=row.estimated_cost,
        live_quality_run_id=row.live_quality_run_id,
    )


def build_task_aware_decision(
    *,
    role: str,
    task: str,
    matrix: TaskQualificationMatrix,
    static_model: str | None,
    static_provider: str | None = None,
    max_qualification_age_seconds: float | None = None,
    matrix_age_seconds: float | None = None,
) -> TaskAwareRoutingDecision:
    """Build one task-aware shadow routing decision from task-qualification
    evidence. Pure and deterministic; never switches production."""
    task = str(task)
    if task not in tasks_for_role(role):
        raise ValueError(
            f"unknown task {task!r} for role {role!r}; expected {tasks_for_role(role)}"
        )
    if matrix.role != role:
        raise ValueError(
            f"role/task mismatch: matrix for role {matrix.role!r} cannot route role {role!r}"
        )

    task_rows = [r for r in matrix.rows if r.task == task]
    qualified_rows = _qualified_rows(matrix, task)

    age = (
        matrix_age_seconds
        if matrix_age_seconds is not None
        else (datetime.now(UTC) - matrix.created_at).total_seconds()
    )
    stale = max_qualification_age_seconds is not None and age > max_qualification_age_seconds

    reason = ""
    if not task_rows:
        reason = "no_qualified_task_model"
    elif stale:
        reason = "stale_qualification"
    elif not qualified_rows:
        reason = "no_qualified_task_model"

    status = TaskAwareRoutingStatus.static_fallback if reason else TaskAwareRoutingStatus.selected

    qualified_candidates = [_candidate(r) for r in qualified_rows]

    primary_id: str | None = None
    fallback_id: str | None = None
    fallback_is_qualified = False
    fallback_not_live_qualified = False
    would_switch: bool | None = None
    shadow_selected: str | None = None
    quality_delta: float | None = None
    latency_delta: float | None = None
    cost_delta: float | None = None

    if status == TaskAwareRoutingStatus.selected:
        ranked = sorted(qualified_rows, key=task_rank_key)
        primary = ranked[0]
        primary_id = primary.candidate_id
        shadow_selected = primary.candidate_id
        would_switch = primary.candidate_id != static_model
        if len(ranked) >= 2:
            fallback_id = ranked[1].candidate_id
            fallback_is_qualified = True
        else:
            fallback_id = static_model
            fallback_not_live_qualified = True

        static_row = next((r for r in qualified_rows if r.candidate_id == static_model), None)
        if static_row is not None:
            if primary.deterministic_pass_rate_mean is not None:
                quality_delta = primary.deterministic_pass_rate_mean - (
                    static_row.deterministic_pass_rate_mean or 0.0
                )
            if primary.latency_ms_p50 is not None:
                latency_delta = primary.latency_ms_p50 - (
                    static_row.latency_ms_p50 if static_row.latency_ms_p50 is not None else 0.0
                )
            if primary.estimated_cost is not None:
                cost_delta = primary.estimated_cost - (
                    static_row.estimated_cost if static_row.estimated_cost is not None else 0.0
                )
    else:
        fallback_id = static_model
        fallback_not_live_qualified = True
        would_switch = False

    decision_policy: dict[str, Any] = {
        "gate": "exact_task_qualification",
        "rank": "correctness/reliability/structured-output then latency then cost; "
        "deterministic candidate_id tie-break",
        "staleness_seconds": max_qualification_age_seconds,
        "transfer_across_tasks": False,
    }

    return TaskAwareRoutingDecision(
        policy_id=TASK_AWARE_POLICY_ID,
        policy_version=TASK_AWARE_POLICY_VERSION,
        decision_policy=decision_policy,
        role=role,
        task=task,
        task_label=task,
        status=status,
        reason=reason,
        matrix_id=matrix.id,
        matrix_age_seconds=round(age, 3),
        current_static_model=static_model,
        static_model_provider=static_provider,
        qualified_candidates=qualified_candidates,
        primary_candidate_id=primary_id,
        fallback_candidate_id=fallback_id,
        fallback_is_qualified=fallback_is_qualified,
        fallback_not_live_qualified=fallback_not_live_qualified,
        would_switch=would_switch,
        shadow_selected_model=shadow_selected,
        qualification_result_ids=[
            r.live_quality_run_id for r in qualified_rows if r.live_quality_run_id
        ],
        expected_quality_delta=quality_delta,
        expected_latency_delta=latency_delta,
        expected_cost_delta=cost_delta,
        shadow={
            "routing_mode": "shadow",
            "current_model": static_model,
            "would_switch": would_switch,
            "expected_quality_delta": quality_delta,
            "expected_latency_delta": latency_delta,
            "expected_cost_delta": cost_delta,
        },
    )
