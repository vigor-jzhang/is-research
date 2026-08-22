"""ScreeningReview — human/autonomy review of a ScreeningDecision."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ReviewerType(str, Enum):
    human = "human"
    autonomy_policy = "autonomy_policy"


class ScreeningReview(BaseModel):
    screening_decision_id: str = Field(description="ScreeningDecision artifact id being reviewed")
    review_reason: str = Field(description="e.g., uncertain, low_confidence")
    original_decision: str = Field(description="Model's original decision")
    final_decision: str = Field(description="Final disposition after review")
    reviewer_type: ReviewerType
    approval_decision_id: str | None = Field(
        default=None, description="Autonomy approval decision id if any"
    )
    notes: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("review_reason", "original_decision", "final_decision")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
