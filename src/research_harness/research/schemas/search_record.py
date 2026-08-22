"""LiteratureSearchRecord — durable search execution record."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class LiteratureSearchRecord(BaseModel):
    """Describes an executed literature search."""

    provider: str = Field(description="crossref or semantic_scholar")
    query: str = Field(description="Search query as issued")
    query_artifact_id: str | None = Field(
        default=None, description="LiteratureQuery artifact id that caused this search"
    )
    filters: dict[str, Any] = Field(default_factory=dict, description="e.g., year_from, year_to")
    executed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    requested_limit: int | None = Field(default=None)
    returned_count: int = Field(description="Number of hits returned in this page")
    total_estimate: int | None = Field(default=None)
    paper_artifact_ids: list[str] = Field(
        default_factory=list, description="Canonical PaperRecord artifact ids"
    )
    provider_snapshot_artifact_ids: list[str] = Field(default_factory=list)
    pagination: dict[str, Any] = Field(
        default_factory=dict, description="opaque pagination metadata: next_page_token, etc."
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
