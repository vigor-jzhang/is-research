"""Phase 6E unit tests — equilibrium evaluator.

Covers: symbolic expression equivalence (not string equality), FOC
accuracy/residuals, best responses, verification/status accuracy, solution
order, conditions, unsolvable detection, and incorrect-candidate rejection.
"""

from __future__ import annotations

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_equilibrium.plugin import EquilibriumEvaluator
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.equilibrium import (
    EquilibriumAnalysis,
    EquilibriumCandidate,
    EquilibriumExecution,
    EquilibriumExpression,
    EquilibriumVerification,
    Expression,
    SolutionMethod,
    VerificationCheck,
    VerificationStatus,
)
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus
from research_harness.research.schemas.model import (
    FormalAnalyticalModel,
    ModelActor,
    ModelParameter,
    ModelTimingStage,
    ModelVariable,
    PayoffFunction,
    SymbolKind,
)


def _cournot_model(model_id: str = "m1") -> FormalAnalyticalModel:
    return FormalAnalyticalModel(
        selected_mechanism_id="mech-1",
        title="Cournot",
        description="Cournot duopoly",
        actors=[ModelActor(actor_id="f1", name="Firm 1"), ModelActor(actor_id="f2", name="Firm 2")],
        variables=[
            ModelVariable(
                symbol="q1",
                name="q1",
                meaning="q1",
                domain="R_+",
                kind=SymbolKind.decision_variable,
                owner_actor_id="f1",
            ),
            ModelVariable(
                symbol="q2",
                name="q2",
                meaning="q2",
                domain="R_+",
                kind=SymbolKind.decision_variable,
                owner_actor_id="f2",
            ),
        ],
        parameters=[
            ModelParameter(symbol="a", name="a", meaning="a", domain="R_+"),
            ModelParameter(symbol="c", name="c", meaning="c", domain="R_+"),
        ],
        timing=[
            ModelTimingStage(
                stage_number=0,
                name="simultaneous",
                description="simultaneous",
                actor_ids=["f1", "f2"],
            )
        ],
        payoffs=[
            PayoffFunction(
                actor_id="f1",
                expression=Expression(
                    expression="q1*(a - q1 - q2) - c*q1", symbols_used=["q1", "a", "c"]
                ),
                decision_variables=["q1"],
                parameters=["a", "c"],
            ),
            PayoffFunction(
                actor_id="f2",
                expression=Expression(
                    expression="q2*(a - q1 - q2) - c*q2", symbols_used=["q2", "a", "c"]
                ),
                decision_variables=["q2"],
                parameters=["a", "c"],
            ),
        ],
    )


def _model_env(model: FormalAnalyticalModel, model_id: str) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=model,
        artifact_type="formal_analytical_model",
        producer="test",
        artifact_id=model_id,
    )


def _candidate_env(
    candidate_id: str,
    expressions: list[tuple[str, str]],
    *,
    method: str = "simultaneous",
    conditions: dict[str, list[str]] | None = None,
) -> ArtifactEnvelope:
    conditions = conditions or {}
    return ArtifactEnvelope.create(
        payload=EquilibriumCandidate(
            model_id="m1",
            expressions=[
                EquilibriumExpression(
                    variable=v,
                    expression=Expression(expression=e, symbols_used=["a", "c"]),
                    conditions=list(conditions.get(v) or []),
                    solution_method=SolutionMethod(method),
                )
                for v, e in expressions
            ],
            decision_variables=[v for v, _ in expressions],
            solution_method=SolutionMethod(method),
            proposed_by="sympy",
            verification_status=VerificationStatus.verified,
        ),
        artifact_type="equilibrium_candidate",
        producer="test",
        artifact_id=candidate_id,
    )


def _verification_env(
    candidate_id: str,
    status: str = "verified",
    checks: list[tuple[str, bool]] | None = None,
    conditions: list[str] | None = None,
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=EquilibriumVerification(
            model_id="m1",
            candidate_id=candidate_id,
            status=VerificationStatus(status),
            checks=[
                VerificationCheck(check_type=ct, passed=passed, detail="d")
                for ct, passed in (checks or [])
            ],
            conditions_required=list(conditions or []),
        ),
        artifact_type="equilibrium_verification",
        producer="test",
    )


def _analysis_env(
    candidate_id: str | None,
    *,
    status: str = "derived",
    order: list[str] | None = None,
    method: str = "simultaneous",
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=EquilibriumAnalysis(
            model_id="m1",
            candidate_ids=[candidate_id] if candidate_id else [],
            verification_ids=[],
            selected_candidate_id=candidate_id,
            status=status,
            solution_order=list(order or []),
            solution_method=SolutionMethod(method),
            revision_rounds=0,
        ),
        artifact_type="equilibrium_analysis",
        producer="test",
    )


def _execution_env(status: str = "derived", revisions: int = 0) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=EquilibriumExecution(
            model_id="m1",
            status=status,
            optimization_problems_created=2,
            focs_created=2,
            best_responses_created=2,
            candidates_created=1,
            verification_status="verified",
            revisions_used=revisions,
        ),
        artifact_type="equilibrium_execution",
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
        evaluation_dimensions=["equilibrium"],
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


