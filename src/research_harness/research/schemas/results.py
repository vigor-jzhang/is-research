"""Phase 4A result-assembly schemas — findings, contribution claims,
implications, and the immutable ResearchResultsPackage.

Everything here is grounded in verified Phase 3 artifacts. The LLM may
organize and interpret results but must not invent mathematical results;
validation is deterministic and rejects unsupported IDs, failed propositions,
dropped conditions, and global-novelty claims. No paper drafting (Phase 4B).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.research.schemas.mechanism import KnowledgeBasis


class FindingType(str, Enum):
    analytical_result = "analytical_result"
    comparative_static = "comparative_static"
    robustness_result = "robustness_result"
    welfare_result = "welfare_result"
    boundary_result = "boundary_result"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class FindingConfidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class ResearchFinding(BaseModel):
    """A finding grounded in verified Phase 3 artifacts (never LLM-invented math)."""

    model_id: str
    equilibrium_candidate_id: str
    statement: str
    finding_type: FindingType = FindingType.analytical_result
    supporting_proposition_ids: list[str] = Field(default_factory=list)
    supporting_comparative_static_ids: list[str] = Field(default_factory=list)
    supporting_numerical_result_ids: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(
        default_factory=list,
        description="Preserved conditions of all referenced supports; never dropped",
    )
    confidence: FindingConfidence = FindingConfidence.medium
    knowledge_basis: KnowledgeBasis = KnowledgeBasis.research_inference
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("finding statement must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class ContributionType(str, Enum):
    theoretical = "theoretical"
    mechanism = "mechanism"
    analytical = "analytical"
    empirical_implication = "empirical_implication"
    managerial = "managerial"
    IS_literature = "IS_literature"
    methodological = "methodological"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class ContributionClaim(BaseModel):
    """A claim about the contribution relative to the reviewed corpus.

    Language must be corpus-bounded; global-novelty claims ("first study")
    are rejected/normalized during assembly.
    """

    gap_id: str
    finding_ids: list[str] = Field(default_factory=list)
    claim: str
    contribution_type: ContributionType = ContributionType.theoretical
    advances_literature: str = Field(
        default="", description="Why this advances the reviewed literature (corpus-bounded)"
    )
    novelty_claim: str | None = Field(
        default=None, description="Optional explicit novelty statement (never 'first')"
    )
    novelty_normalized: bool = Field(
        default=False,
        description="True when a sweeping novelty phrase was removed during assembly",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("claim")
    @classmethod
    def validate_claim(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("contribution claim must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class ImplicationKind(str, Enum):
    theory = "theory"
    IS_research = "IS_research"
    management = "management"
    platform_firm_strategy = "platform_firm_strategy"
    policy = "policy"
    future_research = "future_research"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class ImplicationClaimType(str, Enum):
    """Distinguish the epistemic status of each implication."""

    mathematically_established = "mathematically_established"
    interpretation = "interpretation"
    managerial_implication = "managerial_implication"
    speculation_future_hypothesis = "speculation_future_hypothesis"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class ResearchImplication(BaseModel):
    """A structured implication clearly separated by epistemic status."""

    implication_kind: ImplicationKind = ImplicationKind.theory
    claim_type: ImplicationClaimType = ImplicationClaimType.interpretation
    text: str
    grounded_in_finding_ids: list[str] = Field(default_factory=list)
    note: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("implication text must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class ResultsPackageStatus(str, Enum):
    assembled = "assembled"
    critiqued = "critiqued"
    revised = "revised"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class ResearchResultsPackage(BaseModel):
    """Immutable input to manuscript drafting (Phase 4B)."""

    research_question_id: str | None = Field(default=None)
    gap_id: str
    selected_mechanism_id: str
    model_id: str
    equilibrium_analysis_id: str
    equilibrium_candidate_id: str
    numerical_experiment_id: str | None = Field(default=None)
    finding_ids: list[str] = Field(default_factory=list)
    contribution_claim_ids: list[str] = Field(default_factory=list)
    implication_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    status: ResultsPackageStatus = ResultsPackageStatus.assembled
    critique_ids: list[str] = Field(default_factory=list)
    summary: str | None = Field(default=None)
    model_role: str = Field(default="reasoning")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class ResultsAssemblyExecution(BaseModel):
    """Operational record of a results-assembly run."""

    numerical_experiment_id: str
    equilibrium_analysis_id: str
    model_id: str
    findings_created: int = 0
    contributions_created: int = 0
    implications_created: int = 0
    novelty_claims_normalized: int = 0
    failures: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, Any] = Field(default_factory=dict)
    model_role: str = Field(default="reasoning")
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class ResultsCritiqueCategory(str, Enum):
    overclaiming = "overclaiming"
    unsupported_novelty_claim = "unsupported_novelty_claim"
    missing_conditions = "missing_conditions"
    symbolic_numerical_contradiction = "symbolic_numerical_contradiction"
    causal_overstatement = "causal_overstatement"
    weak_gap_link = "weak_gap_link"
    weak_is_contribution = "weak_is_contribution"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class ResultsCritiqueVerdict(str, Enum):
    approve = "approve"
    revise = "revise"
    reject = "reject"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class ResultsCritiqueIssue(BaseModel):
    category: ResultsCritiqueCategory
    description: str
    severity: str = Field(default="medium", pattern="^(high|medium|low)$")
    location: str | None = Field(default=None)

    model_config = {"extra": "forbid"}


class ResultsCritique(BaseModel):
    """Independent critique of a ResearchResultsPackage, persisted separately.

    Deterministic checks (symbolic/numerical contradiction, missing
    conditions) are merged with the qualitative critique of the `critic` role.
    """

    package_id: str
    issues: list[ResultsCritiqueIssue] = Field(default_factory=list)
    overall_assessment: str
    verdict: ResultsCritiqueVerdict = ResultsCritiqueVerdict.revise
    recommendations: list[str] = Field(default_factory=list)
    model_role: str = Field(default="critic")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("overall_assessment")
    @classmethod
    def validate_assessment(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("overall assessment must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
