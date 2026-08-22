"""PaperIdentity — resolved scholarly-work identity."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.research.schemas.common import ExternalIdentifier


class ResolutionMethod(str, Enum):
    exact_identifier = "exact_identifier"
    exact_content = "exact_content"
    manual = "manual"


class IdentityEvidence(BaseModel):
    """Structured evidence for why members belong to same identity."""

    identifier_scheme: str = Field(description="e.g., doi, arxiv, pmid")
    normalized_value: str = Field(description="Normalized identifier value")
    member_artifact_ids: list[str] = Field(description="Member paper ids sharing this identifier")

    model_config = {"extra": "forbid"}


class PaperIdentity(BaseModel):
    """Resolved scholarly-work identity — groups PaperRecords believed same work.

    Original PaperRecords remain immutable; identity is separate artifact.
    """

    member_paper_artifact_ids: list[str] = Field(description="Member PaperRecord artifact ids")
    canonical_identifiers: list[ExternalIdentifier] = Field(default_factory=list)
    resolution_method: ResolutionMethod = Field(description="How identity was resolved")
    resolution_evidence: list[IdentityEvidence] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: str = Field(default="active", description="active or superseded")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("member_paper_artifact_ids")
    @classmethod
    def validate_members(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("member_paper_artifact_ids must be non-empty")
        for mid in v:
            if not mid.strip():
                raise ValueError("member ids must be non-empty")
        if len(v) != len(set(v)):
            raise ValueError("member ids must be unique")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
