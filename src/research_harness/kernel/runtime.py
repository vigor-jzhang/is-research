"""Runtime - minimal kernel construction.

This module is deliberately generic and must NOT import concrete plugins
or the plugin registry. Plugin discovery and composition is handled by
the application/bootstrap layer (research_harness.app.bootstrap).
"""

from __future__ import annotations

from typing import Any

from research_harness.kernel.events import EventBus
from research_harness.kernel.manager import PluginManager
from research_harness.kernel.services import ServiceRegistry


class Runtime:
    """Minimal runtime that wires kernel components.

    The runtime is infrastructure; it does not contain agent or research logic.
    """

    def __init__(
        self,
        config: Any,
        plugin_manager: PluginManager,
        services: ServiceRegistry,
        events: EventBus,
    ) -> None:
        self.config = config
        self.plugins = plugin_manager
        self.services = services
        self.events = events

    async def start(self) -> None:
        await self.plugins.start_all()

    async def stop(self) -> None:
        await self.plugins.stop_all()

    async def __aenter__(self) -> Runtime:
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()
