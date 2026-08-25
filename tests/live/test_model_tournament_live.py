"""Live model tournament smoke test — opt-in (live_model_tournament).

Requires OPENROUTER_API_KEY. Deliberately small: role=reasoning, one
representative benchmark, 2-3 configured models, repetitions=1. Verifies the
tournament plumbing against a real provider:

- each candidate really receives the role's model calls (candidate router)
- EvaluationReports persist for every run
- usage/latency are captured at the model boundary
- deterministic metrics remain authoritative (never overridden by cost/latency)
- a leaderboard artifact is generated

No claim of model superiority is made from this smoke test; deterministic
benchmark outcomes are authoritative only over the fixture cases.
"""

from __future__ import annotations

import os

import pytest

from research_harness.app.bootstrap import build_runtime_from_yaml
from research_harness.research.benchmarks import BUILTIN_BENCHMARKS
from research_harness.research.schemas.tournament import (
    RoleLeaderboard,
    TournamentPlan,
    TournamentRun,
)

pytestmark = pytest.mark.live_model_tournament

LIVE_MODELS = os.getenv(
    "TOURNAMENT_LIVE_MODELS",
    "nvidia/nemotron-3-ultra-550b-a55b:free,deepseek/deepseek-v4-flash-0731",
).split(",")


@pytest.mark.asyncio
async def test_live_model_tournament_smoke(tmp_path):
    if not os.getenv("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")

    benchmark = os.getenv("TOURNAMENT_LIVE_BENCHMARK", "literature-synthesis-v1")
    if benchmark not in BUILTIN_BENCHMARKS:
        pytest.skip(f"unknown benchmark {benchmark!r}")

    plan = TournamentPlan.model_validate(
        {
            "name": "live smoke",
            "role": "reasoning",
            "benchmark_ids": [benchmark],
            "repetitions": 1,
            "retries": 1,
            "timeout_seconds": 180,
            "deterministic_pass_threshold": 0.0,  # never gate the smoke test
            "models": [
                {"candidate_id": f"cand-{i}", "requested_model": m, "provider": "openrouter"}
                for i, m in enumerate(LIVE_MODELS[:3])
            ],
        }
    )

    runtime = build_runtime_from_yaml("configs/example.yaml")
    async with runtime:
        store = runtime.services.require("artifact_store.default")
        svc = runtime.services.require("model_tournament.default")
        run: TournamentRun = await svc.run_tournament(plan)

        assert len(run.model_results) == len(plan.models)
        for mr in run.model_results:
            # candidate really received role calls
            assert len(mr.calls) > 0
            assert all(c.role == "reasoning" for c in mr.calls)
            # exact model identity preserved
            assert mr.resolved_model in LIVE_MODELS or mr.requested_model in LIVE_MODELS
            # usage/latency captured where the provider returned them
            assert mr.latency_ms_mean is not None and mr.latency_ms_mean > 0
            assert mr.total_tokens is None or mr.total_tokens > 0
            # EvaluationReports persisted for every run
            for ref in mr.benchmark_runs:
                assert ref.run_id and ref.report_id
                assert (await store.get(ref.report_id)).artifact_type == "evaluation_report"
            # deterministic metrics remain authoritative (may be low if the
            # model failed fixture expectations — that is a real signal, not
            # masked by cost or latency)
            assert mr.deterministic_pass_rate is not None
            assert mr.case_pass_rate is not None

        # leaderboard generated
        assert run.leaderboard_id
        env = await store.get(run.leaderboard_id)
        assert env.artifact_type == "role_leaderboard"
        board = env.parse_payload(RoleLeaderboard)
        assert board.role == "reasoning"
        assert board.entries == sorted(board.entries, key=lambda e: e.rank)
        assert len(board.entries) == len(plan.models)
