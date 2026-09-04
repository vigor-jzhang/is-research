"""Unit tests for how the tournament handles repetitions that crash (H4).

A repetition that raises used to be dropped entirely: it never entered any
pass-rate denominator, and its call records were discarded, so a flaky model
was scored on the runs that happened to succeed and looked cheaper than it was.
"""

from __future__ import annotations

import pytest

from research_harness.contracts.common import Usage
from research_harness.contracts.model import Message, ModelRequest, ModelResponse
from research_harness.plugins.research.evaluation_model_tournament.plugin import (
    ModelTournamentService,
)
from research_harness.research.schemas.tournament import (
    TournamentModelConfig,
    TournamentPlan,
)


class _Provider:
    """Answers every call successfully, so call records are always produced."""

    def __init__(self) -> None:
        self.call_count = 0

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.call_count += 1
        return ModelResponse(
            message=Message(role="assistant", content="{}"),
            tool_calls=[],
            finish_reason="stop",
            model=str(request.metadata.get("model") or "unknown"),
            provider="openrouter",
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15, cost=0.001),
            latency_ms=5.0,
        )

    async def close(self) -> None:
        return None


class _BaseRouter:
    def resolve(self, role: str) -> dict[str, str]:
        return {"provider": "openrouter", "model": "base"}

    async def complete(self, role: str, request: ModelRequest) -> ModelResponse:
        raise AssertionError("only the candidate role should be exercised")


class _Store:
    def __init__(self) -> None:
        self.get_calls = 0

    async def get(self, artifact_id: str):
        self.get_calls += 1
        raise KeyError(artifact_id)


class _Harness:
    """Drives one real model call through the router, then crashes.

    This is the shape that used to lose data: the attempt consumed provider
    calls (and cost) but produced no report.
    """

    def __init__(self, attempts: int) -> None:
        self.attempts = attempts
        self.invocations = 0

    async def run_benchmark(
        self, benchmark_id, *, evaluator_ids=None, model_router=None, model_roles=None
    ):
        self.invocations += 1
        await model_router.complete(
            "reasoning",
            ModelRequest(messages=[Message(role="user", content="hello")]),
        )
        raise RuntimeError(f"benchmark {benchmark_id} crashed")


def _service(provider: _Provider, harness: _Harness) -> ModelTournamentService:
    return ModelTournamentService(
        artifact_store=_Store(),
        harness=harness,
        role_router=_BaseRouter(),
        service_lookup=lambda name: provider,
    )


def _plan(repetitions: int) -> TournamentPlan:
    return TournamentPlan(
        role="reasoning",
        benchmark_ids=["b"],
        repetitions=repetitions,
        # Non-empty evaluator_ids keeps _resolve_evaluator_ids off the store.
        evaluator_ids=["evaluator.deterministic"],
        advisory_evaluators=[],
    )


def _candidate() -> TournamentModelConfig:
    return TournamentModelConfig(candidate_id="cand", requested_model="m/cand")


@pytest.mark.asyncio
async def test_crashed_repetitions_still_contribute_their_calls():
    """H4: a crashed attempt must be charged for, not silently discarded.

    Before this, the exception path skipped ``calls.extend(router.records)``,
    so the provider calls made during a failed repetition vanished from the
    aggregate and the candidate looked cheaper than it was.
    """
    provider = _Provider()
    harness = _Harness(attempts=1)
    result, failures = await _service(provider, harness)._run_candidate(_candidate(), _plan(3))

    assert harness.invocations == 3
    assert provider.call_count == 3
    # The calls happened, so they must be recorded.
    assert len(result.calls) == 3, "crashed repetitions dropped their call records"
    assert result.estimated_cost is not None
    assert result.estimated_cost > 0
    assert len(failures) == 3


@pytest.mark.asyncio
async def test_crashed_repetitions_enter_the_pass_rate_denominator():
    """H4: three attempts with none completing must not score as 'no attempts'."""
    provider = _Provider()
    result, _ = await _service(provider, _Harness(attempts=1))._run_candidate(
        _candidate(), _plan(3)
    )

    assert result.repetition_failure_rate == 1.0
    # No run completed, so there is no pass rate to report -- but it must be
    # absent because there were no completions, not because the crashes were
    # treated as never having happened.
    assert result.benchmark_pass_rate == 0.0
    assert result.deterministic_pass_rate is None
