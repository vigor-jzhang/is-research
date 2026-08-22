"""EvidenceItem, Locator, and Phase 2F evidence-extraction schemas."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class EvidenceCategory(str, Enum):
    """Extensible taxonomy of evidence item categories (Phase 2F)."""

    research_question = "research_question"
    theory = "theory"
    construct = "construct"
    mechanism = "mechanism"
    assumption = "assumption"
    method = "method"
    data = "data"
    variable = "variable"
    finding = "finding"
    result = "result"
    boundary_condition = "boundary_condition"
    limitation = "limitation"
    future_research = "future_research"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class Locator(BaseModel):
    """Typed locator inside a source."""

    page: int | None = Field(default=None, ge=1)
    pages: list[int] | None = Field(
        default=None, description="Exact 1-based page numbers (for ranges)"
    )
    section: str | None = Field(default=None, description="e.g., abstract, introduction, table 2")
    paragraph: int | None = Field(default=None, ge=1)
    table: str | None = Field(default=None)
    figure: str | None = Field(default=None)
    appendix: str | None = Field(default=None)
    char_range: tuple[int, int] | None = Field(default=None, description="Character offset range")

    @field_validator("pages")
    @classmethod
    def validate_pages(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return v
        if not v:
            raise ValueError("pages must be non-empty when provided")
        for p in v:
            if p < 1:
                raise ValueError(f"page numbers must be >= 1, got {p}")
        # Keep sorted and deduplicated for canonical grounding
        return sorted(set(v))

    model_config = {"extra": "forbid"}


class EvidenceItem(BaseModel):
    """Evidence-bearing observation extracted from a source artifact.

    Must retain source_artifact_id — free-floating evidence is invalid.
    Phase 2F adds `category` and page-range grounding via `locator.pages`.
    """

    statement: str = Field(description="What was observed")
    source_artifact_id: str = Field(
        description="Artifact id of the source (e.g., FullTextDocument artifact)"
    )
    category: EvidenceCategory | None = Field(
        default=None, description="Evidence taxonomy category (Phase 2F)"
    )
    locator: Locator | None = Field(default=None)
    extraction_method: str | None = Field(
        default=None, description="human, plugin, model-assisted, import"
    )
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    notes: str | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("statement must be non-empty")
        return v

    @field_validator("source_artifact_id")
    @classmethod
    def validate_source(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("source_artifact_id must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
