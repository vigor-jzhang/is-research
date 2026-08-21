"""Agent loop contract."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel


class LoopResult(BaseModel):
    output: str
    steps: int
    session_id: str | None = None
    run_id: str | None = None
    metadata: dict[str, Any] = {}


class AgentLoop(Protocol):
    """Interface for agent loop plugins."""

    async def run(
        self,
        prompt: str,
        *,
        role: str = "fast",
        session_id: str | None = None,
        **kwargs: Any,
    ) -> LoopResult:
        """Execute an agent loop for the given prompt."""
        ...
