"""Phase 6E offline integration — equilibrium-correctness benchmark end to end.

Fixture FormalAnalyticalModel -> REAL EquilibriumDeriverService (SymPy
derivation + symbolic verification + bounded LLM revision) -> evaluator ->
report.

Known expected metrics (10 cases, all pass):
- equilibrium_expression_accuracy 8/8 (6 cases with 8 known-answer variables)
- foc_accuracy 1.0, best_response_accuracy 1.0
- verification_accuracy 10/10, solution_order_accuracy 11/11
- condition_accuracy 3/3 (parameter-conditioned, partial-verification,
  denominator/positivity cases)
- unsolvable_detection_accuracy 10/10
- incorrect_candidate_rejection_rate pooled 5/12 (1+1+3 rejected over max-count)
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
from research_harness.plugins.research.evaluator_equilibrium.plugin import EquilibriumEvaluator
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import EQUILIBRIUM_CORRECTNESS_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase6e_equilibrium_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        evaluators={"evaluator.equilibrium": EquilibriumEvaluator()},
        config={"evaluators": ["evaluator.equilibrium"]},
    )
    await svc.register_benchmark(EQUILIBRIUM_CORRECTNESS_V1)
    run_id, report_id = await svc.run_benchmark("equilibrium-correctness-v1")

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    report = (await store.get(report_id)).parse_payload(EvaluationReport)

    assert report.cases_total == 10
    assert report.cases_passed == 10
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["equilibrium_expression_accuracy"].value == pytest.approx(1.0)
    assert metrics["equilibrium_expression_accuracy"].count == 8
    assert metrics["foc_accuracy"].value == pytest.approx(1.0)
    assert metrics["best_response_accuracy"].value == pytest.approx(1.0)
    assert metrics["verification_accuracy"].value == pytest.approx(1.0)
    assert metrics["verification_accuracy"].count == 10
    assert metrics["solution_order_accuracy"].value == pytest.approx(1.0)
    assert metrics["solution_order_accuracy"].count == 11
    assert metrics["condition_accuracy"].value == pytest.approx(1.0)
    assert metrics["condition_accuracy"].count == 3
    assert metrics["unsolvable_detection_accuracy"].value == pytest.approx(1.0)
    assert metrics["incorrect_candidate_rejection_rate"].value == pytest.approx(5 / 12)
    assert metrics["case_pass_rate"].value == pytest.approx(1.0)
    assert metrics["evaluator_error_count"].value == 0

    # per-case spot checks
    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["eq-sequential-leader-follower"].status.value == "passed"
    assert by_case["eq-sequential-leader-follower"].metrics["solution_order_accuracy"] == 1.0
    assert by_case["eq-incorrect-llm-candidate"].status.value == "passed"
    assert by_case["eq-bounded-revision"].status.value == "passed"
    assert by_case["eq-zero-payoff-unsolvable"].status.value == "passed"
    assert by_case["eq-parameter-conditioned"].status.value == "passed"
    assert by_case["eq-parameter-conditioned"].metrics["condition_accuracy"] == 1.0
    assert by_case["eq-invalid-foc"].metrics["incorrect_candidate_rejection_rate"] == 1.0

    # provenance + reproducibility
    report_parents = await store.get_parents(report_id)
    assert {p.source_artifact_id for p in report_parents} == {run_id}
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"equilibrium-correctness-v1"}
    assert run.benchmark_content_hash
    assert len(run.case_hashes) == 10
    assert run.evaluator_versions["evaluator.equilibrium"]

    # immutability: re-run produces identical outcomes
    run2_id, report2_id = await svc.run_benchmark("equilibrium-correctness-v1")
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
    assert "equilibrium-correctness-v1" in {e.artifact_id for e in lineage}
    await reopened.close()
