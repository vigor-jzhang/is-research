"""ResearchQuestion and ResearchPlan."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class QuestionStatus(str, Enum):
    open = "open"
    refined = "refined"
    answered = "answered"
    abandoned = "abandoned"


class ResearchQuestion(BaseModel):
    question: str = Field(description="Research question text")
    motivation: str | None = Field(default=None)
    scope: str | None = Field(default=None)
    constraints: str | None = Field(default=None)
    status: QuestionStatus = Field(default=QuestionStatus.open)

    @field_validator("question")
    @classmethod
    def validate_question(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class ResearchPlan(BaseModel):
    objective: str = Field(description="Plan objective")
    research_question_id: str | None = Field(
        default=None, description="Artifact id of ResearchQuestion this plan addresses"
    )
    steps: list[str] = Field(default_factory=list, description="Planned steps")
    search_concepts: list[str] = Field(default_factory=list)
    inclusion_focus: str | None = Field(default=None)
    known_constraints: str | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("objective")
    @classmethod
    def validate_objective(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("objective must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
