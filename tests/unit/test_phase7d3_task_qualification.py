"""Phase 7D.3 unit tests — task-specific qualification, evidence diagnostics,
task matrix, capability profiles.

Task qualification reuses the role thresholds exactly (never relaxed); a model
may be qualified_for_task without being qualified for the whole role.
"""

from __future__ import annotations

import pytest

from research_harness.research.routing.qualification import (
    aggregate_task_performance,
    build_model_capability_profiles,
    build_task_matrix,
    evidence_extraction_diagnostics,
    qualify_task,
    task_candidate_result,
    task_rank_key,
)
from research_harness.research.routing.readiness import criteria_for_role
from research_harness.research.schemas.live_quality import (
    LiveQualityModelResult,
    LiveQualityTaskPerformance,
    QualificationCriteria,
)


def _perf(
    task_id: str,
    *,
    det: float = 0.9,
    worst: float | None = None,
    variance: float = 0.0,
    reps: int = 3,
    structured: float = 0.9,
    provider: float = 0.0,
    grounding: int = 0,
) -> LiveQualityTaskPerformance:
    rates = [det] * reps
    return LiveQualityTaskPerformance(
        task_id=task_id,
        task_name=task_id,
        repetitions=reps,
        pass_rate_mean=det,
        pass_rate_worst=worst if worst is not None else det,
        pass_rate_variance=variance,
        pass_rates=rates,
        structured_output_success_rate=structured,
        provider_error_frequency=provider,
        critical_grounding_failures=grounding,
    )


def _result(
    candidate_id: str,
    *,
    role: str = "reasoning",
    tasks: dict[str, LiveQualityTaskPerformance],
    latency: float | None = None,
    cost: float | None = None,
    tokens: int | None = None,
    age_seconds: float | None = None,
) -> LiveQualityModelResult:
    from datetime import UTC, datetime, timedelta
    from statistics import fmean

    means = [t.pass_rate_mean for t in tasks.values() if t.pass_rate_mean is not None]
    worsts = [t.pass_rate_worst for t in tasks.values() if t.pass_rate_worst is not None]
    return LiveQualityModelResult(
        candidate_id=candidate_id,
        model={"candidate_id": candidate_id, "requested_model": f"m-{candidate_id}"},
        resolved_model=f"m-{candidate_id}",
        role=role,
        benchmark_id="live-quality-reasoning-v1",
        repetitions=max((t.repetitions for t in tasks.values()), default=3),
        task_performance=list(tasks.values()),
        deterministic_pass_rate_mean=fmean(means) if means else None,
        deterministic_pass_rate_worst=min(worsts) if worsts else None,
        structured_output_success_rate=0.9,
        provider_error_frequency=0.0,
        critical_grounding_failures=sum(t.critical_grounding_failures for t in tasks.values()),
        latency_ms_p50_mean=latency,
        estimated_cost=cost,
        total_tokens=tokens,
        evidence_timestamp=(
            datetime.now(UTC) - timedelta(seconds=age_seconds) if age_seconds else datetime.now(UTC)
        ),
        task_results=[
            {
                "repetition": 1,
                "run_id": "r",
                "report_id": "p",
                "report_status": "passed",
                "cases_total": 1,
                "cases_passed": 1,
                "cases_failed": 0,
                "cases_error": 0,
                "task_pass_rate": 1.0,
                "task_completed": True,
            }
        ],
    )


def _criteria(role: str = "reasoning") -> QualificationCriteria:
    return criteria_for_role(role)


def _reasoning_tasks(
    overrides: dict[str, LiveQualityTaskPerformance] | None = None,
) -> dict[str, LiveQualityTaskPerformance]:
    tasks = {
        "lq-evidence-extraction": _perf("lq-evidence-extraction", det=0.5),
        "lq-literature-synthesis": _perf("lq-literature-synthesis", det=0.9),
        "lq-gap-analysis": _perf("lq-gap-analysis", det=0.5),
        "lq-mechanism-development": _perf("lq-mechanism-development", det=0.5),
        "lq-model-specification": _perf("lq-model-specification", det=0.5),
        "lq-proposition-generation": _perf("lq-proposition-generation", det=0.5),
    }
    tasks.update(overrides or {})
    return tasks


# ---------------------------------------------------------------------------
# task qualification
# ---------------------------------------------------------------------------


