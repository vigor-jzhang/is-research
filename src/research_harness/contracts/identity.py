"""Paper identity resolver contract."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field

from research_harness.research.schemas.identity import IdentityEvidence


class IdentityMatch(BaseModel):
    member_paper_artifact_ids: list[str]
    method: str  # exact_identifier, exact_content, manual
    evidence: list[IdentityEvidence]
    confidence: float | None = None

    model_config = {"extra": "forbid"}


class IdentityResolutionResult(BaseModel):
    identities_created: list[str] = Field(
        default_factory=list, description="New PaperIdentity artifact ids"
    )
    identities_reused: list[str] = Field(default_factory=list)
    identities_superseded: list[str] = Field(default_factory=list)
    unresolved_paper_ids: list[str] = Field(default_factory=list)
    matches: list[IdentityMatch] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class PaperIdentityResolver(Protocol):
    """Deterministic paper identity resolver."""

    async def resolve(self, paper_artifact_ids: list[str]) -> IdentityResolutionResult:
        """Resolve given PaperRecord artifact ids into PaperIdentity artifacts."""
        ...
