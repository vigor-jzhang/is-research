"""FullTextDocument and related schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.research.schemas.blob import BlobReference


class TextStatus(str, Enum):
    extracted = "extracted"
    insufficient_text = "insufficient_text"
    encrypted = "encrypted"
    extraction_failed = "extraction_failed"


class FullTextDocument(BaseModel):
    paper_identity_id: str
    document_acquisition_id: str
    source_blob: BlobReference = Field(description="Original PDF blob")
    text_blob: BlobReference | None = Field(
        default=None, description="Extracted page-text blob, None if encrypted/insufficient"
    )
    extractor: str = Field(description="Extractor id, e.g. documents.extractor.pypdf")
    extractor_version: str = Field(default="0.1.0")
    page_count: int = Field(ge=0)
    pages_with_text: int = Field(ge=0)
    character_count: int = Field(ge=0)
    text_status: TextStatus
    language: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    quality_metrics: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("paper_identity_id", "document_acquisition_id", "extractor")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must be non-empty")
        return v.strip()

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class DocumentAcquisitionExecution(BaseModel):
    screened_literature_set_id: str
    paper_identity_ids: list[str] = Field(default_factory=list)
    location_artifact_ids: list[str] = Field(default_factory=list)
    acquisition_artifact_ids: list[str] = Field(default_factory=list)
    full_text_document_ids: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)
    counts: dict[str, int] = Field(default_factory=dict)
    failures: list[dict[str, Any]] = Field(default_factory=list)
    budget_stop_reason: str | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class FullTextCorpus(BaseModel):
    document_acquisition_execution_id: str
    screened_literature_set_id: str
    available_document_ids: list[str] = Field(
        default_factory=list, description="FullTextDocument ids with extracted text"
    )
    unavailable_identity_ids: list[str] = Field(default_factory=list)
    restricted_identity_ids: list[str] = Field(default_factory=list)
    failed_identity_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