def test_task_qualified_but_role_not() -> None:
    result = _result("m-a", tasks=_reasoning_tasks())
    criteria = _criteria()
    ok, reasons = qualify_task(result, "synthesis", criteria)
    assert ok is True
    assert reasons == []
    ok, _ = qualify_task(result, "evidence_extraction", criteria)
    assert ok is False


def test_task_qualification_uses_same_thresholds() -> None:
    # borderline below role threshold never qualifies at task level either
    result = _result(
        "m-a",
        tasks=_reasoning_tasks(
            {"lq-literature-synthesis": _perf("lq-literature-synthesis", det=0.849, reps=5)}
        ),
    )
    ok, reasons = qualify_task(result, "synthesis", _criteria())
    assert ok is False
    assert any("deterministic_pass_rate" in r for r in reasons)


def test_task_critical_grounding_blocks() -> None:
    result = _result(
        "m-a",
        tasks=_reasoning_tasks(
            {"lq-literature-synthesis": _perf("lq-literature-synthesis", det=0.9, grounding=1)}
        ),
    )
    ok, reasons = qualify_task(result, "synthesis", _criteria())
    assert ok is False
    assert any("grounding" in r for r in reasons)


def test_task_provider_error_blocks() -> None:
    result = _result(
        "m-a",
        tasks=_reasoning_tasks(
            {"lq-literature-synthesis": _perf("lq-literature-synthesis", det=0.9, provider=0.5)}
        ),
    )
    ok, reasons = qualify_task(result, "synthesis", _criteria())
    assert ok is False
    assert any("provider_error" in r for r in reasons)


def test_task_insufficient_repetitions() -> None:
    result = _result(
        "m-a",
        tasks=_reasoning_tasks(
            {"lq-literature-synthesis": _perf("lq-literature-synthesis", det=0.9, reps=1)}
        ),
    )
    ok, reasons = qualify_task(result, "synthesis", _criteria())
    assert ok is False
    assert any("repetitions" in r for r in reasons)


def test_task_stale_evidence() -> None:
    result = _result(
        "m-a",
        age_seconds=5000,
        tasks=_reasoning_tasks(
            {"lq-literature-synthesis": _perf("lq-literature-synthesis", det=0.9)}
        ),
    )
    criteria = _criteria().model_copy(update={"leaderboard_max_age_seconds": 100})
    ok, reasons = qualify_task(result, "synthesis", criteria)
    assert ok is False
    assert any("stale" in r for r in reasons)


def test_aggregate_task_performance_groups_fast_cases() -> None:
    result = _result(
        "m-a",
        role="fast",
        tasks={
            "lq-fast-screening-clear": _perf("lq-fast-screening-clear", det=1.0),
            "lq-fast-screening-uncertain": _perf("lq-fast-screening-uncertain", det=0.6),
        },
    )
    tp = aggregate_task_performance(result, "screening")
    assert tp is not None
    assert tp.repetitions == 3
    assert tp.pass_rate_mean == pytest.approx(0.8)
    assert tp.pass_rate_worst == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# evidence-extraction diagnostics
# ---------------------------------------------------------------------------


def test_evidence_extraction_diagnostics_classifier() -> None:
    diag = evidence_extraction_diagnostics(
        [
            "evidence[0]: unsupported reference 'x' in source_artifact_id",
            "evidence[0]: locator page 9 outside document pages 1..2",
            "evidence[0]: empty statement (required field)",
            "evidence_extraction: no evidence_item produced",
        ]
    )
    assert diag["hallucinated_evidence_ids"] == 1
    assert diag["wrong_page_locators"] == 1
    assert diag["malformed_structured_output"] == 1
    assert diag["missing_required_evidence"] == 1
    assert diag["unsupported_claims"] == 0
    assert diag["invalid_categories"] == 0


def test_evidence_extraction_diagnostics_structured() -> None:
    diag = evidence_extraction_diagnostics(
        [],
        produced_evidence=[
            {
                "statement": "Sellers increasingly use algorithmic pricing demand data",
                "category": "result",
            },
            {
                "statement": "quantum teleportation governs cookie baking",
                "category": "not_a_category",
            },
        ],
        source_text="Sellers increasingly use algorithmic pricing. Demand data shapes prices.",
    )
    # first statement's core terms appear in the source -> supported claim
    # second statement's terms are absent -> unsupported claim
    assert diag["unsupported_claims"] == 1
    assert diag["invalid_categories"] == 1


