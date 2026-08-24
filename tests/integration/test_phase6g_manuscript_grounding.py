"""Phase 6G offline integration — manuscript-grounding benchmark end to end.

Fixture ResearchResultsPackage + literature artifacts -> REAL
ManuscriptDrafterService (outline + scripted section drafts) + REAL
ManuscriptCriticService (+ REAL revision) -> manuscript-grounding evaluator
-> report.

Expected aggregate metrics (11 cases, all pass):
- claim_grounding_accuracy 15/15, literature_citation_coverage 6/6
- mathematical_claim_accuracy 5/5, condition_preservation_accuracy 5/5
- unsupported_claim_rate 0/15, citation_reference_accuracy 6/6
- novelty_claim_accuracy 15/15, section_consistency_accuracy 15/15
- critique_issue_recall 3/3, revision_success_rate 1/1
- case_pass_rate 11/11
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
from research_harness.plugins.research.evaluator_manuscript_grounding.plugin import (
    ManuscriptGroundingEvaluator,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import MANUSCRIPT_GROUNDING_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase6g_manuscript_grounding_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        evaluators={"evaluator.manuscript_grounding": ManuscriptGroundingEvaluator()},
        config={"evaluators": ["evaluator.manuscript_grounding"]},
    )
    await svc.register_benchmark(MANUSCRIPT_GROUNDING_V1)
    run_id, report_id = await svc.run_benchmark("manuscript-grounding-v1")

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    report = (await store.get(report_id)).parse_payload(EvaluationReport)

    assert report.cases_total == 11
    assert report.cases_passed == 11
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["claim_grounding_accuracy"].value == pytest.approx(1.0)
    assert metrics["claim_grounding_accuracy"].count == 15
    assert metrics["literature_citation_coverage"].value == pytest.approx(1.0)
    assert metrics["literature_citation_coverage"].count == 6
    assert metrics["mathematical_claim_accuracy"].value == pytest.approx(1.0)
    assert metrics["mathematical_claim_accuracy"].count == 5
    assert metrics["condition_preservation_accuracy"].value == pytest.approx(1.0)
    assert metrics["condition_preservation_accuracy"].count == 5
    assert metrics["unsupported_claim_rate"].value == pytest.approx(0.0)
    assert metrics["unsupported_claim_rate"].count == 15
    assert metrics["citation_reference_accuracy"].value == pytest.approx(1.0)
    assert metrics["citation_reference_accuracy"].count == 6
    assert metrics["novelty_claim_accuracy"].value == pytest.approx(1.0)
    assert metrics["novelty_claim_accuracy"].count == 15
    assert metrics["section_consistency_accuracy"].value == pytest.approx(1.0)
    assert metrics["section_consistency_accuracy"].count == 15
    assert metrics["critique_issue_recall"].value == pytest.approx(1.0)
    assert metrics["critique_issue_recall"].count == 3
    assert metrics["revision_success_rate"].value == pytest.approx(1.0)
    assert metrics["revision_success_rate"].count == 1
    assert metrics["case_pass_rate"].value == pytest.approx(1.0)
    assert metrics["evaluator_error_count"].value == 0

    # per-case statuses
    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["ms-grounded-literature-claim"].status.value == "passed"
    assert by_case["ms-grounded-literature-claim"].metrics["literature_citation_coverage"] == 1.0
    assert by_case["ms-grounded-mathematical-claim"].status.value == "passed"
    assert by_case["ms-grounded-mathematical-claim"].metrics["mathematical_claim_accuracy"] == 1.0
    assert by_case["ms-proposition-condition-preserved"].status.value == "passed"
    assert (
        by_case["ms-proposition-condition-preserved"].metrics["condition_preservation_accuracy"]
        == 1.0
    )
    assert by_case["ms-unsupported-literature-claim"].status.value == "passed"
    assert by_case["ms-unsupported-literature-claim"].metrics["unsupported_claim_rate"] == 0.0
    assert by_case["ms-missing-citation"].status.value == "passed"
    assert by_case["ms-hallucinated-citation-id"].status.value == "passed"
    assert by_case["ms-hallucinated-citation-id"].metrics["citation_reference_accuracy"] == 1.0
    assert by_case["ms-failed-proposition-presented"].status.value == "passed"
    assert by_case["ms-novelty-overclaim"].status.value == "passed"
    assert by_case["ms-novelty-overclaim"].metrics["novelty_claim_accuracy"] == 1.0
    assert by_case["ms-gap-contribution-inconsistency"].status.value == "passed"
    assert by_case["ms-gap-contribution-inconsistency"].metrics["critique_issue_recall"] == 1.0
    assert by_case["ms-limitations-omitted"].status.value == "passed"
    assert by_case["ms-limitations-omitted"].metrics["critique_issue_recall"] == 1.0
    assert by_case["ms-revision-repairs-flagged"].status.value == "passed"
    assert by_case["ms-revision-repairs-flagged"].metrics["revision_success_rate"] == 1.0

    # provenance + reproducibility
    report_parents = await store.get_parents(report_id)
    assert {p.source_artifact_id for p in report_parents} == {run_id}
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"manuscript-grounding-v1"}
    assert run.benchmark_content_hash
    assert len(run.case_hashes) == 11
    assert run.evaluator_versions["evaluator.manuscript_grounding"]

    # immutability: re-run produces identical outcomes
    run2_id, report2_id = await svc.run_benchmark("manuscript-grounding-v1")
    assert run2_id != run_id
    report2 = (await store.get(report2_id)).parse_payload(EvaluationReport)
    assert report2.cases_passed == 11 and report2.cases_failed == 0
    old_report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert old_report.cases_total == 11

    # store reopen: provenance survives
    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    report3 = (await reopened.get(report_id)).parse_payload(EvaluationReport)
    assert report3.id == report_id
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    assert "manuscript-grounding-v1" in {e.artifact_id for e in lineage}
