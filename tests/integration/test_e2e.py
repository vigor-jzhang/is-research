import pathlib

import pytest

from research_harness.app.bootstrap import build_runtime
from research_harness.config.loader import load_config_from_dict
from research_harness.contracts.model import Message, ModelRequest, ModelResponse, ToolCall
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.plugins.sessions.jsonl.plugin import JsonlSessionStore


class FakeProviderPlugin(Plugin):
    """Fake model provider for integration tests."""

    def __init__(self, responses: list[ModelResponse]):
        self._responses = responses
        self.calls: list[ModelRequest] = []

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="model.openrouter",
            version="0.1.0",
            plugin_type="model",
            description="fake",
            provides=["model_provider.openrouter"],
            requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        class Provider:
            capabilities = None

            async def complete(inner_self, request: ModelRequest) -> ModelResponse:
                self.calls.append(request)
                if not self._responses:
                    raise RuntimeError("no fake responses left")
                return self._responses.pop(0)

            async def close(inner_self):
                pass

        ctx.register("model_provider.openrouter", Provider())


@pytest.mark.asyncio
async def test_e2e_mocked_run(tmp_path: pathlib.Path):
    # Prepare fake responses: tool call then final
    responses = [
        ModelResponse(
            message=Message(
                role="assistant",
                content=None,
                tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "hello world"})],
            ),
            tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "hello world"})],
            finish_reason="tool_calls",
            model="fake",
        ),
        ModelResponse(
            message=Message(role="assistant", content="The echo result was hello world"),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        ),
    ]
    fake_provider_plugin = FakeProviderPlugin(responses=responses)

    cfg = load_config_from_dict(
        {
            "runtime": {"autonomy": "high"},
            "plugins": [
                "routing.role_router",
                "session.jsonl",
                "autonomy.configurable",
                "tool.echo",
                "loop.simple_tool_loop",
            ],
            "models": {"roles": {"fast": {"provider": "openrouter", "model": "fake-model"}}},
            "session": {"root": str(tmp_path / "sessions")},
            "loop": {"max_steps": 8},
        }
    )

    runtime = build_runtime(cfg, extra_plugins=[fake_provider_plugin])
    async with runtime:
        loop = runtime.services.require("agent_loop.default")
        result = await loop.run("please echo hello world", role="fast")
        assert "hello world" in result.output
        assert result.steps == 2
        assert result.session_id is not None

        # Verify session events persisted
        store: JsonlSessionStore = runtime.services.require("session_store.default")
        events = await store.read(result.session_id)
        # Should have run.started, model.requested, model.completed, tool.requested, tool.completed, etc.
        types = [e["event_type"] for e in events]
        assert "run.started" in types
        assert "model.requested" in types
        assert "tool.completed" in types
        assert "run.completed" in types

        # Verify model was called with correct model slug
        assert fake_provider_plugin.calls[0].metadata["model"] == "fake-model"
        # Ensure no secrets in events
        import json

        assert "OPENROUTER_API_KEY" not in json.dumps(events)


@pytest.mark.asyncio
async def test_e2e_config_validation(tmp_path: pathlib.Path):
    # Ensure runtime fails gracefully on bad config
    from research_harness.kernel.errors import PluginDependencyError

    cfg = load_config_from_dict(
        {
            "runtime": {"autonomy": "high"},
            "plugins": ["loop.simple_tool_loop"],  # missing dependencies
            "models": {"roles": {"fast": {"provider": "openrouter", "model": "m"}}},
            "session": {"root": str(tmp_path / "sessions")},
            "loop": {"max_steps": 5},
        }
    )
    runtime = build_runtime(cfg)
    with pytest.raises(PluginDependencyError):
        await runtime.start()
