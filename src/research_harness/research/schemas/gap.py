"""Phase 2H gap analysis schemas — evidence-grounded research gap artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

# Comprehensive vocabulary of legitimate analytical IS/economic research
# domains for model-construction opportunity assessments. Genuine defect
# repair (Phase 7D.3E): the previous eight-domain set was under-specified and
# crashed the mechanism pipeline whenever a model named a standard field such
# as "industrial organization" or "entry deterrence". This is a vocabulary
# repair for legitimate research domains, never a benchmark relaxation.
_ANALYTICAL_MODEL_DOMAINS = frozenset(
    {
        "strategic interaction",
        "information asymmetry",
        "platform behavior",
        "pricing",
        "technology adoption",
        "incentives",
        "competition",
        "mechanism design",
        "industrial organization",
        "entry deterrence",
        "market structure",
        "market design",
        "network effects",
        "information economics",
        "contract theory",
        "game theory",
        "auction theory",
        "price discrimination",
        "signaling",
        "screening",
        "two-sided markets",
        "oligopoly",
        "behavioral economics",
        "digital platforms",
        "economic regulation",
        "welfare economics",
        "competition policy",
    }
)


class GapType(str, Enum):
    """Structured research gap categories (extensible)."""

    theoretical_gap = "theoretical_gap"
    mechanism_gap = "mechanism_gap"
    empirical_gap = "empirical_gap"
    context_gap = "context_gap"
    boundary_condition_gap = "boundary_condition_gap"
    contradiction_gap = "contradiction_gap"
    methodological_gap = "methodological_gap"
    integration_gap = "integration_gap"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class GapStrength(str, Enum):
    """Deterministic strength label computed from support counts."""

    strongly_supported = "strongly_supported"
    tentative = "tentative"


class GapStatus(str, Enum):
    candidate = "candidate"
    selected = "selected"
    rejected = "rejected"


class GapRankDimension(BaseModel):
    """Raw scores for a single ranking dimension (kept separate, transparent)."""

    evidence_strength: float = Field(ge=0.0, le=1.0)
    research_importance: float = Field(ge=0.0, le=1.0)
    theoretical_relevance: float = Field(ge=0.0, le=1.0)
    analytical_model_potential: float = Field(ge=0.0, le=1.0)
    tractability: float = Field(ge=0.0, le=1.0)

    @property
    def composite(self) -> float:
        return round(
            (
                self.evidence_strength
                + self.research_importance
                + self.theoretical_relevance
                + self.analytical_model_potential
                + self.tractability
            )
            / 5.0,
            3,
        )

    model_config = {"extra": "forbid"}


class AnalyticalModelOpportunity(BaseModel):
    """Opportunity assessment for future analytical IS model construction.

    This is an assessment only; no model is built here.
    """

    suitable: bool = False
    domains: list[str] = Field(
        default_factory=list,
        description="legitimate analytical IS/economic research domains "
        "(e.g. strategic interaction, industrial organization, mechanism design)",
    )
    rationale: str | None = Field(default=None)

    @field_validator("domains")
    @classmethod
    def validate_domains(cls, v: list[str]) -> list[str]:
        for d in v:
            if d not in _ANALYTICAL_MODEL_DOMAINS:
                raise ValueError(f"unknown analytical model domain {d!r}")
        return sorted(set(v))

    model_config = {"extra": "forbid"}


class ResearchGap(BaseModel):
    """A candidate research gap inferred from the reviewed corpus.

    Epistemic rule: a gap is an inference from the reviewed literature, not a
    proven fact that no paper exists anywhere. Descriptions should use
    corpus-bounded language ('within the reviewed corpus...').
    """

    title: str
    gap_type: GapType
    description: str
    why_it_matters: str | None = Field(default=None)
    supporting_synthesis_statement_ids: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradiction_statement_ids: list[str] = Field(default_factory=list)
    relevant_paper_identity_ids: list[str] = Field(default_factory=list)
    supporting_papers: int = 0
    supporting_evidence_items: int = 0
    contradicting_papers: int = 0
    strength: GapStrength = GapStrength.tentative
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    scope: str | None = Field(default=None, description="corpus-bounded scope statement")
    limitations: list[str] = Field(default_factory=list)
    status: GapStatus = GapStatus.candidate
    ranking: GapRankDimension | None = Field(default=None)
    analytical_model_opportunity: AnalyticalModelOpportunity | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("gap title must be non-empty")
        return v

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("gap description must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class GapAnalysis(BaseModel):
    """Durable output of a gap analysis over a LiteratureSynthesis."""

    literature_synthesis_id: str
    evidence_corpus_id: str
    research_question_id: str | None = Field(default=None)
    gap_ids: list[str] = Field(default_factory=list)
    ranked_gap_ids: list[str] = Field(default_factory=list)
    coverage_limitations: list[str] = Field(
        default_factory=list,
        description="Corpus coverage limits (e.g., documents without evidence), NOT gaps",
    )
    summary: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class GapAnalysisExecution(BaseModel):
    """Operational record of a gap analysis run."""

    literature_synthesis_id: str
    evidence_corpus_id: str
    research_question_id: str | None = Field(default=None)
    statements_processed: int = 0
    themes_processed: int = 0
    gaps_created: int = 0
    gaps_rejected: int = 0
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
