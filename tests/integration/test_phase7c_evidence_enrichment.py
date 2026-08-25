"""Phase 7C offline integration — evidence-enrichment benchmark end to end.

Registers evidence-enrichment-v1 -> runs the REAL NoveltyValidationService with
enrichment + pre-acquisition enabled over fixture get()-capable sources ->
evaluator.evidence_enrichment -> aggregate report. 7 cases, all pass. Verifies
rerun stability and provenance-after-reopen.
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
from research_harness.plugins.research.evaluator_evidence_enrichment.plugin import (
    EvidenceEnrichmentEvaluator,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import EVIDENCE_ENRICHMENT_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


class NoopRouter:
    """Fallback router — never used because the benchmark serves fixtures."""

    async def complete(self, role, request):
        raise AssertionError("production router must not be called in an offline benchmark")

    def resolve(self, role):
        return {"provider": "fixture", "model": "fixture"}


@pytest.mark.asyncio
async def test_phase7c_evidence_enrichment_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        evaluators={"evaluator.evidence_enrichment": EvidenceEnrichmentEvaluator()},
        config={"evaluators": ["evaluator.evidence_enrichment"]},
    )
    await svc.register_benchmark(EVIDENCE_ENRICHMENT_V1)
    run_id, report_id = await svc.run_benchmark("evidence-enrichment-v1")

    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert report.cases_total == 7
    assert report.cases_passed == 7
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["enrichment_grounding_accuracy"].value == pytest.approx(1.0)
    assert metrics["enrichment_outcome_accuracy"].value == pytest.approx(1.0)
    assert metrics["source_preservation_accuracy"].value == pytest.approx(1.0)
    assert metrics["unsupported_rejection_accuracy"].value == pytest.approx(1.0)
    assert metrics["stale_reuse_rate"].value == pytest.approx(0.0)
    assert metrics["preacquisition_accuracy"].value == pytest.approx(1.0)
    assert metrics["provenance_version_accuracy"].value == pytest.approx(1.0)

    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["enc-unsupported-rejected"].status.value == "passed"
    assert by_case["enc-stale-not-reused"].status.value == "passed"
    assert by_case["enc-preacquisition-upgrades"].status.value == "passed"

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"evidence-enrichment-v1"}

    # stable rerun + provenance after reopen
    run2_id, _ = await svc.run_benchmark("evidence-enrichment-v1")
    assert run2_id != run_id
    assert (await store.get(report_id)).parse_payload(EvaluationReport).cases_passed == 7

    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    await reopened.close()
