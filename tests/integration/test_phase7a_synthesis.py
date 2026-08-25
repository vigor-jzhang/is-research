"""Phase 7A offline integration — literature-synthesis benchmark end to end.

Fixture EvidenceCorpus + scripted responses -> REAL Phase 2G synthesizer ->
SynthesisStatement / SynthesisTheme / LiteratureSynthesis -> synthesis
evaluator -> report. 8 cases, all pass.
"""

from __future__ import annotations

import pathlib

import pytest

from research_harness.plugins.research.evaluation_harness.plugin import (
    EvaluationHarnessService,
)
from research_harness.plugins.research.evaluator_synthesis.plugin import SynthesisEvaluator
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import LITERATURE_SYNTHESIS_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun


@pytest.mark.asyncio
async def test_phase7a_synthesis_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=None,
        identity_resolver=None,
        evaluators={"evaluator.synthesis": SynthesisEvaluator()},
        config={"evaluators": ["evaluator.synthesis"]},
    )
    await svc.register_benchmark(LITERATURE_SYNTHESIS_V1)
    run_id, report_id = await svc.run_benchmark("literature-synthesis-v1")

    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert report.cases_total == 8
    assert report.cases_passed == 8
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["statement_grounding_accuracy"].value == pytest.approx(1.0)
    assert metrics["consensus_accuracy"].value == pytest.approx(1.0)
    assert metrics["contradiction_accuracy"].value == pytest.approx(1.0)
    assert metrics["multi_paper_support_accuracy"].value == pytest.approx(1.0)
    assert metrics["support_count_accuracy"].value == pytest.approx(1.0)
    assert metrics["unsupported_statement_rate"].value == pytest.approx(0.0)
    assert metrics["hallucinated_reference_count"].value == pytest.approx(0.0)

    by_case = {cr.case_id: cr for cr in report.case_results}
    assert by_case["syn-multi-paper-consensus"].status.value == "passed"
    assert by_case["syn-hallucinated-evidence-rejected"].status.value == "passed"
    assert by_case["syn-unsupported-statement-rejected"].status.value == "passed"

    # provenance + reproducibility
    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"literature-synthesis-v1"}
    assert run.benchmark_content_hash
    assert len(run.case_hashes) == 8

    # immutable rerun stability + store reopen provenance
    run2_id, _ = await svc.run_benchmark("literature-synthesis-v1")
    assert run2_id != run_id
    report2 = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert report2.cases_passed == 8

    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    assert "literature-synthesis-v1" in {e.artifact_id for e in lineage}
    await reopened.close()
