"""Unit tests for tournament accounting (Phase 7B): latency percentiles,
token/cost aggregation, structured-output success, model-error/retry rates,
and never-invented cost semantics."""

from __future__ import annotations

import pytest

from research_harness.research.schemas.tournament import (
    BenchmarkRunRef,
    ModelCallRecord,
    TournamentFailureKind,
    TournamentModelConfig,
)
from research_harness.research.tournament.accounting import (
    aggregate_calls,
    aggregate_run_results,
    call_cost,
    percentile,
)


def _call(**kw):
    defaults = {
        "role": "reasoning",
        "model": "m",
        "requested_model": "m",
        "status": "success",
        "latency_ms": 50.0,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": None,
    }
    defaults.update(kw)
    return ModelCallRecord(**defaults)


def test_percentile():
    assert percentile([], 0.5) is None
    assert percentile([10.0], 0.5) == 10.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.95) == pytest.approx(3.85)


def test_aggregate_calls_tokens_and_latency():
    calls = [
        _call(latency_ms=40.0, prompt_tokens=100, completion_tokens=50),
        _call(latency_ms=60.0, prompt_tokens=200, completion_tokens=100),
        _call(latency_ms=80.0, prompt_tokens=300, completion_tokens=150),
    ]
    out = aggregate_calls(calls)
    assert out["input_tokens"] == 600
    assert out["output_tokens"] == 300
    assert out["total_tokens"] == 900
    assert out["latency_ms_mean"] == 60.0
    assert out["latency_ms_p50"] == 60.0
    assert out["latency_ms_p95"] == pytest.approx(78.0)
    assert out["model_error_rate"] == 0.0
    assert out["retry_rate"] == 0.0


def test_missing_cost_stays_null():
    calls = [_call(provider_cost=None, calculated_cost=None)]
    out = aggregate_calls(calls)
    assert out["estimated_cost"] is None


def test_cost_from_provider_wins_over_pricing():
    class Usage:
        cost = 0.5

    cost, source = call_cost(Usage(), None)
    assert cost == 0.5
    assert source == "provider"


def test_cost_calculated_from_pricing_when_provider_missing():
    class Usage:
        prompt_tokens = 1000
        completion_tokens = 500
        cost = None

    from research_harness.research.schemas.tournament import TournamentPricing

    pricing = TournamentPricing(
        source="catalog", version="1", input_per_million=1.0, output_per_million=2.0
    )
    cost, source = call_cost(Usage(), pricing)
    assert cost == (1000 + 1000) / 1_000_000
    assert source == "pricing"


def test_cost_unknown_without_pricing():
    class Usage:
        prompt_tokens = 1000
        completion_tokens = 500
        cost = None

    cost, source = call_cost(Usage(), None)
    assert cost is None
    assert source is None


def test_aggregate_calls_model_errors_and_structured_failures():
    calls = [
        _call(status="error", failure=TournamentFailureKind.timeout),
        _call(status="error", failure=TournamentFailureKind.rate_limit),
        _call(
            status="structured_output_failure",
            failure=TournamentFailureKind.structured_output_failure,
            structured=True,
        ),
        _call(status="success", structured=True),
        _call(status="success", structured=True),
    ]
    out = aggregate_calls(calls)
    assert out["model_error_rate"] == 2 / 5
    assert out["structured_output_success_rate"] == 2 / 3
    assert out["failure_counts"] == {
        "timeout": 1,
        "rate_limit": 1,
        "structured_output_failure": 1,
    }


def test_aggregate_calls_retry_rate():
    calls = [_call(retries=2), _call(retries=0), _call(retries=1)]
    out = aggregate_calls(calls)
    assert out["retry_rate"] == 3 / 3


