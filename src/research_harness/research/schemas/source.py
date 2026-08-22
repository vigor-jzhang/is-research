"""SourceRecord — generic externally observable source."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from research_harness.research.schemas.common import ExternalIdentifier


class SourceType(str, Enum):
    paper = "paper"
    webpage = "webpage"
    dataset = "dataset"
    book = "book"
    working_paper = "working_paper"
    repository = "repository"


class SourceRecord(BaseModel):
    """Describes an externally observable source (not yet retrieved content)."""

    title: str = Field(description="Source title")
    source_type: SourceType = Field(default=SourceType.paper, description="Type of source")
    url: str | None = Field(default=None, description="Primary URL")
    external_identifiers: list[ExternalIdentifier] = Field(default_factory=list)
    publisher: str | None = Field(default=None, description="Publisher or provider")
    retrieved_at: datetime | None = Field(default=None, description="When source was observed")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Namespaced provider metadata"
    )

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}

    def model_dump_for_hash(self) -> dict[str, Any]:
        # Canonical dict for hashing — exclude None vs missing handling via model_dump
        return self.model_dump(mode="json", exclude_none=False)
