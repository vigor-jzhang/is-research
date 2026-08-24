"""Phase 6F offline integration — comparative-statics benchmark end to end.

Fixture verified equilibrium -> REAL ComparativeStaticsService -> ComparativeStatic
artifacts -> comparative-statics evaluator -> report.

Expected aggregate metrics (8 cases; the incorrect-expected-derivative case
fails by design):
- derivative_accuracy 11/12, sign_accuracy 12/12
- condition_preservation_accuracy 12/12, outcome_parameter_coverage 12/12
- ambiguous_sign_accuracy 2/2
- case_pass_rate 7/8
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
from research_harness.plugins.research.evaluator_comparative_statics.plugin import (
    ComparativeStaticsEvaluator,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import COMPARATIVE_STATICS_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase6f_comparative_statics_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        evaluators={"evaluator.comparative_statics": ComparativeStaticsEvaluator()},
        config={"evaluators": ["evaluator.comparative_statics"]},
    )
    await svc.register_benchmark(COMPARATIVE_STATICS_V1)
    run_id, report_id = await svc.run_benchmark("comparative-statics-v1")

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    report = (await store.get(report_id)).parse_payload(EvaluationReport)

    assert report.cases_total == 8
    assert report.cases_passed == 7
    assert report.cases_failed == 1
    assert report.cases_error == 0
    assert report.status.value == "failed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["derivative_accuracy"].value == pytest.approx(11 / 12)
    assert metrics["derivative_accuracy"].count == 12
    assert metrics["sign_accuracy"].value == pytest.approx(1.0)
    assert metrics["sign_accuracy"].count == 12
    assert metrics["condition_preservation_accuracy"].value == pytest.approx(1.0)
    assert metrics["condition_preservation_accuracy"].count == 12
    assert metrics["outcome_parameter_coverage"].value == pytest.approx(1.0)
    assert metrics["outcome_parameter_coverage"].count == 12
    assert metrics["ambiguous_sign_accuracy"].value == pytest.approx(1.0)
    assert metrics["ambiguous_sign_accuracy"].count == 2
    assert metrics["case_pass_rate"].value == pytest.approx(7 / 8)
    assert metrics["evaluator_error_count"].value == 0

    # per-case statuses
    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["cs-positive-derivative"].status.value == "passed"
    assert by_case["cs-negative-derivative"].status.value == "passed"
    assert by_case["cs-zero-derivative"].status.value == "passed"
    assert by_case["cs-ambiguous-sign"].status.value == "passed"
    assert by_case["cs-ambiguous-sign"].metrics["ambiguous_sign_accuracy"] == 1.0
    assert by_case["cs-conditions-recorded"].status.value == "passed"
    assert by_case["cs-conditions-recorded"].metrics["ambiguous_sign_accuracy"] == 1.0
    assert by_case["cs-multiple-outcomes-parameters"].status.value == "passed"
    assert by_case["cs-multiple-outcomes-parameters"].metrics["derivative_accuracy"] == 1.0
    assert by_case["cs-unused-parameter"].status.value == "passed"
    assert by_case["cs-incorrect-expected-derivative"].status.value == "failed"
    assert by_case["cs-incorrect-expected-derivative"].metrics["derivative_accuracy"] == 0.0

    # provenance + reproducibility
    report_parents = await store.get_parents(report_id)
    assert {p.source_artifact_id for p in report_parents} == {run_id}
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"comparative-statics-v1"}
    assert run.benchmark_content_hash
    assert len(run.case_hashes) == 8
    assert run.evaluator_versions["evaluator.comparative_statics"]

    # immutability: re-run produces identical outcomes
    run2_id, report2_id = await svc.run_benchmark("comparative-statics-v1")
    assert run2_id != run_id
    report2 = (await store.get(report2_id)).parse_payload(EvaluationReport)
    assert report2.cases_passed == 7 and report2.cases_failed == 1
    old_report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert old_report.cases_total == 8

    # store reopen: provenance survives
    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    report3 = (await reopened.get(report_id)).parse_payload(EvaluationReport)
    assert report3.id == report_id
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    assert "comparative-statics-v1" in {e.artifact_id for e in lineage}
