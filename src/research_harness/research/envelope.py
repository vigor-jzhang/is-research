"""Artifact envelope — generic immutable wrapper for research payloads."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from research_harness.research.provenance.relations import ProvenanceLink

T = TypeVar("T", bound=BaseModel)
U = TypeVar("U", bound=BaseModel)


def _canonical_json(obj: Any) -> str:
    """Canonical JSON for hashing — sorted keys, compact separators."""
    # obj should be JSON-serializable dict from model_dump(mode="json")
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_content_hash(payload: BaseModel | dict[str, Any]) -> str:
    """Deterministic SHA-256 over canonical payload JSON.

    Only payload contributes to hash, not envelope metadata (artifact_id,
    created_at, etc.). This allows two artifacts with same payload to have
    same hash but distinct ids.
    """
    if isinstance(payload, BaseModel):
        # Use mode json to handle datetimes, then canonicalize
        data = payload.model_dump(mode="json")
    else:
        data = payload
    canonical = _canonical_json(data)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ArtifactEnvelope(BaseModel, Generic[T]):
    """Generic immutable artifact envelope.

    Infrastructure metadata is separate from domain payload.
    Only payload is hashed; envelope fields are not part of content_hash.
    """

    artifact_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()), description="Stable internal UUID"
    )
    artifact_type: str = Field(description="Domain type, e.g., paper_record, evidence_item")
    schema_version: int = Field(default=1, description="Envelope schema version")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    session_id: str | None = Field(default=None)
    run_id: str | None = Field(default=None)
    producer: str | None = Field(default=None, description="human, plugin id, tool, import")
    payload: T = Field(description="Domain payload")
    content_hash: str = Field(description="SHA-256 of canonical payload JSON")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Non-domain metadata, no secrets"
    )
    provenance: list[ProvenanceLink] | None = Field(
        default=None, description="Optional inline provenance hints; authoritative is store"
    )

    model_config = {"extra": "forbid", "frozen": True}

    @classmethod
    def create(
        cls,
        payload: T,
        artifact_type: str,
        producer: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        artifact_id: str | None = None,
        schema_version: int = 1,
    ) -> ArtifactEnvelope[T]:
        # Validate artifact_type non-empty
        if not artifact_type.strip():
            raise ValueError("artifact_type must be non-empty")
        # Compute hash
        content_hash = compute_content_hash(payload)
        return cls(
            artifact_id=artifact_id or str(uuid.uuid4()),
            artifact_type=artifact_type,
            schema_version=schema_version,
            created_at=datetime.now(UTC),
            session_id=session_id,
            run_id=run_id,
            producer=producer,
            payload=payload,
            content_hash=content_hash,
            metadata=metadata or {},
            provenance=None,
        )

    def with_provenance(self, links: list[ProvenanceLink]) -> ArtifactEnvelope[T]:
        # Since frozen, return new instance with provenance set
        return self.model_copy(update={"provenance": links})

    def parse_payload(self, model_cls: type[U]) -> U:
        """Typed deserialization of payload.

        Storage is generic and returns payload as dict; this helper reconstructs
        the typed Pydantic model without requiring the storage plugin to know
        every artifact type.
        """
        if isinstance(self.payload, model_cls):
            return self.payload  # type: ignore[return-value]
        if isinstance(self.payload, dict):
            return model_cls.model_validate(self.payload)
        # Fallback: payload is some other BaseModel, convert via dump
        data = self.payload.model_dump(mode="json")  # type: ignore[union-attr]
        return model_cls.model_validate(data)
