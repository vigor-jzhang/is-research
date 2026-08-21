import pytest

from research_harness.contracts.model import Message, ModelRequest, ModelResponse
from research_harness.kernel.errors import ModelError
from research_harness.kernel.events import EventBus
from research_harness.kernel.plugin import PluginContext
from research_harness.kernel.services import ServiceRegistry
from research_harness.plugins.routing.role_router.plugin import RoleRouter, RoleRouterPlugin


class FakeProvider:
    def __init__(self):
        self.last_request = None
        self.capabilities = None

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.last_request = request
        return ModelResponse(
            message=Message(role="assistant", content="ok"),
            tool_calls=[],
            finish_reason="stop",
            model=request.metadata.get("model"),
        )


@pytest.mark.asyncio
async def test_role_router_resolve():
    reg = ServiceRegistry()
    provider = FakeProvider()
    reg.register("model_provider.openrouter", provider, owner="test")
    router = RoleRouter(
        roles={
            "fast": {"provider": "openrouter", "model": "m1"},
            "reasoning": {"provider": "openrouter", "model": "m2"},
        },
        service_lookup=reg.require,
    )
    assert router.resolve("fast") == {"provider": "openrouter", "model": "m1"}
    with pytest.raises(ModelError, match="unknown model role"):
        router.resolve("unknown")


@pytest.mark.asyncio
async def test_role_router_complete():
    reg = ServiceRegistry()
    provider = FakeProvider()
    reg.register("model_provider.openrouter", provider, owner="test")
    router = RoleRouter(
        roles={"fast": {"provider": "openrouter", "model": "my-model"}}, service_lookup=reg.require
    )
    req = ModelRequest(messages=[Message(role="user", content="hi")], metadata={})
    resp = await router.complete("fast", req)
    assert resp.model == "my-model"
    assert provider.last_request is not None
    assert provider.last_request.metadata["model"] == "my-model"


@pytest.mark.asyncio
async def test_role_router_missing_provider():
    reg = ServiceRegistry()
    router = RoleRouter(
        roles={"fast": {"provider": "openrouter", "model": "m"}}, service_lookup=reg.require
    )
    req = ModelRequest(messages=[Message(role="user", content="hi")], metadata={})
    with pytest.raises(ModelError, match="no provider"):
        await router.complete("fast", req)


@pytest.mark.asyncio
async def test_plugin_setup():
    bus = EventBus()
    reg = ServiceRegistry()
    provider = FakeProvider()
    reg.register("model_provider.openrouter", provider, owner="test")
    plugin = RoleRouterPlugin()
    ctx = PluginContext(
        plugin_id="routing.role_router",
        config={"models": {"roles": {"fast": {"provider": "openrouter", "model": "m1"}}}},
        services=reg,
        events=bus,
    )
    await plugin.setup(ctx)
    assert reg.has("model_router.default")
    router = reg.require("model_router.default")
    assert router.resolve("fast")["model"] == "m1"
