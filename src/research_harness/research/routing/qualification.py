"""Deterministic live-model qualification (Phase 7D.1).

Pure functions — no network, no LLM. Qualification reuses Phase 7D.0
QualificationCriteria exactly (never loosened to obtain a winner). Produces
structured rejection kinds, and among qualified candidates picks primary +
fallback with quality/reliability first, then latency/cost (policy-driven).
An unqualified model can never be primary or fallback.
"""

from __future__ import annotations

from typing import Any

from research_harness.research.routing.readiness import criteria_for_role, qualify_model
from research_harness.research.schemas.live_quality import (
    LiveQualityModelResult,
    QualificationCriteria,
)
from research_harness.research.schemas.qualification import (
    QualificationCandidateResult,
    QualificationRejectionKind,
    RoleQualificationSummary,
)


def classify_rejection_kinds(reasons: list[str]) -> list[str]:
    """Map qualify_model reason strings to structured rejection kinds."""
    kinds: list[str] = []
    text = " ".join(reasons).lower()
    if "repetitions" in text or "no live-quality task results" in text or "exercised cases" in text:
        kinds.append(QualificationRejectionKind.insufficient_repetitions.value)
    if "deterministic_pass_rate" in text:
        kinds.append(QualificationRejectionKind.below_quality_threshold.value)
    if "structured_output_success_rate" in text:
        kinds.append(QualificationRejectionKind.structured_output_failure.value)
    if "provider_error" in text:
        kinds.append(QualificationRejectionKind.provider_error_rate.value)
    if "grounding" in text:
        kinds.append(QualificationRejectionKind.critical_grounding_failure.value)
    if "stale" in text:
        kinds.append(QualificationRejectionKind.stale_evidence.value)
    if not kinds and reasons:
        kinds.append(QualificationRejectionKind.capability_mismatch.value)
    return kinds


def qualify_candidate(
    result: LiveQualityModelResult, criteria: QualificationCriteria
) -> tuple[bool, list[str], list[str]]:
    qualified, reasons = qualify_model(result, criteria)
    kinds = classify_rejection_kinds(reasons)
    return qualified, kinds, reasons


def candidate_result(
    result: LiveQualityModelResult,
    criteria: QualificationCriteria,
    *,
    live_quality_run_id: str | None = None,
    leaderboard_id: str | None = None,
) -> QualificationCandidateResult:
    qualified, kinds, reasons = qualify_candidate(result, criteria)
    total = max(result.repetitions, 1)
    critical_frequency = result.critical_grounding_failures / total
    return QualificationCandidateResult(
        candidate_id=result.candidate_id,
        model=result.model,
        resolved_model=result.resolved_model or result.model.get("requested_model"),
        role=result.role,
        benchmark_id=result.benchmark_id,
        qualified=qualified,
        rejection_kinds=kinds,
        rejection_reasons=reasons,
        live_quality_run_id=live_quality_run_id,
        leaderboard_id=leaderboard_id,
        deterministic_pass_rate_mean=result.deterministic_pass_rate_mean,
        deterministic_pass_rate_worst=result.deterministic_pass_rate_worst,
        deterministic_pass_rate_variance=result.deterministic_pass_rate_variance,
        critical_failure_frequency=critical_frequency,
        structured_output_success_rate=result.structured_output_success_rate,
        provider_error_frequency=result.provider_error_frequency,
        latency_ms_p50=result.latency_ms_p50_mean,
        total_tokens=result.total_tokens,
        estimated_cost=result.estimated_cost,
    )


def _rank_key(c: QualificationCandidateResult) -> tuple[Any, ...]:
    """Quality/reliability first, then latency/cost. None = worst."""
    det = c.deterministic_pass_rate_mean if c.deterministic_pass_rate_mean is not None else -1.0
    structured = (
        c.structured_output_success_rate if c.structured_output_success_rate is not None else -1.0
    )
    error = c.provider_error_frequency if c.provider_error_frequency is not None else float("inf")
    latency = c.latency_ms_p50 if c.latency_ms_p50 is not None else float("inf")
    cost = c.estimated_cost if c.estimated_cost is not None else float("inf")
    return (-det, -structured, error, latency, cost, c.candidate_id)


def build_role_summary(
    candidates: list[QualificationCandidateResult],
    criteria: QualificationCriteria,
    *,
    benchmark_id: str,
    repetitions: int,
) -> RoleQualificationSummary:
    qualified = [c for c in candidates if c.qualified]
    qualified_models = sorted(c.candidate_id for c in qualified)
    ranked = sorted(qualified, key=_rank_key)

    primary = ranked[0].candidate_id if ranked else None
    fallback = ranked[1].candidate_id if len(ranked) >= 2 else None
    if primary and fallback:
        status = "qualified"
    elif primary:
        status = "qualified_without_fallback"
    else:
        status = "no_qualified_model"

    rejection_counts: dict[str, int] = {}
    for c in candidates:
        for kind in c.rejection_kinds:
            rejection_counts[kind] = rejection_counts.get(kind, 0) + 1

    return RoleQualificationSummary(
        role=criteria.role,
        status=status,
        primary=primary,
        fallback=fallback,
        qualified_models=qualified_models,
        candidates=candidates,
        rejection_counts=rejection_counts,
        criteria=criteria,
        benchmark_id=benchmark_id,
        repetitions=repetitions,
    )


def summarize_role_live(
    live_results: dict[str, LiveQualityModelResult],
    *,
    role: str,
    benchmark_id: str,
    repetitions: int,
    criteria: QualificationCriteria | None = None,
) -> tuple[RoleQualificationSummary, list[QualificationCandidateResult]]:
    """Build a role summary from a dict of live-quality results (offline use).

    Role isolation: results whose `role` does not match the requested role are
    never considered (a reasoning-qualified model is not qualified for critic)."""
    criteria = criteria or criteria_for_role(role)
    if criteria.role != role:
        criteria = criteria.model_copy(update={"role": role})
    candidates = [
        candidate_result(result, criteria)
        for result in live_results.values()
        if result.role == role
    ]
    return build_role_summary(
        candidates, criteria, benchmark_id=benchmark_id, repetitions=repetitions
    ), candidates
