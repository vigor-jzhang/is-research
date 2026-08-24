"""Phase 6E unit tests — numerical evaluator.

Covers: value accuracy with deterministic tolerances, feasibility
classification, condition enforcement, sweep monotonicity/counts, robustness
classification, welfare, and reproducibility metadata.
"""

from __future__ import annotations

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_numerical.plugin import NumericalEvaluator
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus
from research_harness.research.schemas.numerical import (
    NumericalExperimentExecution,
    NumericalResult,
    RobustnessCheck,
    RobustnessCheckType,
    RobustnessOutcome,
    WelfareAnalysis,
    WelfareMetric,
)


def _execution_env() -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=NumericalExperimentExecution(
            model_id="m1",
            equilibrium_candidate_id="c1",
            sweeps_created=3,
            results_created=5,
            results_infeasible=1,
            robustness_created=1,
            welfare_created=1,
            engine="sympy+python",
            seed=0,
        ),
        artifact_type="numerical_experiment_execution",
        producer="test",
    )


def _result_env(
    rid: str,
    *,
    scenario: str,
    feasible: bool = True,
    outcomes: dict[str, float] | None = None,
    x_parameter: str | None = None,
    x_value: float | None = None,
    reason: str | None = None,
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=NumericalResult(
            model_id="m1",
            equilibrium_candidate_id="c1",
            experiment_id="e1",
            scenario=scenario,
            group="g",
            x_parameter=x_parameter,
            x_value=x_value,
            parameter_values={"a": 10.0, "c": 1.0},
            outcomes=dict(outcomes or {}),
            feasible=feasible,
            infeasible_reason=reason,
        ),
        artifact_type="numerical_result",
        producer="test",
        artifact_id=rid,
    )


def _robustness_env(statement: str, outcome: str) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=RobustnessCheck(
            model_id="m1",
            equilibrium_candidate_id="c1",
            experiment_id="e1",
            check_type=RobustnessCheckType.proposition_support,
            description=f"numerical support of: {statement}",
            outcome=RobustnessOutcome(outcome),
            admissible_points=5,
        ),
        artifact_type="robustness_check",
        producer="test",
    )


def _welfare_env(total: float, metrics: int = 2) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=WelfareAnalysis(
            model_id="m1",
            equilibrium_candidate_id="c1",
            experiment_id="e1",
            metrics=[
                WelfareMetric(name=f"f{i} payoff", actor_id=f"f{i}", value=total / metrics)
                for i in range(metrics)
            ],
            total_welfare=total,
        ),
        artifact_type="welfare_analysis",
        producer="test",
    )


