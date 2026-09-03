"""Unit tests for the evaluator-sanity evaluator (Phase 7D.3B).

The sanity evaluator runs a target live-quality evaluator against a fixture
and checks that it reaches the expected verdict and populates the expected
task diagnostics. It is deterministic, so the harness aggregates its metric
contributions -- it must therefore emit them.
"""

from __future__ import annotations

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_sanity.plugin import EvaluatorSanityEvaluator
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus


def _case(reference: dict) -> BenchmarkCase:
    return BenchmarkCase(
        id="c1",
        benchmark_id="b",
        version=1,
        name="case",
        input={},
        reference=reference,
        evaluation_dimensions=["evaluator_sanity"],
        tags=[],
    )


def _ctx(case: BenchmarkCase, produced: list) -> EvaluatorContext:
    return EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=produced,
        config={},
    )


async def test_unknown_task_fails():
    result = await EvaluatorSanityEvaluator().evaluate(_ctx(_case({"task": "nope"}), []))
    assert result.status == EvaluatorStatus.failed
    assert "unknown task" in result.explanation


# --- regression: the sanity evaluator must contribute metrics -------------
#
# It returned a value dict with no ``metrics`` and no ``dimension_scores``.
# Because it is deterministic the harness iterates it for aggregation, so the
# benchmark reported only case_pass_rate and none of the declared dimensions.


async def test_sanity_emits_verdict_metric():
    """A known task with no produced artifacts: the target must not pass."""
    ref = {
        "task": "evidence_extraction",
        "expected_evaluator_status": "failed",
        "expect_provider_not_success": True,
    }
    result = await EvaluatorSanityEvaluator().evaluate(_ctx(_case(ref), []))

    value = result.value or {}
    assert value.get("metrics"), "sanity evaluator produced no aggregate metrics"
    metrics = value["metrics"]
    assert "evaluator_verdict_accuracy" in metrics
    assert metrics["evaluator_verdict_accuracy"]["count"] == 1
    assert "provider_error_safety" in metrics
    assert "evaluator_verdict_accuracy" in (value.get("dimension_scores") or {})


async def test_sanity_metrics_reflect_the_verdict():
    """The verdict metric is 1.0 only when the target's verdict matches."""
    ref = {
        "task": "evidence_extraction",
        # No artifacts produced -> the live-quality evaluator must fail, so
        # expecting "passed" is a mismatch and the metric must be 0.0.
        "expected_evaluator_status": "passed",
    }
    result = await EvaluatorSanityEvaluator().evaluate(_ctx(_case(ref), []))

    assert result.status == EvaluatorStatus.failed
    metric = result.value["metrics"]["evaluator_verdict_accuracy"]
    assert metric["value"] == 0.0
    assert metric["count"] == 1


async def test_sanity_diagnostics_metric_only_when_expected():
    """task_diagnostics_accuracy is emitted only if diagnostics are expected."""
    ref = {
        "task": "evidence_extraction",
        "expected_evaluator_status": "failed",
        "expect_task_diagnostics": ["cases_total", "cases_passed"],
    }
    result = await EvaluatorSanityEvaluator().evaluate(_ctx(_case(ref), []))

    metrics = result.value["metrics"]
    assert "task_diagnostics_accuracy" in metrics
    assert metrics["task_diagnostics_accuracy"]["count"] == 2
