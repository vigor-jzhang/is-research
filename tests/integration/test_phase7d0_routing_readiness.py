"""Phase 7D.0 offline integration — routing-readiness benchmark + live-quality
service readiness path.

Runs production-routing-readiness-v1 end to end (9 cases, unsafe rate 0),
verifies the live-quality service readiness aggregation over synthetic
LiveQualityRun artifacts, rerun stability, and provenance after reopen.
"""

from __future__ import annotations

import pathlib

import pytest

from research_harness.plugins.research.evaluation_harness.plugin import (
    EvaluationHarnessService,
)
from research_harness.plugins.research.evaluation_live_quality.plugin import (
    LiveQualityService,
)
from research_harness.plugins.research.evaluator_routing_readiness.plugin import (
    RoutingReadinessEvaluator,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import PRODUCTION_ROUTING_READINESS_V1
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import EvaluationReport
from research_harness.research.schemas.live_quality import (
    LiveQualityModelResult,
    LiveQualityRun,
    RoutingReadinessAssessment,
)


@pytest.mark.asyncio
async def test_phase7d0_routing_readiness_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=None,
        identity_resolver=None,
        evaluators={"evaluator.routing_readiness": RoutingReadinessEvaluator()},
        config={"evaluators": ["evaluator.routing_readiness"]},
    )
    await svc.register_benchmark(PRODUCTION_ROUTING_READINESS_V1)
    run_id, report_id = await svc.run_benchmark("production-routing-readiness-v1")

    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert report.cases_total == 9
    assert report.cases_passed == 9
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["unsafe_production_qualification_rate"].value == pytest.approx(0.0)
    assert metrics["readiness_decision_accuracy"].value == pytest.approx(1.0)

    # rerun stability + provenance after reopen
    run2_id, _ = await svc.run_benchmark("production-routing-readiness-v1")
    assert run2_id != run_id
    assert (await store.get(report_id)).parse_payload(EvaluationReport).cases_passed == 9

    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    await reopened.close()


def _result(candidate_id: str, det: float) -> LiveQualityModelResult:
    from datetime import UTC, datetime

    return LiveQualityModelResult(
        candidate_id=candidate_id,
        model={"candidate_id": candidate_id, "requested_model": candidate_id},
        resolved_model=candidate_id,
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        repetitions=3,
        deterministic_pass_rate_mean=det,
        deterministic_pass_rate_worst=det,
        structured_output_success_rate=0.95,
        provider_error_frequency=0.0,
        critical_grounding_failures=0,
        evidence_timestamp=datetime.now(UTC),
        task_results=[
            {
                "repetition": i,
                "run_id": f"r{i}",
                "report_id": f"p{i}",
                "report_status": "passed",
                "cases_total": 1,
                "cases_passed": 1,
                "cases_failed": 0,
                "cases_error": 0,
                "task_pass_rate": 1.0,
                "task_completed": True,
            }
            for i in range(3)
        ],
    )


@pytest.mark.asyncio
async def test_live_quality_service_readiness(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = LiveQualityService(
        artifact_store=store,
        harness=None,
        role_router=None,
        service_lookup=None,
        current_roles={"reasoning": {"provider": "openrouter", "model": "m-configured"}},
    )
    # seed two synthetic live-quality runs (configured + fallback)
    for cid, det in (("m-configured", 0.92), ("m-fallback", 0.9)):
        result = _result(cid, det)
        run = LiveQualityRun(
            role="reasoning",
            benchmark_id="live-quality-reasoning-v1",
            model=result.model,
            repetitions=result.repetitions,
            result=result,
        )
        await store.put(
            ArtifactEnvelope.create(
                payload=run, artifact_type="live_quality_run", producer="t", artifact_id=run.id
            )
        )

    assessment = await svc.assess_readiness("reasoning")
    assert isinstance(assessment, RoutingReadinessAssessment)
    assert assessment.role == "reasoning"
    assert assessment.qualified is True
    assert "m-configured" in assessment.qualified_models
    assert assessment.fallback_qualified is True
    assert assessment.fallback_model == "m-fallback"
    assert assessment.unsafe_production_qualification is False
    assert assessment.configured_model == "m-configured"

    # persisted and reopenable
    stored = (await store.get(assessment.id)).parse_payload(RoutingReadinessAssessment)
    assert stored.qualified is True

    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    persisted = (await reopened.get(assessment.id)).parse_payload(RoutingReadinessAssessment)
    assert persisted.qualified is True
    assert persisted.qualified_models == ["m-configured", "m-fallback"]
    await reopened.close()


@pytest.mark.asyncio
async def test_live_quality_service_readiness_unsafe_is_false(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = LiveQualityService(
        artifact_store=store,
        harness=None,
        role_router=None,
        service_lookup=None,
        current_roles={"reasoning": {"provider": "openrouter", "model": "m-configured"}},
    )
    # no live evidence at all -> not qualified, never unsafe
    assessment = await svc.assess_readiness("reasoning")
    assert assessment.qualified is False
    assert assessment.unsafe_production_qualification is False
    assert any("no live-quality evidence" in r for r in assessment.reasons)
    await store.close()