def _case(reference: dict) -> BenchmarkCase:
    return BenchmarkCase(
        id="c1",
        benchmark_id="b",
        version=1,
        name="case",
        input={},
        reference=reference,
        evaluation_dimensions=["numerical"],
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


def _base_produced() -> list:
    baseline = _result_env("r-baseline", scenario="baseline", outcomes={"q1": 3.0, "q2": 3.0})
    sweep = [
        _result_env(
            f"r-sweep-{i}",
            scenario="sweep",
            outcomes={"q1": float(2 + i)},
            x_parameter="a",
            x_value=float(5 + i),
        )
        for i in range(7)
    ]
    return [baseline, *sweep, _execution_env()]


def _expected(**overrides) -> dict:
    base = {"expected_baseline": {"q1": 3.0, "q2": 3.0}}
    base.update(overrides)
    return base


async def test_baseline_values_within_tolerance():
    produced = _base_produced()
    result = await NumericalEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["numerical_value_accuracy"]["value"] == 2.0


async def test_value_outside_tolerance_fails():
    produced = _base_produced()
    produced[0] = _result_env("r-baseline", scenario="baseline", outcomes={"q1": 3.5, "q2": 3.0})
    result = await NumericalEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "q1: produced 3.5, expected 3.0" in result.explanation
    assert result.value["metrics"]["numerical_value_accuracy"]["value"] == 1.0


async def test_infeasible_classification():
    produced = _base_produced()
    produced.append(
        _result_env(
            "r-infeasible",
            scenario="invalid",
            feasible=False,
            reason="parameter a=0.0 violates domain",
        )
    )
    case = _case(_expected(expected_infeasible_reasons=["parameter a violates domain"]))
    result = await NumericalEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["infeasible_reasons"] == ["parameter a violates domain"]


async def test_missing_infeasible_reason_fails():
    produced = _base_produced()
    produced.append(
        _result_env(
            "r-infeasible",
            scenario="invalid",
            feasible=False,
            reason="parameter a=0.0 violates domain",
        )
    )
    case = _case(
        _expected(
            expected_infeasible_reasons=["parameter a violates domain", "outcome q violates domain"]
        )
    )
    result = await NumericalEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.failed
    assert "MISSING INFEASIBLE REASONS" in result.explanation


async def test_condition_enforcement():
    produced = _base_produced()
    produced.append(
        _result_env(
            "r-cond",
            scenario="invalid",
            feasible=False,
            reason="equilibrium condition violated: 2*b != 0",
        )
    )
    case = _case(
        _expected(
            expected_infeasible_reasons=["equilibrium condition violated"],
            expected_condition_violations=1,
        )
    )
    result = await NumericalEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["condition_enforcement_accuracy"]["value"] == 1.0


async def test_sweep_monotonicity_and_count():
    produced = _base_produced()
    case = _case(_expected(expected_sweep={"parameter": "a", "points": 7, "monotonic": True}))
    result = await NumericalEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["sweep_accuracy"]["value"] == 1.0


async def test_sweep_non_monotonic_fails():
    produced = _base_produced()
    sweep = [
        _result_env(
            f"r-sweep-{i}",
            scenario="sweep",
            outcomes={"q1": float(7 - i)},
            x_parameter="a",
            x_value=float(5 + i),
        )
        for i in range(7)
    ]
    produced = [produced[0], *sweep, produced[-1]]
    case = _case(_expected(expected_sweep={"parameter": "a", "points": 7, "monotonic": True}))
    result = await NumericalEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.failed
    assert "not monotonic" in result.explanation


async def test_robustness_classification():
    produced = _base_produced()
    produced.append(_robustness_env("q1 increases in the demand intercept", "supported"))
    produced.append(_robustness_env("q1 decreases in the demand intercept", "violated"))
    case = _case(
        _expected(
            expected_propositions={
                "q1 increases in the demand intercept": "supported",
                "q1 decreases in the demand intercept": "violated",
            }
        )
    )
    result = await NumericalEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["robustness_classification_accuracy"]["value"] == 2.0


async def test_robustness_outcome_mismatch_fails():
    produced = _base_produced()
    produced.append(_robustness_env("q1 increases in the demand intercept", "violated"))
    case = _case(
        _expected(expected_propositions={"q1 increases in the demand intercept": "supported"})
    )
    result = await NumericalEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.failed
    assert "proposition" in result.explanation
    assert result.value["metrics"]["robustness_classification_accuracy"]["value"] == 0.0


async def test_welfare_accuracy():
    produced = _base_produced()
    produced.append(_welfare_env(total=18.0, metrics=2))
    case = _case(_expected(expected_welfare={"metrics": 2, "total": 18.0}))
    result = await NumericalEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["welfare_accuracy"]["value"] == 1.0


async def test_welfare_wrong_total_fails():
    produced = _base_produced()
    produced.append(_welfare_env(total=17.5, metrics=2))
    case = _case(_expected(expected_welfare={"metrics": 2, "total": 18.0}))
    result = await NumericalEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.failed
    assert "welfare total" in result.explanation


async def test_reproducibility_metadata():
    produced = _base_produced()
    case = _case(_expected(expected_reproducible=True))
    result = await NumericalEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["reproducibility_accuracy"]["value"] == 1.0


async def test_non_deterministic_engine_fails():
    produced = _base_produced()
    produced[-1] = ArtifactEnvelope.create(
        payload=NumericalExperimentExecution(
            model_id="m1",
            equilibrium_candidate_id="c1",
            engine="something-else",
            seed=1,
        ),
        artifact_type="numerical_experiment_execution",
        producer="test",
    )
    case = _case(_expected(expected_reproducible=True))
    result = await NumericalEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.failed
    assert "NOT REPRODUCIBLE" in result.explanation


async def test_no_execution_produced():
    result = await NumericalEvaluator().evaluate(_ctx(_case(_expected()), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None
