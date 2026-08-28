"""Phase 7D.3 offline integration — task-specific model-qualification benchmark.

Runs task-specific-model-qualification-v1 (15 cases, incl. the Phase 7D.3B
policy cases: provider-unavailable-not-model-quality, denominator includes all
exercised cases, unexercised task never silently qualified, per-task
primary/fallback, role/task isolation) over the real task-qualification
algorithm; unsafe_task_qualification_rate must be 0. Verifies rerun stability
and provenance after reopen.
"""

from __future__ import annotations

import pathlib

import pytest

from research_harness.plugins.research.evaluation_harness.plugin import (
    EvaluationHarnessService,
)
from research_harness.plugins.research.evaluator_task_model_qualification.plugin import (
    TaskModelQualificationEvaluator,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import TASK_SPECIFIC_MODEL_QUALIFICATION_V1
from research_harness.research.schemas.evaluation import EvaluationReport

_TASK_QUALIFICATION_CASE_COUNT = len(TASK_SPECIFIC_MODEL_QUALIFICATION_V1.cases)


@pytest.mark.asyncio
async def test_task_specific_qualification_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=None,
        identity_resolver=None,
        evaluators={"evaluator.task_model_qualification": TaskModelQualificationEvaluator()},
        config={"evaluators": ["evaluator.task_model_qualification"]},
    )
    await svc.register_benchmark(TASK_SPECIFIC_MODEL_QUALIFICATION_V1)
    run_id, report_id = await svc.run_benchmark("task-specific-model-qualification-v1")

    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert report.cases_total == _TASK_QUALIFICATION_CASE_COUNT
    assert report.cases_passed == _TASK_QUALIFICATION_CASE_COUNT
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["unsafe_task_qualification_rate"].value == pytest.approx(0.0)
    assert metrics["task_qualification_accuracy"].value == pytest.approx(1.0)
    assert metrics["role_task_consistency_accuracy"].value == pytest.approx(1.0)
    assert metrics["ranking_accuracy"].value == pytest.approx(1.0)

    # rerun stability + provenance after reopen
    run2_id, _ = await svc.run_benchmark("task-specific-model-qualification-v1")
    assert run2_id != run_id
    assert (await store.get(report_id)).parse_payload(
        EvaluationReport
    ).cases_passed == _TASK_QUALIFICATION_CASE_COUNT

    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    await reopened.close()
