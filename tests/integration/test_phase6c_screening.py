"""Phase 6C offline integration — literature-screening benchmark end to end.

Fixture papers -> REAL protocol builder (model + approval gate) -> REAL view
builder -> REAL title/abstract screener -> REAL orchestrator ->
ScreeningDecision + ScreenedLiteratureSet -> screening evaluator -> report.

Expected aggregate metrics (9 cases):
- screening_accuracy 8/8, include_precision 4/4, include_recall 4/4
- include_f1 mean 4/4, exclude_accuracy 2/2, uncertain_accuracy 2/2
- false_exclusion_rate 0/6, false_inclusion_rate 0/4
- review_trigger_accuracy 9/9, technical_failure_count 1
- case_pass_rate 9/9
"""

from __future__ import annotations

import pathlib

import pytest

from research_harness.plugins.literature.identity_resolver.plugin import (
    PaperIdentityResolverService,
)
from research_harness.plugins.literature.ingestion.plugin import LiteratureIngestor
from research_harness.plugins.research.evaluation_harness.plugin import (
    EvaluationHarnessService,
)
from research_harness.plugins.research.evaluator_screening.plugin import ScreeningEvaluator
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import LITERATURE_SCREENING_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase6c_screening_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        evaluators={"evaluator.screening": ScreeningEvaluator()},
        config={"evaluators": ["evaluator.screening"]},
    )
    await svc.register_benchmark(LITERATURE_SCREENING_V1)
    run_id, report_id = await svc.run_benchmark("literature-screening-v1")

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    report = (await store.get(report_id)).parse_payload(EvaluationReport)

    assert report.cases_total == 9
    assert report.cases_passed == 9
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["screening_accuracy"].value == pytest.approx(1.0)
    assert metrics["screening_accuracy"].count == 8
    assert metrics["include_precision"].value == pytest.approx(1.0)
    assert metrics["include_precision"].count == 4
    assert metrics["include_recall"].value == pytest.approx(1.0)
    assert metrics["include_recall"].count == 4
    assert metrics["include_f1"].value == pytest.approx(1.0)
    assert metrics["exclude_accuracy"].value == pytest.approx(1.0)
    assert metrics["exclude_accuracy"].count == 2
    assert metrics["uncertain_accuracy"].value == pytest.approx(1.0)
    assert metrics["uncertain_accuracy"].count == 2
    assert metrics["false_exclusion_rate"].value == 0.0
    assert metrics["false_exclusion_rate"].count == 6
    assert metrics["false_inclusion_rate"].value == 0.0
    assert metrics["false_inclusion_rate"].count == 4
    assert metrics["review_trigger_accuracy"].value == pytest.approx(1.0)
    assert metrics["review_trigger_accuracy"].count == 8
    assert metrics["technical_failure_count"].value == 1
    assert metrics["case_pass_rate"].value == pytest.approx(1.0)
    assert metrics["evaluator_error_count"].value == 0

    # the technical-failure case passed only because the failure was not an
    # exclusion
    by_case = {cr.case_id: cr for cr in report.case_results}
    failed_case = by_case["scr-technical-failure-not-exclusion"]
    assert failed_case.status.value == "passed"
    assert failed_case.metrics["technical_failure_count"] == 1.0
    assert by_case["scr-ambiguous-uncertain"].metrics["uncertain_accuracy"] == 1.0
    assert by_case["scr-low-confidence-review-trigger"].metrics["review_trigger_accuracy"] == 1.0
    assert by_case["scr-missing-abstract"].metrics["uncertain_accuracy"] == 1.0

    # provenance + reproducibility
    report_parents = await store.get_parents(report_id)
    assert {p.source_artifact_id for p in report_parents} == {run_id}
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"literature-screening-v1"}
    assert run.benchmark_content_hash
    assert len(run.case_hashes) == 9
    assert run.evaluator_versions["evaluator.screening"]

    # immutability: re-run produces identical outcomes
    run2_id, report2_id = await svc.run_benchmark("literature-screening-v1")
    assert run2_id != run_id
    report2 = (await store.get(report2_id)).parse_payload(EvaluationReport)
    assert report2.cases_passed == 9 and report2.cases_failed == 0
    old_report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert old_report.cases_total == 9

    # store reopen: provenance survives
    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    report3 = (await reopened.get(report_id)).parse_payload(EvaluationReport)
    assert report3.id == report_id
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    assert "literature-screening-v1" in {e.artifact_id for e in lineage}
    await reopened.close()
