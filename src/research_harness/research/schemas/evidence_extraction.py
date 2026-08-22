"""Evidence extraction execution + corpus schemas (Phase 2F)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class EvidenceExtractionExecution(BaseModel):
    """Operational record for processing a FullTextCorpus into evidence."""

    full_text_corpus_id: str
    documents_attempted: int = 0
    documents_completed: int = 0
    chunks_processed: int = 0
    chunks_failed: int = 0
    evidence_items_created: int = 0
    profiles_created: int = 0
    model_role: str = "reasoning"
    failures: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class EvidenceCorpus(BaseModel):
    """Durable output corpus of extracted evidence (input to Phase 2G)."""

    evidence_extraction_execution_id: str
    full_text_corpus_id: str
    paper_profile_ids: list[str] = Field(default_factory=list)
    evidence_item_ids: list[str] = Field(default_factory=list)
    documents_without_evidence: list[str] = Field(
        default_factory=list,
        description="FullTextDocument ids skipped (insufficient/encrypted/failed)",
    )
    failed_document_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