def _produced(
    *,
    expressions: list[tuple[str, str]] | None = None,
    candidate_id: str = "cand-1",
    status: str = "derived",
    method: str = "simultaneous",
    verification: str = "verified",
    verification_checks: list[tuple[str, bool]] | None = None,
    conditions: list[str] | None = None,
    revisions: int = 0,
) -> list:
    exprs = expressions or [("q1", "(a-c)/3"), ("q2", "(a-c)/3")]
    return [
        _model_env(_cournot_model(), "m1"),
        _candidate_env(
            candidate_id, exprs, method=method, conditions={v: (conditions or []) for v, _ in exprs}
        ),
        _verification_env(candidate_id, verification, verification_checks, conditions),
        _analysis_env(candidate_id, status=status, method=method),
        _execution_env(status, revisions),
    ]


def _expected(**overrides) -> dict:
    base = {
        "expected_solution": {"q1": "(a-c)/3", "q2": "(a-c)/3"},
        "expected_verification": "verified",
        "expected_status": "derived",
    }
    base.update(overrides)
    return base


async def test_symbolic_equivalence_not_string_equality():
    # (a-c)/3 written differently must still match
    produced = _produced(expressions=[("q1", "a/3 - c/3"), ("q2", "(a - c)/3")])
    result = await EquilibriumEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["equilibrium_expression_accuracy"]["value"] == 2.0
    assert result.value["metrics"]["foc_accuracy"]["value"] == 2.0


async def test_wrong_expression_fails():
    produced = _produced(expressions=[("q1", "a"), ("q2", "(a-c)/3")])
    result = await EquilibriumEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.failed
    assert result.value["metrics"]["equilibrium_expression_accuracy"]["value"] == 1.0
    assert "not equivalent" in result.explanation


async def test_nonzero_foc_residual_fails():
    # candidate marked verified but the FOC residual is nonzero
    produced = _produced(
        expressions=[("q1", "a"), ("q2", "(a-c)/3")],
        verification_checks=[("symbol_validation", True), ("foc_residual", True)],
    )
    result = await EquilibriumEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "NONZERO FOC RESIDUAL" in result.explanation
    assert result.value["metrics"]["foc_accuracy"]["value"] == 0.0


async def test_incorrect_candidate_marked_verified_fails():
    # expected failed verification but the candidate was marked verified
    produced = _produced(verification="verified", status="derived")
    result = await EquilibriumEvaluator().evaluate(
        _ctx(_case(_expected(expected_verification="failed", expected_status="failed")), produced)
    )
    assert result.status == EvaluatorStatus.failed
    assert "VERIFICATION MISMATCH" in result.explanation
    assert "STATUS MISMATCH" in result.explanation


async def test_solution_order_mismatch_fails():
    produced = _produced(
        expressions=[("x", "(a-c)/2"), ("y", "(a-c)/4")],
        method="backward_induction",
    )
    # replace analysis with the wrong order
    produced[3] = _analysis_env("cand-1", order=["leader", "follower"], method="backward_induction")
    result = await EquilibriumEvaluator().evaluate(
        _ctx(
            _case(
                _expected(
                    expected_solution={"x": "(a-c)/2", "y": "(a-c)/4"},
                    expected_solution_order=["follower", "leader"],
                    expected_method="backward_induction",
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "SOLUTION ORDER MISMATCH" in result.explanation


async def test_conditions_dropped_fails():
    produced = _produced(
        expressions=[("q", "(a-c)/(2*b)")],
        conditions=[],
        verification="partially_verified",
        status="partially_derived",
    )
    produced[1] = _candidate_env("cand-1", [("q", "(a-c)/(2*b)")], conditions={"q": []})
    result = await EquilibriumEvaluator().evaluate(
        _ctx(
            _case(
                _expected(
                    expected_solution={"q": "(a-c)/(2*b)"},
                    expected_conditions=["2*b != 0"],
                    expected_verification="partially_verified",
                    expected_status="partially_derived",
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "CONDITIONS DROPPED" in result.explanation
    assert result.value["metrics"]["condition_accuracy"]["value"] == 0.0


async def test_unsolvable_treated_as_solved_fails():
    produced = _produced(status="derived")
    result = await EquilibriumEvaluator().evaluate(
        _ctx(_case(_expected(expected_status="not_solvable", expected_solution={})), produced)
    )
    assert result.status == EvaluatorStatus.failed
    assert "STATUS MISMATCH" in result.explanation
    assert result.value["metrics"]["unsolvable_detection_accuracy"]["value"] == 0.0


async def test_rejected_candidate_pass():
    # candidate rejected with foc_residual is the expected outcome
    produced = _produced(
        expressions=[("q", "a")],
        verification="failed",
        status="failed",
        verification_checks=[("symbol_validation", True), ("foc_residual", False)],
    )
    result = await EquilibriumEvaluator().evaluate(
        _ctx(
            _case(
                _expected(
                    expected_solution={},
                    expected_verification="failed",
                    expected_status="failed",
                    expected_rejections=1,
                    expected_foc_residual_rejected=True,
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.passed


async def test_no_execution_produced():
    result = await EquilibriumEvaluator().evaluate(_ctx(_case(_expected()), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None
