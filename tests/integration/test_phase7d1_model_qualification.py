"""Phase 7D.1/7D.2 offline integration — model-qualification benchmark end to end.

Runs model-qualification-policy-v1 (16 cases) over the real qualification
algorithm (incl. Phase 7D.2 defect-exclusion, stability, eligibility, and the
cross-role production-qualification matrix); unsafe_model_qualification_rate
must be 0. Verifies rerun stability and provenance after reopen.
"""

from __future__ import annotations

import pathlib

import pytest

from research_harness.plugins.research.evaluation_harness.plugin import (
    EvaluationHarnessService,
)
from research_harness.plugins.research.evaluator_model_qualification.plugin import (
    ModelQualificationEvaluator,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import MODEL_QUALIFICATION_POLICY_V1
from research_harness.research.schemas.evaluation import EvaluationReport


@pytest.mark.asyncio
async def test_phase7d1_model_qualification_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=None,
        identity_resolver=None,
        evaluators={"evaluator.model_qualification": ModelQualificationEvaluator()},
        config={"evaluators": ["evaluator.model_qualification"]},
    )
    await svc.register_benchmark(MODEL_QUALIFICATION_POLICY_V1)
    run_id, report_id = await svc.run_benchmark("model-qualification-policy-v1")

    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert report.cases_total == 16
    assert report.cases_passed == 16
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["unsafe_model_qualification_rate"].value == pytest.approx(0.0)
    assert metrics["qualification_decision_accuracy"].value == pytest.approx(1.0)
    assert metrics["rejection_classification_accuracy"].value == pytest.approx(1.0)
    assert metrics["role_isolation_accuracy"].value == pytest.approx(1.0)
    assert metrics["stability_classification_accuracy"].value == pytest.approx(1.0)
    assert metrics["eligibility_accuracy"].value == pytest.approx(1.0)

    # rerun stability + provenance after reopen
    run2_id, _ = await svc.run_benchmark("model-qualification-policy-v1")
    assert run2_id != run_id
    assert (await store.get(report_id)).parse_payload(EvaluationReport).cases_passed == 16

    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    await reopened.close()
