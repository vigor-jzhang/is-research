"""Plugin contract: metadata, base class, and context."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator

if TYPE_CHECKING:
    from research_harness.kernel.events import EventBus
    from research_harness.kernel.services import ServiceRegistry


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

_PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+.*$")


class PluginMetadata(BaseModel):
    """Validated plugin metadata."""

    id: str = Field(description="Unique plugin identifier, e.g. model.openrouter")
    version: str = Field(description="Semantic version")
    plugin_type: str = Field(description="Plugin type category")
    description: str = Field(default="", description="Human readable description")
    provides: list[str] = Field(default_factory=list, description="Services provided")
    requires: list[str] = Field(default_factory=list, description="Required services")
    optional_requires: list[str] = Field(
        default_factory=list, description="Optional service dependencies"
    )

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not _PLUGIN_ID_RE.match(v):
            raise ValueError(f"plugin id must match {_PLUGIN_ID_RE.pattern!r}, got {v!r}")
        return v

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: str) -> str:
        if not _VERSION_RE.match(v):
            raise ValueError(f"version must be semver-like, got {v!r}")
        return v

    @field_validator("provides", "requires", "optional_requires")
    @classmethod
    def validate_service_names(cls, v: list[str]) -> list[str]:
        for name in v:
            if not name:
                raise ValueError(f"service name must be non-empty string, got {name!r}")
            if " " in name:
                raise ValueError(f"service name must not contain spaces, got {name!r}")
        if len(v) != len(set(v)):
            raise ValueError(f"duplicate service names: {v}")
        return v

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Plugin Context
# ---------------------------------------------------------------------------


@dataclass
class PluginContext:
    """Limited context provided to each plugin.

    Plugins must not access kernel internals directly; they interact
    through this context object.
    """

    plugin_id: str
    config: dict[str, Any] = field(default_factory=dict)
    services: ServiceRegistry | None = None
    events: EventBus | None = None
    runtime_meta: dict[str, Any] = field(default_factory=dict)
    subscription_cleanups: list[Callable[[], None]] = field(default_factory=list)

    def require(self, service_name: str) -> Any:
        """Lookup a required service."""
        if self.services is None:
            raise RuntimeError("PluginContext.services not initialized")
        return self.services.require(service_name)

    def try_get(self, service_name: str) -> Any | None:
        """Try to get an optional service, return None if missing."""
        if self.services is None:
            return None
        return self.services.get(service_name)

    def register(self, service_name: str, instance: Any) -> None:
        """Register a provided service."""
        if self.services is None:
            raise RuntimeError("PluginContext.services not initialized")
        self.services.register(service_name, instance, owner=self.plugin_id)

    async def emit(self, event_type: str, payload: dict[str, Any], **kwargs: Any) -> Any:
        """Emit an event via the event bus."""
        if self.events is None:
            raise RuntimeError("PluginContext.events not initialized")
        from research_harness.kernel.events import Event

        event = Event.create(
            event_type=event_type, source=self.plugin_id, payload=payload, **kwargs
        )
        return await self.events.publish(event)

    def subscribe(self, event_type: str, handler: Any) -> Any:
        """Subscribe to an event type."""
        if self.events is None:
            raise RuntimeError("PluginContext.events not initialized")
        unsubscribe = self.events.subscribe(event_type, handler)
        self.subscription_cleanups.append(unsubscribe)
        return unsubscribe


# ---------------------------------------------------------------------------
# Plugin base class
# ---------------------------------------------------------------------------


class Plugin(ABC):
    """Base class for all plugins.

    Subclasses must define ``metadata`` and implement lifecycle methods.
    The kernel will call setup/start/stop/teardown in deterministic order.
    """

    @property
    @abstractmethod
    def metadata(self) -> PluginMetadata:
        """Return plugin metadata."""
        ...

    async def setup(self, ctx: PluginContext) -> None:
        """Load/setup: register services, subscribe to events.

        Called before ``start``. Dependencies have already been set up
        when this is invoked.
        """
        _ = ctx

    async def start(self) -> None:
        """Start the plugin (activate background work if any)."""

    async def stop(self) -> None:
        """Stop the plugin (halt background work)."""

    async def teardown(self) -> None:
        """Unload/cleanup resources. Called after ``stop``."""
