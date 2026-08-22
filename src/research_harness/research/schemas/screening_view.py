"""PaperScreeningView — deterministic candidate view for screening."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FieldSource(BaseModel):
    paper_artifact_id: str = Field(description="Which PaperRecord supplied this field")
    provider: str | None = Field(default=None, description="crossref, semantic_scholar, etc.")
    field_name: str = Field(description="title, abstract, year, etc.")

    model_config = {"extra": "forbid"}


class PaperScreeningView(BaseModel):
    paper_identity_id: str = Field(description="PaperIdentity artifact id being screened")
    title: str | None = Field(default=None)
    abstract: str | None = Field(default=None)
    authors: list[str] = Field(default_factory=list, description="Author names")
    year: int | None = Field(default=None)
    venue: str | None = Field(default=None)
    field_sources: dict[str, FieldSource] = Field(
        default_factory=dict, description="field -> source"
    )
    member_paper_artifact_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Conflicts, missing info, etc."
    )
    # Keep original member titles/abstracts for audit if needed
    all_titles: list[str] = Field(default_factory=list)
    all_abstracts: list[str | None] = Field(default_factory=list)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
