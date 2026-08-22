"""ResearchClaim."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ClaimType(str, Enum):
    fact = "fact"
    inference = "inference"
    hypothesis = "hypothesis"
    assumption = "assumption"
    recommendation = "recommendation"


class ClaimStatus(str, Enum):
    proposed = "proposed"
    supported = "supported"
    disputed = "disputed"
    retracted = "retracted"


class ResearchClaim(BaseModel):
    statement: str = Field(description="Claim statement")
    claim_type: ClaimType = Field(description="Category of claim")
    evidence_refs: list[str] = Field(
        default_factory=list, description="Artifact ids of supporting EvidenceItems"
    )
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: ClaimStatus = Field(default=ClaimStatus.proposed)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("statement must be non-empty")
        return v

    @field_validator("evidence_refs")
    @classmethod
    def validate_refs(cls, v: list[str]) -> list[str]:
        # Basic non-empty check; per-type requirement is validated at creation helper if needed
        for item in v:
            if not item.strip():
                raise ValueError("evidence_refs must not contain empty ids")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}

    def requires_evidence(self) -> bool:
        # Fact should normally have evidence; hypothesis may exist without
        return self.claim_type == ClaimType.fact
