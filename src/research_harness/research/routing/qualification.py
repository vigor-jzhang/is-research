"""Deterministic live-model qualification (Phase 7D.1/7D.2).

Pure functions — no network, no LLM. Qualification reuses Phase 7D.0
QualificationCriteria exactly (never loosened to obtain a winner). Produces
structured rejection kinds, and among qualified candidates picks primary +
fallback with quality/reliability first, then latency/cost (policy-driven).
An unqualified model can never be primary or fallback.

Phase 7D.2 adds structured failure attribution, per-task performance,
stability (stable/borderline/unstable), and the ProductionQualificationMatrix.
Confirmed benchmark/evaluator defects are excluded from qualification; only
genuine model/provider outcomes count.
"""

from __future__ import annotations

from typing import Any

from research_harness.research.routing.readiness import criteria_for_role, qualify_model
from research_harness.research.schemas.live_quality import (
    FailureAttributionKind,
    LiveQualityModelResult,
    QualificationCriteria,
)
from research_harness.research.schemas.qualification import (
    ProductionQualificationMatrix,
    ProductionQualificationMatrixRow,
    QualificationCandidateResult,
    QualificationRejectionKind,
    RoleQualificationSummary,
)

_STABILITY_STABLE_MARGIN = 0.05
_STABILITY_MAX_VARIANCE = 0.02


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


def attribute_failure_text(text: str) -> FailureAttributionKind:
    """Deterministically attribute one case-failure string to a kind (Phase 7D.2)."""
    t = text.lower()
    if "timeout" in t:
        return FailureAttributionKind.timeout
    if "rate_limit" in t or "rate limit" in t:
        return FailureAttributionKind.rate_limit
    if "provider" in t or "validation_failure" in t:
        return FailureAttributionKind.provider_error
    if (
        "unsupported reference" in t
        or "locator page" in t
        or "no evidence_item produced" in t
        or "no synthesis_statement produced" in t
        or "no research_gap produced" in t
        or "no mechanism_candidate produced" in t
        or "no formal_analytical_model produced" in t
        or "no proposition produced" in t
        or "deterministic verification did not pass" in t
    ):
        return FailureAttributionKind.grounding_failure
    if "required concepts missing" in t or "instruction_adherence" in t:
        return FailureAttributionKind.instruction_following_failure
    if (
        "empty statement" in t
        or "missing required structure" in t
        or "structured_output" in t
        or "no decision produced" in t
        or "missing overall_assessment" in t
        or "structured_output_success_rate" in t
    ):
        return FailureAttributionKind.structured_output_failure
    if (
        "defect_recall" in t
        or "decision_accuracy" in t
        or "gap_type" in t
        or "false exclusion" in t
        or "false_exclusion" in t
        or "required_field" in t
    ):
        return FailureAttributionKind.model_reasoning_failure
    return FailureAttributionKind.model_reasoning_failure


def attribute_failures(
    failures: list[str],
    *,
    defect_case_ids: set[str],
    case_id: str = "",
) -> tuple[dict[str, int], dict[str, int]]:
    """Attribute a list of case failures into genuine (included) and excluded
    (confirmed benchmark/evaluator defect) buckets.

    For a case confirmed in the calibration ledger as a benchmark or evaluator
    defect, ALL of its failures are excluded from qualification (attributed to
    the defect kind). Otherwise each failure is classified by text.

    Returns (failure_attribution, excluded_failure_attribution)."""
    attribution: dict[str, int] = {}
    excluded: dict[str, int] = {}
    if case_id in defect_case_ids:
        # confirmed benchmark/evaluator defect -> excluded, never a model failure
        for _ in failures:
            excluded[FailureAttributionKind.benchmark_reference_defect.value] = (
                excluded.get(FailureAttributionKind.benchmark_reference_defect.value, 0) + 1
            )
        return attribution, excluded
    for f in failures:
        kind = attribute_failure_text(f).value
        attribution[kind] = attribution.get(kind, 0) + 1
    return attribution, excluded


