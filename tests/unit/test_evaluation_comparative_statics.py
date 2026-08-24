"""Phase 6F unit tests — comparative statics evaluator.

Covers: symbolic derivative equivalence (not string equality), sign
accuracy, definite-sign-overclaim, condition preservation (dropped and
spurious), outcome/parameter coverage, and ambiguous-sign handling.
"""

from __future__ import annotations

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_comparative_statics.plugin import (
    ComparativeStaticsEvaluator,
)
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.equilibrium import (
    EquilibriumCandidate,
    EquilibriumExpression,
    SolutionMethod,
    VerificationStatus,
)
from research_harness.research.schemas.equilibrium import (
    Expression as EqExpression,
)
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus
from research_harness.research.schemas.model import (
    Expression,
    FormalAnalyticalModel,
    ModelActor,
    ModelParameter,
    ModelTimingStage,
    ModelVariable,
    PayoffFunction,
    SymbolKind,
)
from research_harness.research.schemas.proposition import ComparativeStatic, StaticSign


def _monopoly_model() -> FormalAnalyticalModel:
    return FormalAnalyticalModel(
        selected_mechanism_id="mech-1",
        title="Monopoly",
        description="Monopoly",
        actors=[ModelActor(actor_id="m1", name="Monopolist")],
        variables=[
            ModelVariable(
                symbol="q",
                name="q",
                meaning="q",
                domain="R_+",
                kind=SymbolKind.decision_variable,
                owner_actor_id="m1",
            )
        ],
        parameters=[
            ModelParameter(symbol="a", name="a", meaning="a", domain="R_+"),
            ModelParameter(symbol="c", name="c", meaning="c", domain="R_+"),
        ],
        timing=[
            ModelTimingStage(stage_number=0, name="move", description="move", actor_ids=["m1"])
        ],
        payoffs=[
            PayoffFunction(
                actor_id="m1",
                expression=Expression(expression="q*(a - q) - c*q", symbols_used=["q", "a", "c"]),
                decision_variables=["q"],
                parameters=["a", "c"],
            )
        ],
    )


def _slope_model() -> FormalAnalyticalModel:
    return FormalAnalyticalModel(
        selected_mechanism_id="mech-1",
        title="Slope",
        description="Monopoly with slope",
        actors=[ModelActor(actor_id="m1", name="Monopolist")],
        variables=[
            ModelVariable(
                symbol="q",
                name="q",
                meaning="q",
                domain="R_+",
                kind=SymbolKind.decision_variable,
                owner_actor_id="m1",
            )
        ],
        parameters=[
            ModelParameter(symbol="a", name="a", meaning="a", domain="R_+"),
            ModelParameter(symbol="c", name="c", meaning="c", domain="R_+"),
            ModelParameter(symbol="b", name="b", meaning="b", domain="R"),
        ],
        timing=[
            ModelTimingStage(stage_number=0, name="move", description="move", actor_ids=["m1"])
        ],
        payoffs=[
            PayoffFunction(
                actor_id="m1",
                expression=Expression(
                    expression="q*(a - b*q) - c*q", symbols_used=["q", "a", "b", "c"]
                ),
                decision_variables=["q"],
                parameters=["a", "b", "c"],
            )
        ],
    )


def _model_env(model: FormalAnalyticalModel) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=model,
        artifact_type="formal_analytical_model",
        producer="test",
        artifact_id="m1",
    )


def _candidate_env(expression: str, model_id: str = "m1") -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=EquilibriumCandidate(
            model_id=model_id,
            expressions=[
                EquilibriumExpression(
                    variable="q",
                    expression=EqExpression(expression=expression, symbols_used=["a", "c"]),
                    conditions=[],
                    solution_method=SolutionMethod("simultaneous"),
                )
            ],
            decision_variables=["q"],
            solution_method=SolutionMethod("simultaneous"),
            proposed_by="sympy",
            verification_status=VerificationStatus.verified,
        ),
        artifact_type="equilibrium_candidate",
        producer="test",
        artifact_id="cand-1",
    )


