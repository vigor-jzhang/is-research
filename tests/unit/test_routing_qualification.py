"""Phase 7D.1 unit tests — live-model qualification logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from research_harness.research.routing.qualification import (
    build_role_summary,
    candidate_result,
    classify_rejection_kinds,
    qualify_candidate,
    summarize_role_live,
)
from research_harness.research.routing.readiness import criteria_for_role
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
    cost: float | None = None,
    role: str = "reasoning",
) -> LiveQualityModelResult:
    return LiveQualityModelResult(
        candidate_id=candidate_id,
        model={"candidate_id": candidate_id, "requested_model": f"m-{candidate_id}"},
        resolved_model=f"m-{candidate_id}",
        role=role,
        benchmark_id="live-quality-reasoning-v1",
        repetitions=repetitions,
        deterministic_pass_rate_mean=det,
        deterministic_pass_rate_worst=det,
        structured_output_success_rate=structured,
        provider_error_frequency=provider_error,
        critical_grounding_failures=grounding,
        estimated_cost=cost,
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


def test_rejection_classification():
    ok, kinds, _ = qualify_candidate(_result("a", det=0.6), criteria_for_role("reasoning"))
    assert not ok
    assert "below_quality_threshold" in kinds

    ok, kinds, _ = qualify_candidate(_result("a", grounding=1), criteria_for_role("reasoning"))
    assert not ok
    assert "critical_grounding_failure" in kinds

    ok, kinds, _ = qualify_candidate(
        _result("a", provider_error=0.5), criteria_for_role("reasoning")
    )
    assert not ok
    assert "provider_error_rate" in kinds

    ok, kinds, _ = qualify_candidate(_result("a", repetitions=1), criteria_for_role("reasoning"))
    assert not ok
    assert "insufficient_repetitions" in kinds

    ok, kinds, _ = qualify_candidate(_result("a", structured=0.5), criteria_for_role("reasoning"))
    assert not ok
    assert "structured_output_failure" in kinds


def test_stale_rejection():
    criteria = QualificationCriteria(role="reasoning", leaderboard_max_age_seconds=100)
    ok, kinds, _ = qualify_candidate(_result("a", age_seconds=5000), criteria)
    assert not ok
    assert "stale_evidence" in kinds


def test_classify_rejection_kinds_fallback_to_capability():
    kinds = classify_rejection_kinds(["some other reason"])
    assert kinds == ["capability_mismatch"]
    assert classify_rejection_kinds([]) == []


def test_primary_and_fallback_from_qualified():
    candidates = [
        candidate_result(_result("m-b", det=0.9), criteria_for_role("reasoning")),
        candidate_result(_result("m-a", det=0.92), criteria_for_role("reasoning")),
    ]
    summary = build_role_summary(
        candidates, criteria_for_role("reasoning"), benchmark_id="b", repetitions=3
    )
    assert summary.status == "qualified"
    assert summary.primary == "m-a"
    assert summary.fallback == "m-b"
    assert summary.qualified_models == ["m-a", "m-b"]


def test_cheaper_unqualified_never_wins():
    candidates = [
        candidate_result(_result("m-cheap", det=0.6, cost=0.0001), criteria_for_role("reasoning")),
        candidate_result(_result("m-good", det=0.9, cost=0.05), criteria_for_role("reasoning")),
    ]
    summary = build_role_summary(
        candidates, criteria_for_role("reasoning"), benchmark_id="b", repetitions=3
    )
    assert summary.primary == "m-good"
    assert summary.status == "qualified_without_fallback"


def test_deterministic_tie():
    candidates = [
        candidate_result(_result("m-beta", det=0.9), criteria_for_role("reasoning")),
        candidate_result(_result("m-alpha", det=0.9), criteria_for_role("reasoning")),
    ]
    summary = build_role_summary(
        candidates, criteria_for_role("reasoning"), benchmark_id="b", repetitions=3
    )
    assert summary.primary == "m-alpha"
    assert summary.fallback == "m-beta"


def test_no_qualified_model_status():
    candidates = [
        candidate_result(_result("m-a", det=0.6), criteria_for_role("reasoning")),
        candidate_result(_result("m-b", det=0.7), criteria_for_role("reasoning")),
    ]
    summary = build_role_summary(
        candidates, criteria_for_role("reasoning"), benchmark_id="b", repetitions=3
    )
    assert summary.status == "no_qualified_model"
    assert summary.primary is None
    assert summary.fallback is None


def test_role_isolation():
    summary, candidates = summarize_role_live(
        {"m-a": _result("m-a", det=0.95, role="reasoning")},
        role="critic",
        benchmark_id="live-quality-critic-v1",
        repetitions=3,
    )
    assert summary.status == "no_qualified_model"
    assert candidates == []


def test_rejection_counts():
    candidates = [
        candidate_result(_result("m-a", det=0.6), criteria_for_role("reasoning")),
        candidate_result(_result("m-b", grounding=1), criteria_for_role("reasoning")),
    ]
    summary = build_role_summary(
        candidates, criteria_for_role("reasoning"), benchmark_id="b", repetitions=3
    )
    assert summary.rejection_counts["below_quality_threshold"] == 1
    assert summary.rejection_counts["critical_grounding_failure"] == 1


def test_borderline_stays_unqualified():
    ok, kinds, _ = qualify_candidate(_result("m-border", det=0.849), criteria_for_role("reasoning"))
    assert not ok
    assert "below_quality_threshold" in kinds
