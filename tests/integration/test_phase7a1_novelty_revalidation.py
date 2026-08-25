"""Phase 7A.1 offline integration — novelty-revalidation benchmark.

Real NoveltyValidationService.create_report twice (baseline + changed fixture
sources) -> evaluator -> report. 7 cases, all pass (no network / no model API).
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
from research_harness.plugins.research.evaluator_novelty_revalidation.plugin import (
    NoveltyRevalidationEvaluator,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import NOVELTY_REVALIDATION_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase7a1_novelty_revalidation_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        evaluators={"evaluator.novelty_revalidation": NoveltyRevalidationEvaluator()},
        config={"evaluators": ["evaluator.novelty_revalidation"]},
    )
    await svc.register_benchmark(NOVELTY_REVALIDATION_V1)
    run_id, report_id = await svc.run_benchmark("novelty-revalidation-v1")

    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert report.cases_total == 7
    assert report.cases_passed == 7
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["revalidation_trigger_accuracy"].value == pytest.approx(1.0)
    assert metrics["stale_reuse_rate"].value == pytest.approx(0.0)
    assert metrics["novelty_threat_detection_accuracy"].value == pytest.approx(1.0)
    assert metrics["irrelevant_update_accuracy"].value == pytest.approx(1.0)
    assert metrics["supersession_accuracy"].value == pytest.approx(1.0)
    assert metrics["provenance_version_accuracy"].value == pytest.approx(1.0)

    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["nvr-new-relevant-paper"].status.value == "passed"
    assert by_case["nvr-irrelevant-paper"].status.value == "passed"
    assert by_case["nvr-stale-not-silently-reused"].status.value == "passed"
    assert by_case["nvr-supersession-preserves-history"].status.value == "passed"

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"novelty-revalidation-v1"}

    # stable rerun + store reopen provenance
    run2_id, _ = await svc.run_benchmark("novelty-revalidation-v1")
    assert run2_id != run_id
    assert (await store.get(report_id)).parse_payload(EvaluationReport).cases_passed == 7

    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    await reopened.close()
