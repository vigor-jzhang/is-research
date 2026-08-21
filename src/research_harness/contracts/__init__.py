"""Contracts - typed interfaces for plugin services."""

from research_harness.contracts.autonomy import ApprovalDecision, ApprovalRequest, AutonomyPolicy
from research_harness.contracts.common import Usage
from research_harness.contracts.loop import AgentLoop, LoopResult
from research_harness.contracts.model import (
    Message,
    ModelCapabilities,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ToolCall,
    ToolSpec,
)
from research_harness.contracts.routing import ModelRouter
from research_harness.contracts.session import SessionEvent, SessionStore
from research_harness.contracts.tool import Tool

__all__ = [
    "AgentLoop",
    "ApprovalDecision",
    "ApprovalRequest",
    "AutonomyPolicy",
    "LoopResult",
    "Message",
    "ModelCapabilities",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "ModelRouter",
    "SessionEvent",
    "SessionStore",
    "Tool",
    "ToolCall",
    "ToolSpec",
    "Usage",
]
