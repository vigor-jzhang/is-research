"""Phase 6B offline integration — citation-correctness benchmark end to end.

Fixture manuscript -> real Phase 4C formatter (inline citations, bibliography,
citation_map) -> citation evaluator (manuscript_citation mode) -> aggregate
report with known expected metrics -> persist/reopen.

Expected aggregate metrics (10 cases; case 4 expects formatter refusal):
- citation_resolution_accuracy = 11/12
- citation_map_accuracy = 11/12
- bibliography_deduplication_accuracy = 8/9
- bibliography_coverage = 9/9
- unresolved_citation_count = 1, leftover_placeholder_count = 1
- unsupported_bibliography_entry_count = 0
- invented_bibliographic_field_count = 0
- inline_citation_accuracy = 1/1, anonymous_review_ok = 1/1,
  formatter_failure_ok = 1/1
- case_pass_rate = 7/10 (missing-citation-id, leftover, wrong-mapping fail)
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
from research_harness.plugins.research.evaluator_citation_correctness.plugin import (
    CitationCorrectnessEvaluator,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import CITATION_CORRECTNESS_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase6b_citation_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        evaluators={
            "evaluator.citation_correctness": CitationCorrectnessEvaluator(),
        },
        config={"evaluators": ["evaluator.citation_correctness"]},
    )
    await svc.register_benchmark(CITATION_CORRECTNESS_V1)
    run_id, report_id = await svc.run_benchmark("citation-correctness-v1")

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    report = (await store.get(report_id)).parse_payload(EvaluationReport)

    assert report.cases_total == 10
    assert report.cases_passed == 7
    assert report.cases_failed == 3
    assert report.cases_error == 0
    assert report.status.value == "failed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["citation_resolution_accuracy"].value == pytest.approx(11 / 12)
    assert metrics["citation_resolution_accuracy"].count == 12
    assert metrics["citation_map_accuracy"].value == pytest.approx(11 / 12)
    assert metrics["citation_map_accuracy"].count == 12
    assert metrics["bibliography_deduplication_accuracy"].value == pytest.approx(8 / 9)
    assert metrics["bibliography_deduplication_accuracy"].count == 9
    assert metrics["bibliography_coverage"].value == pytest.approx(9 / 9)
    assert metrics["bibliography_coverage"].count == 9
    assert metrics["unresolved_citation_count"].value == 1
    assert metrics["leftover_placeholder_count"].value == 1
    assert metrics["unsupported_bibliography_entry_count"].value == 0
    assert metrics["invented_bibliographic_field_count"].value == 0
    assert metrics["inline_citation_accuracy"].value == pytest.approx(1 / 1)
    assert metrics["anonymous_review_ok"].value == pytest.approx(1 / 1)
    assert metrics["formatter_failure_ok"].value == pytest.approx(1 / 1)
    assert metrics["case_pass_rate"].value == pytest.approx(7 / 10)
    assert metrics["evaluator_error_count"].value == 0

    # per-case statuses
    by_case = {cr.case_id: cr for cr in report.case_results}
    for cid in (
        "cit-valid-citation",
        "cit-multiple-citation-ids-same-paper",
        "cit-missing-paper-identity",
        "cit-sparse-metadata",
        "cit-page-locator",
        "cit-anonymous-manuscript",
        "cit-multiple-sections-same-paper",
    ):
        assert by_case[cid].status.value == "passed", cid
    for cid in (
        "cit-missing-citation-id",
        "cit-leftover-placeholder",
        "cit-wrong-identity-mapping",
    ):
        assert by_case[cid].status.value == "failed", cid

    # the missing-identity case passed only because the formatter refused
    assert by_case["cit-missing-paper-identity"].error
    assert "formatter failed" in by_case["cit-missing-paper-identity"].error

    # the bibliography really deduplicated: multiple citation ids -> 1 entry
    grouped = by_case["cit-multiple-sections-same-paper"].metrics
    assert grouped["bibliography_dedup"] == 1.0

    # reproducibility
    assert run.benchmark_content_hash
    assert len(run.case_hashes) == 10
    assert run.evaluator_versions["evaluator.citation_correctness"]
    assert run.produced_artifact_ids

    # provenance: report -> run -> benchmark -> cases; evaluator results
    # downstream of produced artifacts
    report_parents = await store.get_parents(report_id)
    assert {p.source_artifact_id for p in report_parents} == {run_id}
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"citation-correctness-v1"}
    cr = by_case["cit-valid-citation"]
    result_parents = await store.get_parents(cr.evaluator_result_ids[0])
    produced_sources = {p.source_artifact_id for p in result_parents}
    assert produced_sources & set(cr.produced_artifact_ids)

    # immutability: re-running creates a NEW run with identical outcomes
    run2_id, report2_id = await svc.run_benchmark("citation-correctness-v1")
    assert run2_id != run_id
    report2 = (await store.get(report2_id)).parse_payload(EvaluationReport)
    assert report2.cases_passed == 7 and report2.cases_failed == 3
    old_report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert old_report.cases_total == 10  # historical report unchanged

    # store reopen: provenance survives
    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    report2 = (await reopened.get(report_id)).parse_payload(EvaluationReport)
    assert report2.id == report_id
    assert report2.cases_passed == 7
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    assert "citation-correctness-v1" in {e.artifact_id for e in lineage}
    await reopened.close()
