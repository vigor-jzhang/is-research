"""Phase 7A unit tests — analytical model-specification evaluator.

Covers: structural validity (created/rejected), symbol table, payoff
completeness, decision ownership, timing, information structure, assumption
grounding, and critic issue recall.
"""

from __future__ import annotations

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_model_specification.plugin import (
    ModelSpecificationEvaluator,
)
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus
from research_harness.research.schemas.model import (
    Expression,
    FormalAnalyticalModel,
    ModelActor,
    ModelCritiqueCategory,
    ModelCritiqueIssue,
    ModelParameter,
    ModelSpecificationCritique,
    ModelSpecificationExecution,
    ModelTimingStage,
    ModelVariable,
    PayoffFunction,
    SymbolKind,
)


def _model_env(model_id: str, **overrides) -> ArtifactEnvelope:
    kwargs: dict = {
        "selected_mechanism_id": "mech-1",
        "title": "Pricing Model",
        "description": "A pricing model.",
        "actors": [ModelActor(actor_id="firm", name="Firm", strategic=True)],
        "variables": [
            ModelVariable(
                symbol="p",
                name="price",
                meaning="price",
                kind=SymbolKind.decision_variable,
                owner_actor_id="firm",
            )
        ],
        "parameters": [
            ModelParameter(symbol="c", name="cost", meaning="cost"),
            ModelParameter(symbol="a", name="intercept", meaning="intercept"),
        ],
        "timing": [ModelTimingStage(stage_number=0, name="pricing", description="pricing")],
        "information_structure": {
            "items": [
                {
                    "actor_id": "firm",
                    "variable_symbols": [],
                    "available_at_stage": 0,
                    "visibility": "public",
                }
            ],
            "uncertainty": [],
            "summary": "complete information",
        },
        "payoffs": [
            PayoffFunction(
                actor_id="firm",
                expression=Expression(expression="(p - c) * (a - p)", symbols_used=["p", "c", "a"]),
                decision_variables=["p"],
                parameters=["c", "a"],
            )
        ],
    }
    kwargs.update(overrides)
    model = FormalAnalyticalModel(**kwargs)
    return ArtifactEnvelope.create(
        payload=model,
        artifact_type="formal_analytical_model",
        producer="test",
        artifact_id=model_id,
    )


def _exec_env(
    model_created: bool = True, rejected: bool = False, failures: list | None = None
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=ModelSpecificationExecution(
            selected_mechanism_id="mech-1",
            model_created=model_created,
            rejected=rejected,
            failures=list(failures or []),
        ),
        artifact_type="model_specification_execution",
        producer="test",
    )


def _critique_env(*categories: str) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=ModelSpecificationCritique(
            model_id="m1",
            issues=[
                ModelCritiqueIssue(
                    category=ModelCritiqueCategory(cat),
                    description="issue",
                    severity="high",
                )
                for cat in categories
            ],
            overall_assessment="assess",
            verdict="revise",
        ),
        artifact_type="model_specification_critique",
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
        evaluation_dimensions=["model_specification"],
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


async def test_valid_model_passes():
    result = await ModelSpecificationEvaluator().evaluate(
        _ctx(
            _case(
                {
                    "expected_model_created": True,
                    "expected_rejected": False,
                    "expected_symbols": ["p", "c", "a"],
                    "expected_payoff_actors": ["firm"],
                    "expected_decision_owners": {"p": "firm"},
                    "expected_critique_issues": ["mechanism_model_mismatch"],
                }
            ),
            [_model_env("m1"), _exec_env(), _critique_env("mechanism_model_mismatch")],
        )
    )
    assert result.status == EvaluatorStatus.passed
    m = result.value["metrics"]
    assert m["symbol_table_accuracy"]["value"] == 3.0
    assert m["payoff_completeness"]["value"] == 1.0
    assert m["decision_ownership_accuracy"]["value"] == 1.0
    assert m["timing_accuracy"]["value"] == 1.0
    assert m["information_structure_accuracy"]["value"] == 1.0
    assert m["structural_validity_accuracy"]["value"] == 1.0
    assert m["critic_issue_recall"]["value"] == 1.0


