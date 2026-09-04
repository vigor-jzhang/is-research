"""Phase 7D.2 unit tests — failure attribution, stability, qualification matrix.

Covers the new Phase 7D.2 machinery: structured failure attribution,
stability (stable/borderline/unstable), defect exclusion from qualification,
and the production-qualification matrix (eligibility, raw dimensions, role
partial qualification). Qualification thresholds are never loosened.
"""

from __future__ import annotations

import pytest

from research_harness.research.benchmarks.calibration import (
    audit_all_live_quality_benchmarks,
    audit_live_quality_benchmark,
)
from research_harness.research.routing.qualification import (
    attribute_failure_text,
    attribute_failures,
    build_qualification_matrix,
    qualify_candidate,
    stability_status,
    summarize_role_live,
)
from research_harness.research.routing.readiness import criteria_for_role
from research_harness.research.schemas.live_quality import (
    FailureAttributionKind,
    LiveQualityModelResult,
    QualificationCriteria,
)


def _result(
    candidate_id: str,
    *,
    det: float = 0.9,
    worst: float | None = None,
    variance: float = 0.0,
    structured: float = 0.9,
    provider_error: float = 0.0,
    grounding: int = 0,
    repetitions: int = 3,
    role: str = "reasoning",
    excluded_attribution: dict[str, int] | None = None,
    attribution: dict[str, int] | None = None,
    stability: str | None = None,
    cost: float | None = None,
) -> LiveQualityModelResult:
    return LiveQualityModelResult(
        candidate_id=candidate_id,
        model={"candidate_id": candidate_id, "requested_model": f"m-{candidate_id}"},
        resolved_model=f"m-{candidate_id}",
        role=role,
        benchmark_id="live-quality-reasoning-v1",
        repetitions=repetitions,
        deterministic_pass_rate_mean=det,
        deterministic_pass_rate_worst=worst if worst is not None else det,
        deterministic_pass_rate_variance=variance,
        structured_output_success_rate=structured,
        provider_error_frequency=provider_error,
        critical_grounding_failures=grounding,
        excluded_failure_attribution=excluded_attribution or {},
        failure_attribution=attribution or {},
        stability=stability,
        estimated_cost=cost,
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


def _criteria(role: str = "reasoning") -> QualificationCriteria:
    return criteria_for_role(role)


# ---------------------------------------------------------------------------
# failure attribution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("timeout waiting for provider response", FailureAttributionKind.timeout),
        ("rate_limit exceeded", FailureAttributionKind.rate_limit),
        ("provider_error from upstream", FailureAttributionKind.provider_error),
        (
            "evidence[0]: unsupported reference 'x' (not produced by this run)",
            FailureAttributionKind.grounding_failure,
        ),
        (
            "evidence[0]: locator page 9 outside document pages 1..2",
            FailureAttributionKind.grounding_failure,
        ),
        (
            "proposition_generation: deterministic verification did not pass",
            FailureAttributionKind.grounding_failure,
        ),
        (
            "instruction_adherence: required concepts missing: ['foo']",
            FailureAttributionKind.instruction_following_failure,
        ),
        (
            "statement[0]: empty statement (required field)",
            FailureAttributionKind.structured_output_failure,
        ),
        (
            "model[0]: missing required structure 'payoffs'",
            FailureAttributionKind.structured_output_failure,
        ),
        (
            "structured_output: no decision produced for ['x']",
            FailureAttributionKind.structured_output_failure,
        ),
        ("defect_recall 0.500 < required 1.000", FailureAttributionKind.model_reasoning_failure),
        (
            "decision_accuracy 0.333 < required 0.800",
            FailureAttributionKind.model_reasoning_failure,
        ),
        ("gap[0]: gap_type 'x' not in allowed", FailureAttributionKind.model_reasoning_failure),
        (
            "false exclusion: expected include, produced exclude",
            FailureAttributionKind.model_reasoning_failure,
        ),
        ("something unrecognized", FailureAttributionKind.model_reasoning_failure),
    ],
)
def test_attribute_failure_text(text: str, expected: FailureAttributionKind) -> None:
    assert attribute_failure_text(text) == expected


def test_attribute_failures_treats_star_as_a_benchmark_level_defect() -> None:
    """M79: "*" must exclude every case in a defective benchmark.

    The calibration audit records unknown-benchmark defects as case_id="*",
    and confirmed_defect_map is matched by exact case id, so "*" previously
    matched nothing and the defect excluded nothing -- every failure stayed
    counted against the model.
    """
    included, excluded = attribute_failures(
        ["evidence[0]: unsupported reference 'x'", "decision_accuracy 0.5 < 0.8"],
        defect_case_ids={"*"},
        case_id="lq-evidence-extraction",
    )
    assert included == {}
    assert excluded[FailureAttributionKind.benchmark_reference_defect.value] == 2


