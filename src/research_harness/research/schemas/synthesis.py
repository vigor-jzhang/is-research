"""Phase 2G synthesis schemas — cross-paper evidence synthesis artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class SynthesisStatementType(str, Enum):
    """Typed synthesis statement categories (extensible)."""

    consensus = "consensus"
    mixed = "mixed"
    contradiction = "contradiction"
    pattern = "pattern"
    boundary_condition = "boundary_condition"
    methodological_pattern = "methodological_pattern"
    theoretical_pattern = "theoretical_pattern"
    limitation_pattern = "limitation_pattern"
    future_research_pattern = "future_research_pattern"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class SupportType(str, Enum):
    single_paper = "single_paper"
    multi_paper = "multi_paper"


class SynthesisStatement(BaseModel):
    """A cross-paper synthesis statement grounded in EvidenceItem IDs.

    `papers_supporting` / `evidence_items_supporting` / `papers_conflicting` /
    `evidence_items_conflicting` are deterministic counts computed by the
    orchestrator (never invented by the model).
    """

    statement: str
    type: SynthesisStatementType
    supporting_evidence_ids: list[str] = Field(min_length=1)
    conflicting_evidence_ids: list[str] = Field(default_factory=list)
    supporting_paper_identity_ids: list[str] = Field(default_factory=list)
    conflicting_paper_identity_ids: list[str] = Field(default_factory=list)
    papers_supporting: int = 0
    evidence_items_supporting: int = 0
    papers_conflicting: int = 0
    evidence_items_conflicting: int = 0
    support_type: SupportType = SupportType.single_paper
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("statement must be non-empty")
        return v

    @field_validator("supporting_evidence_ids", "conflicting_evidence_ids")
    @classmethod
    def validate_ids(cls, v: list[str]) -> list[str]:
        cleaned = [x.strip() for x in v if x.strip()]
        if cleaned != sorted(set(cleaned)):
            return sorted(set(cleaned))
        return cleaned

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class SynthesisTheme(BaseModel):
    """A recurring theme with its grounded statements."""

    title: str
    dimension: str | None = Field(
        default=None,
        description="Synthesis dimension: theories, constructs, mechanisms, assumptions, methods, data, variables, findings, boundary_conditions, limitations, future_research",
    )
    statements: list[SynthesisStatement] = Field(default_factory=list)
    evidence_item_ids: list[str] = Field(default_factory=list)
    paper_identity_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("theme title must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class LiteratureSynthesis(BaseModel):
    """Durable output of cross-paper synthesis (input to Phase 2H)."""

    evidence_corpus_id: str
    theme_ids: list[str] = Field(default_factory=list)
    statement_ids: list[str] = Field(default_factory=list)
    counts: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class SynthesisExecution(BaseModel):
    """Operational record of a synthesis run over an EvidenceCorpus."""

    evidence_corpus_id: str
    profiles_processed: int = 0
    evidence_items_processed: int = 0
    batches_processed: int = 0
    batches_failed: int = 0
    themes_created: int = 0
    statements_created: int = 0
    statements_rejected: int = 0
    model_role: str = "reasoning"
    failures: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
