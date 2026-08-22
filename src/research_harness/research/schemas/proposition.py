"""Phase 3D artifacts — comparative statics, propositions, symbolic
verification, and economic interpretation.

No numerical experiments, parameter sweeps, plots, or welfare/robustness
experiments here (Phase 3E+).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.research.schemas.model import Expression


class StaticSign(str, Enum):
    positive = "positive"
    negative = "negative"
    zero = "zero"
    ambiguous = "ambiguous"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class ComparativeStatic(BaseModel):
    """∂outcome/∂parameter at the verified equilibrium, with sign conditions."""

    model_id: str
    equilibrium_candidate_id: str
    analysis_id: str | None = Field(default=None)
    outcome_variable: str
    parameter: str
    derivative_expression: Expression
    sign: StaticSign
    conditions: list[str] = Field(
        default_factory=list,
        description="Conditions under which the stated sign holds (never inferred when ambiguous)",
    )
    interpretation: str | None = Field(default=None)
    derived_by: str = Field(default="sympy")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class ComparativeStaticsAnalysis(BaseModel):
    """Durable aggregate of a comparative-statics run over an equilibrium."""

    model_id: str
    equilibrium_candidate_id: str
    static_ids: list[str] = Field(default_factory=list)
    status: str = Field(default="derived", description="derived | failed")
    summary: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class ComparativeStaticsExecution(BaseModel):
    """Operational record of a comparative-statics run."""

    model_id: str
    equilibrium_candidate_id: str
    statics_created: int = 0
    status: str = Field(default="derived", description="derived | failed")
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


class PropositionClaimType(str, Enum):
    monotonicity = "monotonicity"
    equality = "equality"
    threshold = "threshold"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class PropositionStatus(str, Enum):
    candidate = "candidate"
    verified = "verified"
    conditionally_verified = "conditionally_verified"
    failed = "failed"
    rejected = "rejected"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class Proposition(BaseModel):
    """A candidate proposition referencing the mathematical result supporting it."""

    model_id: str
    equilibrium_candidate_id: str
    comparative_statics_analysis_id: str
    statement: str
    claim_type: PropositionClaimType = PropositionClaimType.monotonicity
    outcome_variable: str | None = Field(default=None)
    parameter: str | None = Field(default=None)
    expected_sign: str | None = Field(
        default=None, description="positive | negative | zero (monotonicity)"
    )
    mathematical_form: Expression | None = Field(
        default=None, description="e.g. equality 'q1 - q2' with lhs/rhs semantics"
    )
    conditions: list[str] = Field(default_factory=list)
    supporting_static_ids: list[str] = Field(default_factory=list)
    status: PropositionStatus = PropositionStatus.candidate
    proposed_by: str = Field(default="llm")
    model_role: str = Field(default="reasoning")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("proposition statement must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class PropositionCheckType(str, Enum):
    equilibrium_consistency = "equilibrium_consistency"
    derivative_sign = "derivative_sign"
    algebraic_relation = "algebraic_relation"
    condition_requirement = "condition_requirement"
    symbol_validation = "symbol_validation"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class PropositionCheck(BaseModel):
    check_type: PropositionCheckType
    passed: bool
    detail: str
    symbolic_detail: str | None = Field(default=None)

    model_config = {"extra": "forbid"}


class PropositionVerificationStatus(str, Enum):
    verified = "verified"
    conditionally_verified = "conditionally_verified"
    failed = "failed"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class PropositionVerification(BaseModel):
    """Deterministic symbolic verification of a Proposition."""

    proposition_id: str
    model_id: str
    status: PropositionVerificationStatus
    checks: list[PropositionCheck] = Field(default_factory=list)
    conditions_required: list[str] = Field(default_factory=list)
    verification_method: str = Field(default="symbolic")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class PropositionCritiqueCategory(str, Enum):
    overclaiming = "overclaiming"
    interpretation_beyond_support = "interpretation_beyond_support"
    missing_conditions = "missing_conditions"
    trivial_proposition = "trivial_proposition"
    contradicts_assumptions_or_mechanism = "contradicts_assumptions_or_mechanism"
    weak_is_relevance = "weak_is_relevance"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class PropositionCritiqueIssue(BaseModel):
    category: PropositionCritiqueCategory
    description: str
    severity: str = Field(default="medium", description="high | medium | low")
    location: str | None = Field(default=None)

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        if v not in ("high", "medium", "low"):
            raise ValueError(f"severity must be high|medium|low, got {v!r}")
        return v

    model_config = {"extra": "forbid"}


class PropositionCritiqueVerdict(str, Enum):
    keep = "keep"
    revise = "revise"
    reject = "reject"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class PropositionCritique(BaseModel):
    """Independent critique of a proposition (durable)."""

    proposition_id: str
    issues: list[PropositionCritiqueIssue] = Field(default_factory=list)
    overall_assessment: str
    verdict: PropositionCritiqueVerdict = PropositionCritiqueVerdict.revise
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


class EconomicInterpretation(BaseModel):
    """Structured economic/IS interpretation of a proposition.

    Separates the mathematical result from the economic interpretation,
    the managerial implication, and the IS/theoretical implication.
    """

    proposition_id: str
    model_id: str
    selected_mechanism_id: str | None = Field(default=None)
    gap_id: str | None = Field(default=None)
    mathematical_result: str
    economic_interpretation: str
    managerial_implication: str
    is_theoretical_implication: str
    consistency_note: str | None = Field(
        default=None,
        description="How the interpretation stays within the verified result",
    )
    model_role: str = Field(default="reasoning")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("mathematical_result", "economic_interpretation")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
