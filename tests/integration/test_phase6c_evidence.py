"""Phase 6C offline integration — evidence-extraction benchmark end to end.

Fixture full-text documents (blob-backed pages) -> REAL extractor +
orchestrator -> EvidenceItem / PaperResearchProfile / EvidenceCorpus ->
evidence evaluator -> report.

Expected aggregate metrics (9 cases):
- evidence_precision 7/8, evidence_recall 7/8
- evidence_f1 mean per-case 5/7
- category_accuracy 7/7, locator_accuracy 7/7
- required_evidence_recall 7/8
- unsupported_evidence_rate 1/8, duplicate_evidence_rate 0/8
- documents_with_required_evidence_missed 1, chunk_failure_count 2
- case_pass_rate 7/9 (unsupported-claim and missing-evidence fail)
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
from research_harness.plugins.research.evaluator_evidence.plugin import EvidenceEvaluator
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.plugins.storage.blobs_filesystem.plugin import FilesystemBlobStore
from research_harness.research.benchmarks import EVIDENCE_EXTRACTION_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase6c_evidence_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        evaluators={"evaluator.evidence": EvidenceEvaluator()},
        config={"evaluators": ["evaluator.evidence"]},
        blob_store=blobs,
    )
    await svc.register_benchmark(EVIDENCE_EXTRACTION_V1)
    run_id, report_id = await svc.run_benchmark("evidence-extraction-v1")

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    report = (await store.get(report_id)).parse_payload(EvaluationReport)

    assert report.cases_total == 9
    assert report.cases_passed == 7
    assert report.cases_failed == 2
    assert report.cases_error == 0
    assert report.status.value == "failed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["evidence_precision"].value == pytest.approx(7 / 8)
    assert metrics["evidence_precision"].count == 8
    assert metrics["evidence_recall"].value == pytest.approx(7 / 8)
    assert metrics["evidence_recall"].count == 8
    assert metrics["required_evidence_recall"].value == pytest.approx(7 / 8)
    assert metrics["required_evidence_recall"].count == 8
    assert metrics["evidence_f1"].value == pytest.approx(5 / 7)
    assert metrics["category_accuracy"].value == pytest.approx(1.0)
    assert metrics["category_accuracy"].count == 7
    assert metrics["locator_accuracy"].value == pytest.approx(1.0)
    assert metrics["locator_accuracy"].count == 7
    assert metrics["unsupported_evidence_rate"].value == pytest.approx(1 / 8)
    assert metrics["unsupported_evidence_rate"].count == 8
    assert metrics["duplicate_evidence_rate"].value == 0.0
    assert metrics["documents_with_required_evidence_missed"].value == 1
    assert metrics["chunk_failure_count"].value == 2
    assert metrics["case_pass_rate"].value == pytest.approx(7 / 9)
    assert metrics["evaluator_error_count"].value == 0

    # per-case statuses
    by_case = {cr.case_id: cr for cr in report.case_results}
    for cid in (
        "ev-single-page-finding",
        "ev-multi-page-evidence",
        "ev-multiple-categories",
        "ev-similar-text-wrong-page",
        "ev-duplicate-evidence",
        "ev-partial-chunk-failure",
        "ev-insufficient-text-document",
    ):
        assert by_case[cid].status.value == "passed", cid
    for cid in ("ev-unsupported-claim", "ev-missing-evidence"):
        assert by_case[cid].status.value == "failed", cid
    assert by_case["ev-similar-text-wrong-page"].metrics["chunk_failure_count"] == 1.0
    assert by_case["ev-partial-chunk-failure"].metrics["chunk_failure_count"] == 1.0
    assert (
        by_case["ev-insufficient-text-document"].metrics["documents_with_required_evidence_missed"]
        == 0.0
    )

    # provenance + reproducibility
    report_parents = await store.get_parents(report_id)
    assert {p.source_artifact_id for p in report_parents} == {run_id}
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"evidence-extraction-v1"}
    assert run.benchmark_content_hash
    assert len(run.case_hashes) == 9
    assert run.evaluator_versions["evaluator.evidence"]

    # immutability: re-run produces identical outcomes (fixture documents are
    # run-unique so the global evidence dedup cannot stale the run)
    run2_id, report2_id = await svc.run_benchmark("evidence-extraction-v1")
    assert run2_id != run_id
    report2 = (await store.get(report2_id)).parse_payload(EvaluationReport)
    assert report2.cases_passed == 7 and report2.cases_failed == 2
    old_report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert old_report.cases_total == 9

    # store reopen: provenance survives
    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    report3 = (await reopened.get(report_id)).parse_payload(EvaluationReport)
    assert report3.id == report_id
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    assert "evidence-extraction-v1" in {e.artifact_id for e in lineage}
    await reopened.close()
