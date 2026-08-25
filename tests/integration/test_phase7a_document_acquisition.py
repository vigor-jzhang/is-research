"""Phase 7A offline integration — document-acquisition benchmark.

Fixture PaperIdentity + OA URLs + ScreenedLiteratureSet -> REAL metadata
locator -> REAL HttpFetcherService (mocked HTTP) -> REAL PypdfExtractorService
-> REAL acquisition orchestrator -> FullTextCorpus -> evaluator -> report.
8 cases, all pass (no network).
"""

from __future__ import annotations

import pathlib

import pytest

from research_harness.plugins.research.evaluation_harness.plugin import (
    EvaluationHarnessService,
)
from research_harness.plugins.research.evaluator_document_acquisition.plugin import (
    DocumentAcquisitionEvaluator,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.plugins.storage.blobs_filesystem.plugin import FilesystemBlobStore
from research_harness.research.benchmarks import DOCUMENT_ACQUISITION_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase7a_document_acquisition_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=None,
        identity_resolver=None,
        evaluators={"evaluator.document_acquisition": DocumentAcquisitionEvaluator()},
        config={"evaluators": ["evaluator.document_acquisition"]},
        blob_store=FilesystemBlobStore(root=tmp_path / "blobs"),
    )
    await svc.register_benchmark(DOCUMENT_ACQUISITION_V1)
    run_id, report_id = await svc.run_benchmark("document-acquisition-v1")

    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert report.cases_total == 8
    assert report.cases_passed == 8
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["acquisition_success_rate"].value == pytest.approx(0.5)
    assert metrics["failure_classification_accuracy"].value == pytest.approx(1.0)
    assert metrics["corpus_availability_accuracy"].value == pytest.approx(1.0)

    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["acq-valid-oa-pdf"].status.value == "passed"
    assert by_case["acq-fallback-location"].status.value == "passed"
    assert by_case["acq-html-masquerading-as-pdf"].status.value == "passed"
    assert by_case["acq-oversized-document"].status.value == "passed"
    assert by_case["acq-duplicate-blob"].status.value == "passed"
    assert by_case["acq-insufficient-extracted-text"].status.value == "passed"

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"document-acquisition-v1"}

    # stable rerun + store reopen provenance
    run2_id, _ = await svc.run_benchmark("document-acquisition-v1")
    assert run2_id != run_id
    assert (await store.get(report_id)).parse_payload(EvaluationReport).cases_passed == 8

    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    await reopened.close()
