"""Phase 7A.1 offline integration — gap-selection benchmark end to end.

Fixture GapAnalysis + ranked gaps -> REAL GapSelectionService (model selection,
operator override, autonomy checkpoint, deterministic fallback) -> evaluator ->
report. 8 cases, all pass.
"""

from __future__ import annotations

import pathlib

import pytest

from research_harness.plugins.research.evaluation_harness.plugin import (
    EvaluationHarnessService,
)
from research_harness.plugins.research.evaluator_gap_selection.plugin import (
    GapSelectionEvaluator,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import GAP_SELECTION_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase7a1_gap_selection_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=None,
        identity_resolver=None,
        evaluators={"evaluator.gap_selection": GapSelectionEvaluator()},
        config={"evaluators": ["evaluator.gap_selection"]},
    )
    await svc.register_benchmark(GAP_SELECTION_V1)
    run_id, report_id = await svc.run_benchmark("gap-selection-v1")

    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert report.cases_total == 8
    assert report.cases_passed == 8
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["selected_gap_validity"].value == pytest.approx(1.0)
    assert metrics["selection_rationale_grounding"].value == pytest.approx(1.0)
    assert metrics["alternative_consideration_accuracy"].value == pytest.approx(1.0)
    assert metrics["fallback_accuracy"].value == pytest.approx(1.0)
    assert metrics["autonomy_decision_accuracy"].value == pytest.approx(1.0)
    assert metrics["operator_override_accuracy"].value == pytest.approx(1.0)
    assert metrics["reuse_accuracy"].value == pytest.approx(1.0)

    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["gs-invalid-model-selection-fallback"].status.value == "passed"
    assert by_case["gs-autonomy-rejection"].status.value == "passed"
    assert by_case["gs-unsupported-gap-id-rejected"].status.value == "passed"
    assert by_case["gs-deterministic-rerun"].status.value == "passed"

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"gap-selection-v1"}

    # stable rerun + store reopen provenance
    run2_id, _ = await svc.run_benchmark("gap-selection-v1")
    assert run2_id != run_id
    assert (await store.get(report_id)).parse_payload(EvaluationReport).cases_passed == 8

    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    await reopened.close()