def stability_status(result: LiveQualityModelResult, criteria: QualificationCriteria) -> str:
    """Evidence-stability status (Phase 7D.2).

    - stable: every repetition meets the deterministic threshold and variance is
      small, with a comfortable margin above the threshold.
    - borderline: qualified evidence with a thin margin (worst within
      `_STABILITY_STABLE_MARGIN` of the threshold) or moderate variance.
    - unstable: evidence is not dependable (a repetition below the threshold,
      critical grounding failures, or provider-error rate above the cap)."""
    worst = result.deterministic_pass_rate_worst
    mean = result.deterministic_pass_rate_mean
    variance = result.deterministic_pass_rate_variance or 0.0
    threshold = criteria.min_deterministic_pass_rate
    provider_error = (
        result.provider_error_frequency if result.provider_error_frequency is not None else 1.0
    )
    if (
        worst is None
        or worst < threshold
        or result.critical_grounding_failures
        or provider_error > criteria.max_provider_error_rate
    ):
        return "unstable"
    if mean is not None and worst >= threshold:
        if variance <= _STABILITY_MAX_VARIANCE and worst >= threshold + _STABILITY_STABLE_MARGIN:
            return "stable"
        return "borderline"
    return "unstable"


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
        stability=result.stability or stability_status(result, criteria),
        repetitions=result.repetitions,
        critical_grounding_failures=result.critical_grounding_failures,
        failure_attribution=dict(result.failure_attribution or {}),
        excluded_failure_attribution=dict(result.excluded_failure_attribution or {}),
        task_performance=[tp.model_dump(mode="json") for tp in (result.task_performance or [])],
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

    # eligibility (Phase 7D.2): qualified and not unstable; the selected primary
    # is never its own fallback
    for c in candidates:
        stable_ok = (c.stability or "unstable") != "unstable"
        c.primary_eligible = bool(c.qualified and stable_ok)
        c.fallback_eligible = bool(c.qualified and stable_ok and c.candidate_id != primary)

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


def build_qualification_matrix(
    candidates: list[QualificationCandidateResult],
    *,
    role: str,
    benchmark_id: str,
    repetitions: int,
    criteria: QualificationCriteria,
) -> ProductionQualificationMatrix:
    """Build the production-qualification matrix for one role (Phase 7D.2).

    Eligibility is stricter than qualification: a qualified candidate is
    primary/fallback eligible only if its evidence is not unstable. Rows keep
    raw dimensions; unqualified candidates are never ranked above qualified."""
    qualified = [c for c in candidates if c.qualified]
    ranked = sorted(qualified, key=_rank_key)
    primary = ranked[0].candidate_id if ranked else None
    fallback = ranked[1].candidate_id if len(ranked) >= 2 else None
    if primary and fallback:
        status = "qualified"
    elif primary:
        status = "qualified_without_fallback"
    else:
        status = "no_qualified_model"

    rows: list[ProductionQualificationMatrixRow] = []
    for c in candidates:
        rows.append(
            ProductionQualificationMatrixRow(
                role=role,
                candidate=c.candidate_id,
                qualified=c.qualified,
                stability=c.stability,
                primary_eligible=c.primary_eligible,
                fallback_eligible=c.fallback_eligible,
                repetitions=c.repetitions,
                rejection_kinds=list(c.rejection_kinds),
                rejection_reasons=list(c.rejection_reasons),
                live_quality_run_ids=[c.live_quality_run_id] if c.live_quality_run_id else [],
                deterministic_pass_rate_mean=c.deterministic_pass_rate_mean,
                deterministic_pass_rate_worst=c.deterministic_pass_rate_worst,
                deterministic_pass_rate_variance=c.deterministic_pass_rate_variance,
                structured_output_success_rate=c.structured_output_success_rate,
                provider_error_frequency=c.provider_error_frequency,
                critical_grounding_failures=c.critical_grounding_failures,
                latency_ms_p50=c.latency_ms_p50,
                estimated_cost=c.estimated_cost,
                total_tokens=c.total_tokens,
            )
        )
    return ProductionQualificationMatrix(
        role=role,
        status=status,
        primary=primary,
        fallback=fallback,
        rows=rows,
        benchmark_id=benchmark_id,
        repetitions=repetitions,
        criteria=criteria.model_dump(mode="json") if criteria else None,
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
