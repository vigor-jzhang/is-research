import httpx
import pytest
import respx

from research_harness.contracts.model import Message, ModelRequest, ToolSpec
from research_harness.kernel.errors import ModelError
from research_harness.plugins.models.openrouter.plugin import OpenRouterProvider


async def _no_sleep(_delay: float) -> None:
    """Skip the real backoff so retry tests stay fast and deterministic."""
    return None



@pytest.mark.asyncio
@respx.mock
async def test_successful_response():
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "gen-1",
                "model": "openai/gpt-4o-mini",
                "choices": [
                    {"message": {"role": "assistant", "content": "hello"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )
    )
    provider = OpenRouterProvider(api_key="test-key", base_url="https://openrouter.ai/api/v1")
    req = ModelRequest(
        messages=[Message(role="user", content="hi")],
        metadata={"model": "openai/gpt-4o-mini"},
    )
    resp = await provider.complete(req)
    assert route.called
    assert resp.message.content == "hello"
    assert resp.finish_reason == "stop"
    assert resp.usage is not None
    assert resp.usage.total_tokens == 15
    assert resp.model == "openai/gpt-4o-mini"


@pytest.mark.asyncio
@respx.mock
async def test_tool_call_parsing():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "echo", "arguments": '{"text": "hi"}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            },
        )
    )
    provider = OpenRouterProvider(
            api_key="k",
            base_url="https://openrouter.ai/api/v1",
            sleep_fn=_no_sleep,
        )
    req = ModelRequest(
        messages=[Message(role="user", content="use echo")],
        tools=[
            ToolSpec(
                name="echo",
                description="echo",
                parameters={"type": "object", "properties": {"text": {"type": "string"}}},
            )
        ],
        metadata={"model": "m"},
    )
    resp = await provider.complete(req)
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "echo"
    assert resp.tool_calls[0].arguments == {"text": "hi"}


@pytest.mark.asyncio
@respx.mock
async def test_structured_output():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": '{"a": 1}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {},
            },
        )
    )
    provider = OpenRouterProvider(
            api_key="k",
            base_url="https://openrouter.ai/api/v1",
            sleep_fn=_no_sleep,
        )
    req = ModelRequest(
        messages=[Message(role="user", content="give json")],
        response_schema={"type": "object", "properties": {"a": {"type": "integer"}}},
        metadata={"model": "m"},
    )
    resp = await provider.complete(req)
    assert resp.message.content == '{"a": 1}'


@pytest.mark.asyncio
@respx.mock
async def test_auth_failure_normalized():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(401, text="Unauthorized")
    )
    provider = OpenRouterProvider(
            api_key="bad",
            base_url="https://openrouter.ai/api/v1",
            sleep_fn=_no_sleep,
        )
    req = ModelRequest(messages=[Message(role="user", content="hi")], metadata={"model": "m"})
    with pytest.raises(ModelError, match="authentication failed"):
        await provider.complete(req)


@pytest.mark.asyncio
@respx.mock
async def test_timeout_handling():
    # Simulate timeout by making the mock raise TimeoutException
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        side_effect=httpx.TimeoutException("timeout")
    )
    provider = OpenRouterProvider(api_key="k", base_url="https://openrouter.ai/api/v1", timeout=0.1)
    req = ModelRequest(messages=[Message(role="user", content="hi")], metadata={"model": "m"})
    with pytest.raises(ModelError, match="timed out"):
        await provider.complete(req)


@pytest.mark.asyncio
@respx.mock
async def test_malformed_response():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"bad": "data"})
    )
    provider = OpenRouterProvider(
            api_key="k",
            base_url="https://openrouter.ai/api/v1",
            sleep_fn=_no_sleep,
        )
    req = ModelRequest(messages=[Message(role="user", content="hi")], metadata={"model": "m"})
    with pytest.raises(ModelError, match="malformed"):
        await provider.complete(req)


@pytest.mark.asyncio
async def test_missing_api_key():
    provider = OpenRouterProvider(api_key=None, base_url="https://openrouter.ai/api/v1")
    # Ensure env var not set for this test
    import os

    os.environ.pop("OPENROUTER_API_KEY", None)
    # Create new provider without env
    provider2 = OpenRouterProvider(api_key="", base_url="https://openrouter.ai/api/v1")
    req = ModelRequest(messages=[Message(role="user", content="hi")], metadata={"model": "m"})
    with pytest.raises(ModelError, match="OPENROUTER_API_KEY"):
        await provider2.complete(req)


@pytest.mark.asyncio
@respx.mock
async def test_usage_extraction():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 2,
                    "total_tokens": 3,
                    "cost": 0.001,
                },
            },
        )
    )
    provider = OpenRouterProvider(
            api_key="k",
            base_url="https://openrouter.ai/api/v1",
            sleep_fn=_no_sleep,
        )
    req = ModelRequest(messages=[Message(role="user", content="hi")], metadata={"model": "m"})
    resp = await provider.complete(req)
    assert resp.usage is not None
    assert resp.usage.cost == 0.001


@pytest.mark.asyncio
@respx.mock
async def test_generic_error_normalized():
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(500, text="internal")
    )
    provider = OpenRouterProvider(
            api_key="k",
            base_url="https://openrouter.ai/api/v1",
            sleep_fn=_no_sleep,
        )
    req = ModelRequest(messages=[Message(role="user", content="hi")], metadata={"model": "m"})
    with pytest.raises(ModelError, match="500"):
        await provider.complete(req)


@pytest.mark.asyncio
@respx.mock
async def test_retry_on_transient_failure():
    # First call timeout, second succeeds
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("connect failed")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "recovered"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(side_effect=side_effect)
    provider = OpenRouterProvider(
            api_key="k",
            base_url="https://openrouter.ai/api/v1",
            sleep_fn=_no_sleep,
        )
    req = ModelRequest(messages=[Message(role="user", content="hi")], metadata={"model": "m"})
    resp = await provider.complete(req)
    assert resp.message.content == "recovered"
    assert call_count == 2


@pytest.mark.asyncio
async def test_provider_reuses_owned_client():
    provider = OpenRouterProvider(api_key="k")
    first = provider._get_client()
    assert provider._get_client() is first
    await provider.close()
    assert first.is_closed
