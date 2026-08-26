"""Phase 7D.0 unit tests — production-routing qualification/readiness logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from research_harness.research.routing.readiness import (
    assess_role_readiness,
    criteria_for_role,
    qualify_model,
)
from research_harness.research.schemas.live_quality import (
    LiveQualityModelResult,
    QualificationCriteria,
)


def _result(
    candidate_id: str,
    *,
    det: float = 0.9,
    structured: float = 0.9,
    provider_error: float = 0.0,
    grounding: int = 0,
    repetitions: int = 3,
    age_seconds: float | None = None,
) -> LiveQualityModelResult:
    return LiveQualityModelResult(
        candidate_id=candidate_id,
        model={"candidate_id": candidate_id, "requested_model": f"m-{candidate_id}"},
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        repetitions=repetitions,
        deterministic_pass_rate_mean=det,
        structured_output_success_rate=structured,
        provider_error_frequency=provider_error,
        critical_grounding_failures=grounding,
        evidence_timestamp=(
            datetime.now(UTC) - timedelta(seconds=age_seconds) if age_seconds else datetime.now(UTC)
        ),
        task_results=[
            {
                "repetition": i,
                "run_id": f"r{i}",
                "report_id": f"p{i}",
                "report_status": "passed",
                "cases_total": 1,
                "cases_passed": 1,
                "cases_failed": 0,
                "cases_error": 0,
                "task_pass_rate": 1.0,
                "task_completed": True,
            }
            for i in range(repetitions)
        ],
    )


def test_threshold_qualification():
    ok, reasons = qualify_model(_result("a", det=0.92), criteria_for_role("reasoning"))
    assert ok
    assert reasons == []


def test_below_threshold_rejected():
    ok, reasons = qualify_model(_result("a", det=0.6), criteria_for_role("reasoning"))
    assert not ok
    assert any("deterministic_pass_rate" in r for r in reasons)


def test_insufficient_repetitions_rejected():
    ok, reasons = qualify_model(_result("a", repetitions=1), criteria_for_role("reasoning"))
    assert not ok
    assert any("repetitions" in r for r in reasons)


def test_critical_grounding_failure_rejected():
    ok, reasons = qualify_model(_result("a", grounding=1), criteria_for_role("reasoning"))
    assert not ok
    assert any("grounding" in r for r in reasons)


def test_high_provider_error_rejected():
    ok, reasons = qualify_model(_result("a", provider_error=0.5), criteria_for_role("reasoning"))
    assert not ok
    assert any("provider_error" in r for r in reasons)


def test_stale_evidence_rejected():
    criteria = QualificationCriteria(role="reasoning", leaderboard_max_age_seconds=100)
    ok, reasons = qualify_model(_result("a", age_seconds=5000), criteria)
    assert not ok
    assert any("stale" in r for r in reasons)


def test_fast_role_stricter_standards():
    # fast requires higher decision accuracy + lower provider error
    fast_criteria = criteria_for_role("fast")
    assert (
        fast_criteria.min_deterministic_pass_rate
        >= criteria_for_role("reasoning").min_deterministic_pass_rate
    )
    ok, _ = qualify_model(_result("a", det=0.88, provider_error=0.03), fast_criteria)
    assert not ok  # 0.88 < 0.9 for fast


def test_role_readiness_configured_qualified_with_fallback():
    live = {
        "m-configured": _result("m-configured", det=0.92),
        "m-fallback": _result("m-fallback", det=0.9),
    }
    verdict = assess_role_readiness(
        live, criteria_for_role("reasoning"), configured_model="m-configured", require_fallback=True
    )
    assert verdict["qualified"] is True
    assert verdict["configured_qualified"] is True
    assert verdict["fallback_qualified"] is True
    assert verdict["fallback_model"] == "m-fallback"
    assert verdict["qualified_models"] == ["m-configured", "m-fallback"]


def test_role_readiness_no_live_evidence():
    verdict = assess_role_readiness(
        {}, criteria_for_role("reasoning"), configured_model="m-configured", require_fallback=True
    )
    assert verdict["qualified"] is False
    assert any("no live-quality evidence" in r for r in verdict["reasons"])


def test_role_readiness_configured_not_in_evidence():
    live = {"m-other": _result("m-other", det=0.95)}
    verdict = assess_role_readiness(
        live, criteria_for_role("reasoning"), configured_model="m-production", require_fallback=True
    )
    assert verdict["qualified"] is False
    assert any("configured model" in r for r in verdict["reasons"])


def test_role_readiness_no_qualified_fallback():
    live = {"m-configured": _result("m-configured", det=0.95)}
    verdict = assess_role_readiness(
        live, criteria_for_role("reasoning"), configured_model="m-configured", require_fallback=True
    )
    assert verdict["qualified"] is False
    assert any("fallback" in r for r in verdict["reasons"])
    assert verdict["fallback_qualified"] is False


def test_role_readiness_fallback_not_required():
    live = {"m-configured": _result("m-configured", det=0.95)}
    verdict = assess_role_readiness(
        live,
        criteria_for_role("reasoning"),
        configured_model="m-configured",
        require_fallback=False,
    )
    assert verdict["qualified"] is True


def test_deterministic_rerun_same_verdict():
    live = {
        "m-configured": _result("m-configured", det=0.92),
        "m-fallback": _result("m-fallback", det=0.9),
    }
    v1 = assess_role_readiness(
        live, criteria_for_role("reasoning"), configured_model="m-configured", require_fallback=True
    )
    v2 = assess_role_readiness(
        live, criteria_for_role("reasoning"), configured_model="m-configured", require_fallback=True
    )
    assert v1 == v2
    assert v1["qualified"] == v2["qualified"]
    assert v1["qualified_models"] == v2["qualified_models"]
