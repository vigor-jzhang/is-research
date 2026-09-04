"""Routing eligibility + selection algorithm (Phase 7C) — pure, deterministic,
no LLM. Consumes leaderboard evidence and returns eligible/rejected candidates
plus the selected model under a documented policy.

Selection hierarchy:
1. capability compatibility (structured output / context)
2. deterministic eligibility threshold
3. reliability requirements
4. explicit user/policy constraints (allowed models/providers, max cost,
   latency limit, min repetitions, freshness)
5. quality
6. latency
7. cost
8. deterministic tie-break (candidate_id)

Correctness is never traded away for lower cost unless the request explicitly
lowers the required quality threshold. Missing/stale evidence is handled
explicitly (never a silent unqualified choice).
"""

from __future__ import annotations

from research_harness.research.routing.policies import PolicySpec
from research_harness.research.schemas.routing import (
    RoutingCandidateAssessment,
    RoutingDecisionStatus,
    RoutingRequest,
)
from research_harness.research.schemas.tournament import RoleLeaderboard

DEFAULT_REQUIRED_PASS_RATE = 0.85
DEFAULT_MIN_STRUCTURED_RATE = 0.5
DEFAULT_MAX_MODEL_ERROR_RATE = 0.5
# Errored cases are excluded from deterministic_pass_rate by design, so this is
# the only gate that stops a model which fails to complete most cases from
# qualifying on the strength of the few it did finish.
DEFAULT_MAX_CASE_ERROR_RATE = 0.10


def build_assessments(
    leaderboard: RoleLeaderboard,
    capability_ok: dict[str, bool] | None = None,
) -> list[RoutingCandidateAssessment]:
    """Map leaderboard entries to candidate assessments (raw dimensions kept)."""
    assessments: list[RoutingCandidateAssessment] = []
    cap_map = capability_ok or {}
    for entry in leaderboard.entries:
        model = entry.model or {}
        provider = str(model.get("provider") or "openrouter")
        requested = str(model.get("requested_model") or entry.resolved_model or "")
        capability_ok_val = cap_map.get(entry.candidate_id, True)
        assessments.append(
            RoutingCandidateAssessment(
                candidate_id=entry.candidate_id,
                model=model,
                provider=provider,
                requested_model=requested,
                eligibility=entry.eligibility,
                deterministic_pass_rate=entry.deterministic_pass_rate,
                benchmark_pass_rate=entry.benchmark_pass_rate,
                case_pass_rate=entry.case_pass_rate,
                case_error_rate=entry.case_error_rate,
                repetition_failure_rate=entry.repetition_failure_rate,
                structured_output_success_rate=entry.structured_output_success_rate,
                model_error_rate=entry.model_error_rate,
                retry_rate=entry.retry_rate,
                latency_ms_p50=entry.latency_ms_p50,
                estimated_cost=entry.estimated_cost,
                cost_per_successful_case=entry.cost_per_successful_case,
                advisory_score=entry.advisory_score,
                capability_ok=capability_ok_val,
                leaderboard_id=leaderboard.id,
            )
        )
    return assessments


