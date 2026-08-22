"""LiteratureSearchExecution — aggregate execution of a strategy."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class LiteratureSearchExecution(BaseModel):
    """Durable record of executing a search strategy."""

    strategy_artifact_id: str = Field(description="LiteratureSearchStrategy artifact id")
    query_artifact_ids: list[str] = Field(default_factory=list)
    search_record_artifact_ids: list[str] = Field(default_factory=list)
    paper_artifact_ids: list[str] = Field(default_factory=list)
    paper_identity_artifact_ids: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)
    provider_failures: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
