"""Phase 7A offline integration — incremental-revalidation benchmark.

Drives REAL production services twice per stage (baseline + changed upstream)
and verifies immutable recomputation vs deterministic reuse across screening,
screening views, evidence, synthesis, gap analysis, and equilibrium.
7 cases, all pass.
"""

from __future__ import annotations

import pathlib

import pytest

from research_harness.plugins.research.evaluation_harness.plugin import (
    EvaluationHarnessService,
)
from research_harness.plugins.research.evaluator_revalidation.plugin import (
    RevalidationEvaluator,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.plugins.storage.blobs_filesystem.plugin import FilesystemBlobStore
from research_harness.research.benchmarks import INCREMENTAL_REVALIDATION_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase7a_revalidation_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=None,
        identity_resolver=None,
        evaluators={"evaluator.revalidation": RevalidationEvaluator()},
        config={"evaluators": ["evaluator.revalidation"]},
        blob_store=FilesystemBlobStore(root=tmp_path / "blobs"),
    )
    await svc.register_benchmark(INCREMENTAL_REVALIDATION_V1)
    run_id, report_id = await svc.run_benchmark("incremental-revalidation-v1")

    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert report.cases_total == 7
    assert report.cases_passed == 7
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["stale_reuse_rate"].value == pytest.approx(0.0)
    assert metrics["required_recomputation_accuracy"].value == pytest.approx(1.0)
    assert metrics["unchanged_reuse_accuracy"].value == pytest.approx(1.0)
    assert metrics["provenance_version_accuracy"].value == pytest.approx(1.0)

    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["rev-new-protocol-new-decisions"].status.value == "passed"
    assert by_case["rev-superseding-identity-new-view"].status.value == "passed"
    assert by_case["rev-model-config-new-evidence"].status.value == "passed"
    assert by_case["rev-changed-corpus-new-synthesis"].status.value == "passed"
    assert by_case["rev-changed-synthesis-new-gap"].status.value == "passed"
    assert by_case["rev-changed-model-new-equilibrium"].status.value == "passed"
    assert by_case["rev-unchanged-deterministic-reuse"].status.value == "passed"

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"incremental-revalidation-v1"}

    # stable rerun + store reopen provenance
    run2_id, _ = await svc.run_benchmark("incremental-revalidation-v1")
    assert run2_id != run_id
    assert (await store.get(report_id)).parse_payload(EvaluationReport).cases_passed == 7

    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    await reopened.close()
