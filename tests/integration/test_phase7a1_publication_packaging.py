"""Phase 7A.1 offline integration — publication-packaging benchmark.

Fixture papers + ManuscriptDraft sections -> REAL Phase 4C formatter ->
bibliography -> validate -> exporters (BlobStore) -> SubmissionPackage ->
evaluator -> report. 8 cases, all pass.
"""

from __future__ import annotations

import pathlib

import pytest

from research_harness.plugins.research.evaluation_harness.plugin import (
    EvaluationHarnessService,
)
from research_harness.plugins.research.evaluator_publication_packaging.plugin import (
    PublicationPackagingEvaluator,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.plugins.storage.blobs_filesystem.plugin import FilesystemBlobStore
from research_harness.research.benchmarks import PUBLICATION_PACKAGING_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase7a1_publication_packaging_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=None,
        identity_resolver=None,
        evaluators={"evaluator.publication_packaging": PublicationPackagingEvaluator()},
        config={"evaluators": ["evaluator.publication_packaging"]},
        blob_store=FilesystemBlobStore(root=tmp_path / "blobs"),
    )
    await svc.register_benchmark(PUBLICATION_PACKAGING_V1)
    run_id, report_id = await svc.run_benchmark("publication-packaging-v1")

    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert report.cases_total == 8
    assert report.cases_passed == 8
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["package_validation_accuracy"].value == pytest.approx(1.0)
    assert metrics["export_success_accuracy"].value == pytest.approx(1.0)
    assert metrics["bibliography_integrity"].value == pytest.approx(1.0)
    assert metrics["placeholder_removal_accuracy"].value == pytest.approx(1.0)
    assert metrics["anonymization_accuracy"].value == pytest.approx(1.0)
    assert metrics["blob_persistence_accuracy"].value == pytest.approx(1.0)
    assert metrics["deterministic_render_accuracy"].value == pytest.approx(1.0)

    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["pkg-correct-citation-resolution"].status.value == "passed"
    assert by_case["pkg-unresolved-citation-blocks-ready"].status.value == "passed"
    assert by_case["pkg-bibliography-dedup"].status.value == "passed"
    assert by_case["pkg-anonymous-review-mode"].status.value == "passed"
    assert by_case["pkg-markdown-latex-docx-pdf-exports"].status.value == "passed"
    assert by_case["pkg-invalid-not-publication-ready"].status.value == "passed"

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"publication-packaging-v1"}

    # stable rerun + store reopen provenance
    run2_id, _ = await svc.run_benchmark("publication-packaging-v1")
    assert run2_id != run_id
    assert (await store.get(report_id)).parse_payload(EvaluationReport).cases_passed == 8

    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    await reopened.close()
