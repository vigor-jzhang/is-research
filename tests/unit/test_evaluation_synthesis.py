"""Phase 7A unit tests — synthesis evaluator.

Covers: statement grounding, consensus/contradiction/mixed accuracy,
multi-paper support, support counts, unsupported-statement rate, and
hallucinated-reference rejection.
"""

from __future__ import annotations

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_synthesis.plugin import SynthesisEvaluator
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus
from research_harness.research.schemas.synthesis import (
    SupportType,
    SynthesisExecution,
    SynthesisStatement,
    SynthesisStatementType,
)


def _evidence_env(eid: str, statement: str) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload={"statement": statement, "category": "finding"},
        artifact_type="evidence_item",
        producer="test",
        artifact_id=eid,
    )


def _stmt_env(
    statement: str,
    stype: str,
    supporting: list[str],
    conflicting: list[str] | None = None,
    *,
    papers_supporting: int = 1,
    papers_conflicting: int = 0,
    support_type: str = "single_paper",
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=SynthesisStatement(
            statement=statement,
            type=SynthesisStatementType(stype),
            supporting_evidence_ids=supporting,
            conflicting_evidence_ids=list(conflicting or []),
            supporting_paper_identity_ids=[f"paper-{i}" for i in range(papers_supporting)],
            conflicting_paper_identity_ids=[f"cpaper-{i}" for i in range(papers_conflicting)],
            papers_supporting=papers_supporting,
            papers_conflicting=papers_conflicting,
            evidence_items_supporting=len(supporting),
            evidence_items_conflicting=len(conflicting or []),
            support_type=SupportType(support_type),
        ),
        artifact_type="synthesis_statement",
        producer="test",
    )


def _exec_env(rejected: int = 0) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=SynthesisExecution(
            evidence_corpus_id="c1",
            themes_created=1,
            statements_created=1,
            statements_rejected=rejected,
            completed_at=None,
        ),
        artifact_type="synthesis_execution",
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
        evaluation_dimensions=["synthesis"],
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


async def test_grounded_multi_paper_consensus_passes():
    evidence = [
        _evidence_env("ev-0", "Pricing reduces welfare."),
        _evidence_env("ev-1", "Pricing reduces welfare."),
        _evidence_env("ev-2", "Pricing reduces welfare."),
    ]
    stmt = _stmt_env(
        "Pricing reduces welfare.",
        "consensus",
        ["ev-0", "ev-1", "ev-2"],
        papers_supporting=3,
        support_type="multi_paper",
    )
    result = await SynthesisEvaluator().evaluate(
        _ctx(
            _case(
                {
                    "expected_statements": [
                        {
                            "statement": "Pricing reduces welfare.",
                            "type": "consensus",
                            "support_type": "multi_paper",
                            "papers_supporting": 3,
                        }
                    ],
                    "expected_rejections": 0,
                }
            ),
            evidence + [stmt, _exec_env()],
        )
    )
    assert result.status == EvaluatorStatus.passed
    m = result.value["metrics"]
    assert m["statement_grounding_accuracy"]["value"] == 1.0
    assert m["consensus_accuracy"]["value"] == 1.0
    assert m["multi_paper_support_accuracy"]["value"] == 1.0
    assert m["support_count_accuracy"]["value"] == 1.0
    assert m["unsupported_statement_rate"]["value"] == 0.0
    assert m["hallucinated_reference_count"]["value"] == 0.0


