"""Phase 6A offline integration — novelty-threat benchmark end to end.

Register novelty-threat-v1 -> run all cases over the PRODUCTION
NoveltyValidationService (fixture model + fixture literature sources, no
network) -> run all evaluators -> aggregate report with known expected
metrics -> persist -> close/reopen -> verify the provenance chain.

Expected metrics (fixed by the fixture design):
- 7 cases: 6 passed, 1 failed (the deliberately-missed prior art)
- candidate_relationship_accuracy = 5/6 (6 cases have prior-art entries; the
  missed case mismatches)
- claim_status_accuracy = 6/7
- report_status_accuracy = 6/7
- false_clear_count = 1 (the missed case), false_clear_rate = 1/5
- evaluator_error_count = 0
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
from research_harness.plugins.research.evaluator_claim_grounding.plugin import (
    ClaimGroundingEvaluator,
)
from research_harness.plugins.research.evaluator_deterministic.plugin import (
    DeterministicEvaluator,
)
from research_harness.plugins.research.evaluator_llm_judge.plugin import LlmJudgeEvaluator
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import NOVELTY_THREAT_V1
from research_harness.research.schemas.evaluation import (
    EvaluationReport,
    EvaluationRun,
)
from research_harness.research.schemas.evaluation import (
    EvaluatorResult as EvaluationRunResult,
)
from research_harness.research.schemas.novelty import NoveltyValidationReport


class NoopRouter:
    """Fallback router — never used because the benchmark serves fixtures."""

    async def complete(self, role, request):
        raise AssertionError("production router must not be called in an offline benchmark")

    def resolve(self, role):
        return {"provider": "fixture", "model": "fixture"}


async def _report_status_by_case(store, report: EvaluationReport) -> dict[str, str]:
    out: dict[str, str] = {}
    for cr in report.case_results:
        status = "none"
        for aid in cr.produced_artifact_ids:
            env = await store.get(aid)
            if env.artifact_type == "novelty_validation_report":
                status = env.parse_payload(NoveltyValidationReport).overall_status.value
        out[cr.case_id] = status
    return out


@pytest.mark.asyncio
async def test_phase6a_novelty_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ingestor = LiteratureIngestor(artifact_store=store)
    resolver = PaperIdentityResolverService(artifact_store=store)
    router = NoopRouter()

    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=ingestor,
        identity_resolver=resolver,
        evaluators={
            "evaluator.deterministic": DeterministicEvaluator(),
            "evaluator.claim_grounding": ClaimGroundingEvaluator(model_router=router),
            "evaluator.citation_correctness": CitationCorrectnessEvaluator(),
            "evaluator.llm_judge": LlmJudgeEvaluator(model_router=router),
        },
        config={"evaluators": ["evaluator.deterministic"]},
    )

    await svc.register_benchmark(NOVELTY_THREAT_V1)
    run_id, report_id = await svc.run_benchmark("novelty-threat-v1")

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    report = (await store.get(report_id)).parse_payload(EvaluationReport)

    # ------------------------------------------------------------------
    # aggregate report
    # ------------------------------------------------------------------
    assert report.benchmark_id == "novelty-threat-v1"
    assert report.cases_total == 7
    assert report.cases_passed == 6
    assert report.cases_failed == 1
    assert report.cases_error == 0
    assert report.status.value == "failed"  # the false-clear case fails the run

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["candidate_relationship_accuracy"].value == pytest.approx(5 / 6)
    assert metrics["candidate_relationship_accuracy"].count == 6
    assert metrics["claim_status_accuracy"].value == pytest.approx(6 / 7)
    assert metrics["claim_status_accuracy"].count == 7
    assert metrics["report_status_accuracy"].value == pytest.approx(6 / 7)
    assert metrics["report_status_accuracy"].count == 7
    assert metrics["false_clear_count"].value == 1
    assert metrics["false_clear_count"].count == 5  # claims expected threatened/unverified
    assert metrics["false_clear_rate"].value == pytest.approx(1 / 5)
    assert metrics["case_pass_rate"].value == pytest.approx(6 / 7)
    assert metrics["evaluator_error_count"].value == 0
    assert report.false_negative_counts == {"false_clear": 1}
    assert report.false_positive_counts == {"false_threat": 0}
    assert run.status == report.status
    assert run.cases_passed == 6 and run.cases_failed == 1

    # ------------------------------------------------------------------
    # per-case production report statuses (the workflow really ran)
    # ------------------------------------------------------------------
    statuses = await _report_status_by_case(store, report)
    assert statuses == {
        "nt-direct-prior-art": "blocked",
        "nt-strong-overlap": "blocked",
        "nt-partial-overlap": "revise",
        "nt-distinct-paper": "clear",
        "nt-insufficient-evidence": "unverified",
        "nt-provider-failure": "unverified",
        "nt-missed-prior-art": "clear",  # the false clear
    }

    # ------------------------------------------------------------------
    # false-clear case is failed by the deterministic evaluator
    # ------------------------------------------------------------------
    missed = next(cr for cr in report.case_results if cr.case_id == "nt-missed-prior-art")
    assert missed.status.value == "failed"
    assert missed.metrics["candidate_relationship"] == 0.0
    assert missed.metrics["claim_status"] == 0.0
    assert missed.metrics["report_status"] == 0.0
    assert missed.metrics["false_clear"] == 0.0

    passed = next(cr for cr in report.case_results if cr.case_id == "nt-direct-prior-art")
    assert passed.status.value == "passed"
    assert passed.metrics["candidate_relationship"] == 1.0

    # ------------------------------------------------------------------
    # reproducibility metadata
    # ------------------------------------------------------------------
    assert run.benchmark_content_hash
    assert len(run.case_hashes) == 7
    assert run.evaluator_versions["evaluator.deterministic"]
    assert run.evaluation_config["judge_role"] == "critic"
    assert run.produced_artifact_ids
    assert run.cost_usd == 0.0  # offline fixtures, no real model cost
    assert run.latency_ms >= 0

    # ------------------------------------------------------------------
    # provenance: report -> run -> benchmark -> cases
    # ------------------------------------------------------------------
    report_parents = await store.get_parents(report_id)
    assert {p.source_artifact_id for p in report_parents} == {run_id}
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"novelty-threat-v1"}
    benchmark_children = await store.get_children("novelty-threat-v1")
    child_ids = {c.target_artifact_id for c in benchmark_children}
    assert {c.id for c in NOVELTY_THREAT_V1.cases} <= child_ids
    assert run_id in child_ids

    # produced research artifacts -> evaluator results
    for cr in report.case_results:
        for rid in cr.evaluator_result_ids:
            res = (await store.get(rid)).parse_payload(EvaluationRunResult)
            assert set(res.evidence_artifact_ids) <= set(cr.produced_artifact_ids)
            result_parents = {p.source_artifact_id for p in await store.get_parents(rid)}
            assert cr.case_id in result_parents
            if res.evidence_artifact_ids:
                assert set(res.evidence_artifact_ids) <= result_parents

    # ------------------------------------------------------------------
    # immutability: re-running creates a NEW run; history is preserved
    # ------------------------------------------------------------------
    run2_id, _ = await svc.run_benchmark("novelty-threat-v1")
    assert run2_id != run_id
    runs = await store.list(artifact_type="evaluation_run")
    assert {r.artifact_id for r in runs} >= {run_id, run2_id}
    old_report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert old_report.cases_total == 7  # unchanged historical report

    # ------------------------------------------------------------------
    # store reopen: provenance survives
    # ------------------------------------------------------------------
    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    report2 = (await reopened.get(report_id)).parse_payload(EvaluationReport)
    assert report2.id == report_id
    assert report2.false_negative_counts == {"false_clear": 1}
    parents = await reopened.get_parents(report_id)
    assert {p.source_artifact_id for p in parents} == {run_id}
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    assert "novelty-threat-v1" in {e.artifact_id for e in lineage}
    await reopened.close()