# ---------------------------------------------------------------------------
# task matrix + ranking + capability profiles
# ---------------------------------------------------------------------------


def test_task_matrix_ranked_only_qualified() -> None:
    m_a = _result(
        "m-a",
        tasks=_reasoning_tasks(),
        latency=100.0,
        cost=0.05,
        tokens=1000,
    )
    m_b = _result(
        "m-b",
        tasks=_reasoning_tasks(
            {"lq-literature-synthesis": _perf("lq-literature-synthesis", det=0.5)}
        ),
        latency=5.0,
        cost=0.0001,
    )
    matrix, rows = build_task_matrix(
        {"m-a": m_a, "m-b": m_b},
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        repetitions=3,
    )
    assert matrix.qualified_models_by_task["synthesis"] == ["m-a"]
    assert matrix.ranked_models_by_task["synthesis"] == ["m-a"]
    assert matrix.role_qualified_models == []
    assert matrix.qualified_tasks_by_model["m-a"] == ["synthesis"]
    assert matrix.qualified_tasks_by_model["m-b"] == []


def test_task_matrix_deterministic_tiebreak() -> None:
    m_alpha = _result("m-alpha", tasks=_reasoning_tasks())
    m_beta = _result("m-beta", tasks=_reasoning_tasks())
    matrix, _rows = build_task_matrix(
        {"m-alpha": m_alpha, "m-beta": m_beta},
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        repetitions=3,
    )
    assert matrix.ranked_models_by_task["synthesis"] == ["m-alpha", "m-beta"]


def test_task_rank_key_orders_by_correctness_then_latency() -> None:
    high = task_candidate_result(
        _result("m-good", tasks=_reasoning_tasks(), latency=200.0, cost=0.1),
        "synthesis",
        _criteria(),
    )
    fast = task_candidate_result(
        _result("m-fast", tasks=_reasoning_tasks(), latency=10.0, cost=0.05),
        "synthesis",
        _criteria(),
    )
    assert high is not None and fast is not None
    ranked = sorted([high, fast], key=task_rank_key)
    assert [r.candidate_id for r in ranked] == ["m-fast", "m-good"]


def test_task_matrix_critic_fallback() -> None:
    critic_tasks = {
        "lq-mechanism-critique": _perf("lq-mechanism-critique", det=0.9),
        "lq-model-critique": _perf("lq-model-critique", det=0.9),
        "lq-proposition-critique": _perf("lq-proposition-critique", det=0.9),
        "lq-results-critique": _perf("lq-results-critique", det=0.9),
        "lq-manuscript-critique": _perf("lq-manuscript-critique", det=0.9),
    }
    matrix, _rows = build_task_matrix(
        {
            "m-a": _result("m-a", role="critic", tasks=critic_tasks),
            "m-b": _result("m-b", role="critic", tasks=critic_tasks),
        },
        role="critic",
        benchmark_id="live-quality-critic-v1",
        repetitions=3,
    )
    assert sorted(matrix.role_qualified_models) == ["m-a", "m-b"]
    assert sorted(matrix.qualified_models_by_task["mechanism_critique"]) == ["m-a", "m-b"]


def test_capability_profiles() -> None:
    m_a = _result("m-a", tasks=_reasoning_tasks(), latency=100.0, cost=0.05, tokens=1000)
    matrix, _rows = build_task_matrix(
        {"m-a": m_a},
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        repetitions=3,
    )
    profiles = build_model_capability_profiles(
        matrix,
        stability_by_model={"m-a": "stable"},
        latency_by_model={"m-a": 100.0},
        cost_by_model={"m-a": 0.05},
        tokens_by_model={"m-a": 1000},
    )
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile.model == "m-a"
    assert profile.task_qualifications["synthesis"] == "qualified_for_task"
    assert profile.task_qualifications["evidence_extraction"] == "not_qualified_for_task"
    assert profile.role_qualified is False
    assert profile.stability == "stable"
    assert profile.latency_ms_p50 == 100.0
    assert profile.estimated_cost == 0.05
    assert profile.total_tokens == 1000
