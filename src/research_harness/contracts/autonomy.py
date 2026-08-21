"""Autonomy policy contract."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class ApprovalRequest(BaseModel):
    request_id: str
    checkpoint: str
    description: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    request_id: str
    approved: bool
    reason: str | None = None
    decided_by: str = "policy"


class AutonomyPolicy(Protocol):
    """Decides whether human approval is required."""

    @property
    def mode(self) -> str: ...

    async def requires_approval(self, checkpoint: str) -> bool:
        """Return True if approval is needed for the checkpoint."""
        ...

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        """Request approval for a checkpoint; returns decision per policy."""
        ...
