"""Provenance model — small explicit lineage."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ProvenanceRelation(str, Enum):
    derived_from = "derived_from"
    extracted_from = "extracted_from"
    generated_from = "generated_from"
    supersedes = "supersedes"


class ProvenanceLink(BaseModel):
    """Directed provenance edge.

    Semantics: target `derived_from` source means target is newer/derived, source is upstream.
    Example: EvidenceItem E1 derived_from PaperRecord P1 — E1.target, P1.source.
    Fields are named source_artifact_id (upstream) and target_artifact_id (downstream)
    to avoid ambiguity.
    """

    relation: ProvenanceRelation
    source_artifact_id: str = Field(description="Upstream source artifact id")
    target_artifact_id: str = Field(description="Downstream derived artifact id")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    producer: str | None = Field(default=None, description="human, plugin id, or tool")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_artifact_id", "target_artifact_id")
    @classmethod
    def validate_ids(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("artifact ids must be non-empty")
        return v

    @field_validator("target_artifact_id")
    @classmethod
    def validate_not_self(cls, v: str, info):  # type: ignore[no-untyped-def]
        # info.data contains already validated source_artifact_id
        source = info.data.get("source_artifact_id") if info.data else None
        if source is not None and v == source:
            raise ValueError("self-provenance edges are not permitted")
        return v

    model_config = {"extra": "forbid"}
