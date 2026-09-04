"""Phase 6H unit tests — evaluation coverage matrix and readiness rules."""

from __future__ import annotations

import re

from research_harness.research.benchmarks import BUILTIN_BENCHMARKS
from research_harness.research.evaluation_coverage import (
    COVERAGE_MATRIX,
    benchmark_coverage_ok,
    rows_for_benchmark,
    uncovered_capabilities,
)
from research_harness.research.evaluation_readiness import (
    _known_by_design_failures,
    readiness_report,
)


def test_coverage_matrix_covers_every_builtin_benchmark():
    assert benchmark_coverage_ok()
    covered = {row.benchmark for row in COVERAGE_MATRIX}
    assert covered == set(BUILTIN_BENCHMARKS)


def test_coverage_matrix_fields_present():
    for row in COVERAGE_MATRIX:
        assert row.capability
        assert row.phase
        assert row.benchmark
        assert row.evaluator
        assert row.metrics
        assert row.gating in ("deterministic", "advisory")
        assert row.gaps


def test_coverage_matrix_gating_is_deterministic():
    for row in COVERAGE_MATRIX:
        assert row.gating == "deterministic", row.capability


def test_every_row_has_an_evaluator_metric_link():
    for row in COVERAGE_MATRIX:
        assert row.evaluator.startswith("evaluator.")


def test_uncovered_capabilities_are_explicit():
    gaps = uncovered_capabilities()
    assert gaps
    for name, note in gaps:
        assert name and note


def test_every_deterministic_benchmark_has_a_coverage_row():
    for bid in BUILTIN_BENCHMARKS:
        assert rows_for_benchmark(bid), bid


def test_readiness_verdict_is_deterministic():
    result = readiness_report()
    assert result.verdict in ("ready", "ready_with_gaps", "not_ready")
    assert result.narrative
    criteria = result.criteria
    assert criteria["benchmark_count"] == len(BUILTIN_BENCHMARKS)
    assert criteria["e2e_benchmark_present"] is True
    assert criteria["benchmarks_without_deterministic_gating"] == []
    assert criteria["evaluators_unresolved"] == []
    assert criteria["missing_coverage_rows"] == []


def test_readiness_inventories():
    result = readiness_report()
    criteria = result.criteria
    assert criteria["benchmark_inventory"]
    assert criteria["evaluator_inventory"]
    assert criteria["coverage_matrix_rows"] == len(COVERAGE_MATRIX)
    assert "evaluator.llm_judge" in criteria["evaluator_inventory"]


def test_readiness_by_design_failures_documented():
    by_design = _known_by_design_failures()
    assert len(by_design) == 4
    assert any("comparative-statics-v1" in c for c in by_design)
    assert any("results-assembly-v1" in c for c in by_design)
    assert any("analytical-model-specification-v1" in c for c in by_design)


def test_readiness_verdict_not_ready_when_e2e_missing():
    import research_harness.research.benchmarks as benchmarks

    original = benchmarks.BUILTIN_BENCHMARKS
    try:
        benchmarks.BUILTIN_BENCHMARKS = {
            k: v for k, v in original.items() if k != "research-pipeline-e2e-v1"
        }
        result = readiness_report()
        assert result.verdict == "not_ready"
    finally:
        benchmarks.BUILTIN_BENCHMARKS = original


# Metric ids are snake_case; "@" is allowed because the retrieval evaluator
# emits rank-cutoff metrics such as f1@10 and precision@5. Spaces, capitals
# and punctuation beyond "@" are still rejected, which is what catches
# free-text descriptions masquerading as metric ids.
_METRIC_ID = re.compile(r"[a-z0-9][a-z0-9_@]*")


def test_coverage_matrix_metrics_are_metric_ids():
    """Every declared metric must be an id the harness can aggregate.

    Rows have drifted into free-text descriptions: the sanity-audit row listed
    sentences ("provider errors never counted as successes") until round 5.
    No aggregation can ever match a sentence, so the row advertises coverage
    that is not measurable -- the same class of staleness as the
    novelty-threat-v1 row, which declared metric ids its evaluator never
    emitted.
    """
    for row in COVERAGE_MATRIX:
        for metric in row.metrics:
            assert _METRIC_ID.fullmatch(metric), f"{row.benchmark}: {metric!r} is not a metric id"


def test_coverage_matrix_metrics_are_unique_per_row():
    for row in COVERAGE_MATRIX:
        assert len(set(row.metrics)) == len(row.metrics), (
            f"{row.benchmark}: duplicate metric ids in {row.metrics}"
        )
