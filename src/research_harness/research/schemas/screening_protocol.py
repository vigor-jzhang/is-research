"""ScreeningProtocol — approved title/abstract screening rules."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class CriterionKind(str, Enum):
    inclusion = "inclusion"
    exclusion = "exclusion"


class ScreeningCriterion(BaseModel):
    criterion_id: str = Field(description="Stable ID, e.g., I1, E2")
    kind: CriterionKind
    description: str = Field(description="Criterion text")
    rationale: str | None = Field(default=None)
    required: bool = Field(default=True, description="If true, must be satisfied for inclusion")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("criterion_id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("criterion_id must be non-empty")
        if len(v) > 20:
            raise ValueError("criterion_id too long")
        return v

    @field_validator("description")
    @classmethod
    def validate_desc(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("description must be non-empty")
        if len(v) > 1000:
            raise ValueError("description too long")
        return v

    model_config = {"extra": "forbid"}


class ProtocolStatus(str, Enum):
    draft = "draft"
    approved = "approved"
    rejected = "rejected"
    superseded = "superseded"


class ScreeningProtocol(BaseModel):
    research_question_id: str = Field(description="Artifact id of ResearchQuestion")
    research_plan_id: str | None = Field(default=None)
    objective: str = Field(description="Screening objective")
    inclusion_criteria: list[ScreeningCriterion] = Field(default_factory=list)
    exclusion_criteria: list[ScreeningCriterion] = Field(default_factory=list)
    decision_rules: str | None = Field(default=None, description="Human-readable decision rules")
    screening_stage: str = Field(
        default="title_abstract", description="title_abstract, full_text, etc."
    )
    model_role: str = Field(
        default="reasoning", description="Logical model role used to generate this protocol"
    )
    status: ProtocolStatus = Field(default=ProtocolStatus.draft)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

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

    @field_validator("inclusion_criteria", "exclusion_criteria")
    @classmethod
    def validate_criteria_count(cls, v: list[ScreeningCriterion]) -> list[ScreeningCriterion]:
        if len(v) > 12:
            raise ValueError("too many criteria, max 12")
        # Check duplicate ids within list
        ids = [c.criterion_id for c in v]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate criterion ids within list: {ids}")
        return v

    @field_validator("exclusion_criteria")
    @classmethod
    def validate_exclusion_not_empty_if_needed(
        cls, v: list[ScreeningCriterion]
    ) -> list[ScreeningCriterion]:
        # Allow empty, but not too many
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}

    def all_criterion_ids(self) -> set[str]:
        return {c.criterion_id for c in self.inclusion_criteria + self.exclusion_criteria}

    def inclusion_ids(self) -> set[str]:
        return {c.criterion_id for c in self.inclusion_criteria}

    def exclusion_ids(self) -> set[str]:
        return {c.criterion_id for c in self.exclusion_criteria}
