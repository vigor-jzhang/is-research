"""PaperResearchProfile — paper-level structured research profile (Phase 2F)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ProfileClaim(BaseModel):
    """A profile statement referencing supporting evidence items.

    `inference=True` marks claims organized by the model from already-extracted
    evidence but not directly copied from a single evidence item.
    """

    text: str
    evidence_item_ids: list[str] = Field(default_factory=list)
    inference: bool = False
    category: str | None = Field(default=None)

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("profile claim text must be non-empty")
        return v

    @field_validator("evidence_item_ids")
    @classmethod
    def validate_evidence_ids(cls, v: list[str]) -> list[str]:
        return [x.strip() for x in v if x.strip()]

    model_config = {"extra": "forbid"}


class PaperResearchProfile(BaseModel):
    """Durable paper-level research profile built from EvidenceItems.

    Each section is a list of ProfileClaim referencing EvidenceItem artifact ids,
    so unsupported claims are traceable to extracted evidence.
    """

    paper_identity_id: str
    full_text_document_id: str
    research_question: list[ProfileClaim] = Field(default_factory=list)
    research_context: list[ProfileClaim] = Field(default_factory=list)
    theories: list[ProfileClaim] = Field(default_factory=list)
    constructs: list[ProfileClaim] = Field(default_factory=list)
    mechanisms: list[ProfileClaim] = Field(default_factory=list)
    assumptions: list[ProfileClaim] = Field(default_factory=list)
    methodology: list[ProfileClaim] = Field(default_factory=list)
    data: list[ProfileClaim] = Field(default_factory=list)
    sample: list[ProfileClaim] = Field(default_factory=list)
    variables: list[ProfileClaim] = Field(default_factory=list)
    main_findings: list[ProfileClaim] = Field(default_factory=list)
    results: list[ProfileClaim] = Field(default_factory=list)
    boundary_conditions: list[ProfileClaim] = Field(default_factory=list)
    limitations: list[ProfileClaim] = Field(default_factory=list)
    future_research: list[ProfileClaim] = Field(default_factory=list)
    evidence_item_ids: list[str] = Field(default_factory=list)
    model_role: str | None = Field(default=None)
    extraction_method: str = Field(default="model-assisted")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("paper_identity_id", "full_text_document_id")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("id must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}

    def category_field(self, category: str) -> list[ProfileClaim]:
        return getattr(self, category, [])
