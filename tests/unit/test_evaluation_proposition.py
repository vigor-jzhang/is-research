"""Phase 6F unit tests — proposition evaluator.

Covers: verification accuracy, incorrect-proposition-marked-verified,
wrong-sign/wrong-derivative detection, condition preservation, equality
accuracy, hallucinated support ids, threshold rejection, and rejection
justification.
"""

from __future__ import annotations

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_proposition.plugin import PropositionEvaluator
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
from research_harness.research.schemas.proposition import (
    ComparativeStatic,
    Proposition,
    PropositionClaimType,
    PropositionStatus,
    PropositionVerification,
    PropositionVerificationStatus,
    StaticSign,
)


def _model() -> FormalAnalyticalModel:
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


def _model_env() -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=_model(),
        artifact_type="formal_analytical_model",
        producer="test",
        artifact_id="m1",
    )


def _candidate_env(
    expressions: list[tuple[str, str]] | None = None, candidate_id: str = "cand-1"
) -> ArtifactEnvelope:
    expressions = expressions or [("q1", "(a-c)/3"), ("q2", "(a-c)/3")]
    return ArtifactEnvelope.create(
        payload=EquilibriumCandidate(
            model_id="m1",
            expressions=[
                EquilibriumExpression(
                    variable=v,
                    expression=EqExpression(expression=e, symbols_used=["a", "c"]),
                    conditions=[],
                    solution_method=SolutionMethod("simultaneous"),
                )
                for v, e in expressions
            ],
            decision_variables=[v for v, _ in expressions],
            solution_method=SolutionMethod("simultaneous"),
            proposed_by="sympy",
            verification_status=VerificationStatus.verified,
        ),
        artifact_type="equilibrium_candidate",
        producer="test",
        artifact_id=candidate_id,
    )


def _static_env(
    sid: str,
    outcome: str = "q1",
    param: str = "a",
    derivative: str = "1/3",
    sign: str = "positive",
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=ComparativeStatic(
            model_id="m1",
            equilibrium_candidate_id="cand-1",
            outcome_variable=outcome,
            parameter=param,
            derivative_expression=Expression(expression=derivative, symbols_used=[]),
            sign=StaticSign(sign),
            conditions=[],
        ),
        artifact_type="comparative_static",
        producer="test",
        artifact_id=sid,
    )


def _prop_env(
    pid: str,
    *,
    claim_type: str = "monotonicity",
    outcome_variable: str | None = "q1",
    parameter: str | None = "a",
    expected_sign: str | None = "positive",
    mathematical_form: str | None = None,
    conditions: list[str] | None = None,
    supporting_static_ids: list[str] | None = None,
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=Proposition(
            model_id="m1",
            equilibrium_candidate_id="cand-1",
            comparative_statics_analysis_id="cs-1",
            statement="test proposition",
            claim_type=PropositionClaimType(claim_type),
            outcome_variable=outcome_variable,
            parameter=parameter,
            expected_sign=expected_sign,
            mathematical_form=(
                Expression(expression=mathematical_form, symbols_used=["q1", "q2"])
                if mathematical_form
                else None
            ),
            conditions=list(conditions or []),
            supporting_static_ids=list(supporting_static_ids or []),
            status=PropositionStatus.candidate,
            proposed_by="llm",
        ),
        artifact_type="proposition",
        producer="test",
        artifact_id=pid,
    )


def _verification_env(pid: str, status: str) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=PropositionVerification(
            proposition_id=pid,
            model_id="m1",
            status=PropositionVerificationStatus(status),
            checks=[],
        ),
        artifact_type="proposition_verification",
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
        evaluation_dimensions=["proposition"],
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


def _prop_reference(entries: list[dict]) -> dict:
    return {"expected_propositions": entries}


def _prop_ref(
    claim_type: str,
    expected_verification: str,
    *,
    outcome_variable: str | None = None,
    parameter: str | None = None,
    expected_sign: str | None = None,
    expected_conditions: list[str] | None = None,
    expected_equality: bool | None = None,
    expected_rejected: bool = False,
) -> dict:
    return {
        "claim_type": claim_type,
        "outcome_variable": outcome_variable,
        "parameter": parameter,
        "expected_sign": expected_sign,
        "expected_verification": expected_verification,
        "expected_conditions": expected_conditions or [],
        "expected_equality": expected_equality,
        "expected_rejected": expected_rejected,
    }


