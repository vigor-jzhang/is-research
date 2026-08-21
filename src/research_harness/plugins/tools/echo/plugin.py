"""Echo tool plugin - deterministic test tool."""

from __future__ import annotations

from typing import Any

from research_harness.kernel.errors import ToolError
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata


class EchoTool:
    name = "echo"
    description = "Echoes the input text back. Deterministic test tool."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to echo"},
        },
        "required": ["text"],
        "additionalProperties": False,
    }

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        text = arguments.get("text")
        if text is None:
            raise ToolError("echo tool requires 'text' argument")
        if not isinstance(text, str):
            raise ToolError(f"echo 'text' must be string, got {type(text).__name__}")
        return {"echoed": text}


class EchoToolPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="tool.echo",
            version="0.1.0",
            plugin_type="tool",
            description="Echo tool for pipeline validation",
            provides=["tool.echo"],
            requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        tool = EchoTool()
        ctx.register("tool.echo", tool)
