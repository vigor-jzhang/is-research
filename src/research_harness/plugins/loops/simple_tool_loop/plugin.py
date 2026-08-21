"""Simple tool loop plugin."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from research_harness.contracts.loop import LoopResult
from research_harness.contracts.model import Message, ModelRequest, ToolSpec
from research_harness.kernel.errors import LoopLimitError, ServiceError
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata

logger = logging.getLogger(__name__)


class SimpleToolLoop:
    """Minimal agent loop: model -> tool dispatch -> model until final."""

    def __init__(
        self,
        max_steps: int = 8,
        service_lookup: Any | None = None,
        events: Any | None = None,
        session_store: Any | None = None,
        source: str = "loop.simple_tool_loop",
    ) -> None:
        self.max_steps = max_steps
        self._service_lookup = service_lookup
        self._events = events
        self._session_store = session_store
        self._source = source

    async def run(
        self,
        prompt: str,
        *,
        role: str = "fast",
        session_id: str | None = None,
        tools: list[str] | None = None,
        **kwargs: Any,
    ) -> LoopResult:
        run_id = str(uuid.uuid4())
        # Create session if not provided
        if session_id is None and self._session_store is not None:
            try:
                session_id = await self._session_store.create_session(
                    {"created_by": "simple_tool_loop", "prompt": prompt[:200]}
                )
            except Exception as e:
                logger.warning("failed to create session: %s", e)
                session_id = None

        await self._emit(
            "run.started", {"prompt": prompt, "role": role, "run_id": run_id}, session_id, run_id
        )

        # Resolve model_router
        if self._service_lookup is None:
            raise RuntimeError("SimpleToolLoop service_lookup not configured")
        try:
            router = self._service_lookup("model_router.default")
        except ServiceError as e:
            await self._emit("run.failed", {"error": str(e), "run_id": run_id}, session_id, run_id)
            raise

        # Build tool specs for allowed tools
        allowed_tools = tools or ["echo"]
        tool_specs: list[ToolSpec] = []
        tool_instances: dict[str, Any] = {}
        for tname in allowed_tools:
            svc_name = f"tool.{tname}"
            tool = None
            try:
                tool = self._service_lookup(svc_name)
            except ServiceError:
                logger.debug("tool %s not available, skipping", tname)
                continue
            tool_instances[tname] = tool
            # Tool protocol has name, description, input_schema
            try:
                tool_specs.append(
                    ToolSpec(
                        name=tool.name,  # type: ignore[attr-defined]
                        description=tool.description,  # type: ignore[attr-defined]
                        parameters=tool.input_schema,  # type: ignore[attr-defined]
                    )
                )
            except Exception as e:
                logger.warning("failed to build spec for tool %s: %s", tname, e)

        messages: list[Message] = [Message(role="user", content=prompt)]
        steps = 0
        final_output: str | None = None

        for step in range(self.max_steps):
            steps = step + 1
            req = ModelRequest(
                messages=list(messages),
                tools=tool_specs if tool_specs else None,
                metadata={},
            )
            await self._emit(
                "model.requested",
                {"step": steps, "role": role, "messages": [m.model_dump() for m in messages]},
                session_id,
                run_id,
            )

            try:
                response = await router.complete(role, req)
            except Exception as e:
                await self._emit(
                    "model.failed", {"error": str(e), "step": steps}, session_id, run_id
                )
                await self._emit(
                    "run.failed", {"error": str(e), "run_id": run_id}, session_id, run_id
                )
                raise

            await self._emit(
                "model.completed",
                {
                    "step": steps,
                    "content": response.message.content,
                    "tool_calls": [tc.model_dump() for tc in response.tool_calls],
                    "finish_reason": response.finish_reason,
                    "usage": response.usage.model_dump() if response.usage else None,
                    "model": response.model,
                },
                session_id,
                run_id,
            )

            # If model returned tool calls, dispatch them
            if response.tool_calls:
                # Append assistant message with tool calls
                messages.append(
                    Message(
                        role="assistant",
                        content=response.message.content,
                        tool_calls=response.tool_calls,
                    )
                )
                for tc in response.tool_calls:
                    tname = tc.name
                    await self._emit(
                        "tool.requested",
                        {"tool": tname, "arguments": tc.arguments, "id": tc.id},
                        session_id,
                        run_id,
                    )
                    tool = tool_instances.get(tname)
                    if tool is None:
                        # Try dynamic lookup
                        try:
                            tool = self._service_lookup(f"tool.{tname}")
                            tool_instances[tname] = tool
                        except ServiceError:
                            err_msg = f"tool {tname!r} not found"
                            await self._emit(
                                "tool.failed", {"tool": tname, "error": err_msg}, session_id, run_id
                            )
                            # Append tool error as message
                            messages.append(
                                Message(
                                    role="tool",
                                    content=f"Error: {err_msg}",
                                    tool_call_id=tc.id,
                                    name=tname,
                                )
                            )
                            continue
                    try:
                        # Normalize arguments
                        args = tc.arguments
                        if isinstance(args, str):
                            import json

                            try:
                                args = json.loads(args) if args.strip() else {}
                            except json.JSONDecodeError:
                                args = {"text": args}
                        if not isinstance(args, dict):
                            args = {"value": args}
                        result = await tool.execute(args)  # type: ignore[attr-defined]
                        # Ensure result is JSON-serializable
                        if not isinstance(result, dict):
                            result = {"result": result}
                        import json

                        content = json.dumps(result, ensure_ascii=False)
                        await self._emit(
                            "tool.completed",
                            {"tool": tname, "result": result, "id": tc.id},
                            session_id,
                            run_id,
                        )
                        messages.append(
                            Message(role="tool", content=content, tool_call_id=tc.id, name=tname)
                        )
                    except Exception as e:
                        err = f"tool {tname} failed: {e}"
                        await self._emit(
                            "tool.failed", {"tool": tname, "error": err}, session_id, run_id
                        )
                        messages.append(
                            Message(
                                role="tool", content=f"Error: {err}", tool_call_id=tc.id, name=tname
                            )
                        )
                # Continue loop for next model call with tool results
                continue
            else:
                # No tool calls -> final answer
                final_output = response.message.content or ""
                break
        else:
            # Loop exhausted without break
            msg = f"loop exceeded max_steps={self.max_steps}"
            await self._emit("run.failed", {"error": msg, "run_id": run_id}, session_id, run_id)
            raise LoopLimitError(msg)

        await self._emit(
            "run.completed",
            {"output": final_output, "steps": steps, "run_id": run_id},
            session_id,
            run_id,
        )
        return LoopResult(
            output=final_output or "",
            steps=steps,
            session_id=session_id,
            run_id=run_id,
            metadata={},
        )

    async def _emit(
        self, event_type: str, payload: dict[str, Any], session_id: str | None, run_id: str | None
    ) -> None:
        if self._events is None:
            return
        from research_harness.kernel.events import Event

        event = Event.create(
            event_type=event_type,
            source=self._source,
            payload=payload,
            session_id=session_id,
            run_id=run_id,
        )
        # Publish to EventBus (transport). Persistence is handled by the
        # session plugin's subscriber which listens to all events with a
        # session_id and appends them to the JSONL store. This keeps the
        # loop decoupled from persistence and makes SessionStore the
        # single source of truth for replay.
        await self._events.publish(event)


class SimpleToolLoopPlugin(Plugin):
    def __init__(self, max_steps: int | None = None) -> None:
        self._max_steps_override = max_steps
        self._loop: SimpleToolLoop | None = None
        self._ctx: PluginContext | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="loop.simple_tool_loop",
            version="0.1.0",
            plugin_type="agent_loop",
            description="Simple tool-calling agent loop",
            provides=["agent_loop.default"],
            requires=["model_router.default"],
            optional_requires=["tool.echo", "session_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        cfg = ctx.config or {}
        max_steps = self._max_steps_override
        if max_steps is None:
            if "loop" in cfg and isinstance(cfg["loop"], dict):
                max_steps = cfg["loop"].get("max_steps", 8)
            elif "max_steps" in cfg:
                max_steps = cfg["max_steps"]  # type: ignore[assignment]
            else:
                max_steps = 8

        # Lazy lookup for session_store to avoid hard dependency
        def lookup(name: str) -> Any:
            return ctx.require(name)

        # Try to get session_store optionally
        session_store = ctx.try_get("session_store.default")

        loop = SimpleToolLoop(
            max_steps=int(max_steps),  # type: ignore[arg-type]
            service_lookup=lookup,
            events=ctx.events,
            session_store=session_store,
            source=ctx.plugin_id,
        )
        self._loop = loop
        ctx.register("agent_loop.default", loop)
