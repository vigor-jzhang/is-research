"""Phase 6D offline integration — mechanism-development benchmark end to end.

Fixture gap context -> REAL GapSelectionService (approval gate) -> REAL
MechanismGeneratorService (deterministic candidate validation) -> REAL
MechanismCriticService (critique + revision selection) -> mechanism evaluator
-> report.

Expected aggregate metrics (10 cases):
- candidate_validity_rate 11/11, candidate_validity_f1 1.0
- knowledge_basis_accuracy 11/11, grounding_accuracy 11/11
- gap_alignment_accuracy 11/11, unsupported_support_rate 0/11
- critic_issue_recall 3/3, revision_success_rate 11/11
- selected_mechanism_validity 11/11
- invalid_candidates_rejected 2
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
from research_harness.plugins.research.evaluator_mechanism.plugin import MechanismEvaluator
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import MECHANISM_DEVELOPMENT_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase6d_mechanism_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        evaluators={"evaluator.mechanism": MechanismEvaluator()},
        config={"evaluators": ["evaluator.mechanism"]},
    )
    await svc.register_benchmark(MECHANISM_DEVELOPMENT_V1)
    run_id, report_id = await svc.run_benchmark("mechanism-development-v1")

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    report = (await store.get(report_id)).parse_payload(EvaluationReport)

    assert report.cases_total == 10
    assert report.cases_passed == 10
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["candidate_validity_rate"].value == pytest.approx(1.0)
    assert metrics["candidate_validity_rate"].count == 11
    assert metrics["candidate_validity_f1"].value == pytest.approx(1.0)
    assert metrics["knowledge_basis_accuracy"].value == pytest.approx(1.0)
    assert metrics["knowledge_basis_accuracy"].count == 11
    assert metrics["grounding_accuracy"].value == pytest.approx(1.0)
    assert metrics["grounding_accuracy"].count == 11
    assert metrics["gap_alignment_accuracy"].value == pytest.approx(1.0)
    assert metrics["gap_alignment_accuracy"].count == 11
    assert metrics["unsupported_support_rate"].value == 0.0
    assert metrics["critic_issue_recall"].value == pytest.approx(1.0)
    assert metrics["critic_issue_recall"].count == 3
    assert metrics["revision_success_rate"].value == pytest.approx(1.0)
    assert metrics["revision_success_rate"].count == 11
    assert metrics["selected_mechanism_validity"].value == pytest.approx(1.0)
    assert metrics["selected_mechanism_validity"].count == 11
    assert metrics["invalid_candidates_rejected"].value == 2
    assert metrics["case_pass_rate"].value == pytest.approx(1.0)
    assert metrics["evaluator_error_count"].value == 0

    # per-case statuses + critic/revision behavior
    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["mech-hallucinated-literature-support"].status.value == "passed"
    assert (
        by_case["mech-hallucinated-literature-support"].metrics["invalid_candidates_rejected"]
        == 1.0
    )
    assert (
        by_case["mech-invalid-rejected-valid-survives"].metrics["invalid_candidates_rejected"]
        == 1.0
    )
    assert by_case["mech-incoherent-causal-direction"].metrics["critic_issue_recall"] == 1.0
    assert by_case["mech-incoherent-causal-direction"].metrics["revision_success_rate"] == 1.0
    assert by_case["mech-missing-actor-incentive"].metrics["critic_issue_recall"] == 1.0
    assert by_case["mech-multiple-plausible"].metrics["candidate_validity_rate"] == 1.0

    # provenance + reproducibility
    report_parents = await store.get_parents(report_id)
    assert {p.source_artifact_id for p in report_parents} == {run_id}
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"mechanism-development-v1"}
    assert run.benchmark_content_hash
    assert len(run.case_hashes) == 10
    assert run.evaluator_versions["evaluator.mechanism"]

    # immutability: re-run produces identical outcomes
    run2_id, report2_id = await svc.run_benchmark("mechanism-development-v1")
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
    assert "mechanism-development-v1" in {e.artifact_id for e in lineage}
    await reopened.close()
