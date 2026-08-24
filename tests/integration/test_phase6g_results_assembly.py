"""Phase 6G offline integration — results-assembly benchmark end to end.

Fixture verified Phase 3 outputs -> REAL ResultsAssemblerService + REAL
ResultsCriticService -> results-grounding evaluator -> report.

Expected aggregate metrics (10 cases; the unsupported-managerial-implication
case fails by design):
- finding_grounding_accuracy 10/10, condition_preservation_accuracy 10/10
- proposition_support_accuracy 9/9, numerical_support_accuracy 1/1
- contribution_gap_alignment_accuracy 10/10, novelty_claim_accuracy 10/10
- implication_grounding_accuracy 9/10
- contradiction_detection_accuracy 1/10 (pooled per-case)
- unsupported_claim_rate 1/20, case_pass_rate 9/10
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
from research_harness.plugins.research.evaluator_results_grounding.plugin import (
    ResultsGroundingEvaluator,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import RESULTS_ASSEMBLY_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase6g_results_assembly_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        evaluators={"evaluator.results_grounding": ResultsGroundingEvaluator()},
        config={"evaluators": ["evaluator.results_grounding"]},
    )
    await svc.register_benchmark(RESULTS_ASSEMBLY_V1)
    run_id, report_id = await svc.run_benchmark("results-assembly-v1")

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    report = (await store.get(report_id)).parse_payload(EvaluationReport)

    assert report.cases_total == 10
    assert report.cases_passed == 9
    assert report.cases_failed == 1
    assert report.cases_error == 0
    assert report.status.value == "failed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["finding_grounding_accuracy"].value == pytest.approx(1.0)
    assert metrics["finding_grounding_accuracy"].count == 10
    assert metrics["condition_preservation_accuracy"].value == pytest.approx(1.0)
    assert metrics["condition_preservation_accuracy"].count == 10
    assert metrics["proposition_support_accuracy"].value == pytest.approx(1.0)
    assert metrics["proposition_support_accuracy"].count == 9
    assert metrics["numerical_support_accuracy"].value == pytest.approx(1.0)
    assert metrics["numerical_support_accuracy"].count == 1
    assert metrics["contribution_gap_alignment_accuracy"].value == pytest.approx(1.0)
    assert metrics["contribution_gap_alignment_accuracy"].count == 10
    assert metrics["implication_grounding_accuracy"].value == pytest.approx(9 / 10)
    assert metrics["implication_grounding_accuracy"].count == 10
    assert metrics["novelty_claim_accuracy"].value == pytest.approx(1.0)
    assert metrics["novelty_claim_accuracy"].count == 10
    assert metrics["contradiction_detection_accuracy"].value == pytest.approx(1 / 10)
    assert metrics["contradiction_detection_accuracy"].count == 10
    assert metrics["unsupported_claim_rate"].value == pytest.approx(1 / 20)
    assert metrics["unsupported_claim_rate"].count == 20
    assert metrics["case_pass_rate"].value == pytest.approx(9 / 10)
    assert metrics["evaluator_error_count"].value == 0

    # per-case statuses
    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["res-grounded-analytical-finding"].status.value == "passed"
    assert by_case["res-grounded-analytical-finding"].metrics["finding_grounding_accuracy"] == 1.0
    assert by_case["res-conditional-conditions-preserved"].status.value == "passed"
    assert (
        by_case["res-conditional-conditions-preserved"].metrics["condition_preservation_accuracy"]
        == 1.0
    )
    assert by_case["res-numerical-robustness-support"].status.value == "passed"
    assert by_case["res-numerical-robustness-support"].metrics["numerical_support_accuracy"] == 1.0
    assert by_case["res-symbolic-numerical-contradiction"].status.value == "passed"
    assert (
        by_case["res-symbolic-numerical-contradiction"].metrics["contradiction_detection_accuracy"]
        == 1.0
    )
    assert by_case["res-failed-proposition-rejected"].status.value == "passed"
    assert by_case["res-failed-proposition-rejected"].metrics["proposition_support_accuracy"] == 1.0
    assert by_case["res-unsupported-artifact-id-rejected"].status.value == "passed"
    assert by_case["res-valid-theoretical-contribution"].status.value == "passed"
    assert (
        by_case["res-valid-theoretical-contribution"].metrics["contribution_gap_alignment_accuracy"]
        == 1.0
    )
    assert by_case["res-weak-gap-contribution-link"].status.value == "passed"
    assert by_case["res-global-novelty-normalized"].status.value == "passed"
    assert by_case["res-global-novelty-normalized"].metrics["novelty_claim_accuracy"] == 1.0
    assert by_case["res-unsupported-managerial-implication"].status.value == "failed"
    assert (
        by_case["res-unsupported-managerial-implication"].metrics["implication_grounding_accuracy"]
        == 0.0
    )

    # provenance + reproducibility
    report_parents = await store.get_parents(report_id)
    assert {p.source_artifact_id for p in report_parents} == {run_id}
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"results-assembly-v1"}
    assert run.benchmark_content_hash
    assert len(run.case_hashes) == 10
    assert run.evaluator_versions["evaluator.results_grounding"]

    # immutability: re-run produces identical outcomes
    run2_id, report2_id = await svc.run_benchmark("results-assembly-v1")
    assert run2_id != run_id
    report2 = (await store.get(report2_id)).parse_payload(EvaluationReport)
    assert report2.cases_passed == 9 and report2.cases_failed == 1
    old_report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert old_report.cases_total == 10

    # store reopen: provenance survives
    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    report3 = (await reopened.get(report_id)).parse_payload(EvaluationReport)
    assert report3.id == report_id
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    assert "results-assembly-v1" in {e.artifact_id for e in lineage}