def filter_eligible(
    assessments: list[RoutingCandidateAssessment],
    request: RoutingRequest,
) -> tuple[list[RoutingCandidateAssessment], list[RoutingCandidateAssessment]]:
    """Apply the mandatory gate. Returns (eligible, rejected)."""
    required_rate = (
        DEFAULT_REQUIRED_PASS_RATE
        if request.required_deterministic_pass_rate is None
        else request.required_deterministic_pass_rate
    )
    min_structured = (
        DEFAULT_MIN_STRUCTURED_RATE
        if request.min_structured_output_success_rate is None
        else request.min_structured_output_success_rate
    )
    max_error = (
        DEFAULT_MAX_MODEL_ERROR_RATE
        if request.max_model_error_rate is None
        else request.max_model_error_rate
    )
    max_case_error = (
        DEFAULT_MAX_CASE_ERROR_RATE
        if request.max_case_error_rate is None
        else request.max_case_error_rate
    )

    eligible: list[RoutingCandidateAssessment] = []
    rejected: list[RoutingCandidateAssessment] = []
    for a in assessments:
        reason: str | None = None

        # 1. capability compatibility
        if not a.capability_ok:
            reason = (
                "capability: model/provider does not meet structured-output or context requirements"
            )

        # 2. deterministic eligibility
        if reason is None:
            if a.deterministic_pass_rate is None:
                reason = "eligibility: no deterministic benchmark evidence"
            elif a.deterministic_pass_rate < required_rate:
                reason = (
                    f"eligibility: deterministic_pass_rate "
                    f"{a.deterministic_pass_rate:.3f} < required {required_rate:.3f}"
                )
            elif a.case_error_rate is not None and a.case_error_rate > max_case_error:
                # Without this, 1 passed / 99 errored scores 1.0 and qualifies.
                # Unknown (None) is not treated as passing: it only occurs when
                # there are no cases at all, which the branch above rejects.
                reason = (
                    f"eligibility: case_error_rate {a.case_error_rate:.3f} > "
                    f"max {max_case_error:.3f}"
                )
            elif a.eligibility != "eligible":
                reason = "eligibility: candidate marked ineligible in leaderboard evidence"

        # 3. reliability
        if reason is None and request.require_structured_output:
            if (
                a.structured_output_success_rate is not None
                and a.structured_output_success_rate < min_structured
            ):
                reason = (
                    f"reliability: structured_output_success_rate "
                    f"{a.structured_output_success_rate:.3f} < {min_structured:.3f}"
                )
        if reason is None:
            if a.model_error_rate is not None and a.model_error_rate > max_error:
                reason = f"reliability: model_error_rate {a.model_error_rate:.3f} > {max_error:.3f}"

        # 4. explicit constraints
        if reason is None and request.allowed_models:
            if a.requested_model not in request.allowed_models:
                reason = f"constraint: model {a.requested_model!r} not in allowed_models"
        if reason is None and request.allowed_providers:
            if a.provider not in request.allowed_providers:
                reason = f"constraint: provider {a.provider!r} not in allowed_providers"
        if reason is None and request.max_estimated_cost is not None:
            if a.estimated_cost is None:
                reason = "constraint: cost unknown, cannot satisfy max_estimated_cost"
            elif a.estimated_cost > request.max_estimated_cost:
                reason = (
                    f"constraint: estimated_cost {a.estimated_cost:.6f} > "
                    f"max {request.max_estimated_cost:.6f}"
                )
        if reason is None and request.latency_limit_ms is not None:
            if a.latency_ms_p50 is None:
                reason = "constraint: latency unknown, cannot satisfy latency_limit_ms"
            elif a.latency_ms_p50 > request.latency_limit_ms:
                reason = (
                    f"constraint: latency_p50 {a.latency_ms_p50:.1f} ms > "
                    f"limit {request.latency_limit_ms:.1f} ms"
                )

        a.eligibility = "eligible" if reason is None else "not_eligible"
        a.rejection_reason = reason
        if reason is None:
            eligible.append(a)
        else:
            rejected.append(a)
    return eligible, rejected


def select(
    eligible: list[RoutingCandidateAssessment],
    policy: PolicySpec,
    *,
    use_fallback: bool = False,
) -> tuple[RoutingCandidateAssessment | None, RoutingCandidateAssessment | None]:
    """Pick the primary (and fallback) from eligible candidates by the policy's
    deterministic rank. Fallback is the next-best distinct eligible candidate
    (approved fallback satisfying the same gate); None when unavailable."""
    if not eligible:
        return None, None
    ranked = sorted(eligible, key=policy.rank_key())
    if use_fallback:
        if len(ranked) >= 2:
            return ranked[1], ranked[0]
        return None, None
    primary = ranked[0]
    fallback = ranked[1] if len(ranked) >= 2 else None
    return primary, fallback


def decide_status(
    leaderboard: RoleLeaderboard | None,
    eligible: list[RoutingCandidateAssessment],
    leaderboard_too_old: bool,
    insufficient_repetitions: bool,
    *,
    use_fallback: bool = False,
) -> tuple[RoutingDecisionStatus, str]:
    """Determine the decision status + rationale given evidence availability."""
    if leaderboard is None or not leaderboard.entries:
        return RoutingDecisionStatus.insufficient_evidence, "no role leaderboard evidence"
    if leaderboard_too_old:
        return RoutingDecisionStatus.insufficient_evidence, "leaderboard evidence is stale"
    if insufficient_repetitions:
        return (
            RoutingDecisionStatus.insufficient_evidence,
            "leaderboard evidence has insufficient repetitions",
        )
    if not eligible:
        return RoutingDecisionStatus.no_eligible_model, "no candidate satisfies the gate"
    if use_fallback:
        return RoutingDecisionStatus.fallback, "fallback model selected"
    return RoutingDecisionStatus.selected, "primary model selected"