async def test_rejected_undefined_symbol_passes():
    result = await ModelSpecificationEvaluator().evaluate(
        _ctx(
            _case(
                {
                    "expected_model_created": False,
                    "expected_rejected": True,
                    "expected_failure_substring": "undefined symbol",
                }
            ),
            [
                _exec_env(
                    model_created=False,
                    rejected=True,
                    failures=[{"error": "structural validation failed: undefined symbol 't'"}],
                )
            ],
        )
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["structural_validity_accuracy"]["value"] == 1.0


async def test_rejected_but_expected_created_fails():
    result = await ModelSpecificationEvaluator().evaluate(
        _ctx(
            _case({"expected_model_created": True, "expected_rejected": False}),
            [_exec_env(model_created=False, rejected=True, failures=[{"error": "x"}])],
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "MODEL NOT CREATED" in result.explanation


async def test_rejection_reason_mismatch_fails():
    result = await ModelSpecificationEvaluator().evaluate(
        _ctx(
            _case(
                {
                    "expected_model_created": False,
                    "expected_rejected": True,
                    "expected_failure_substring": "duplicate symbol",
                }
            ),
            [_exec_env(model_created=False, rejected=True, failures=[{"error": "timing is bad"}])],
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "REJECTION REASON MISMATCH" in result.explanation


async def test_missing_payoff_for_strategic_actor_fails():
    model = _model_env(
        "m1",
        actors=[
            ModelActor(actor_id="firm", name="Firm", strategic=True),
            ModelActor(actor_id="rival", name="Rival", strategic=True),
        ],
        variables=[
            ModelVariable(
                symbol="p",
                name="price",
                meaning="price",
                kind=SymbolKind.decision_variable,
                owner_actor_id="firm",
            ),
            ModelVariable(
                symbol="r",
                name="rival",
                meaning="rival",
                kind=SymbolKind.decision_variable,
                owner_actor_id="rival",
            ),
        ],
    )
    result = await ModelSpecificationEvaluator().evaluate(
        _ctx(
            _case(
                {
                    "expected_model_created": True,
                    "expected_rejected": False,
                    "expected_symbols": ["p", "c", "a", "r"],
                    "expected_payoff_actors": ["firm", "rival"],
                    "expected_decision_owners": {"p": "firm", "r": "rival"},
                }
            ),
            [model, _exec_env(), _critique_env("mechanism_model_mismatch")],
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "MISSING PAYOFF" in result.explanation
    assert result.value["metrics"]["payoff_completeness"]["value"] == 1.0
    assert result.value["metrics"]["payoff_completeness"]["count"] == 2


async def test_symbol_table_mismatch_fails():
    result = await ModelSpecificationEvaluator().evaluate(
        _ctx(
            _case(
                {
                    "expected_model_created": True,
                    "expected_rejected": False,
                    "expected_symbols": ["p", "c", "a", "extra"],
                }
            ),
            [_model_env("m1"), _exec_env()],
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "SYMBOL TABLE MISMATCH" in result.explanation
    assert result.value["metrics"]["symbol_table_accuracy"]["value"] == 0.0


async def test_invalid_decision_ownership_fails():
    model = _model_env(
        "m1",
        variables=[
            ModelVariable(
                symbol="p",
                name="price",
                meaning="price",
                kind=SymbolKind.decision_variable,
                owner_actor_id="ghost",
            )
        ],
    )
    result = await ModelSpecificationEvaluator().evaluate(
        _ctx(
            _case(
                {
                    "expected_model_created": True,
                    "expected_rejected": False,
                    "expected_decision_owners": {"p": "firm"},
                }
            ),
            [model, _exec_env()],
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "DECISION OWNERSHIP" in result.explanation


async def test_invalid_timing_fails():
    model = _model_env(
        "m1",
        timing=[
            ModelTimingStage(stage_number=1, name="a", description="a"),
            ModelTimingStage(stage_number=1, name="b", description="b"),
        ],
    )
    result = await ModelSpecificationEvaluator().evaluate(
        _ctx(
            _case({"expected_model_created": True, "expected_rejected": False}),
            [model, _exec_env()],
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "TIMING" in result.explanation


async def test_unsupported_literature_assumption_fails():
    from research_harness.research.schemas.model import KnowledgeBasis, ModelAssumption

    model = _model_env(
        "m1",
        assumptions=[
            ModelAssumption(
                statement="Literature-backed assumption.",
                knowledge_basis=KnowledgeBasis.literature_supported,
                source_ids=["ghost-source"],
            )
        ],
    )
    result = await ModelSpecificationEvaluator().evaluate(
        _ctx(
            _case({"expected_model_created": True, "expected_rejected": False}),
            [model, _exec_env()],
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "ASSUMPTION GROUNDING" in result.explanation


async def test_critic_missing_issue_fails():
    result = await ModelSpecificationEvaluator().evaluate(
        _ctx(
            _case(
                {
                    "expected_model_created": True,
                    "expected_rejected": False,
                    "expected_critique_issues": ["mechanism_model_mismatch"],
                }
            ),
            [_model_env("m1"), _exec_env(), _critique_env("payoff_inconsistency")],
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "CRITIC ISSUES MISSING" in result.explanation
    assert result.value["metrics"]["critic_issue_recall"]["value"] == 0.0


async def test_no_execution_fails():
    result = await ModelSpecificationEvaluator().evaluate(_ctx(_case({}), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None
