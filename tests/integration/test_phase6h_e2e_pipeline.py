"""Phase 6H offline integration — research-pipeline-e2e benchmark end to end.

Fixture corpus + scripted responses -> REAL production chain (retrieval ->
screening -> evidence -> synthesis -> gap -> mechanism -> model ->
equilibrium -> propositions -> numerical -> results -> manuscript ->
citation formatting) -> pipeline-integrity evaluator -> report.

Expected aggregate metrics (single case, passes):
- stage_completion_rate 13/13, provenance_integrity_rate 16/16
- grounding_integrity_rate 14/14, condition_preservation_rate 2/2
- citation_integrity_rate 1/1, bibliography_fidelity_rate 1/1
- deterministic_failure_count 0, end_to_end_pass 1.0
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
from research_harness.plugins.research.evaluator_pipeline_integrity.plugin import (
    PipelineIntegrityEvaluator,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.plugins.storage.blobs_filesystem.plugin import FilesystemBlobStore
from research_harness.research.benchmarks import RESEARCH_PIPELINE_E2E_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase6h_e2e_pipeline_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        evaluators={"evaluator.pipeline_integrity": PipelineIntegrityEvaluator()},
        config={"evaluators": ["evaluator.pipeline_integrity"]},
        blob_store=FilesystemBlobStore(root=tmp_path / "blobs"),
    )
    await svc.register_benchmark(RESEARCH_PIPELINE_E2E_V1)
    run_id, report_id = await svc.run_benchmark("research-pipeline-e2e-v1")

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    report = (await store.get(report_id)).parse_payload(EvaluationReport)

    assert report.cases_total == 1
    assert report.cases_passed == 1
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["stage_completion_rate"].value == pytest.approx(1.0)
    assert metrics["stage_completion_rate"].count == 13
    assert metrics["provenance_integrity_rate"].value == pytest.approx(1.0)
    assert metrics["provenance_integrity_rate"].count == 16
    assert metrics["grounding_integrity_rate"].value == pytest.approx(1.0)
    assert metrics["grounding_integrity_rate"].count == 14
    assert metrics["condition_preservation_rate"].value == pytest.approx(1.0)
    assert metrics["condition_preservation_rate"].count == 2
    assert metrics["citation_integrity_rate"].value == pytest.approx(1.0)
    assert metrics["citation_integrity_rate"].count == 1
    assert metrics["bibliography_fidelity_rate"].value == pytest.approx(1.0)
    assert metrics["bibliography_fidelity_rate"].count == 1
    assert metrics["deterministic_failure_count"].value == 0
    assert metrics["end_to_end_pass"].value == pytest.approx(1.0)
    assert metrics["case_pass_rate"].value == pytest.approx(1.0)
    assert metrics["evaluator_error_count"].value == 0

    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["e2e-research-pipeline"].status.value == "passed"

    # provenance + reproducibility
    report_parents = await store.get_parents(report_id)
    assert {p.source_artifact_id for p in report_parents} == {run_id}
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"research-pipeline-e2e-v1"}
    assert run.benchmark_content_hash
    assert len(run.case_hashes) == 1
    assert run.evaluator_versions["evaluator.pipeline_integrity"]

    # immutability + rerun stability
    run2_id, report2_id = await svc.run_benchmark("research-pipeline-e2e-v1")
    assert run2_id != run_id
    report2 = (await store.get(report2_id)).parse_payload(EvaluationReport)
    assert report2.cases_passed == 1 and report2.cases_failed == 0
    old_report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert old_report.cases_total == 1

    # store reopen: provenance survives
    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    report3 = (await reopened.get(report_id)).parse_payload(EvaluationReport)
    assert report3.id == report_id
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    assert "research-pipeline-e2e-v1" in {e.artifact_id for e in lineage}
