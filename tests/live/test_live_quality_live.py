"""Live live-quality run smoke test — opt-in (live_live_quality).

Requires OPENROUTER_API_KEY. Deliberately small: one role, the reasoning
live-quality benchmark, repetitions=1. Verifies the live-quality plumbing
against a real provider: LiveQualityRun persists, a live_quality_evidence
RoleLeaderboard is produced, deterministic metrics are authoritative, and
the readiness assessment can be computed. No claim of model quality is made
from a single repetition.
"""

from __future__ import annotations

import os

import pytest

from research_harness.app.bootstrap import build_runtime_from_yaml
from research_harness.research.schemas.tournament import RoleLeaderboard, TournamentModelConfig

pytestmark = pytest.mark.live_live_quality

LIVE_MODEL = os.getenv("TOURNAMENT_LIVE_MODELS", "deepseek/deepseek-v4-flash-0731").split(",")[0]


@pytest.mark.asyncio
async def test_live_live_quality_smoke():
    if not os.getenv("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")

    runtime = build_runtime_from_yaml("configs/example.yaml")
    async with runtime:
        store = runtime.services.require("artifact_store.default")
        svc = runtime.services.require("live_quality.default")
        model_config = TournamentModelConfig(
            candidate_id=f"live:{LIVE_MODEL}", requested_model=LIVE_MODEL
        )
        run = await svc.run_live_quality(
            "reasoning",
            "live-quality-reasoning-v1",
            model_config,
            repetitions=1,
            timeout_seconds=180,
        )
        result = run.result
        assert run.id
        assert result.resolved_model
        assert result.repetitions == 1
        assert result.deterministic_pass_rate_mean is not None
        assert result.structured_output_success_rate is not None
        # live-quality leaderboard produced with the right evidence type
        assert run.leaderboard_id
        env = await store.get(run.leaderboard_id)
        assert env.artifact_type == "role_leaderboard"
        board = env.parse_payload(RoleLeaderboard)
        assert board.evidence_type == "live_quality_evidence"
        assert board.role == "reasoning"

        # readiness assessment can be computed
        assessment = await svc.assess_readiness("reasoning")
        assert assessment.unsafe_production_qualification is False
