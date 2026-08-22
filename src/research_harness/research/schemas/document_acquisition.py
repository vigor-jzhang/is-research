"""DocumentAcquisition — attempt/result of obtaining document bytes."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.research.schemas.blob import BlobReference


class AcquisitionStatus(str, Enum):
    downloaded = "downloaded"
    imported = "imported"
    not_available = "not_available"
    access_restricted = "access_restricted"
    invalid_content = "invalid_content"
    too_large = "too_large"
    failed = "failed"


class DocumentAcquisition(BaseModel):
    paper_identity_id: str
    document_location_id: str | None = Field(
        default=None, description="DocumentLocation tried, None for import"
    )
    status: AcquisitionStatus
    attempted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)
    blob: BlobReference | None = Field(
        default=None, description="Stored bytes reference if downloaded/imported"
    )
    sha256: str | None = Field(default=None)
    size_bytes: int | None = Field(default=None, ge=0)
    media_type: str | None = Field(default=None)
    http_status: int | None = Field(default=None)
    final_url: str | None = Field(default=None)
    source_type: str = Field(default="http", description="http, user_provided, none")
    failure_code: str | None = Field(default=None)
    failure_message: str | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("paper_identity_id")
    @classmethod
    def validate_pid(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("paper_identity_id must be non-empty")
        return v.strip()

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
