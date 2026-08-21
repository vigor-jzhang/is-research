"""Model routing contract."""

from __future__ import annotations

from typing import Protocol

from research_harness.contracts.model import ModelRequest, ModelResponse


class ModelRouter(Protocol):
    """Maps logical model roles to provider/model and delegates."""

    async def complete(self, role: str, request: ModelRequest) -> ModelResponse:
        """Route a request for a logical role to the appropriate provider."""
        ...

    def resolve(self, role: str) -> dict[str, str]:
        """Return provider/model mapping for a role."""
        ...