def test_aggregate_run_results_pass_rates():
    refs = [
        BenchmarkRunRef(
            benchmark_id="b",
            benchmark_version=1,
            repetition=1,
            run_id="r1",
            report_id="p1",
            report_status="passed",
            cases_total=10,
            cases_passed=9,
            cases_failed=1,
            cases_error=0,
            latency_ms=100,
        ),
        BenchmarkRunRef(
            benchmark_id="b",
            benchmark_version=1,
            repetition=2,
            run_id="r2",
            report_id="p2",
            report_status="failed",
            cases_total=10,
            cases_passed=5,
            cases_failed=4,
            cases_error=1,
            latency_ms=120,
        ),
    ]
    out = aggregate_run_results(refs, {"estimated_cost": 0.1})
    assert out["case_pass_rate"] == 14 / 20
    assert out["deterministic_pass_rate"] == 14 / (14 + 5)
    assert out["benchmark_pass_rate"] == 0.5
    assert out["cost_per_successful_benchmark"] == 0.1 / 1
    assert out["cost_per_successful_case"] == 0.1 / 14


def test_case_error_rate_survives_a_perfect_deterministic_pass_rate():
    """H2: 1 passed / 0 failed / 99 errored must not look like a perfect model.

    deterministic_pass_rate deliberately measures quality among *completed*
    cases, so it stays 1.0 here. case_error_rate is the separate signal that
    makes the model unqualified; before this it was computed by the caller but
    never exposed, so errored cases had no effect on any gate.
    """
    refs = [
        BenchmarkRunRef(
            benchmark_id="b",
            benchmark_version=1,
            repetition=1,
            run_id="r1",
            report_id="p1",
            report_status="failed",
            cases_total=100,
            cases_passed=1,
            cases_failed=0,
            cases_error=99,
        ),
    ]
    out = aggregate_run_results(refs, {})
    assert out["deterministic_pass_rate"] == 1.0
    assert out["case_error_rate"] == 0.99
    assert out["case_pass_rate"] == 0.01


def test_failed_repetitions_count_as_attempts():
    """H4: a crashed repetition must not vanish from the denominator.

    Two of three attempts crashed. Scoring only the survivor would report a
    benchmark_pass_rate of 1.0 for a candidate that completed one run in three.
    """
    refs = [
        BenchmarkRunRef(
            benchmark_id="b",
            benchmark_version=1,
            repetition=1,
            run_id="r1",
            report_id="p1",
            report_status="passed",
            cases_total=10,
            cases_passed=9,
            cases_failed=1,
            cases_error=0,
        ),
    ]
    out = aggregate_run_results(refs, {}, failed_repetitions=2)
    assert out["benchmark_pass_rate"] == 1 / 3
    assert out["repetition_failure_rate"] == 2 / 3
    # The surviving run's case rates are unaffected.
    assert out["deterministic_pass_rate"] == 0.9


def test_effective_pricing_plan_wins():
    from research_harness.research.tournament.accounting import effective_pricing

    candidate = TournamentModelConfig(
        candidate_id="c",
        requested_model="m",
        pricing={
            "source": "plan",
            "input_per_million": 5.0,
            "output_per_million": 6.0,
        },
    )
    pricing = effective_pricing(candidate, None)
    assert pricing is not None
    assert pricing.input_per_million == 5.0


def test_effective_pricing_none_without_rates():
    from research_harness.research.tournament.accounting import effective_pricing

    candidate = TournamentModelConfig(candidate_id="c", requested_model="m")
    assert effective_pricing(candidate, None) is None


def test_resolved_model_is_deterministic_on_ties():
    """Ties must not be broken by string hash order.

    ``max(set(...), key=list.count)`` depends on PYTHONHASHSEED, so the same
    calls could resolve to a different model between runs, which breaks the
    reproducibility the tournament's plan_hash is meant to provide.
    """
    calls = [
        _call(model="model-B"),
        _call(model="model-A"),
        _call(model="model-B"),
        _call(model="model-A"),
        _call(model="model-B"),
        _call(model="model-A"),
    ]
    # Equal counts -> choose the lexicographically smallest, not a hash-order
    # artefact. Repeat to make accidental stability obvious if it regresses.
    for _ in range(5):
        assert aggregate_calls(calls)["resolved_model"] == "model-A"


def test_resolved_model_prefers_the_most_frequent():
    calls = [_call(model="model-A"), _call(model="model-A"), _call(model="model-B")]
    assert aggregate_calls(calls)["resolved_model"] == "model-A"
