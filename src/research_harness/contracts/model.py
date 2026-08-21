"""Model abstraction contracts."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from research_harness.contracts.common import Usage


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] | str
    # arguments may be JSON string as returned by provider; normalized to dict when possible


class Message(BaseModel):
    role: str  # system, user, assistant, tool
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema


class ModelCapabilities(BaseModel):
    tool_calling: bool = False
    structured_output: bool = False
    streaming: bool = False
    context_length: int | None = None


class ModelRequest(BaseModel):
    messages: list[Message]
    tools: list[ToolSpec] | None = None
    response_schema: dict[str, Any] | None = Field(
        default=None, description="JSON schema for structured output"
    )
    temperature: float | None = None
    max_tokens: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class ModelResponse(BaseModel):
    message: Message
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage | None = None
    model: str | None = None
    provider: str | None = None
    latency_ms: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelProvider(Protocol):
    """Interface for model providers."""

    capabilities: ModelCapabilities

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Send a completion request and return response."""
        ...

    async def close(self) -> None: ...
