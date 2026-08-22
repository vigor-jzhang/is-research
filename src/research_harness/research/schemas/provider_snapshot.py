"""ProviderRecordSnapshot — preserves raw provider response."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class ProviderRecordSnapshot(BaseModel):
    """Snapshot of a single provider's raw record."""

    provider: str = Field(description="crossref or semantic_scholar")
    provider_record_id: str = Field(description="DOI or paperId, as provider identifies it")
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    request_kind: str = Field(description="search or get")
    request_metadata: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(description="Original provider JSON for this record")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Non-domain metadata")

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
