"""Integration test: the coverage matrix must describe what benchmarks measure.

`COVERAGE_MATRIX` is the operator-facing statement of what each benchmark
measures. Nothing checked it against reality, so rows drifted: `novelty-threat-v1`
declared metric ids its evaluator never emitted, `live-quality-evaluator-sanity-v1`
listed free-text sentences, and five more rows named metrics that do not exist.
A row can look precise while describing nothing.

This runs every benchmark that can run offline and asserts each declared metric
id is actually emitted. It is an integration test because running all 31
benchmarks is too slow for the unit suite (~25s here).
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

from research_harness.research.benchmarks import BUILTIN_BENCHMARKS
from research_harness.research.evaluation_coverage import COVERAGE_MATRIX
from research_harness.research.schemas.evaluation import EvaluationReport

# Benchmarks whose cases only produce meaningful results against a real
# provider; offline they error before emitting any metric, so a declared-vs-
# emitted comparison would be meaningless rather than passing.
LIVE_ONLY = {
    "live-quality-reasoning-v1",
    "live-quality-critic-v1",
    "live-quality-fast-v1",
}

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_tournament_helpers():
    """Reuse the fake-provider harness from the model-tournament tests."""
    spec = importlib.util.spec_from_file_location(
        "_t7b_helpers", _ROOT / "tests" / "integration" / "test_phase7b_model_tournament.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _emitted_metric_ids(runtime, report_id: str) -> set[str]:
    store = runtime.services.require("artifact_store.default")
    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    return {m.metric_id for m in report.metrics}


@pytest.mark.asyncio
async def test_coverage_matrix_metrics_are_actually_emitted(tmp_path: pathlib.Path):
    helpers = _load_tournament_helpers()
    # The shared evaluator list omits two evaluators; without them those
    # benchmarks fail to start rather than being measured.
    helpers.ALL_EVALUATORS = list(helpers.ALL_EVALUATORS) + [
        "evaluator.task_aware_routing",
        "evaluator.evaluator_sanity",
    ]

    failures: list[str] = []
    checked = 0
    for row in COVERAGE_MATRIX:
        if row.benchmark in LIVE_ONLY:
            continue
        bench_dir = tmp_path / row.benchmark
        bench_dir.mkdir(parents=True, exist_ok=True)
        runtime, _provider = helpers._build_runtime(
            bench_dir, {}, helpers._fixtures_for([row.benchmark])
        )
        async with runtime:
            harness = runtime.services.require("evaluation_harness.default")
            await harness.register_benchmark(BUILTIN_BENCHMARKS[row.benchmark])
            _run_id, report_id = await harness.run_benchmark(row.benchmark)
            emitted = await _emitted_metric_ids(runtime, report_id)

        missing = sorted(set(row.metrics) - emitted)
        checked += 1
        if missing:
            failures.append(
                f"{row.benchmark} ({row.evaluator}) declares metrics that are never "
                f"emitted: {missing}"
            )

    assert checked >= 25, f"only {checked} benchmarks were exercised"
    assert not failures, "coverage matrix rows do not match evaluator output:\n" + "\n".join(
        f"  - {f}" for f in failures
    )


@pytest.mark.asyncio
async def test_live_only_rows_are_explicitly_excluded():
    """The skip list must stay small and must name real benchmarks.

    If a benchmark stops needing a live provider it should move out of this
    list and back under the check above.
    """
    known = set(BUILTIN_BENCHMARKS)
    assert known >= LIVE_ONLY, LIVE_ONLY - known
    assert len(LIVE_ONLY) == 3, LIVE_ONLY
    assert all(
        "live-quality-" in b for b in LIVE_ONLY
    ), "only live-quality benchmarks are expected to need a real provider"
