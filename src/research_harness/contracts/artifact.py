"""ArtifactStore contract — durable research objects."""

from __future__ import annotations

from typing import Any, Protocol

from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink


class ArtifactStore(Protocol):
    """Replaceable artifact persistence."""

    async def put(self, envelope: ArtifactEnvelope[Any]) -> None:
        """Persist an artifact envelope. Must reject duplicate artifact_id."""
        ...

    async def get(self, artifact_id: str) -> ArtifactEnvelope[Any]:
        """Retrieve envelope by id or raise."""
        ...

    async def exists(self, artifact_id: str) -> bool: ...

    async def list(
        self,
        *,
        artifact_type: str | None = None,
        session_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ArtifactEnvelope[Any]]:
        """List artifacts with optional filters, ordered by created_at."""
        ...

    async def find_by_type(self, artifact_type: str) -> list[ArtifactEnvelope[Any]]: ...

    async def add_provenance(self, link: ProvenanceLink) -> None:
        """Add a provenance edge. Validates artifacts exist, no self-edge, no cycle."""
        ...

    async def get_parents(self, artifact_id: str) -> list[ProvenanceLink]:
        """Return links where target == artifact_id (incoming)."""
        ...

    async def get_children(self, artifact_id: str) -> list[ProvenanceLink]:
        """Return links where source == artifact_id (outgoing)."""
        ...

    async def get_provenance(
        self, artifact_id: str
    ) -> tuple[list[ProvenanceLink], list[ProvenanceLink]]:
        """Return (parents, children)."""
        ...

    async def get_lineage(
        self, artifact_id: str, direction: str = "ancestors"
    ) -> list[ArtifactEnvelope[Any]]:
        """Walk lineage transitively.

        direction="ancestors": walk parents recursively (backward)
        direction="descendants": walk children recursively (forward)
        Returns list of envelopes in traversal order (closest first).
        """
        ...

    async def close(self) -> None: ...