def _static_env(
    outcome: str,
    param: str,
    derivative: str,
    sign: str,
    conditions: list[str] | None = None,
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=ComparativeStatic(
            model_id="m1",
            equilibrium_candidate_id="cand-1",
            outcome_variable=outcome,
            parameter=param,
            derivative_expression=Expression(expression=derivative, symbols_used=[]),
            sign=StaticSign(sign),
            conditions=list(conditions or []),
        ),
        artifact_type="comparative_static",
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
        evaluation_dimensions=["comparative_statics"],
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


def _monopoly_produced(
    derivative: str = "1/2", sign: str = "positive", conditions: list[str] | None = None
) -> list:
    return [
        _model_env(_monopoly_model()),
        _candidate_env("(a-c)/2"),
        _static_env("q", "a", derivative, sign, conditions),
    ]


def _cs_reference(statics: dict[str, dict[str, object]]) -> dict:
    return {"expected_statics": statics}


def _static_ref(
    derivative: str, sign: str, conditions: list[str] | None = None
) -> dict[str, object]:
    return {"derivative": derivative, "sign": sign, "conditions": conditions or []}


async def test_symbolic_equivalence_not_string_equality():
    # derivative written differently must still match
    produced = _monopoly_produced(derivative="2/4")
    result = await ComparativeStaticsEvaluator().evaluate(
        _ctx(_case(_cs_reference({"q/a": _static_ref("1/2", "positive")})), produced)
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["derivative_accuracy"]["value"] == 1.0


async def test_wrong_derivative_fails():
    produced = _monopoly_produced(derivative="1/3")
    result = await ComparativeStaticsEvaluator().evaluate(
        _ctx(_case(_cs_reference({"q/a": _static_ref("1/2", "positive")})), produced)
    )
    assert result.status == EvaluatorStatus.failed
    assert "WRONG DERIVATIVE" in result.explanation
    assert result.value["metrics"]["derivative_accuracy"]["value"] == 0.0


async def test_derivative_contradicts_candidate_fails():
    # static derivative correct per reference but contradicts the candidate
    produced = _monopoly_produced(derivative="1/3")
    result = await ComparativeStaticsEvaluator().evaluate(
        _ctx(_case(_cs_reference({"q/a": _static_ref("1/3", "positive")})), produced)
    )
    assert result.status == EvaluatorStatus.failed
    assert "contradicts recomputed" in result.explanation


async def test_wrong_sign_fails():
    produced = _monopoly_produced(sign="negative")
    result = await ComparativeStaticsEvaluator().evaluate(
        _ctx(_case(_cs_reference({"q/a": _static_ref("1/2", "positive")})), produced)
    )
    assert result.status == EvaluatorStatus.failed
    assert "WRONG SIGN" in result.explanation
    assert result.value["metrics"]["sign_accuracy"]["value"] == 0.0


async def test_definite_sign_asserted_when_ambiguous_fails():
    produced = [
        _model_env(_slope_model()),
        _candidate_env("(a-c)/(2*b)"),
        _static_env("q", "a", "1/(2*b)", "positive"),
    ]
    result = await ComparativeStaticsEvaluator().evaluate(
        _ctx(
            _case(
                _cs_reference(
                    {"q/a": _static_ref("1/(2*b)", "ambiguous", ["sign of da depends on: b"])}
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "DEFINITE SIGN OVERCLAIM" in result.explanation


async def test_ambiguous_sign_handled_correctly_passes():
    produced = [
        _model_env(_slope_model()),
        _candidate_env("(a-c)/(2*b)"),
        _static_env("q", "a", "1/(2*b)", "ambiguous", ["sign of da depends on: b"]),
    ]
    result = await ComparativeStaticsEvaluator().evaluate(
        _ctx(
            _case(
                _cs_reference(
                    {"q/a": _static_ref("1/(2*b)", "ambiguous", ["sign of da depends on: b"])}
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["ambiguous_sign_accuracy"]["value"] == 1.0
    assert result.value["metrics"]["ambiguous_sign_accuracy"]["count"] == 1


async def test_conditions_dropped_fails():
    produced = _monopoly_produced(conditions=[])
    result = await ComparativeStaticsEvaluator().evaluate(
        _ctx(
            _case(
                _cs_reference(
                    {"q/a": _static_ref("1/2", "ambiguous", ["sign of da depends on: b"])}
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "CONDITIONS DROPPED" in result.explanation
    assert result.value["metrics"]["condition_preservation_accuracy"]["value"] == 0.0


async def test_spurious_conditions_fail():
    produced = _monopoly_produced(conditions=["sign of da depends on: b"])
    result = await ComparativeStaticsEvaluator().evaluate(
        _ctx(_case(_cs_reference({"q/a": _static_ref("1/2", "positive")})), produced)
    )
    assert result.status == EvaluatorStatus.failed
    assert "SPURIOUS CONDITIONS" in result.explanation


async def test_condition_equivalence_by_sides():
    # expected condition written differently is still preserved
    produced = [
        _model_env(_slope_model()),
        _candidate_env("(a-c)/(2*b)"),
        _static_env("q", "a", "1/(2*b)", "ambiguous", ["b > 0"]),
    ]
    result = await ComparativeStaticsEvaluator().evaluate(
        _ctx(
            _case(_cs_reference({"q/a": _static_ref("1/(2*b)", "ambiguous", ["b - 0 > 0"])})),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.passed


async def test_missing_coverage_fails():
    # a static exists but not for the expected outcome/parameter pair
    produced = [
        _model_env(_monopoly_model()),
        _candidate_env("(a-c)/2"),
        _static_env("q", "c", "-1/2", "negative"),
    ]
    result = await ComparativeStaticsEvaluator().evaluate(
        _ctx(_case(_cs_reference({"q/a": _static_ref("1/2", "positive")})), produced)
    )
    assert result.status == EvaluatorStatus.failed
    assert "COVERAGE" in result.explanation
    assert result.value["metrics"]["outcome_parameter_coverage"]["value"] == 0.0


async def test_multiple_outcomes_and_parameters():
    produced = [
        _model_env(_monopoly_model()),
        _candidate_env("(a-c)/2"),
        _static_env("q", "a", "1/2", "positive"),
        _static_env("q", "c", "-1/2", "negative"),
    ]
    result = await ComparativeStaticsEvaluator().evaluate(
        _ctx(
            _case(
                _cs_reference(
                    {
                        "q/a": _static_ref("1/2", "positive"),
                        "q/c": _static_ref("-1/2", "negative"),
                    }
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["derivative_accuracy"]["value"] == 2.0
    assert result.value["metrics"]["derivative_accuracy"]["count"] == 2


async def test_incorrect_expected_derivative_fails_by_design():
    # a wrong reference must deterministically fail the case
    produced = _monopoly_produced(derivative="1/2")
    result = await ComparativeStaticsEvaluator().evaluate(
        _ctx(_case(_cs_reference({"q/a": _static_ref("2", "positive")})), produced)
    )
    assert result.status == EvaluatorStatus.failed
    assert "WRONG DERIVATIVE" in result.explanation


async def test_no_statics_produced():
    result = await ComparativeStaticsEvaluator().evaluate(_ctx(_case({}), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None