def test_attribute_failures_star_does_not_leak_across_benchmarks() -> None:
    """Callers filter defect ids by benchmark, so "*" only binds within its own."""
    included, excluded = attribute_failures(
        ["evidence[0]: unsupported reference 'x'"],
        defect_case_ids=set(),
        case_id="lq-evidence-extraction",
    )
    assert excluded == {}
    assert included

def test_attribute_failures_excludes_confirmed_defect_cases() -> None:
    included, excluded = attribute_failures(
        ["evidence[0]: unsupported reference 'x'", "decision_accuracy 0.5 < 0.8"],
        defect_case_ids={"lq-evidence-extraction"},
        case_id="lq-evidence-extraction",
    )
    assert included == {}
    assert excluded[FailureAttributionKind.benchmark_reference_defect.value] == 2

    included, excluded = attribute_failures(
        ["evidence[0]: unsupported reference 'x'"],
        defect_case_ids=set(),
        case_id="lq-evidence-extraction",
    )
    assert included[FailureAttributionKind.grounding_failure.value] == 1
    assert excluded == {}


# ---------------------------------------------------------------------------
# stability
# ---------------------------------------------------------------------------


def test_stability_stable_borderline_unstable() -> None:
    criteria = _criteria()
    assert stability_status(_result("s", det=0.92, worst=0.92), criteria) == "stable"
    # borderline: worst within margin of the threshold
    assert stability_status(_result("b", det=0.86, worst=0.86), criteria) == "borderline"
    # borderline: qualified but variance above the cap
    assert (
        stability_status(_result("bv", det=0.92, worst=0.9, variance=0.03), criteria)
        == "borderline"
    )
    # unstable: a repetition below the threshold (worst below)
    assert stability_status(_result("u", det=0.9, worst=0.8), criteria) == "unstable"
    # unstable: critical grounding failures
    assert stability_status(_result("ug", det=0.9, worst=0.9, grounding=1), criteria) == "unstable"
    # unstable: provider error above cap
    assert (
        stability_status(_result("up", det=0.9, worst=0.9, provider_error=0.5), criteria)
        == "unstable"
    )


# ---------------------------------------------------------------------------
# defect exclusion (benchmark/evaluator defects never counted against the model)
# ---------------------------------------------------------------------------


def test_benchmark_defect_excluded_from_qualification() -> None:
    result = _result(
        "m-a",
        det=0.9,
        grounding=1,
        excluded_attribution={FailureAttributionKind.benchmark_reference_defect.value: 1},
        attribution={FailureAttributionKind.benchmark_reference_defect.value: 1},
    )
    qualified, _kinds, reasons = qualify_candidate(result, _criteria())
    assert qualified is True
    assert reasons == []


def test_evaluator_defect_excluded_from_qualification() -> None:
    result = _result(
        "m-a",
        det=0.9,
        grounding=2,
        excluded_attribution={FailureAttributionKind.evaluator_defect.value: 2},
    )
    qualified, _kinds, _reasons = qualify_candidate(result, _criteria())
    assert qualified is True


def test_partial_exclusion_still_fails_on_genuine_grounding() -> None:
    result = _result(
        "m-a",
        det=0.9,
        grounding=3,
        excluded_attribution={FailureAttributionKind.benchmark_reference_defect.value: 1},
    )
    qualified, _kinds, reasons = qualify_candidate(result, _criteria())
    assert qualified is False
    assert any("critical grounding" in r for r in reasons)


def test_threshold_never_loosened() -> None:
    result = _result("m-border", det=0.849)
    qualified, _kinds, reasons = qualify_candidate(result, _criteria())
    assert qualified is False
    assert any("deterministic_pass_rate" in r for r in reasons)


# ---------------------------------------------------------------------------
# qualification matrix
# ---------------------------------------------------------------------------


def test_matrix_unstable_candidate_not_eligible() -> None:
    criteria = _criteria()
    summary, candidates = summarize_role_live(
        {"m-a": _result("m-a", det=0.9, worst=0.9, variance=0.01)},
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        repetitions=3,
        criteria=criteria,
    )
    assert summary.status == "qualified_without_fallback"
    assert summary.primary == "m-a"
    matrix = build_qualification_matrix(
        candidates,
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        repetitions=3,
        criteria=criteria,
    )
    row = next(r for r in matrix.rows if r.candidate == "m-a")
    assert row.stability == "stable"
    assert row.primary_eligible is True
    assert row.fallback_eligible is False

    unstable = _result("m-u", det=0.9, worst=0.7, variance=0.02)
    _sum, cands = summarize_role_live(
        {"m-u": unstable},
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        repetitions=3,
        criteria=criteria,
    )
    matrix = build_qualification_matrix(
        cands,
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        repetitions=3,
        criteria=criteria,
    )
    row = next(r for r in matrix.rows if r.candidate == "m-u")
    # qualified by the existing mean criteria, but unstable -> not eligible
    assert row.qualified is True
    assert row.stability == "unstable"
    assert row.primary_eligible is False
    assert row.fallback_eligible is False


