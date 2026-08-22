"""ScreeningDecision — model assessment for a PaperIdentity."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ScreeningDecisionEnum(str, Enum):
    include = "include"
    exclude = "exclude"
    uncertain = "uncertain"


class InformationSufficiency(str, Enum):
    sufficient = "sufficient"
    insufficient = "insufficient"


class ScreeningDecision(BaseModel):
    paper_identity_id: str = Field(description="PaperIdentity being screened")
    screening_view_id: str = Field(description="PaperScreeningView artifact id")
    screening_protocol_id: str = Field(description="ScreeningProtocol artifact id used")
    decision: ScreeningDecisionEnum
    matched_inclusion_criteria: list[str] = Field(default_factory=list)
    matched_exclusion_criteria: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    rationale_summary: str = Field(description="Concise audit rationale")
    confidence: float = Field(ge=0.0, le=1.0)
    information_sufficiency: InformationSufficiency = Field(
        default=InformationSufficiency.sufficient
    )
    model_assessed: bool = Field(default=True)
    status: str = Field(default="completed", description="completed, superseded, etc.")
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("rationale_summary")
    @classmethod
    def validate_rationale(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("rationale_summary must be non-empty")
        if len(v) > 2000:
            raise ValueError("rationale_summary too long")
        return v

    @field_validator("matched_inclusion_criteria", "matched_exclusion_criteria")
    @classmethod
    def validate_criteria_non_empty(cls, v: list[str]) -> list[str]:
        for item in v:
            if not item.strip():
                raise ValueError("criterion ids must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
