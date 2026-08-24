"""Phase 6F offline integration — proposition-correctness benchmark end to end.

Fixture verified equilibrium -> REAL ComparativeStaticsService -> REAL
PropositionGeneratorService + PropositionVerifierService + PropositionCriticService
(scripted responses) -> proposition evaluator -> report.

Expected aggregate metrics (10 cases, all pass):
- proposition_verification_accuracy 10/10, monotonicity_accuracy 7/7
- equality_accuracy 2/2, condition_accuracy 1/1
- support_reference_accuracy 10/10
- incorrect_proposition_rejection_rate 5/10 (pooled over all cases)
- case_pass_rate 10/10
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
from research_harness.plugins.research.evaluator_proposition.plugin import PropositionEvaluator
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import PROPOSITION_CORRECTNESS_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase6f_proposition_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        evaluators={"evaluator.proposition": PropositionEvaluator()},
        config={"evaluators": ["evaluator.proposition"]},
    )
    await svc.register_benchmark(PROPOSITION_CORRECTNESS_V1)
    run_id, report_id = await svc.run_benchmark("proposition-correctness-v1")

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    report = (await store.get(report_id)).parse_payload(EvaluationReport)

    assert report.cases_total == 10
    assert report.cases_passed == 10
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["proposition_verification_accuracy"].value == pytest.approx(1.0)
    assert metrics["proposition_verification_accuracy"].count == 10
    assert metrics["monotonicity_accuracy"].value == pytest.approx(1.0)
    assert metrics["monotonicity_accuracy"].count == 7
    assert metrics["equality_accuracy"].value == pytest.approx(1.0)
    assert metrics["equality_accuracy"].count == 2
    assert metrics["condition_accuracy"].value == pytest.approx(1.0)
    assert metrics["condition_accuracy"].count == 1
    assert metrics["support_reference_accuracy"].value == pytest.approx(1.0)
    assert metrics["support_reference_accuracy"].count == 10
    assert metrics["incorrect_proposition_rejection_rate"].value == pytest.approx(0.5)
    assert metrics["incorrect_proposition_rejection_rate"].count == 10
    assert metrics["case_pass_rate"].value == pytest.approx(1.0)
    assert metrics["evaluator_error_count"].value == 0

    # per-case statuses
    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["prop-positive-monotonicity"].status.value == "passed"
    assert by_case["prop-positive-monotonicity"].metrics["monotonicity_accuracy"] == 1.0
    assert by_case["prop-negative-monotonicity"].status.value == "passed"
    assert by_case["prop-zero-effect"].status.value == "passed"
    assert by_case["prop-conditional"].status.value == "passed"
    assert by_case["prop-conditional"].metrics["condition_accuracy"] == 1.0
    assert by_case["prop-wrong-sign-rejected"].status.value == "passed"
    assert (
        by_case["prop-wrong-sign-rejected"].metrics["incorrect_proposition_rejection_rate"] == 1.0
    )
    assert by_case["prop-missing-condition-rejected"].status.value == "passed"
    assert by_case["prop-valid-equality"].status.value == "passed"
    assert by_case["prop-valid-equality"].metrics["equality_accuracy"] == 1.0
    assert by_case["prop-invalid-equality"].status.value == "passed"
    assert by_case["prop-invalid-equality"].metrics["incorrect_proposition_rejection_rate"] == 1.0
    assert by_case["prop-hallucinated-support"].status.value == "passed"
    assert by_case["prop-hallucinated-support"].metrics["support_reference_accuracy"] == 1.0
    assert by_case["prop-unsupported-threshold"].status.value == "passed"
    assert (
        by_case["prop-unsupported-threshold"].metrics["incorrect_proposition_rejection_rate"] == 1.0
    )

    # provenance + reproducibility
    report_parents = await store.get_parents(report_id)
    assert {p.source_artifact_id for p in report_parents} == {run_id}
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"proposition-correctness-v1"}
    assert run.benchmark_content_hash
    assert len(run.case_hashes) == 10
    assert run.evaluator_versions["evaluator.proposition"]

    # immutability: re-run produces identical outcomes
    run2_id, report2_id = await svc.run_benchmark("proposition-correctness-v1")
    assert run2_id != run_id
    report2 = (await store.get(report2_id)).parse_payload(EvaluationReport)
    assert report2.cases_passed == 10 and report2.cases_failed == 0
    old_report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert old_report.cases_total == 10

    # store reopen: provenance survives
    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    report3 = (await reopened.get(report_id)).parse_payload(EvaluationReport)
    assert report3.id == report_id
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    assert "proposition-correctness-v1" in {e.artifact_id for e in lineage}
