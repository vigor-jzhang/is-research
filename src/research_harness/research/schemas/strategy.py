"""LiteratureSearchStrategy — intended search strategy."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class LiteratureSearchStrategy(BaseModel):
    """Describes intended multi-query search strategy."""

    research_question_id: str = Field(description="Artifact id of ResearchQuestion")
    research_plan_id: str | None = Field(default=None)
    objective: str = Field(description="Strategy objective")
    concepts: list[str] = Field(default_factory=list)
    query_artifact_ids: list[str] = Field(
        default_factory=list, description="LiteratureQuery artifact ids"
    )
    source_names: list[str] = Field(default_factory=list, description="crossref, semantic_scholar")
    year_constraints: dict[str, int | None] = Field(default_factory=dict)
    max_results_per_query: int | None = Field(default=None, ge=1, le=200)
    max_total_results: int | None = Field(default=None, ge=1, le=5000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("research_question_id")
    @classmethod
    def validate_rq(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("research_question_id must be non-empty")
        return v

    @field_validator("objective")
    @classmethod
    def validate_obj(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("objective must be non-empty")
        return v

    @field_validator("source_names")
    @classmethod
    def validate_sources(cls, v: list[str]) -> list[str]:
        allowed = {"crossref", "semantic_scholar"}
        for s in v:
            if s not in allowed:
                raise ValueError(f"source_names must be subset of {allowed}")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
