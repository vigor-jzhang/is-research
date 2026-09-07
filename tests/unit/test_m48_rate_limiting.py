"""Regression tests for M48a and M48b (round 33).

M48a — no rate limiting anywhere: the only way to learn a provider's limit was
to exceed it and eat a 429. Retries existed, but the budget was a module
constant and nothing paced requests proactively.

M48b — `_PinnedNetworkBackend._addresses` was mutated without a lock.

M48c (a global concurrency cap) is deliberately NOT implemented — see the
round-33 notes. There is no concurrency in src/ at all, so a semaphore would be
inert, which is the failure mode M85 was fixed for.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from research_harness.plugins.models.openrouter.plugin import MAX_RETRIES, OpenRouterProvider


def _ok_response() -> Any:
    class _Resp:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "id": "gen-1",
                "model": "m",
                "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }

    return _Resp()


def _retryable(status: int = 429, retry_after: str | None = None) -> Any:
    class _Resp:
        status_code = status
        text = "rate limited"

        @property
        def headers(self) -> dict[str, str]:
            return {"Retry-After": retry_after} if retry_after else {}

        def json(self) -> dict[str, Any]:
            return {}

    return _Resp()


class _Client:
    """Records posts and returns a scripted sequence of responses."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[int] = []

    async def post(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(1)
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


def _request() -> Any:
    from research_harness.contracts.model import Message, ModelRequest

    return ModelRequest(
        messages=[Message(role="user", content="hello")],
        metadata={"model": "some/model"},
    )


# ---------------------------------------------------------------------------
# M48a — pacing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requests_are_paced() -> None:
    """M48a: with requests_per_second set, calls are spaced out.

    Nothing paced requests at all, so every caller found the provider's limit by
    exceeding it.
    """
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    client = _Client([_ok_response()])
    provider = OpenRouterProvider(
        api_key="k",
        http_client=client,
        sleep_fn=fake_sleep,
        requests_per_second=10.0,  # 0.1 s apart
    )
    await provider.complete(_request())
    await provider.complete(_request())

    assert len(client.calls) == 2
    pacing = [s for s in sleeps if s > 0]
    assert pacing, "the second request went out without waiting"
    assert 0.05 < pacing[0] <= 0.1, f"paced by {pacing[0]}s, expected ~0.1s"


@pytest.mark.asyncio
async def test_unpaced_by_default() -> None:
    """Guard: no requests_per_second means no delay — the old behaviour."""
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    client = _Client([_ok_response()])
    provider = OpenRouterProvider(api_key="k", http_client=client, sleep_fn=fake_sleep)
    await provider.complete(_request())
    await provider.complete(_request())
    assert sleeps == [], f"paced without being asked to: {sleeps}"


@pytest.mark.asyncio
async def test_pacing_applies_to_every_retry_attempt() -> None:
    """M48a: a retry is another outbound request, so it is paced too."""
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    client = _Client([_retryable(), _ok_response()])
    provider = OpenRouterProvider(
        api_key="k",
        http_client=client,
        sleep_fn=fake_sleep,
        max_retries=1,
        requests_per_second=10.0,
    )
    await provider.complete(_request())
    assert len(client.calls) == 2
    # one pacing wait before each attempt, plus the backoff after the 429
    assert len([s for s in sleeps if s > 0]) >= 2


# ---------------------------------------------------------------------------
# M48a — the retry budget was a constant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_retries_is_respected() -> None:
    """M48a: the budget was the module constant MAX_RETRIES, whatever config said."""

    async def fake_sleep(delay: float) -> None:
        return None

    client = _Client([_retryable()])
    provider = OpenRouterProvider(
        api_key="k",
        http_client=client,
        sleep_fn=fake_sleep,
        max_retries=4,
    )
    from research_harness.kernel.errors import ModelError

    with pytest.raises(ModelError):
        await provider.complete(_request())
    assert len(client.calls) == 5, f"max_retries=4 should allow 5 attempts, got {len(client.calls)}"


@pytest.mark.asyncio
async def test_default_retry_budget_is_unchanged() -> None:
    """Guard: the default still matches the old constant."""

    async def fake_sleep(delay: float) -> None:
        return None

    client = _Client([_retryable()])
    provider = OpenRouterProvider(api_key="k", http_client=client, sleep_fn=fake_sleep)
    from research_harness.kernel.errors import ModelError

    with pytest.raises(ModelError):
        await provider.complete(_request())
    assert len(client.calls) == MAX_RETRIES + 1


def test_config_exposes_the_knobs() -> None:
    """M48a: config had no requests_per_second / max_retries fields at all."""
    from research_harness.config.schema import ModelsConfig

    cfg = ModelsConfig(roles={})
    assert cfg.requests_per_second is None, "pacing is opt-in"
    assert cfg.max_retries == 2

    cfg2 = ModelsConfig(roles={}, requests_per_second=2.5, max_retries=5)
    assert cfg2.requests_per_second == 2.5
    assert cfg2.max_retries == 5


def test_provider_config_is_derived_for_openrouter() -> None:
    """M48a: the knobs have to reach the provider to mean anything."""
    from research_harness.app.bootstrap import _derived_plugin_configs
    from research_harness.config.schema import AppConfig

    cfg = AppConfig.model_validate(
        {
            "plugins": [],
            "models": {
                "roles": {
                    "fast": {"provider": "openrouter", "model": "m"},
                    "reasoning": {"provider": "openrouter", "model": "m"},
                    "critic": {"provider": "openrouter", "model": "m"},
                },
                "requests_per_second": 3.0,
                "max_retries": 7,
            },
        }
    )
    pc = _derived_plugin_configs(cfg, None)
    assert pc["model.openrouter"]["requests_per_second"] == 3.0
    assert pc["model.openrouter"]["max_retries"] == 7


# ---------------------------------------------------------------------------
# M48b — the pinned address map was unlocked
# ---------------------------------------------------------------------------


def test_pinned_backend_guards_its_address_map() -> None:
    """M48b: `pin` wrote the map and `connect_tcp` read it with no lock."""
    from research_harness.plugins.documents.fetcher_http.plugin import _PinnedNetworkBackend

    backend = _PinnedNetworkBackend()
    assert hasattr(backend, "_lock"), "no lock on the address map"


@pytest.mark.asyncio
async def test_pin_and_read_serialize() -> None:
    """M48b: pin/reads take the same lock, so they cannot interleave."""
    from research_harness.plugins.documents.fetcher_http.plugin import _PinnedNetworkBackend

    backend = _PinnedNetworkBackend()
    order: list[str] = []
    real_lock = backend._lock

    class _TrackingLock:
        def __init__(self) -> None:
            self._l = asyncio.Lock()

        async def __aenter__(self) -> _TrackingLock:
            await self._l.acquire()
            order.append("acquired")
            return self

        async def __aexit__(self, *exc: Any) -> None:
            order.append("released")
            self._l.release()

    backend._lock = _TrackingLock()  # type: ignore[assignment]
    await backend.pin("example.com", 443, ("1.2.3.4",))
    assert order == ["acquired", "released"]
    assert backend._addresses[("example.com", 443)] == ("1.2.3.4",)
    del real_lock
