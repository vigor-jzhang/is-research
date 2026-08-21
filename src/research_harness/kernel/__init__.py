"""Kernel package - minimal infrastructure."""

from typing import TYPE_CHECKING

from research_harness.kernel.errors import (
    ConfigurationError,
    LoopLimitError,
    ModelError,
    PluginDependencyError,
    PluginError,
    ResearchHarnessError,
    ServiceError,
    SessionError,
    ToolError,
)
from research_harness.kernel.events import Event, EventBus
from research_harness.kernel.manager import PluginManager
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.kernel.services import ServiceRegistry

if TYPE_CHECKING:
    from research_harness.kernel.runtime import Runtime  # noqa: F401

__all__ = [
    "ConfigurationError",
    "Event",
    "EventBus",
    "LoopLimitError",
    "ModelError",
    "Plugin",
    "PluginContext",
    "PluginDependencyError",
    "PluginError",
    "PluginManager",
    "PluginMetadata",
    "ResearchHarnessError",
    "Runtime",
    "ServiceError",
    "ServiceRegistry",
    "SessionError",
    "ToolError",
]
