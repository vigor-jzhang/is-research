"""Phase 6D offline integration — research-gap-analysis benchmark end to end.

Fixture synthesis/evidence -> REAL GapAnalyzerService -> ResearchGap +
GapAnalysis -> gap evaluator -> report.

Expected aggregate metrics (10 cases; the unsupported/global-novelty case
fails by design):
- gap_precision 8/9, gap_recall 8/8, gap_f1 (mean per-case) 9/10
- gap_type_accuracy 8/8, grounding_accuracy 8/9
- corpus_bounded_claim_accuracy 9/9, support_count_accuracy 8/8 (specs)
- ranking_accuracy 2/2 (ordered pairs)
- unsupported_gap_rate 1/9, hallucinated_reference_count 1
- case_pass_rate 9/10
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
from research_harness.plugins.research.evaluator_gap_analysis.plugin import (
    GapAnalysisEvaluator,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import RESEARCH_GAP_ANALYSIS_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase6d_gap_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        evaluators={"evaluator.gap_analysis": GapAnalysisEvaluator()},
        config={"evaluators": ["evaluator.gap_analysis"]},
    )
    await svc.register_benchmark(RESEARCH_GAP_ANALYSIS_V1)
    run_id, report_id = await svc.run_benchmark("research-gap-analysis-v1")

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    report = (await store.get(report_id)).parse_payload(EvaluationReport)

    assert report.cases_total == 10
    assert report.cases_passed == 9
    assert report.cases_failed == 1
    assert report.cases_error == 0
    assert report.status.value == "failed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["gap_precision"].value == pytest.approx(8 / 9)
    assert metrics["gap_precision"].count == 9
    assert metrics["gap_recall"].value == pytest.approx(1.0)
    assert metrics["gap_recall"].count == 8
    assert metrics["gap_f1"].value == pytest.approx(9 / 10)
    assert metrics["gap_type_accuracy"].value == pytest.approx(1.0)
    assert metrics["gap_type_accuracy"].count == 8
    assert metrics["grounding_accuracy"].value == pytest.approx(8 / 9)
    assert metrics["grounding_accuracy"].count == 9
    assert metrics["corpus_bounded_claim_accuracy"].value == pytest.approx(1.0)
    assert metrics["corpus_bounded_claim_accuracy"].count == 9
    assert metrics["support_count_accuracy"].value == pytest.approx(1.0)
    assert metrics["support_count_accuracy"].count == 8
    assert metrics["ranking_accuracy"].value == pytest.approx(1.0)
    assert metrics["ranking_accuracy"].count == 2
    assert metrics["unsupported_gap_rate"].value == pytest.approx(1 / 9)
    assert metrics["unsupported_gap_rate"].count == 9
    assert metrics["hallucinated_reference_count"].value == 1
    assert metrics["case_pass_rate"].value == pytest.approx(9 / 10)
    assert metrics["evaluator_error_count"].value == 0

    # per-case statuses
    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["gap-contradiction"].status.value == "passed"
    assert by_case["gap-contradiction"].metrics["ranking_accuracy"] == 1.0
    assert by_case["gap-repeated-limitation"].status.value == "passed"
    assert by_case["gap-hallucinated-id"].status.value == "passed"
    assert by_case["gap-hallucinated-id"].metrics["hallucinated_reference_count"] == 1.0
    assert by_case["gap-no-defensible-gap"].status.value == "passed"
    assert by_case["gap-unsupported-global-novelty"].status.value == "failed"

    # provenance + reproducibility
    report_parents = await store.get_parents(report_id)
    assert {p.source_artifact_id for p in report_parents} == {run_id}
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"research-gap-analysis-v1"}
    assert run.benchmark_content_hash
    assert len(run.case_hashes) == 10
    assert run.evaluator_versions["evaluator.gap_analysis"]

    # immutability: re-run produces identical outcomes
    run2_id, report2_id = await svc.run_benchmark("research-gap-analysis-v1")
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
    assert "research-gap-analysis-v1" in {e.artifact_id for e in lineage}
    await reopened.close()
