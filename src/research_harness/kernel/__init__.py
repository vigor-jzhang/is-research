"""Kernel package - minimal infrastructure."""

from research_harness.kernel.errors import (
    AutonomyError,
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
from research_harness.kernel.runtime import Runtime
from research_harness.kernel.services import ServiceRegistry

__all__ = [
    "AutonomyError",
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
