"""Service registry - explicit service registration and discovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from research_harness.kernel.errors import ServiceError


@dataclass(frozen=True)
class ServiceEntry:
    name: str
    instance: Any
    owner: str


class ServiceRegistry:
    """Registry for plugin-provided services.

    Supports registration, lookup, duplicate detection, ownership,
    and lifecycle-aware cleanup.
    """

    def __init__(self) -> None:
        self._services: dict[str, ServiceEntry] = {}

    def register(self, name: str, instance: Any, owner: str = "unknown") -> None:
        if name in self._services:
            existing = self._services[name]
            raise ServiceError(
                f"service {name!r} already registered by plugin {existing.owner!r}; "
                f"cannot register again from {owner!r}"
            )
        self._services[name] = ServiceEntry(name=name, instance=instance, owner=owner)

    def unregister(self, name: str, owner: str | None = None) -> None:
        entry = self._services.get(name)
        if entry is None:
            raise ServiceError(f"service {name!r} not found for unregistration")
        if owner is not None and entry.owner != owner:
            raise ServiceError(
                f"service {name!r} owned by {entry.owner!r}, cannot unregister from {owner!r}"
            )
        del self._services[name]

    def require(self, name: str) -> Any:
        entry = self._services.get(name)
        if entry is None:
            available = sorted(self._services.keys())
            raise ServiceError(f"required service {name!r} not found. Available: {available}")
        return entry.instance

    def get(self, name: str) -> Any | None:
        entry = self._services.get(name)
        if entry is None:
            return None
        return entry.instance

    def has(self, name: str) -> bool:
        return name in self._services

    def list_services(self) -> dict[str, ServiceEntry]:
        return dict(self._services)

    def clear_owner(self, owner: str) -> None:
        """Remove all services owned by a plugin (lifecycle cleanup)."""
        to_remove = [name for name, entry in self._services.items() if entry.owner == owner]
        for name in to_remove:
            del self._services[name]

    def __len__(self) -> int:
        return len(self._services)

    def __bool__(self) -> bool:
        # Registry should be truthy even when empty to avoid `or` pitfalls
        return True

    def __contains__(self, name: object) -> bool:
        return name in self._services
