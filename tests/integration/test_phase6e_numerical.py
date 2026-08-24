"""Phase 6E offline integration — numerical-analysis benchmark end to end.

Fixture verified equilibrium -> REAL NumericalAnalysisService (deterministic
sweeps/grids/probes, feasibility + condition enforcement, robustness,
welfare) -> evaluator -> report.

Known expected metrics (9 cases, all pass):
- numerical_value_accuracy 4/4 (baseline values in cases 1 and 9)
- feasibility_classification_accuracy 2/2 (domain + condition reasons)
- condition_enforcement_accuracy 1/1
- sweep_accuracy 1/1, robustness_classification_accuracy 2/2
- welfare_accuracy 1/1, reproducibility_accuracy 1/1
- case_pass_rate 9/9
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
from research_harness.plugins.research.evaluator_numerical.plugin import NumericalEvaluator
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import NUMERICAL_ANALYSIS_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase6e_numerical_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        evaluators={"evaluator.numerical": NumericalEvaluator()},
        config={"evaluators": ["evaluator.numerical"]},
    )
    await svc.register_benchmark(NUMERICAL_ANALYSIS_V1)
    run_id, report_id = await svc.run_benchmark("numerical-analysis-v1")

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    report = (await store.get(report_id)).parse_payload(EvaluationReport)

    assert report.cases_total == 9
    assert report.cases_passed == 9
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["numerical_value_accuracy"].value == pytest.approx(1.0)
    assert metrics["numerical_value_accuracy"].count == 4
    assert metrics["feasibility_classification_accuracy"].value == pytest.approx(1.0)
    assert metrics["feasibility_classification_accuracy"].count == 2
    assert metrics["condition_enforcement_accuracy"].value == pytest.approx(1.0)
    assert metrics["sweep_accuracy"].value == pytest.approx(1.0)
    assert metrics["robustness_classification_accuracy"].value == pytest.approx(1.0)
    assert metrics["robustness_classification_accuracy"].count == 2
    assert metrics["welfare_accuracy"].value == pytest.approx(1.0)
    assert metrics["reproducibility_accuracy"].value == pytest.approx(1.0)
    assert metrics["case_pass_rate"].value == pytest.approx(1.0)
    assert metrics["evaluator_error_count"].value == 0

    # per-case spot checks
    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["num-baseline-evaluation"].status.value == "passed"
    assert by_case["num-baseline-evaluation"].metrics["numerical_value_accuracy"] == 1.0
    assert by_case["num-1d-sweep"].metrics["sweep_accuracy"] == 1.0
    assert (
        by_case["num-infeasible-domain-point"].metrics["feasibility_classification_accuracy"] == 1.0
    )
    assert (
        by_case["num-violated-equilibrium-condition"].metrics["condition_enforcement_accuracy"]
        == 1.0
    )
    assert by_case["num-proposition-supported"].metrics["robustness_classification_accuracy"] == 1.0
    assert by_case["num-proposition-violated"].metrics["robustness_classification_accuracy"] == 1.0
    assert by_case["num-welfare-calculation"].metrics["welfare_accuracy"] == 1.0
    assert by_case["num-deterministic-rerun"].metrics["reproducibility_accuracy"] == 1.0

    # provenance + reproducibility
    report_parents = await store.get_parents(report_id)
    assert {p.source_artifact_id for p in report_parents} == {run_id}
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"numerical-analysis-v1"}
    assert run.benchmark_content_hash
    assert len(run.case_hashes) == 9
    assert run.evaluator_versions["evaluator.numerical"]

    # immutability: re-run produces identical outcomes
    run2_id, report2_id = await svc.run_benchmark("numerical-analysis-v1")
    assert run2_id != run_id
    report2 = (await store.get(report2_id)).parse_payload(EvaluationReport)
    assert report2.cases_passed == 9 and report2.cases_failed == 0
    old_report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert old_report.cases_total == 9

    # store reopen: provenance survives
    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    report3 = (await reopened.get(report_id)).parse_payload(EvaluationReport)
    assert report3.id == report_id
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    assert "numerical-analysis-v1" in {e.artifact_id for e in lineage}
    await reopened.close()
