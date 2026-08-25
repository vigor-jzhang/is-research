"""Unit tests for the CandidateModelRouter (Phase 7B): role binding,
fallback delegation, per-call recording, failure classification, retries,
and structured-output failure detection."""

from __future__ import annotations

import pytest

from research_harness.contracts.common import Usage
from research_harness.contracts.model import Message, ModelRequest, ModelResponse
from research_harness.kernel.errors import ModelError
from research_harness.plugins.research.evaluation_model_tournament.plugin import (
    CandidateModelRouter,
    _classify_model_error,
)
from research_harness.research.schemas.tournament import (
    TournamentFailureKind,
    TournamentModelConfig,
)


class FakeBase:
    def __init__(self) -> None:
        self.roles = {"fast": {"provider": "openrouter", "model": "base-fast"}}
        self.redirected: list[str] = []

    def resolve(self, role):
        return dict(self.roles[role])

    async def complete(self, role, request):
        self.redirected.append(role)
        return ModelResponse(
            message=Message(role="assistant", content='{"ok": true}'),
            model="base-fast",
            provider="openrouter",
            latency_ms=1.0,
        )


class FakeProvider:
    def __init__(self, responses=None, errors=None):
        self.responses = responses or []
        self.errors = errors or []
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        if self.responses:
            return self.responses.pop(0)
        raise ModelError("no more fake responses")

    async def close(self):
        pass


def _lookup(provider):
    def get(name):
        if name == "model_provider.openrouter":
            return provider
        raise ModelError(f"no provider {name}")

    return get


def _req(schema: dict | None = None):
    return ModelRequest(
        messages=[Message(role="user", content="do the thing")],
        response_schema=schema,
    )


def _candidate(requested_model="cand"):
    return TournamentModelConfig(candidate_id="cand", requested_model=requested_model)


def _router(provider, candidate=None, role="reasoning", retries=1):
    return CandidateModelRouter(
        base_router=FakeBase(),
        role=role,
        candidate=candidate or _candidate(),
        service_lookup=_lookup(provider),
        timeout_seconds=5.0,
        retries=retries,
        benchmark_id="bench-v1",
    )


async def test_role_binding_and_fallback():
    provider = FakeProvider(
        responses=[
            ModelResponse(
                message=Message(role="assistant", content="{}"),
                model="cand",
                provider="openrouter",
                latency_ms=10.0,
            ),
        ]
    )
    router = _router(provider, role="reasoning")
    assert router.resolve("reasoning") == {"provider": "openrouter", "model": "cand"}
    assert router.resolve("fast") == {"provider": "openrouter", "model": "base-fast"}
    await router.complete("fast", _req())
    assert provider.calls == 0  # fallback never hit the candidate provider path

    resp = await router.complete("reasoning", _req())
    assert resp.model == "cand"
    assert len(router.records) == 1
    assert router.records[0].role == "reasoning"
    assert router.records[0].requested_model == "cand"


async def test_usage_and_cost_recorded():
    provider = FakeProvider(
        responses=[
            ModelResponse(
                message=Message(role="assistant", content="{}"),
                model="cand",
                provider="openrouter",
                latency_ms=25.0,
                usage=Usage(prompt_tokens=100, completion_tokens=40, total_tokens=140, cost=0.002),
            )
        ]
    )
    router = _router(provider)
    await router.complete("reasoning", _req())
    rec = router.records[0]
    assert rec.prompt_tokens == 100
    assert rec.completion_tokens == 40
    assert rec.total_tokens == 140
    assert rec.provider_cost == 0.002
    assert rec.cost_source == "provider"
    assert rec.latency_ms == 25.0
    assert rec.status == "success"


async def test_structured_output_failure_detected():
    provider = FakeProvider(
        responses=[
            ModelResponse(
                message=Message(role="assistant", content="not valid json"),
                model="cand",
                latency_ms=10.0,
            )
        ]
    )
    router = _router(provider)
    resp = await router.complete("reasoning", _req(schema={"type": "object"}))
    assert resp is not None  # response still returned; service decides
    rec = router.records[0]
    assert rec.status == "structured_output_failure"
    assert rec.failure == TournamentFailureKind.structured_output_failure


async def test_retries_then_success():
    provider = FakeProvider(
        errors=[ModelError("OpenRouter request timed out after 30s")],
        responses=[
            ModelResponse(
                message=Message(role="assistant", content="{}"), model="cand", latency_ms=5.0
            )
        ],
    )
    router = _router(provider, retries=2)
    await router.complete("reasoning", _req())
    assert provider.calls == 2
    assert len(router.records) == 1  # one logical call, retries=1
    assert router.records[0].retries == 1
    assert router.records[0].status == "success"
    assert router.records[0].failure is None


async def test_rate_limit_classified_and_not_retried_when_exhausted():
    provider = FakeProvider(errors=[ModelError("OpenRouter rate limited (429)")] * 3)
    router = _router(provider, retries=1)
    with pytest.raises(ModelError):
        await router.complete("reasoning", _req())
    assert len(router.records) == 1
    assert router.records[0].status == "error"
    assert router.records[0].failure == TournamentFailureKind.rate_limit
    assert router.records[0].retries == 1  # one retry attempted before final failure


async def test_timeout_classification():
    provider = FakeProvider(errors=[ModelError("OpenRouter request timed out after 30s")] * 5)
    router = _router(provider, retries=0)
    with pytest.raises(ModelError):
        await router.complete("reasoning", _req())
    assert router.records[0].failure == TournamentFailureKind.timeout


def test_classify_model_error():
    assert (
        _classify_model_error(ModelError("rate limited (429)")) == TournamentFailureKind.rate_limit
    )
    assert _classify_model_error(ModelError("timed out")) == TournamentFailureKind.timeout
    assert (
        _classify_model_error(ModelError("validation error"))
        == TournamentFailureKind.validation_failure
    )
    assert _classify_model_error(ModelError("upstream 500")) == TournamentFailureKind.provider_error
