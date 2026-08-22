"""ScreeningExecution and ScreenedLiteratureSet."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class ScreeningExecution(BaseModel):
    protocol_artifact_id: str = Field(description="ScreeningProtocol used")
    search_execution_artifact_id: str | None = Field(default=None)
    candidate_identity_ids: list[str] = Field(default_factory=list)
    screening_view_ids: list[str] = Field(default_factory=list)
    decision_artifact_ids: list[str] = Field(default_factory=list)
    review_artifact_ids: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)
    counts: dict[str, int] = Field(default_factory=dict)
    model_role: str | None = Field(default=None)
    failures: list[dict[str, Any]] = Field(default_factory=list)
    budget_stop_reason: str | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class ScreenedLiteratureSet(BaseModel):
    screening_execution_id: str = Field(description="ScreeningExecution artifact id")
    screening_protocol_id: str = Field(description="Protocol used")
    included_identity_ids: list[str] = Field(default_factory=list)
    excluded_identity_ids: list[str] = Field(default_factory=list)
    uncertain_identity_ids: list[str] = Field(default_factory=list)
    decision_artifact_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
