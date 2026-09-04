"""Unit tests for the live-quality fast/screening evaluator (M81).

The evaluator reports ``uncertain_case_handling`` as a rate: of the cases
expected to be uncertain, how many were left uncertain. The counting that fed
it was broken in two ways at once, so it was never a rate.
"""

from __future__ import annotations

import pytest

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_live_quality_fast.plugin import (
    LiveQualityFastEvaluator,
)
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase
from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
from research_harness.research.schemas.paper import PaperRecord
from research_harness.research.schemas.screening_decision import (
    ScreeningDecision,
    ScreeningDecisionEnum,
)


def _case(reference: dict) -> BenchmarkCase:
    return BenchmarkCase(
        id="c1",
        benchmark_id="b",
        version=1,
        name="fast screening",
        input={},
        reference=reference,
        evaluation_dimensions=["screening"],
        tags=[],
    )


def _identity(identity_id: str, title: str) -> list[ArtifactEnvelope]:
    rec = ArtifactEnvelope.create(
        payload=PaperRecord(title=title),
        artifact_type="paper_record",
        producer="test",
        artifact_id=f"rec-{identity_id}",
    )
    ident = ArtifactEnvelope.create(
        payload=PaperIdentity(
            member_paper_artifact_ids=[rec.artifact_id],
            resolution_method=ResolutionMethod.exact_identifier,
        ),
        artifact_type="paper_identity",
        producer="test",
        artifact_id=identity_id,
    )
    return [rec, ident]


def _decision(identity_id: str, decision: str) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=ScreeningDecision(
            paper_identity_id=identity_id,
            screening_view_id="view-1",
            screening_protocol_id="protocol-1",
            decision=ScreeningDecisionEnum(decision),
            rationale_summary="fixture",
            confidence=1.0,
        ),
        artifact_type="screening_decision",
        producer="test",
    )


def _ctx(case: BenchmarkCase, produced: list[ArtifactEnvelope]) -> EvaluatorContext:
    return EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=produced,
        config={},
    )


@pytest.mark.asyncio
async def test_uncertain_case_handling_is_a_proportion_not_a_step_function():
    """M81: three expected-uncertain cases, two handled correctly => 2/3.

    Before this, ``uncertain_expected`` was incremented only on mismatch and
    ``uncertain_handled`` was unreachable, so the value was 1.0 when nothing
    was mishandled and 0.0 otherwise -- never a proportion.
    """
    produced: list[ArtifactEnvelope] = []
    expected = {}
    decisions = ["uncertain", "uncertain", "exclude"]
    for i, decision in enumerate(decisions):
        ident_id = f"id-{i}"
        title = f"Paper {i}"
        produced.extend(_identity(ident_id, title))
        produced.append(_decision(ident_id, decision))
        expected[title] = "uncertain"

    case = _case({"expected_decisions": expected, "required_decision_accuracy": 0.5})
    result = await LiveQualityFastEvaluator().evaluate(_ctx(case, produced))

    metric = result.value["metrics"]["uncertain_case_handling"]
    assert metric["count"] == 3, "expected-uncertain cases are the denominator"
    assert metric["value"] == pytest.approx(2 / 3)


@pytest.mark.asyncio
async def test_uncertain_case_handling_is_perfect_when_all_kept_uncertain():
    produced: list[ArtifactEnvelope] = []
    expected = {}
    for i in range(2):
        ident_id = f"id-{i}"
        title = f"Paper {i}"
        produced.extend(_identity(ident_id, title))
        produced.append(_decision(ident_id, "uncertain"))
        expected[title] = "uncertain"

    case = _case({"expected_decisions": expected, "required_decision_accuracy": 0.5})
    result = await LiveQualityFastEvaluator().evaluate(_ctx(case, produced))

    metric = result.value["metrics"]["uncertain_case_handling"]
    assert metric["count"] == 2
    assert metric["value"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_no_expected_uncertain_cases_reports_nothing_measured():
    """With no expected-uncertain cases the rate must not claim a measurement."""
    produced = _identity("id-0", "Paper 0")
    produced.append(_decision("id-0", "include"))
    case = _case({"expected_decisions": {"Paper 0": "include"}, "required_decision_accuracy": 1.0})
    result = await LiveQualityFastEvaluator().evaluate(_ctx(case, produced))

    metric = result.value["metrics"]["uncertain_case_handling"]
    assert metric["count"] == 0
