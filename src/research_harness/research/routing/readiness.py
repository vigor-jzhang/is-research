"""Deterministic production-routing readiness (Phase 7D.0).

Pure functions — no network, no LLM. Qualification criteria are role-specific
(different roles require different standards) and are persisted with every
assessment. Live-quality evidence is required for production routing; offline
fixture tournament results alone can never authorize routing.

The critical invariant: a role is never qualified from unsafe/insufficient
evidence (`unsafe_production_qualification` must stay False).
"""

from __future__ import annotations

from typing import Any

from research_harness.research.schemas.live_quality import (
    LiveQualityModelResult,
    QualificationCriteria,
)

# Role-specific standards (overridable per run, persisted with the assessment).
ROLE_QUALIFICATION_CRITERIA: dict[str, QualificationCriteria] = {
    "fast": QualificationCriteria(
        role="fast",
        min_deterministic_pass_rate=0.9,
        min_structured_output_success_rate=0.9,
        max_provider_error_rate=0.05,
        min_repetitions=3,
        description="Lightweight structured tasks require high decision accuracy "
        "and near-zero provider failures.",
    ),
    "reasoning": QualificationCriteria(
        role="reasoning",
        min_deterministic_pass_rate=0.85,
        min_structured_output_success_rate=0.85,
        max_provider_error_rate=0.1,
        min_repetitions=3,
        description="Analytical generation: high structural validity required.",
    ),
    "critic": QualificationCriteria(
        role="critic",
        min_deterministic_pass_rate=0.85,
        min_structured_output_success_rate=0.85,
        max_provider_error_rate=0.1,
        min_repetitions=3,
        description="Independent critique: defect detection must be reliable.",
    ),
}


def criteria_for_role(role: str) -> QualificationCriteria:
    criteria = ROLE_QUALIFICATION_CRITERIA.get(role)
    if criteria is None:
        from research_harness.research.routing.roles import validate_role

        validate_role(role)
        criteria = QualificationCriteria(role=role)
    return criteria


def _rate(value: float | None, default: float) -> float:
    return default if value is None else value


def qualify_model(
    result: LiveQualityModelResult,
    criteria: QualificationCriteria,
) -> tuple[bool, list[str]]:
    """Deterministic qualification of one model's live-quality result."""
    reasons: list[str] = []

    if result.repetitions < criteria.min_repetitions:
        reasons.append(
            f"insufficient repetitions: {result.repetitions} < {criteria.min_repetitions}"
        )
    if not result.task_results:
        reasons.append("no live-quality task results")
    if criteria.leaderboard_max_age_seconds is not None:
        from datetime import UTC, datetime

        age = (datetime.now(UTC) - result.evidence_timestamp).total_seconds()
        if age > criteria.leaderboard_max_age_seconds:
            reasons.append(
                f"stale live evidence: {age:.0f}s > {criteria.leaderboard_max_age_seconds}s"
            )

    det = _rate(result.deterministic_pass_rate_mean, 0.0)
    if det < criteria.min_deterministic_pass_rate:
        reasons.append(
            f"deterministic_pass_rate {det:.3f} < {criteria.min_deterministic_pass_rate}"
        )
    structured = _rate(result.structured_output_success_rate, 0.0)
    if structured < criteria.min_structured_output_success_rate:
        reasons.append(
            f"structured_output_success_rate {structured:.3f} < "
            f"{criteria.min_structured_output_success_rate}"
        )
    provider_error = _rate(result.provider_error_frequency, 1.0)
    if provider_error > criteria.max_provider_error_rate:
        reasons.append(
            f"provider_error_frequency {provider_error:.3f} > {criteria.max_provider_error_rate}"
        )
    if criteria.require_no_critical_grounding_failures and result.critical_grounding_failures:
        reasons.append(f"{result.critical_grounding_failures} critical grounding failure(s)")
    cases = sum(t.cases_total for t in result.task_results)
    if cases < criteria.min_cases:
        reasons.append(f"exercised cases {cases} < {criteria.min_cases}")

    qualified = not reasons
    return qualified, reasons


def assess_role_readiness(
    live_results: dict[str, LiveQualityModelResult],
    criteria: QualificationCriteria,
    *,
    configured_model: str | None,
    require_fallback: bool,
) -> dict[str, Any]:
    """Aggregate per-model live evidence into a role readiness verdict."""
    qualified_models: list[str] = []
    qualified_reasons: dict[str, list[str]] = {}
    for candidate_id, result in live_results.items():
        ok, reasons = qualify_model(result, criteria)
        qualified_reasons[candidate_id] = reasons
        if ok:
            qualified_models.append(candidate_id)

    reasons: list[str] = []
    if not live_results:
        reasons.append("no live-quality evidence available for this role")
    elif configured_model is None:
        reasons.append("no configured production model for this role")
    elif configured_model not in live_results:
        reasons.append(f"no live-quality evidence for the configured model {configured_model!r}")
    else:
        reasons.extend(qualified_reasons[configured_model])

    configured_qualified = configured_model in qualified_models
    fallback_qualified = False
    fallback_model: str | None = None
    if configured_qualified and require_fallback:
        fallbacks = [m for m in qualified_models if m != configured_model]
        if fallbacks:
            fallback_qualified = True
            fallback_model = sorted(fallbacks)[0]
        else:
            reasons.append("no qualified fallback model (policy requires one)")

    qualified = bool(live_results) and configured_qualified
    if require_fallback and qualified and not fallback_qualified:
        qualified = False

    return {
        "qualified": qualified,
        "reasons": reasons,
        "qualified_models": sorted(qualified_models),
        "configured_qualified": configured_qualified,
        "fallback_qualified": fallback_qualified,
        "fallback_model": fallback_model,
        "configured_model": configured_model,
        "qualified_reasons": qualified_reasons,
    }


def summary_for(result: LiveQualityModelResult) -> dict[str, Any]:
    """Compact, human-readable live-quality summary for an assessment."""
    return {
        "benchmark": result.benchmark_id,
        "repetitions": result.repetitions,
        "deterministic_pass_rate_mean": result.deterministic_pass_rate_mean,
        "deterministic_pass_rate_worst": result.deterministic_pass_rate_worst,
        "structured_output_success_rate": result.structured_output_success_rate,
        "provider_error_frequency": result.provider_error_frequency,
        "critical_grounding_failures": result.critical_grounding_failures,
        "qualified": result.qualification,
    }
