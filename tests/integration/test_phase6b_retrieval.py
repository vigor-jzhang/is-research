"""Phase 6B offline integration — literature-retrieval benchmark end to end.

Fixture corpus -> real literature.search_orchestrator (ingestion + PaperIdentity
dedup) -> retrieval evaluator -> aggregate report with known expected metrics
-> persist/reopen -> provenance verified.

Expected aggregate metrics (fixed by the fixture design, 6 cases):
- precision@5 = 1.4/6, precision@10 = 0.7/6
- recall@5 = recall@10 = 5/6
- f1@5 = 2.0832/6, f1@10 = 1.1888/6
- mrr = 5/6, duplicate_rate = 0.5/6
- relevant_papers_missed = 0, irrelevant_papers_retrieved = 3
  (2 in the overlap case, 1 in the no-relevant case)
- case_pass_rate = 1.0, evaluator_error_count = 0
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
from research_harness.plugins.research.evaluator_retrieval.plugin import RetrievalEvaluator
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import LITERATURE_RETRIEVAL_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase6b_retrieval_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        evaluators={"evaluator.retrieval": RetrievalEvaluator()},
        config={"evaluators": ["evaluator.retrieval"]},
    )
    await svc.register_benchmark(LITERATURE_RETRIEVAL_V1)
    run_id, report_id = await svc.run_benchmark("literature-retrieval-v1")

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    report = (await store.get(report_id)).parse_payload(EvaluationReport)

    assert report.cases_total == 6
    assert report.cases_passed == 6
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["precision@5"].value == pytest.approx(1.4 / 6)
    assert metrics["precision@5"].count == 6
    assert metrics["precision@10"].value == pytest.approx(0.7 / 6)
    assert metrics["recall@5"].value == pytest.approx(5 / 6)
    assert metrics["recall@10"].value == pytest.approx(5 / 6)
    assert metrics["f1@5"].value == pytest.approx(2.0832 / 6, abs=1e-4)
    assert metrics["f1@10"].value == pytest.approx(1.1888 / 6, abs=1e-4)
    assert metrics["mrr"].value == pytest.approx(5 / 6)
    assert metrics["duplicate_rate"].value == pytest.approx(0.5 / 6)
    assert metrics["relevant_papers_missed"].value == 0
    assert metrics["irrelevant_papers_retrieved"].value == 3
    assert metrics["case_pass_rate"].value == 1.0
    assert metrics["evaluator_error_count"].value == 0

    # per-case spot checks
    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["ret-exact-terminology"].status.value == "passed"
    assert by_case["ret-exact-terminology"].metrics["recall@5"] == 1.0
    assert by_case["ret-exact-terminology"].metrics["mrr"] == 1.0
    assert by_case["ret-duplicate-provider-results"].metrics["duplicate_rate"] == pytest.approx(0.5)
    assert by_case["ret-no-relevant-result"].metrics["mrr"] == 0.0
    assert by_case["ret-no-relevant-result"].metrics["recall@5"] == 0.0
    assert (
        by_case["ret-irrelevant-high-keyword-overlap"].metrics["irrelevant_papers_retrieved"] == 2
    )

    # reproducibility metadata
    assert run.benchmark_content_hash
    assert len(run.case_hashes) == 6
    assert run.evaluator_versions["evaluator.retrieval"]
    assert run.produced_artifact_ids

    # provenance: report -> run -> benchmark -> cases
    report_parents = await store.get_parents(report_id)
    assert {p.source_artifact_id for p in report_parents} == {run_id}
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"literature-retrieval-v1"}

    # produced search artifacts -> evaluator results
    retrieval_cr = by_case["ret-exact-terminology"]
    assert len(retrieval_cr.evaluator_result_ids) == 1
    result_parents = await store.get_parents(retrieval_cr.evaluator_result_ids[0])
    produced_sources = {p.source_artifact_id for p in result_parents}
    assert produced_sources & set(retrieval_cr.produced_artifact_ids)

    # immutability: re-running creates a NEW run with identical outcomes
    run2_id, report2_id = await svc.run_benchmark("literature-retrieval-v1")
    assert run2_id != run_id
    report2 = (await store.get(report2_id)).parse_payload(EvaluationReport)
    assert report2.cases_passed == 6 and report2.cases_failed == 0
    old_report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert old_report.cases_total == 6  # historical report unchanged

    # store reopen: provenance survives
    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    report2 = (await reopened.get(report_id)).parse_payload(EvaluationReport)
    assert report2.id == report_id
    assert report2.metrics[0].metric_id == "case_pass_rate" or any(
        m.metric_id == "precision@5" for m in report2.metrics
    )
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    assert "literature-retrieval-v1" in {e.artifact_id for e in lineage}
    await reopened.close()
