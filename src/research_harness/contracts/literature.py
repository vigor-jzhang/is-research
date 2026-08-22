"""Literature source contract — provider-neutral."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from research_harness.research.schemas.paper import PaperRecord


class LiteratureError(Exception):
    """Base for literature provider errors."""


class LiteratureSourceError(LiteratureError):
    pass


class LiteratureAuthenticationError(LiteratureSourceError):
    pass


class LiteratureRateLimitError(LiteratureSourceError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LiteratureNotFoundError(LiteratureSourceError):
    pass


class LiteratureResponseError(LiteratureSourceError):
    pass


class LiteratureSearchRequest(BaseModel):
    query: str = Field(description="Search query, e.g., keywords")
    year_from: int | None = Field(default=None, ge=1000, le=3000)
    year_to: int | None = Field(default=None, ge=1000, le=3000)
    limit: int | None = Field(default=10, ge=1, le=200)
    page_token: str | None = Field(default=None, description="Opaque pagination token")
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class LiteratureSearchHit(BaseModel):
    """Normalized search hit — provider-specific JSON stops here."""

    paper: PaperRecord
    raw_payload: dict[str, Any] = Field(description="Original provider JSON for this record")
    provider: str = Field(description="crossref or semantic_scholar")
    provider_record_id: str = Field(description="Provider's stable id (DOI or paperId)")
    rank: int | None = Field(default=None, description="Search rank, if provider supplies")
    score: float | None = Field(default=None, description="Provider search score, optional")
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class LiteratureSearchPage(BaseModel):
    provider: str
    hits: list[LiteratureSearchHit]
    total_estimate: int | None = Field(default=None)
    next_page_token: str | None = Field(default=None, description="Opaque token for next page")
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class LiteratureSource(Protocol):
    """Provider-neutral literature source."""

    @property
    def provider_name(self) -> str:
        """e.g., crossref, semantic_scholar"""
        ...

    async def search(self, request: LiteratureSearchRequest) -> LiteratureSearchPage:
        """Search works. Must not expose httpx exceptions directly."""
        ...

    async def get(self, identifier: str) -> LiteratureSearchHit:
        """Retrieve a single paper by provider identifier (DOI, paperId, etc.)."""
        ...
