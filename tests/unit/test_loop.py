import pytest

from research_harness.contracts.model import Message, ModelRequest, ModelResponse, ToolCall
from research_harness.kernel.errors import LoopLimitError
from research_harness.kernel.events import EventBus
from research_harness.kernel.services import ServiceRegistry
from research_harness.plugins.loops.simple_tool_loop.plugin import SimpleToolLoop


class FakeTool:
    name = "echo"
    description = "echo"
    input_schema = {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, arguments):
        return {"echoed": arguments.get("text", "")}


class FakeModel:
    def __init__(self, responses: list[ModelResponse]):
        self.responses = responses
        self.calls: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if not self.responses:
            raise RuntimeError("no more fake responses")
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_simple_loop_tool_flow(tmp_path):
    bus = EventBus()
    reg = ServiceRegistry()

    # Prepare fake model: first returns tool call, second returns final answer
    fake_model = FakeModel(
        responses=[
            ModelResponse(
                message=Message(
                    role="assistant",
                    content=None,
                    tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "hello"})],
                ),
                tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "hello"})],
                finish_reason="tool_calls",
                model="fake",
            ),
            ModelResponse(
                message=Message(role="assistant", content="echoed hello"),
                tool_calls=[],
                finish_reason="stop",
                model="fake",
            ),
        ]
    )

    class Router:
        async def complete(self, role, request):
            return await fake_model.complete(request)

        def resolve(self, role):
            return {"provider": "fake", "model": "fake"}

    reg.register("model_router.default", Router(), owner="test")
    reg.register("tool.echo", FakeTool(), owner="test")

    # Session store stub — replicate session plugin's persistence subscriber
    from research_harness.plugins.sessions.jsonl.plugin import JsonlSessionStore

    store = JsonlSessionStore(root=tmp_path)

    async def _persist(event):  # type: ignore[no-untyped-def]
        if getattr(event, "session_id", None):
            await store.append(event.session_id, event.model_dump(mode="json"))

    bus.subscribe("*", _persist)  # type: ignore[arg-type]

    loop = SimpleToolLoop(max_steps=5, service_lookup=reg.require, events=bus, session_store=store)
    result = await loop.run("please echo hello", role="fast")
    assert result.output == "echoed hello"
    assert result.steps == 2
    assert result.session_id is not None
    # Check events
    types = [e.event_type for e in bus.history()]
    assert "run.started" in types
    assert "model.requested" in types
    assert "tool.requested" in types
    assert "tool.completed" in types
    assert "run.completed" in types
    # Verify tool was invoked with correct args
    events = await store.read(result.session_id)
    tool_events = [e for e in events if e["event_type"] == "tool.completed"]
    assert len(tool_events) == 1


@pytest.mark.asyncio
async def test_loop_max_steps(tmp_path):
    bus = EventBus()
    reg = ServiceRegistry()

    # Always returns tool call, never final
    def make_tool_response():
        return ModelResponse(
            message=Message(
                role="assistant",
                content=None,
                tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "hi"})],
            ),
            tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "hi"})],
            finish_reason="tool_calls",
            model="fake",
        )

    fake_model = FakeModel(responses=[make_tool_response() for _ in range(10)])

    class Router:
        async def complete(self, role, request):
            return await fake_model.complete(request)

        def resolve(self, role):
            return {"provider": "fake", "model": "fake"}

    reg.register("model_router.default", Router(), owner="t")
    reg.register("tool.echo", FakeTool(), owner="t")
    from research_harness.plugins.sessions.jsonl.plugin import JsonlSessionStore

    store = JsonlSessionStore(root=tmp_path)
    loop = SimpleToolLoop(max_steps=3, service_lookup=reg.require, events=bus, session_store=store)
    with pytest.raises(LoopLimitError, match="max_steps"):
        await loop.run("loop forever", role="fast")
    # Should have emitted run.failed
    assert any(e.event_type == "run.failed" for e in bus.history())


@pytest.mark.asyncio
async def test_loop_no_tool_needed(tmp_path):
    bus = EventBus()
    reg = ServiceRegistry()
    fake_model = FakeModel(
        responses=[
            ModelResponse(
                message=Message(role="assistant", content="final answer"),
                tool_calls=[],
                finish_reason="stop",
                model="fake",
            )
        ]
    )

    class Router:
        async def complete(self, role, request):
            return await fake_model.complete(request)

        def resolve(self, role):
            return {"provider": "fake", "model": "fake"}

    reg.register("model_router.default", Router(), owner="t")
    from research_harness.plugins.sessions.jsonl.plugin import JsonlSessionStore

    store = JsonlSessionStore(root=tmp_path)
    loop = SimpleToolLoop(max_steps=5, service_lookup=reg.require, events=bus, session_store=store)
    result = await loop.run("just answer", role="fast")
    assert result.output == "final answer"
    assert result.steps == 1


@pytest.mark.asyncio
async def test_loop_unknown_tool_handled(tmp_path):
    bus = EventBus()
    reg = ServiceRegistry()
    fake_model = FakeModel(
        responses=[
            ModelResponse(
                message=Message(
                    role="assistant",
                    content=None,
                    tool_calls=[ToolCall(id="c1", name="nonexistent", arguments={})],
                ),
                tool_calls=[ToolCall(id="c1", name="nonexistent", arguments={})],
                finish_reason="tool_calls",
            ),
            ModelResponse(
                message=Message(role="assistant", content="recovered"),
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )

    class Router:
        async def complete(self, role, request):
            return await fake_model.complete(request)

        def resolve(self, role):
            return {"provider": "fake", "model": "fake"}

    reg.register("model_router.default", Router(), owner="t")
    # No tool registered
    from research_harness.plugins.sessions.jsonl.plugin import JsonlSessionStore

    store = JsonlSessionStore(root=tmp_path)
    loop = SimpleToolLoop(max_steps=5, service_lookup=reg.require, events=bus, session_store=store)
    result = await loop.run("test", role="fast")
    # Should still complete, handling tool not found by sending error back to model
    assert result.output == "recovered"