async def test_contradiction_with_both_sides_passes():
    stmt = _stmt_env(
        "Studies disagree.",
        "contradiction",
        ["ev-0"],
        conflicting=["ev-1"],
        papers_supporting=1,
        papers_conflicting=1,
    )
    result = await SynthesisEvaluator().evaluate(
        _ctx(
            _case(
                {
                    "expected_statements": [
                        {
                            "statement": "Studies disagree.",
                            "type": "contradiction",
                            "support_type": "single_paper",
                            "papers_supporting": 1,
                            "papers_conflicting": 1,
                        }
                    ],
                    "expected_rejections": 0,
                }
            ),
            [_evidence_env("ev-0", "A"), _evidence_env("ev-1", "B"), stmt, _exec_env()],
        )
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["contradiction_accuracy"]["value"] == 1.0


async def test_single_paper_not_consensus():
    stmt = _stmt_env("One study finds X.", "pattern", ["ev-0"], papers_supporting=1)
    result = await SynthesisEvaluator().evaluate(
        _ctx(
            _case(
                {
                    "expected_statements": [
                        {
                            "statement": "One study finds X.",
                            "type": "pattern",
                            "support_type": "single_paper",
                            "papers_supporting": 1,
                        }
                    ],
                    "expected_not_consensus": True,
                    "expected_rejections": 0,
                }
            ),
            [_evidence_env("ev-0", "One study."), stmt, _exec_env()],
        )
    )
    assert result.status == EvaluatorStatus.passed


async def test_missing_expected_statement_fails():
    result = await SynthesisEvaluator().evaluate(
        _ctx(
            _case(
                {
                    "expected_statements": [
                        {
                            "statement": "Never produced.",
                            "type": "consensus",
                            "support_type": "multi_paper",
                            "papers_supporting": 2,
                        }
                    ],
                    "expected_rejections": 0,
                }
            ),
            [_evidence_env("ev-0", "A"), _evidence_env("ev-1", "B"), _exec_env()],
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "missing expected statement" in result.explanation
    assert result.value["metrics"]["consensus_accuracy"]["value"] == 0.0


async def test_wrong_type_fails():
    stmt = _stmt_env(
        "Pricing reduces welfare.",
        "mixed",
        ["ev-0", "ev-1"],
        papers_supporting=2,
        support_type="multi_paper",
    )
    result = await SynthesisEvaluator().evaluate(
        _ctx(
            _case(
                {
                    "expected_statements": [
                        {
                            "statement": "Pricing reduces welfare.",
                            "type": "consensus",
                            "support_type": "multi_paper",
                            "papers_supporting": 2,
                        }
                    ],
                    "expected_rejections": 0,
                }
            ),
            [_evidence_env("ev-0", "A"), _evidence_env("ev-1", "B"), stmt, _exec_env()],
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "expected type 'consensus', produced 'mixed'" in result.explanation
    assert result.value["metrics"]["consensus_accuracy"]["value"] == 0.0


async def test_hallucinated_evidence_fails():
    stmt = _stmt_env("A fabricated claim.", "consensus", ["ev-0", "ev-999"], papers_supporting=2)
    result = await SynthesisEvaluator().evaluate(
        _ctx(
            _case({"expected_statements": [], "expected_rejections": 0}),
            [_evidence_env("ev-0", "A"), stmt, _exec_env()],
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert result.value["metrics"]["unsupported_statement_rate"]["value"] == 1.0
    assert result.value["metrics"]["hallucinated_reference_count"]["value"] == 1.0


async def test_expected_rejections_verified():
    # the synthesizer rejected one statement; the reference expects >= 1
    result = await SynthesisEvaluator().evaluate(
        _ctx(
            _case({"expected_statements": [], "expected_rejections": 1}),
            [_evidence_env("ev-0", "A"), _exec_env(rejected=1)],
        )
    )
    assert result.status == EvaluatorStatus.passed


async def test_expected_rejections_not_met_fails():
    result = await SynthesisEvaluator().evaluate(
        _ctx(
            _case({"expected_statements": [], "expected_rejections": 1}),
            [_evidence_env("ev-0", "A"), _exec_env(rejected=0)],
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "REJECTIONS" in result.explanation


async def test_expected_absent_statement_present_fails():
    stmt = _stmt_env("Must be rejected.", "pattern", ["ev-0"])
    result = await SynthesisEvaluator().evaluate(
        _ctx(
            _case(
                {
                    "expected_statements": [],
                    "expected_absent_statements": ["Must be rejected."],
                    "expected_rejections": 0,
                }
            ),
            [_evidence_env("ev-0", "A"), stmt, _exec_env()],
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "EXPECTED-ABSENT STATEMENTS PRESENT" in result.explanation


async def test_no_synthesis_artifacts_fails():
    result = await SynthesisEvaluator().evaluate(_ctx(_case({}), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None
