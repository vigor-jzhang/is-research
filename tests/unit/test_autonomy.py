import pytest

from research_harness.contracts.autonomy import ApprovalRequest
from research_harness.kernel.events import EventBus
from research_harness.kernel.plugin import PluginContext
from research_harness.plugins.autonomy.configurable.plugin import (
    ConfigurableAutonomyPlugin,
    ConfigurableAutonomyPolicy,
)


@pytest.mark.asyncio
async def test_high_never_requires():
    policy = ConfigurableAutonomyPolicy(mode="high")
    assert await policy.requires_approval("research_question") is False
    assert await policy.requires_approval("anything") is False


@pytest.mark.asyncio
async def test_interactive_requires_important():
    policy = ConfigurableAutonomyPolicy(mode="interactive")
    assert await policy.requires_approval("research_question") is True
    assert await policy.requires_approval("literature_set") is True
    assert await policy.requires_approval("random_checkpoint") is False


@pytest.mark.asyncio
async def test_request_approval_high():
    bus = EventBus()
    policy = ConfigurableAutonomyPolicy(mode="high", events=bus)
    req = ApprovalRequest(request_id="1", checkpoint="research_question", description="test")
    decision = await policy.request_approval(req)
    assert decision.approved is True
    assert decision.request_id == "1"


@pytest.mark.asyncio
async def test_plugin_setup():
    bus = EventBus()
    from research_harness.kernel.services import ServiceRegistry

    reg = ServiceRegistry()
    plugin = ConfigurableAutonomyPlugin()
    ctx = PluginContext(
        plugin_id="autonomy.configurable",
        config={"autonomy": "interactive"},
        services=reg,
        events=bus,
    )
    await plugin.setup(ctx)
    policy = reg.require("autonomy_policy.default")
    assert policy.mode == "interactive"