def test_matrix_primary_and_fallback_eligibility() -> None:
    criteria = _criteria()
    _summary, candidates = summarize_role_live(
        {
            "m-a": _result("m-a", det=0.92, worst=0.92),
            "m-b": _result("m-b", det=0.9, worst=0.9),
        },
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        repetitions=3,
        criteria=criteria,
    )
    matrix = build_qualification_matrix(
        candidates,
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        repetitions=3,
        criteria=criteria,
    )
    assert matrix.status == "qualified"
    assert matrix.primary == "m-a"
    assert matrix.fallback == "m-b"
    by_candidate = {r.candidate: r for r in matrix.rows}
    assert by_candidate["m-a"].primary_eligible is True
    assert by_candidate["m-a"].fallback_eligible is False
    assert by_candidate["m-b"].primary_eligible is True
    assert by_candidate["m-b"].fallback_eligible is True


def test_matrix_unqualified_never_ranked_above_qualified() -> None:
    criteria = _criteria()
    _summary, candidates = summarize_role_live(
        {
            "m-good": _result("m-good", det=0.9, worst=0.9, cost=0.05),
            "m-cheap-bad": _result("m-cheap-bad", det=0.6, worst=0.6, cost=0.0001),
        },
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        repetitions=3,
        criteria=criteria,
    )
    matrix = build_qualification_matrix(
        candidates,
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        repetitions=3,
        criteria=criteria,
    )
    assert matrix.primary == "m-good"
    row = next(r for r in matrix.rows if r.candidate == "m-cheap-bad")
    assert row.qualified is False
    assert row.primary_eligible is False
    # raw dimensions preserved
    assert row.estimated_cost == 0.0001


# ---------------------------------------------------------------------------
# calibration audit
# ---------------------------------------------------------------------------


def test_calibration_audit_all_benchmarks_pass() -> None:
    for audit in audit_all_live_quality_benchmarks():
        assert audit.verdict == "ok", audit.benchmark_id
        assert all(c.passed for c in audit.checks), audit.benchmark_id
        assert audit.confirmed_defects == [], audit.benchmark_id


def test_calibration_audit_unknown_benchmark_reports_defect() -> None:
    audit = audit_live_quality_benchmark("does-not-exist")
    assert audit.verdict == "repair_needed"
    assert len(audit.confirmed_defects) == 1
    assert (
        audit.confirmed_defects[0].kind == FailureAttributionKind.benchmark_reference_defect.value
    )


# ---------------------------------------------------------------------------
# live-quality service attribution + task-performance aggregation
# ---------------------------------------------------------------------------


def _service() -> object:
    from research_harness.plugins.research.evaluation_live_quality.plugin import LiveQualityService

    return LiveQualityService(
        artifact_store=None, harness=None, role_router=None, service_lookup=None
    )


def test_service_build_task_performance() -> None:
    svc = _service()
    performance = svc._build_task_performance(
        {
            "lq-evidence-extraction": [1.0, 0.0, 1.0],
            "lq-literature-synthesis": [1.0, 1.0, 1.0],
        },
        {
            "lq-evidence-extraction": "evidence extraction",
            "lq-literature-synthesis": "literature synthesis",
        },
        {"lq-evidence-extraction": 1},
        {
            "lq-evidence-extraction": [
                "evidence[0]: unsupported reference 'x' (not produced by this run)"
            ]
        },
    )
    by_task = {tp.task_id: tp for tp in performance}
    ev = by_task["lq-evidence-extraction"]
    assert ev.repetitions == 3
    assert ev.pass_rate_mean == pytest.approx(2 / 3)
    assert ev.pass_rate_worst == 0.0
    assert ev.critical_grounding_failures == 1
    assert ev.failure_attribution[FailureAttributionKind.grounding_failure.value] == 1
    syn = by_task["lq-literature-synthesis"]
    assert syn.pass_rate_mean == 1.0
    assert syn.failure_attribution == {}


def test_service_apply_attribution_merges_call_failures() -> None:
    from research_harness.research.schemas.live_quality import LiveQualityModelResult

    svc = _service()
    result = LiveQualityModelResult(
        candidate_id="m-a",
        model={},
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        repetitions=3,
        failure_counts={
            "timeout": 1,
            "structured_output_failure": 2,
            "provider_error": 1,
            "rate_limit": 1,
        },
    )
    svc._apply_attribution(
        result,
        {
            "lq-evidence-extraction": [
                "evidence[0]: unsupported reference 'x' (not produced by this run)"
            ]
        },
        "live-quality-reasoning-v1",
    )
    assert result.failure_attribution[FailureAttributionKind.timeout.value] == 1
    assert result.failure_attribution[FailureAttributionKind.rate_limit.value] == 1
    assert result.failure_attribution[FailureAttributionKind.provider_error.value] == 1
    assert result.failure_attribution[FailureAttributionKind.structured_output_failure.value] == 2
    assert result.failure_attribution[FailureAttributionKind.grounding_failure.value] == 1
    assert result.excluded_failure_attribution == {}
