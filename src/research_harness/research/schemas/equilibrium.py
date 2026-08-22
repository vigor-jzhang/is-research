"""Phase 3C equilibrium artifacts — optimization problems, FOCs, best
responses, equilibrium candidates, and symbolic verification.

No propositions, comparative statics, or numerical experiments here (Phase 3D+).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from research_harness.research.schemas.model import Expression


class SolutionMethod(str, Enum):
    simultaneous = "simultaneous"
    backward_induction = "backward_induction"
    sympy_solved = "sympy_solved"
    llm_proposed = "llm_proposed"
    implicit_foc = "implicit_foc"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class VerificationStatus(str, Enum):
    pending = "pending"
    verified = "verified"
    partially_verified = "partially_verified"
    failed = "failed"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class OptimizationProblem(BaseModel):
    """An actor's optimization problem derived structurally from the model."""

    model_id: str
    actor_id: str
    decision_variables: list[str] = Field(default_factory=list)
    objective: Expression
    constraints: list[Expression] = Field(default_factory=list)
    method: str = Field(default="maximize")
    derived_by: str = Field(default="structural", description="structural | llm_proposed")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class FirstOrderCondition(BaseModel):
    """A symbolic first-order condition ∂payoff/∂x = 0 (interior case)."""

    model_id: str
    actor_id: str
    decision_variable: str
    payoff_expression: Expression
    foc_expression: Expression
    constraints: list[Expression] = Field(default_factory=list)
    candidate_solutions: list[Expression] = Field(default_factory=list)
    applicable: bool = Field(
        default=True,
        description="False when FOCs do not apply (e.g. constant payoff); never fabricated",
    )
    note: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class BestResponse(BaseModel):
    """Closed-form best response when solvable; otherwise the implicit FOC."""

    model_id: str
    actor_id: str
    decision_variable: str
    response_expression: Expression | None = Field(
        default=None, description="Closed form x*(other vars); None when implicit"
    )
    implicit: bool = False
    conditions: list[str] = Field(default_factory=list)
    derivation: str = Field(default="foc_solved")
    solution_method: str = Field(default=SolutionMethod.sympy_solved.value)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class EquilibriumExpression(BaseModel):
    """One variable's equilibrium expression with its conditions."""

    variable: str
    expression: Expression
    conditions: list[str] = Field(
        default_factory=list,
        description="Parameter restrictions, interiority, positivity, denominator != 0",
    )
    solution_method: str = Field(default=SolutionMethod.sympy_solved.value)

    model_config = {"extra": "forbid"}


class EquilibriumCandidate(BaseModel):
    """A candidate equilibrium: expressions for all decision variables."""

    model_id: str
    analysis_id: str | None = Field(default=None)
    expressions: list[EquilibriumExpression] = Field(default_factory=list)
    decision_variables: list[str] = Field(default_factory=list)
    solution_method: str = Field(default=SolutionMethod.simultaneous.value)
    proposed_by: str = Field(default="sympy", description="sympy | llm")
    verification_status: VerificationStatus = VerificationStatus.pending
    revision_round: int = Field(default=0)
    revision_notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class CheckType(str, Enum):
    symbol_validation = "symbol_validation"
    foc_residual = "foc_residual"
    second_order_condition = "second_order_condition"
    best_response_consistency = "best_response_consistency"
    timing_order = "timing_order"
    constraint_domain = "constraint_domain"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class VerificationCheck(BaseModel):
    """One deterministic symbolic check with its result."""

    check_type: CheckType
    passed: bool
    detail: str
    symbolic_detail: str | None = Field(
        default=None, description="e.g. the residual expression after substitution"
    )

    model_config = {"extra": "forbid"}


class EquilibriumVerification(BaseModel):
    """Deterministic symbolic verification of an EquilibriumCandidate.

    `verified` requires all applicable checks to pass; `partially_verified`
    when residuals pass but some conditions cannot be signed symbolically;
    `failed` when any residual/structural check fails.
    """

    model_id: str
    candidate_id: str
    status: VerificationStatus
    checks: list[VerificationCheck] = Field(default_factory=list)
    conditions_required: list[str] = Field(default_factory=list)
    verification_method: str = Field(default="symbolic")
    notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class EquilibriumAnalysisStatus(str, Enum):
    derived = "derived"
    partially_derived = "partially_derived"
    failed = "failed"


class EquilibriumAnalysis(BaseModel):
    """Durable aggregate of a derivation run over a FormalAnalyticalModel.

    Immutable; updated by superseding artifacts as verification results and
    revised candidates arrive.
    """

    model_id: str
    candidate_ids: list[str] = Field(default_factory=list)
    verification_ids: list[str] = Field(default_factory=list)
    selected_candidate_id: str | None = Field(default=None)
    status: EquilibriumAnalysisStatus = EquilibriumAnalysisStatus.failed
    solution_order: list[str] = Field(
        default_factory=list,
        description="Solution order honoring timing, e.g. ['seller', 'platform']",
    )
    solution_method: str = Field(default=SolutionMethod.simultaneous.value)
    revision_rounds: int = Field(default=0)
    summary: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class EquilibriumExecutionStatus(str, Enum):
    solvable = "solvable"
    not_solvable = "not_solvable"
    derived = "derived"
    partially_derived = "partially_derived"
    failed = "failed"


class EquilibriumExecution(BaseModel):
    """Operational record of an equilibrium derivation run."""

    model_id: str
    status: EquilibriumExecutionStatus = EquilibriumExecutionStatus.solvable
    optimization_problems_created: int = 0
    focs_created: int = 0
    best_responses_created: int = 0
    candidates_created: int = 0
    verification_status: VerificationStatus = VerificationStatus.pending
    revisions_used: int = 0
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
