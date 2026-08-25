"""Phase 7C offline integration — model-routing benchmark + shadow mode.

Registers model-routing-policy-v1 -> runs the REAL policy router over synthetic
RoleLeaderboard fixtures -> evaluator.model_routing -> report. 12 cases, all
pass, unsafe_selection_rate = 0. Also verifies shadow-mode comparison,
rerun stability, and provenance-after-reopen.
"""

from __future__ import annotations

import pathlib

import pytest

from research_harness.plugins.research.evaluation_harness.plugin import (
    EvaluationHarnessService,
)
from research_harness.plugins.research.evaluator_model_routing.plugin import (
    ModelRoutingEvaluator,
)
from research_harness.plugins.routing.policy_router.plugin import (
    PolicyModelRouterService,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import MODEL_ROUTING_POLICY_V1
from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun
from research_harness.research.schemas.routing import RoutingRequest
from research_harness.research.schemas.tournament import LeaderboardEntry, RoleLeaderboard


def _entry(
    cid: str,
    *,
    det: float,
    latency: float | None,
    cost: float | None = None,
    provider: str = "openrouter",
) -> LeaderboardEntry:
    return LeaderboardEntry.model_validate(
        {
            "candidate_id": cid,
            "model": {
                "candidate_id": cid,
                "provider": provider,
                "requested_model": f"m-{cid}",
            },
            "resolved_model": f"m-{cid}",
            "rank": 1,
            "eligibility": "eligible",
            "deterministic_pass_rate": det,
            "benchmark_pass_rate": det,
            "case_pass_rate": det,
            "structured_output_success_rate": 1.0,
            "model_error_rate": 0.0,
            "retry_rate": 0.0,
            "latency_ms_p50": latency,
            "estimated_cost": cost,
        }
    )


class _CapProvider:
    def __init__(self, capabilities: dict | None = None) -> None:
        from research_harness.contracts.model import ModelCapabilities

        self.capabilities = ModelCapabilities(**{"structured_output": True, **(capabilities or {})})

    async def complete(self, request):
        raise AssertionError("router never calls the provider")

    async def close(self):
        pass


def _cap_lookup():
    def lookup(name: str):
        return _CapProvider()

    return lookup


@pytest.mark.asyncio
async def test_phase7c_model_routing_benchmark_end_to_end(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=None,
        identity_resolver=None,
        evaluators={"evaluator.model_routing": ModelRoutingEvaluator()},
        config={"evaluators": ["evaluator.model_routing"]},
    )
    await svc.register_benchmark(MODEL_ROUTING_POLICY_V1)
    run_id, report_id = await svc.run_benchmark("model-routing-policy-v1")

    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert report.cases_total == 12
    assert report.cases_passed == 12
    assert report.cases_failed == 0
    assert report.cases_error == 0
    assert report.status.value == "passed"

    metrics = {m.metric_id: m for m in report.metrics}
    assert metrics["routing_decision_accuracy"].value == pytest.approx(1.0)
    assert metrics["eligibility_filter_accuracy"].value == pytest.approx(1.0)
    assert metrics["constraint_satisfaction_accuracy"].value == pytest.approx(1.0)
    assert metrics["fallback_accuracy"].value == pytest.approx(1.0)
    assert metrics["role_isolation_accuracy"].value == pytest.approx(1.0)
    assert metrics["stale_evidence_handling_accuracy"].value == pytest.approx(1.0)
    assert metrics["deterministic_tiebreak_accuracy"].value == pytest.approx(1.0)
    assert metrics["unsafe_selection_rate"].value == pytest.approx(0.0)

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    run_parents = await store.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"model-routing-policy-v1"}

    # rerun stability + provenance after reopen
    run2_id, _ = await svc.run_benchmark("model-routing-policy-v1")
    assert run2_id != run_id
    assert (await store.get(report_id)).parse_payload(EvaluationReport).cases_passed == 12

    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    lineage = await reopened.get_lineage(report_id, direction="ancestors")
    assert run_id in {e.artifact_id for e in lineage}
    await reopened.close()


@pytest.mark.asyncio
async def test_shadow_mode_would_switch(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    board = RoleLeaderboard(
        role="reasoning",
        plan_id="p",
        tournament_run_id="r",
        plan_hash="h",
        entries=[
            _entry("winner", det=0.99, latency=40.0, cost=0.05),
            _entry("current", det=0.9, latency=100.0, cost=0.02),
        ],
        metadata={"repetitions": 2},
    )
    from research_harness.research.envelope import ArtifactEnvelope

    await store.put(
        ArtifactEnvelope.create(
            payload=board, artifact_type="role_leaderboard", producer="t", artifact_id="lb-shadow"
        )
    )
    service = PolicyModelRouterService(
        artifact_store=store,
        service_lookup=_cap_lookup(),
        current_roles={"reasoning": {"provider": "openrouter", "model": "m-current"}},
    )
    decision = await service.shadow("reasoning", "quality_first", RoutingRequest(role="reasoning"))
    assert decision.status.value == "selected"
    assert decision.selected_candidate_id == "winner"
    assert decision.shadow["routing_mode"] == "shadow"
    assert decision.shadow["current_model"] == "m-current"
    assert decision.shadow["would_switch"] is True
    assert decision.shadow["same_as_current"] is False
    assert decision.shadow["expected_quality_delta"] == pytest.approx(0.09)
    assert decision.shadow["expected_latency_delta"] == pytest.approx(-60.0)
    assert decision.shadow["expected_cost_delta"] == pytest.approx(0.03)

    # the decision is persisted and reproducible
    stored = await service.get_decision(decision.id)
    assert stored.policy_id == "quality_first"
    assert stored.selected_candidate_id == "winner"
    await store.close()


@pytest.mark.asyncio
async def test_shadow_same_as_current_no_switch(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    board = RoleLeaderboard(
        role="reasoning",
        plan_id="p",
        tournament_run_id="r",
        plan_hash="h",
        entries=[
            _entry("winner", det=0.99, latency=40.0),
        ],
        metadata={"repetitions": 1},
    )
    from research_harness.research.envelope import ArtifactEnvelope

    await store.put(
        ArtifactEnvelope.create(
            payload=board, artifact_type="role_leaderboard", producer="t", artifact_id="lb-same"
        )
    )
    service = PolicyModelRouterService(
        artifact_store=store,
        service_lookup=_cap_lookup(),
        current_roles={"reasoning": {"provider": "openrouter", "model": "m-winner"}},
    )
    decision = await service.shadow("reasoning", "quality_first", RoutingRequest(role="reasoning"))
    assert decision.shadow["would_switch"] is False
    assert decision.shadow["same_as_current"] is True
    await store.close()
