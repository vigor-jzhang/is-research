"""PaperRecord — canonical bibliographic record."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.research.schemas.common import ExternalIdentifier, normalize_doi


class Author(BaseModel):
    name: str = Field(description="Author display name")
    external_ids: list[ExternalIdentifier] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class PaperRecord(BaseModel):
    """Canonical paper record used by all future literature plugins."""

    title: str = Field(description="Paper title")
    authors: list[Author] = Field(default_factory=list)
    year: int | None = Field(default=None, ge=1000, le=3000)
    venue: str | None = Field(default=None, description="Journal, conference, or repository")

    abstract: str | None = Field(default=None)

    doi: str | None = Field(default=None, description="Canonical bare DOI (10.xxxx/yyyy) if known")
    external_identifiers: list[ExternalIdentifier] = Field(default_factory=list)

    url: str | None = Field(default=None, description="Primary URL")
    open_access_url: str | None = Field(default=None)

    publication_type: str | None = Field(
        default=None, description="e.g., journal-article, preprint"
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Namespaced provider metadata"
    )

    @field_validator("doi")
    @classmethod
    def normalize_doi_field(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        n = normalize_doi(v)
        return n

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
