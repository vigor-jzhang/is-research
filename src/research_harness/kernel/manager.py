"""Plugin manager - discovery, dependency resolution, lifecycle."""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from research_harness.kernel.errors import PluginDependencyError, PluginError
from research_harness.kernel.events import EventBus
from research_harness.kernel.plugin import Plugin, PluginContext
from research_harness.kernel.services import ServiceRegistry

logger = logging.getLogger(__name__)


class PluginManager:
    """Manages plugin lifecycle with deterministic dependency ordering."""

    def __init__(
        self,
        services: ServiceRegistry | None = None,
        events: EventBus | None = None,
        plugin_configs: dict[str, dict[str, Any]] | None = None,
        runtime_meta: dict[str, Any] | None = None,
    ) -> None:
        self.services = services if services is not None else ServiceRegistry()
        self.events = events if events is not None else EventBus()
        self.plugin_configs = plugin_configs or {}
        self.runtime_meta = runtime_meta or {}
        self._plugins: dict[str, Plugin] = {}
        self._ordered_ids: list[str] = []
        self._started = False
        # Map service name -> provider plugin id
        self._service_providers: dict[str, str] = {}
        self._subscription_cleanups: dict[str, list[Callable[[], None]]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, plugin: Plugin) -> None:
        meta = plugin.metadata
        pid = meta.id
        if pid in self._plugins:
            raise PluginError(f"duplicate plugin id {pid!r}")
        # Validate provides uniqueness across already registered plugins
        for svc in meta.provides:
            if svc in self._service_providers:
                raise PluginError(
                    f"service {svc!r} already provided by plugin {self._service_providers[svc]!r}; "
                    f"conflict with {pid!r}"
                )
        self._plugins[pid] = plugin
        for svc in meta.provides:
            self._service_providers[svc] = pid

    def register_many(self, plugins: list[Plugin]) -> None:
        for p in plugins:
            self.register(p)

    # ------------------------------------------------------------------
    # Dependency resolution
    # ------------------------------------------------------------------

    def _validate_dependencies(self) -> None:
        # Check missing dependencies
        for pid, plugin in self._plugins.items():
            for dep in plugin.metadata.requires:
                if dep not in self._service_providers:
                    raise PluginDependencyError(
                        f"plugin {pid!r} requires service {dep!r} which is not provided by any plugin. "
                        f"Available services: {sorted(self._service_providers.keys())}"
                    )
        # Check optional_requires are not strictly validated

    def _build_dependency_graph(self) -> dict[str, set[str]]:
        """Build plugin_id -> set(dependency_plugin_ids)."""
        # Map plugin id -> set of plugin ids it depends on
        graph: dict[str, set[str]] = {pid: set() for pid in self._plugins}
        # Need reverse map: service -> plugin
        for pid, plugin in self._plugins.items():
            for dep_service in plugin.metadata.requires:
                provider = self._service_providers.get(dep_service)
                if provider is not None and provider != pid:
                    graph[pid].add(provider)
            for dep_service in plugin.metadata.optional_requires:
                provider = self._service_providers.get(dep_service)
                if provider is not None and provider != pid:
                    # Optional deps only impose ordering if provider exists; no failure if missing
                    graph[pid].add(provider)
        return graph

    def resolve_order(self) -> list[str]:
        """Topological sort with deterministic tie-breaking; raises on cycle."""
        self._validate_dependencies()
        graph = self._build_dependency_graph()
        # Kahn's algorithm with sorted queues for determinism
        in_degree: dict[str, int] = {pid: len(deps) for pid, deps in graph.items()}
        # Reverse adjacency: provider -> list of dependents
        dependents: dict[str, set[str]] = defaultdict(set)
        for pid, deps in graph.items():
            for dep in deps:
                dependents[dep].add(pid)

        queue: deque[str] = deque(sorted([pid for pid, deg in in_degree.items() if deg == 0]))
        result: list[str] = []

        while queue:
            current = queue.popleft()
            result.append(current)
            for dep in sorted(dependents.get(current, [])):
                in_degree[dep] -= 1
                if in_degree[dep] == 0:
                    # Insert in sorted order; simpler: collect and sort queue each iteration
                    queue.append(dep)
            # Keep queue sorted for determinism
            # Convert to sorted list then deque
            if queue:
                sorted_q = sorted(queue)
                queue = deque(sorted_q)

        if len(result) != len(self._plugins):
            # Find cycle: remaining nodes have in_degree >0
            remaining = [pid for pid, deg in in_degree.items() if deg > 0]
            raise PluginDependencyError(
                f"dependency cycle detected among plugins: {sorted(remaining)}"
            )

        return result

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def setup_all(self) -> None:
        if self._started:
            raise PluginError("plugins already started")
        order = self.resolve_order()
        initialized: list[str] = []
        try:
            for pid in order:
                plugin = self._plugins[pid]
                ctx = PluginContext(
                    plugin_id=pid,
                    config=self.plugin_configs.get(pid, {}),
                    services=self.services,
                    events=self.events,
                    runtime_meta=dict(self.runtime_meta),
                    subscription_cleanups=[],
                )
                logger.debug("setting up plugin %s", pid)
                await plugin.setup(ctx)
                self._subscription_cleanups[pid] = ctx.subscription_cleanups
                initialized.append(pid)
                await self.events.publish(_make_event("plugin.loaded", pid, {"plugin_id": pid}))
        except Exception:
            for pid in reversed(initialized):
                try:
                    await self._plugins[pid].teardown()
                except Exception:
                    logger.exception("error tearing down plugin %s during setup rollback", pid)
                self.services.clear_owner(pid)
                for unsubscribe in self._subscription_cleanups.pop(pid, []):
                    unsubscribe()
            self._ordered_ids = []
            raise
        self._ordered_ids = order

    async def start_all(self) -> None:
        if not self._ordered_ids:
            await self.setup_all()
        started: list[str] = []
        try:
            for pid in self._ordered_ids:
                plugin = self._plugins[pid]
                logger.debug("starting plugin %s", pid)
                await plugin.start()
                started.append(pid)
                await self.events.publish(_make_event("plugin.started", pid, {"plugin_id": pid}))
        except Exception:
            for pid in reversed(started):
                try:
                    await self._plugins[pid].stop()
                except Exception:
                    logger.exception("error stopping plugin %s during startup rollback", pid)
            await self.stop_all()
            raise
        self._started = True

    async def stop_all(self) -> None:
        if not self._started and not self._ordered_ids:
            return
        for pid in reversed(self._ordered_ids):
            plugin = self._plugins[pid]
            try:
                logger.debug("stopping plugin %s", pid)
                await plugin.stop()
                await self.events.publish(_make_event("plugin.stopped", pid, {"plugin_id": pid}))
            except Exception:
                logger.exception("error stopping plugin %s", pid)
            try:
                await plugin.teardown()
            except Exception:
                logger.exception("error tearing down plugin %s", pid)
            # lifecycle-aware cleanup of services
            self.services.clear_owner(pid)
            for unsubscribe in self._subscription_cleanups.pop(pid, []):
                try:
                    unsubscribe()
                except Exception:
                    logger.exception("error unsubscribing plugin %s", pid)
        self._started = False
        self._ordered_ids = []

    def get_plugin(self, plugin_id: str) -> Plugin:
        if plugin_id not in self._plugins:
            raise PluginError(f"plugin {plugin_id!r} not found")
        return self._plugins[plugin_id]

    def list_plugins(self) -> list[Plugin]:
        return list(self._plugins.values())

    def ordered_plugins(self) -> list[Plugin]:
        if self._ordered_ids:
            return [self._plugins[pid] for pid in self._ordered_ids]
        return self.list_plugins()

    @property
    def is_started(self) -> bool:
        return self._started


def _make_event(event_type: str, source: str, payload: dict[str, Any]):  # type: ignore[no-untyped-def]
    from research_harness.kernel.events import Event

    return Event.create(event_type=event_type, source=source, payload=payload)
