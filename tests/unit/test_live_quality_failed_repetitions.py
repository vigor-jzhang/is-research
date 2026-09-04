"""Unit tests for H25: live-quality crashed repetitions must keep their calls.

`LiveQualityService` caught a failed `run_benchmark`, appended an error task
result, and `continue`d -- past the only `calls.extend(router.records)`. The
router had already recorded the failed call (`CandidateModelRouter` calls
`_record(status="error")` before raising), so the record was discarded.

That shrank both the numerator and the denominator of
`provider_error_frequency`, which is a live qualification gate.
"""

from __future__ import annotations

import pytest

from research_harness.contracts.model import ModelRequest
from research_harness.kernel.errors import ModelError
from research_harness.plugins.research.evaluation_live_quality import plugin as lq
from research_harness.plugins.research.evaluation_live_quality.plugin import LiveQualityService
from research_harness.research.benchmarks import BenchmarkCaseDefinition, BenchmarkDefinition
from research_harness.research.schemas.tournament import TournamentModelConfig

BID = "synthetic-live-quality-v1"


class _FailingProvider:
    """Every call times out, which the router records before raising."""

    def __init__(self) -> None:
        self.call_count = 0

    async def complete(self, request: ModelRequest):
        self.call_count += 1
        raise TimeoutError("provider timed out")

    async def close(self) -> None:
        return None


class _BaseRouter:
    def resolve(self, role: str) -> dict[str, str]:
        return {"provider": "openrouter", "model": "base"}

    async def complete(self, role: str, request: ModelRequest):
        raise AssertionError("only the candidate role should be exercised")


class _Store:
    def __init__(self) -> None:
        self.put_count = 0

    async def put(self, envelope) -> None:
        self.put_count += 1

    async def get(self, artifact_id: str):
        raise KeyError(artifact_id)


class _Harness:
    """Drives model calls through the router, then crashes the benchmark."""

    def __init__(self) -> None:
        self.invocations = 0

    async def register_benchmark(self, definition) -> None:
        return None

    async def run_benchmark(
        self, benchmark_id, *, model_router=None, model_roles=None, evaluator_ids=None
    ):
        self.invocations += 1
        # The router records the failed call, then raises -- and then this
        # raise is what the service catches.
        with pytest.raises(ModelError):
            await model_router.complete(
                "reasoning", ModelRequest(messages=[])
            )
        raise RuntimeError(f"benchmark {benchmark_id} crashed")


@pytest.fixture
def synthetic_benchmark(monkeypatch):
    definition = BenchmarkDefinition(
        benchmark_id=BID,
        version=1,
        name="synthetic",
        description="synthetic live-quality benchmark for H25 coverage",
        category="live_quality",
        config={"evaluators": []},
        cases=[
            BenchmarkCaseDefinition(
                id="c1", name="c1", description="c1", input={}, reference={"task": "screening"}
            )
        ],
    )
    patched = dict(lq.BUILTIN_BENCHMARKS)
    patched[BID] = definition
    monkeypatch.setattr(lq, "BUILTIN_BENCHMARKS", patched)
    return definition


def _service(provider: _FailingProvider, harness: _Harness) -> LiveQualityService:
    return LiveQualityService(
        artifact_store=_Store(),
        harness=harness,
        role_router=_BaseRouter(),
        service_lookup=lambda name: provider,
        repetitions=1,
    )


@pytest.mark.asyncio
async def test_crashed_repetitions_keep_their_call_records(synthetic_benchmark):
    """H25: a crashed attempt must still be charged for.

    Before this, the exception path skipped calls.extend(router.records), so
    every call made during a crashed repetition vanished and the candidate
    looked more reliable than it was. The call list is local to the service,
    so it is observed through the metrics derived from it.
    """
    provider = _FailingProvider()
    harness = _Harness()
    run = await _service(provider, harness).run_live_quality(
        "reasoning", BID, TournamentModelConfig(candidate_id="cand", requested_model="m/cand"),
        repetitions=3,
    )
    result = run.result

    assert harness.invocations == 3
    # Each attempt retried (retries=2) before the router recorded the failure,
    # so the provider saw more calls than there are records.
    assert provider.call_count >= 3
    # All three attempts timed out; each must be recorded as a failure.
    assert result.failure_counts.get("timeout") == 3, result.failure_counts


@pytest.mark.asyncio
async def test_crashed_repetitions_raise_provider_error_frequency(synthetic_benchmark):
    """The qualification gate must see the errors that caused the crash.

    provider_error_frequency is provider_errors / len(calls). Dropping the
    crashed attempt's records removed the numerator along with the
    denominator, so a benchmark that crashed on provider errors reported a
    frequency of 0.0 (or nothing at all).
    """
    provider = _FailingProvider()
    run = await _service(provider, _Harness()).run_live_quality(
        "reasoning", BID, TournamentModelConfig(candidate_id="cand", requested_model="m/cand"),
        repetitions=3,
    )

    # Every attempt failed with a timeout, which is a provider error kind.
    assert run.result.provider_error_frequency == 1.0


@pytest.mark.asyncio
async def test_crashed_repetitions_still_count_as_repetitions(synthetic_benchmark):
    """Crashed attempts were already counted here; the fix must not change that."""
    run = await _service(_FailingProvider(), _Harness()).run_live_quality(
        "reasoning", BID, TournamentModelConfig(candidate_id="cand", requested_model="m/cand"),
        repetitions=3,
    )
    assert run.result.repetitions == 3
    assert all(t.report_status == "error" for t in run.result.task_results)
