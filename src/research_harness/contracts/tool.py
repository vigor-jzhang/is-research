"""Tool contract."""

from __future__ import annotations

from typing import Any, Protocol


class Tool(Protocol):
    """Interface for tool plugins."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def input_schema(self) -> dict[str, Any]: ...

    async def execute(self, arguments: dict[str, Any]) -> Any:
        """Execute tool with arguments and return result."""
        ...