def _produced(
    props: list[tuple[str, dict]],
    verifications: list[tuple[str, str]],
    statics: list[ArtifactEnvelope] | None = None,
    candidate: ArtifactEnvelope | None = None,
) -> list:
    statics = statics or [
        _static_env("static-0"),
        _static_env("static-1", param="c", derivative="-1/3", sign="negative"),
    ]
    out: list = [_model_env(), candidate or _candidate_env(), *statics]
    for pid, kwargs in props:
        out.append(_prop_env(pid, **kwargs))
    for pid, status in verifications:
        out.append(_verification_env(pid, status))
    return out


async def test_correct_positive_monotonicity_passes():
    produced = _produced(
        props=[("p0", {"supporting_static_ids": ["static-0"]})],
        verifications=[("p0", "verified")],
    )
    result = await PropositionEvaluator().evaluate(
        _ctx(
            _case(
                _prop_reference(
                    [
                        _prop_ref(
                            "monotonicity",
                            "verified",
                            outcome_variable="q1",
                            parameter="a",
                            expected_sign="positive",
                        )
                    ]
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["proposition_verification_accuracy"]["value"] == 1.0
    assert result.value["metrics"]["monotonicity_accuracy"]["value"] == 1.0


async def test_incorrect_proposition_marked_verified_fails():
    # an incorrect proposition (wrong sign claim) marked verified
    produced = _produced(
        props=[("p0", {"expected_sign": "negative", "supporting_static_ids": ["static-0"]})],
        verifications=[("p0", "verified")],
    )
    result = await PropositionEvaluator().evaluate(
        _ctx(
            _case(
                _prop_reference(
                    [
                        _prop_ref(
                            "monotonicity",
                            "failed",
                            outcome_variable="q1",
                            parameter="a",
                            expected_sign="negative",
                            expected_rejected=True,
                        )
                    ]
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "verification" in result.explanation
    assert result.value["metrics"]["proposition_verification_accuracy"]["value"] == 0.0


async def test_wrong_sign_claim_fails():
    # proposition claims negative but the recomputed sign is positive
    produced = _produced(
        props=[("p0", {"expected_sign": "negative", "supporting_static_ids": ["static-0"]})],
        verifications=[("p0", "verified")],
    )
    result = await PropositionEvaluator().evaluate(
        _ctx(
            _case(
                _prop_reference(
                    [
                        _prop_ref(
                            "monotonicity",
                            "verified",
                            outcome_variable="q1",
                            parameter="a",
                            expected_sign="positive",
                        )
                    ]
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "d q1/d a".replace(" ", "") in result.explanation.replace(" ", "")
    assert result.value["metrics"]["monotonicity_accuracy"]["value"] == 0.0


async def test_wrong_static_derivative_fails():
    # the static derivative contradicts the candidate's recomputed derivative
    produced = _produced(
        props=[("p0", {"supporting_static_ids": ["static-0"]})],
        verifications=[("p0", "verified")],
        statics=[_static_env("static-0", derivative="1/7")],
    )
    result = await PropositionEvaluator().evaluate(
        _ctx(
            _case(
                _prop_reference(
                    [
                        _prop_ref(
                            "monotonicity",
                            "verified",
                            outcome_variable="q1",
                            parameter="a",
                            expected_sign="positive",
                        )
                    ]
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "wrong symbolic derivative" in result.explanation


async def test_wrong_sign_correctly_rejected_passes():
    produced = _produced(
        props=[("p0", {"expected_sign": "negative", "supporting_static_ids": ["static-0"]})],
        verifications=[("p0", "failed")],
    )
    result = await PropositionEvaluator().evaluate(
        _ctx(
            _case(
                _prop_reference(
                    [
                        _prop_ref(
                            "monotonicity",
                            "failed",
                            outcome_variable="q1",
                            parameter="a",
                            expected_sign="negative",
                            expected_rejected=True,
                        )
                    ]
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["incorrect_proposition_rejection_rate"]["value"] == 1.0


async def test_rejected_despite_matching_sign_fails():
    # rejected although the claimed sign matches the static (unjustified)
    produced = _produced(
        props=[("p0", {"supporting_static_ids": ["static-0"]})],
        verifications=[("p0", "failed")],
    )
    result = await PropositionEvaluator().evaluate(
        _ctx(
            _case(
                _prop_reference(
                    [
                        _prop_ref(
                            "monotonicity",
                            "failed",
                            outcome_variable="q1",
                            parameter="a",
                            expected_sign="positive",
                            expected_rejected=True,
                        )
                    ]
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "rejected despite claimed sign" in result.explanation


async def test_missing_condition_rejection_passes():
    slope_candidate = _candidate_env([("q", "(a-c)/(2*b)")])
    slope_static = _static_env(
        "static-0", outcome="q", param="a", derivative="1/(2*b)", sign="ambiguous"
    )
    produced = _produced(
        props=[
            (
                "p0",
                {"outcome_variable": "q", "parameter": "a", "supporting_static_ids": ["static-0"]},
            )
        ],
        verifications=[("p0", "failed")],
        statics=[slope_static],
        candidate=slope_candidate,
    )
    result = await PropositionEvaluator().evaluate(
        _ctx(
            _case(
                _prop_reference(
                    [
                        _prop_ref(
                            "monotonicity",
                            "failed",
                            outcome_variable="q",
                            parameter="a",
                            expected_sign="positive",
                            expected_rejected=True,
                        )
                    ]
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.passed


async def test_conditional_proposition_passes():
    slope_candidate = _candidate_env([("q", "(a-c)/(2*b)")])
    slope_static = _static_env(
        "static-0", outcome="q", param="a", derivative="1/(2*b)", sign="ambiguous"
    )
    produced = _produced(
        props=[
            (
                "p0",
                {
                    "outcome_variable": "q",
                    "parameter": "a",
                    "conditions": ["b > 0"],
                    "supporting_static_ids": ["static-0"],
                },
            )
        ],
        verifications=[("p0", "conditionally_verified")],
        statics=[slope_static],
        candidate=slope_candidate,
    )
    result = await PropositionEvaluator().evaluate(
        _ctx(
            _case(
                _prop_reference(
                    [
                        _prop_ref(
                            "monotonicity",
                            "conditionally_verified",
                            outcome_variable="q",
                            parameter="a",
                            expected_sign="positive",
                            expected_conditions=["b > 0"],
                        )
                    ]
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["condition_accuracy"]["value"] == 1.0


async def test_conditionally_verified_without_conditions_fails():
    slope_candidate = _candidate_env([("q", "(a-c)/(2*b)")])
    slope_static = _static_env(
        "static-0", outcome="q", param="a", derivative="1/(2*b)", sign="ambiguous"
    )
    produced = _produced(
        props=[
            (
                "p0",
                {"outcome_variable": "q", "parameter": "a", "supporting_static_ids": ["static-0"]},
            )
        ],
        verifications=[("p0", "conditionally_verified")],
        statics=[slope_static],
        candidate=slope_candidate,
    )
    result = await PropositionEvaluator().evaluate(
        _ctx(
            _case(
                _prop_reference(
                    [
                        _prop_ref(
                            "monotonicity",
                            "conditionally_verified",
                            outcome_variable="q",
                            parameter="a",
                            expected_sign="positive",
                            expected_conditions=["b > 0"],
                        )
                    ]
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "without conditions" in result.explanation


async def test_conditions_dropped_fails():
    produced = _produced(
        props=[
            (
                "p0",
                {
                    "outcome_variable": "q1",
                    "parameter": "a",
                    "conditions": [],
                    "supporting_static_ids": ["static-0"],
                },
            )
        ],
        verifications=[("p0", "verified")],
    )
    result = await PropositionEvaluator().evaluate(
        _ctx(
            _case(
                _prop_reference(
                    [
                        _prop_ref(
                            "monotonicity",
                            "verified",
                            outcome_variable="q1",
                            parameter="a",
                            expected_sign="positive",
                            expected_conditions=["a > c"],
                        )
                    ]
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "conditions dropped" in result.explanation
    assert result.value["metrics"]["condition_accuracy"]["value"] == 0.0


async def test_valid_equality_passes():
    produced = _produced(
        props=[
            (
                "p0",
                {
                    "claim_type": "equality",
                    "mathematical_form": "q1 = q2",
                    "supporting_static_ids": ["static-0"],
                },
            )
        ],
        verifications=[("p0", "verified")],
    )
    result = await PropositionEvaluator().evaluate(
        _ctx(
            _case(_prop_reference([_prop_ref("equality", "verified", expected_equality=True)])),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["equality_accuracy"]["value"] == 1.0


async def test_invalid_equality_accepted_fails():
    produced = _produced(
        props=[
            (
                "p0",
                {
                    "claim_type": "equality",
                    "mathematical_form": "q1 = 2*q2",
                    "supporting_static_ids": ["static-0"],
                },
            )
        ],
        verifications=[("p0", "verified")],
    )
    result = await PropositionEvaluator().evaluate(
        _ctx(
            _case(
                _prop_reference(
                    [
                        _prop_ref(
                            "equality", "failed", expected_equality=False, expected_rejected=True
                        )
                    ]
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "invalid equality accepted" in result.explanation
    assert result.value["metrics"]["equality_accuracy"]["value"] == 1.0


async def test_invalid_equality_correctly_rejected_passes():
    produced = _produced(
        props=[
            (
                "p0",
                {
                    "claim_type": "equality",
                    "mathematical_form": "q1 = 2*q2",
                    "supporting_static_ids": ["static-0"],
                },
            )
        ],
        verifications=[("p0", "failed")],
    )
    result = await PropositionEvaluator().evaluate(
        _ctx(
            _case(
                _prop_reference(
                    [
                        _prop_ref(
                            "equality", "failed", expected_equality=False, expected_rejected=True
                        )
                    ]
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.passed


async def test_hallucinated_support_correctly_rejected_passes():
    produced = _produced(
        props=[("p0", {"supporting_static_ids": ["ghost-1"]})],
        verifications=[("p0", "failed")],
    )
    result = await PropositionEvaluator().evaluate(
        _ctx(
            _case(
                _prop_reference(
                    [
                        _prop_ref(
                            "monotonicity",
                            "failed",
                            outcome_variable="q1",
                            parameter="a",
                            expected_sign="positive",
                            expected_rejected=True,
                        )
                    ]
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.passed


async def test_hallucinated_support_accepted_fails():
    produced = _produced(
        props=[("p0", {"supporting_static_ids": ["ghost-1"]})],
        verifications=[("p0", "verified")],
    )
    result = await PropositionEvaluator().evaluate(
        _ctx(
            _case(
                _prop_reference(
                    [
                        _prop_ref(
                            "monotonicity",
                            "verified",
                            outcome_variable="q1",
                            parameter="a",
                            expected_sign="positive",
                        )
                    ]
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "hallucinated support" in result.explanation
    assert result.value["metrics"]["support_reference_accuracy"]["value"] == 0.0


async def test_threshold_rejected_passes():
    produced = _produced(
        props=[("p0", {"claim_type": "threshold", "supporting_static_ids": ["static-0"]})],
        verifications=[("p0", "failed")],
    )
    result = await PropositionEvaluator().evaluate(
        _ctx(
            _case(_prop_reference([_prop_ref("threshold", "failed", expected_rejected=True)])),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.passed


async def test_threshold_accepted_fails():
    produced = _produced(
        props=[("p0", {"claim_type": "threshold", "supporting_static_ids": ["static-0"]})],
        verifications=[("p0", "verified")],
    )
    result = await PropositionEvaluator().evaluate(
        _ctx(
            _case(_prop_reference([_prop_ref("threshold", "failed", expected_rejected=True)])),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "not rejected" in result.explanation


async def test_no_propositions_produced():
    result = await PropositionEvaluator().evaluate(_ctx(_case({}), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None
