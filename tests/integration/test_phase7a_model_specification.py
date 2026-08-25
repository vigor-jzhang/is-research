"""Phase 7A offline integration — analytical-model-specification benchmark.

Fixture SelectedMechanism + scripted specs -> REAL Phase 3B ModelBuilderService
(8 pass + 1 by-design failure `model-missing-payoff`) + REAL
ModelSpecificationCriticService -> evaluator -> report.
"""

from __future__ import annotations

import pathlib

import pytest

from research_harness.plugins.research.evaluation_harness.plugin import (
    EvaluationHarnessService,
)
from research_harness.plugins.research.evaluator_model_specification.plugin import (
    ModelSpecificationEvaluator,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import ANALYTICAL_MODEL_SPECIFICATION_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase7a_model_specification_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=None,
        identity_resolver=None,
        evaluators={"evaluator.model_specification": ModelSpecificationEvaluator()},
        config={"evaluators": ["evaluator.model_specification"]},
    )
    await svc.register_benchmark(ANALYTICAL_MODEL_SPECIFICATION_V1)
    run_id, report_id = await svc.run_benchmark("analytical-model-specification-v1")

    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert report.cases_total == 9
    assert report.cases_passed == 8
    assert report.cases_failed == 1
    assert report.cases_error == 0

    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["model-valid-strategic"].status.value == "passed"
    assert by_case["model-undefined-symbol"].status.value == "passed"
    assert by_case["model-duplicate-symbol"].status.value == "passed"
    assert by_case["model-invalid-decision-ownership"].status.value == "passed"
    assert by_case["model-invalid-timing"].status.value == "passed"
    assert by_case["model-invalid-information-structure"].status.value == "passed"
    assert by_case["model-unsupported-literature-assumption"].status.value == "passed"
    assert by_case["model-critic-detects-mismatch"].status.value == "passed"
    # by-design deterministic failure: strategic actor without a payoff
    assert by_case["model-missing-payoff"].status.value == "failed"
    assert by_case["model-missing-payoff"].metrics["payoff_completeness"] == pytest.approx(0.5)

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["structural_validity_accuracy"].value == pytest.approx(1.0)
    assert metrics["symbol_table_accuracy"].value == pytest.approx(1.0)
    assert metrics["critic_issue_recall"].value == pytest.approx(1.0)

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"analytical-model-specification-v1"}

    # stable rerun + immutable historical run
    run2_id, _ = await svc.run_benchmark("analytical-model-specification-v1")
    assert run2_id != run_id
    assert (await store.get(report_id)).parse_payload(EvaluationReport).cases_passed == 8

    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    await reopened.close()
