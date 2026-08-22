"""LiteratureQuery — intended scholarly search query."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class LiteratureQuery(BaseModel):
    """Durable intended query, not provider pagination params."""

    query: str = Field(description="Search query string")
    purpose: str | None = Field(default=None, description="Why this query exists")
    concepts: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    year_from: int | None = Field(default=None, ge=1000, le=3000)
    year_to: int | None = Field(default=None, ge=1000, le=3000)
    target_sources: list[str] = Field(
        default_factory=list, description="crossref, semantic_scholar"
    )
    expected_relevance: str | None = Field(default=None)
    generated_by: str | None = Field(default=None, description="model role or human")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must be non-empty")
        if len(v) > 500:
            raise ValueError("query must be <= 500 chars")
        return v

    @field_validator("target_sources")
    @classmethod
    def validate_sources(cls, v: list[str]) -> list[str]:
        allowed = {"crossref", "semantic_scholar"}
        for s in v:
            if s not in allowed:
                raise ValueError(f"target_sources must be subset of {allowed}, got {v!r}")
        return v

    @field_validator("year_to")
    @classmethod
    def validate_year_range(cls, v: int | None, info) -> int | None:  # type: ignore[no-untyped-def]
        year_from = info.data.get("year_from") if info.data else None
        if v is not None and year_from is not None and v < year_from:
            raise ValueError("year_to must be >= year_from")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
