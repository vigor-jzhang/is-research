"""Phase 7A.1 offline integration — ingestion + identity benchmark end to end.

Fixture provider sources -> REAL LiteratureIngestor -> REAL PaperIdentityResolver
-> evaluator -> report. 8 cases, all pass.
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
from research_harness.plugins.research.evaluator_identity_resolution.plugin import (
    IdentityResolutionEvaluator,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import LITERATURE_INGESTION_IDENTITY_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase7a1_ingestion_identity_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        evaluators={"evaluator.identity_resolution": IdentityResolutionEvaluator()},
        config={"evaluators": ["evaluator.identity_resolution"]},
    )
    await svc.register_benchmark(LITERATURE_INGESTION_IDENTITY_V1)
    run_id, report_id = await svc.run_benchmark("literature-ingestion-identity-v1")

    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert report.cases_total == 8
    assert report.cases_passed == 8
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["canonical_mapping_accuracy"].value == pytest.approx(1.0)
    assert metrics["duplicate_collapse_accuracy"].value == pytest.approx(1.0)
    assert metrics["false_merge_rate"].value == pytest.approx(0.0)
    assert metrics["false_split_rate"].value == pytest.approx(0.0)
    assert metrics["identifier_normalization_accuracy"].value == pytest.approx(1.0)
    assert metrics["supersession_accuracy"].value == pytest.approx(1.0)
    assert metrics["partial_ingestion_accuracy"].value == pytest.approx(1.0)

    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["ing-same-doi-across-providers"].status.value == "passed"
    assert by_case["ing-similar-title-no-strong-id"].status.value == "passed"
    assert by_case["ing-identity-supersession"].status.value == "passed"

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"literature-ingestion-identity-v1"}
    assert run.benchmark_content_hash
    assert len(run.case_hashes) == 8

    # stable rerun + store reopen provenance
    run2_id, _ = await svc.run_benchmark("literature-ingestion-identity-v1")
    assert run2_id != run_id
    assert (await store.get(report_id)).parse_payload(EvaluationReport).cases_passed == 8

    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    await reopened.close()
