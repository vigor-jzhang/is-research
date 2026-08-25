"""Phase 7A.1 unit tests — gap-selection evaluator."""

from __future__ import annotations

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_gap_selection.plugin import (
    GapSelectionEvaluator,
)
from research_harness.research.benchmarks.workflows import GapSelectionReport
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus
from research_harness.research.schemas.mechanism import GapSelection, SelectionStatus


def _report(
    gap_ids: dict[str, str],
    selection_id: str | None,
    reuse: str | None = None,
    error: str | None = None,
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=GapSelectionReport(
            benchmark_case_id="c1",
            gap_ids=gap_ids,
            selection_id=selection_id,
            reuse_selection_id=reuse,
            error=error,
        ),
        artifact_type="gap_selection_report",
        producer="test",
    )


def _selection_env(
    sel_id: str,
    selected_gap: str,
    alternatives: list[str],
    *,
    status: str = "approved",
    selected_by: str = "model",
    approval_required: bool = False,
    rationale: str = "fixture rationale",
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=GapSelection(
            gap_analysis_id="ga",
            selected_gap_id=selected_gap,
            alternative_gap_ids=alternatives,
            selection_rationale=rationale,
            status=SelectionStatus(status),
            autonomy_mode="high" if not approval_required else "interactive",
            approval_required=approval_required,
            approval_decided_by="fixture" if approval_required else None,
            selected_by=selected_by,
        ),
        artifact_type="gap_selection",
        producer="test",
        artifact_id=sel_id,
    )


def _case(reference: dict) -> BenchmarkCase:
    return BenchmarkCase(
        id="c1",
        benchmark_id="b",
        version=1,
        name="case",
        input={},
        reference=reference,
        evaluation_dimensions=["gap_selection"],
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


async def test_rank1_selection_passes():
    gap_ids = {"gap-0": "g0", "gap-1": "g1", "gap-2": "g2"}
    ref = {
        "expected_selected_gap": "gap-0",
        "expected_status": "approved",
        "expected_selected_by": "model",
    }
    produced = [
        _report(gap_ids, "sel-1"),
        _selection_env("sel-1", "g0", ["g1", "g2"]),
    ]
    result = await GapSelectionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["selected_gap_validity"]["value"] == 1.0
    assert result.value["metrics"]["alternative_consideration_accuracy"]["value"] == 2.0


async def test_nonrank1_selection_passes():
    gap_ids = {"gap-0": "g0", "gap-1": "g1", "gap-2": "g2"}
    ref = {"expected_selected_gap": "gap-2", "expected_selected_by": "model"}
    produced = [
        _report(gap_ids, "sel-1"),
        _selection_env("sel-1", "g2", ["g0", "g1"]),
    ]
    result = await GapSelectionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.passed


async def test_operator_override_passes():
    gap_ids = {"gap-0": "g0", "gap-1": "g1", "gap-2": "g2"}
    ref = {"expected_selected_gap": "gap-1", "expected_selected_by": "operator"}
    produced = [
        _report(gap_ids, "sel-1"),
        _selection_env(
            "sel-1",
            "g1",
            ["g0", "g2"],
            selected_by="operator",
            rationale="Gap explicitly selected by operator: g1",
        ),
    ]
    result = await GapSelectionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["operator_override_accuracy"]["value"] == 1.0


async def test_fallback_passes():
    gap_ids = {"gap-0": "g0", "gap-1": "g1", "gap-2": "g2"}
    ref = {"expected_selected_gap": "gap-0", "expected_fallback": True}
    produced = [
        _report(gap_ids, "sel-1"),
        _selection_env(
            "sel-1",
            "g0",
            ["g1", "g2"],
            rationale="Model proposed unknown gap id 'ghost'; deterministic fallback to top-ranked gap.",
        ),
    ]
    result = await GapSelectionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["fallback_accuracy"]["value"] == 1.0


async def test_autonomy_rejection_passes():
    gap_ids = {"gap-0": "g0", "gap-1": "g1", "gap-2": "g2"}
    ref = {
        "expected_selected_gap": "gap-0",
        "expected_status": "rejected",
        "expected_approval_required": True,
    }
    produced = [
        _report(gap_ids, "sel-1"),
        _selection_env("sel-1", "g0", ["g1", "g2"], status="rejected", approval_required=True),
    ]
    result = await GapSelectionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["autonomy_decision_accuracy"]["value"] == 1.0


async def test_error_case_passes():
    gap_ids = {"gap-0": "g0", "gap-1": "g1", "gap-2": "g2"}
    ref = {"expected_error": True}
    produced = [_report(gap_ids, None, error="selected gap 'not-in-analysis' not among gaps")]
    result = await GapSelectionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.passed


async def test_reuse_passes():
    gap_ids = {"gap-0": "g0", "gap-1": "g1", "gap-2": "g2"}
    ref = {"expected_selected_gap": "gap-1", "expected_reuse": True}
    produced = [
        _report(gap_ids, "sel-1", reuse="sel-1"),
        _selection_env("sel-1", "g1", ["g0", "g2"]),
    ]
    result = await GapSelectionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["reuse_accuracy"]["value"] == 1.0


async def test_invalid_selection_fails():
    gap_ids = {"gap-0": "g0", "gap-1": "g1", "gap-2": "g2"}
    ref = {"expected_selected_gap": "gap-0"}
    produced = [_report(gap_ids, "sel-1"), _selection_env("sel-1", "g1", ["g0", "g2"])]
    result = await GapSelectionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.failed
    assert "SELECTED GAP MISMATCH" in result.explanation


async def test_no_report_fails():
    result = await GapSelectionEvaluator().evaluate(_ctx(_case({}), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None
