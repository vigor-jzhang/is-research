"""Phase 3B formal analytical model schemas — the model specification.

The model is a structured, machine-checkable specification: actors, symbol
table (variables/parameters), timing stages, information structure,
assumptions with grounding, and payoff functions with validated symbols.
No equilibrium, propositions, or numerical experiments here (Phase 3C+).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.research.schemas.mechanism import KnowledgeBasis


class SymbolKind(str, Enum):
    """Taxonomy of model symbols."""

    decision_variable = "decision_variable"
    state_variable = "state_variable"
    parameter = "parameter"
    derived_quantity = "derived_quantity"
    random_variable = "random_variable"
    private_information = "private_information"
    observable_signal = "observable_signal"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class Visibility(str, Enum):
    public = "public"
    private = "private"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class Expression(BaseModel):
    """Machine-readable expression with human-readable LaTeX.

    `expression` prefers SymPy-compatible syntax so later phases can verify
    expressions symbolically. `symbols_used` is the declared symbol set;
    validation checks it against the model symbol table and the parsed
    expression's free symbols.
    """

    expression: str
    latex: str | None = Field(default=None)
    symbols_used: list[str] = Field(default_factory=list)

    @field_validator("expression")
    @classmethod
    def validate_expression(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("expression must be non-empty")
        return v

    model_config = {"extra": "forbid"}


class ModelActor(BaseModel):
    actor_id: str
    name: str
    role: str | None = Field(default=None, description="e.g. platform, seller, buyer")
    strategic: bool = Field(default=True, description="optimizes their own payoff")
    description: str | None = Field(default=None)

    @field_validator("actor_id", "name")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must be non-empty")
        return v

    model_config = {"extra": "forbid"}


class ModelVariable(BaseModel):
    symbol: str
    name: str
    meaning: str
    domain: str = Field(default="R", description="e.g. R, R_+, [0,1], {0,1}")
    units: str | None = Field(default=None)
    kind: SymbolKind = SymbolKind.state_variable
    owner_actor_id: str | None = Field(
        default=None,
        description="Required for decision_variable; optional otherwise",
    )

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("symbol must be non-empty")
        if not v.replace("_", "").replace("'", "").isalnum():
            raise ValueError(f"invalid symbol {v!r} (use letters, digits, underscores)")
        return v

    @field_validator("name", "meaning")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must be non-empty")
        return v

    model_config = {"extra": "forbid"}


class ModelParameter(BaseModel):
    symbol: str
    name: str
    meaning: str
    domain: str = Field(default="R")
    units: str | None = Field(default=None)

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("symbol must be non-empty")
        if not v.replace("_", "").replace("'", "").isalnum():
            raise ValueError(f"invalid symbol {v!r} (use letters, digits, underscores)")
        return v

    @field_validator("name", "meaning")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must be non-empty")
        return v

    model_config = {"extra": "forbid"}


class ModelTimingStage(BaseModel):
    """One stage of the model timing (explicit, not hidden in prose)."""

    stage_number: int = Field(ge=0, description="0-based stage index")
    name: str
    description: str
    actor_ids: list[str] = Field(
        default_factory=list, description="Actors active at this stage (nature may be empty)"
    )

    @field_validator("name", "description")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must be non-empty")
        return v

    model_config = {"extra": "forbid"}


class InformationItem(BaseModel):
    """Who observes what, when, and with which visibility."""

    actor_id: str
    variable_symbols: list[str] = Field(default_factory=list)
    available_at_stage: int = Field(default=0, ge=0)
    visibility: Visibility = Visibility.public
    description: str | None = Field(default=None)

    model_config = {"extra": "forbid"}


class UncertaintyItem(BaseModel):
    """Uncertainty / distributions over random variables."""

    variable_symbol: str
    distribution: str = Field(description="e.g. Uniform(0,1), Normal(mu, sigma)")
    belief_note: str | None = Field(default=None)

    @field_validator("distribution")
    @classmethod
    def validate_distribution(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("distribution must be non-empty")
        return v

    model_config = {"extra": "forbid"}


class InformationStructure(BaseModel):
    """Explicit information structure: who observes what/when; uncertainty."""

    items: list[InformationItem] = Field(default_factory=list)
    uncertainty: list[UncertaintyItem] = Field(default_factory=list)
    summary: str | None = Field(default=None)

    model_config = {"extra": "forbid"}


class ModelAssumption(BaseModel):
    statement: str
    mathematical_form: Expression | None = Field(default=None)
    knowledge_basis: KnowledgeBasis = KnowledgeBasis.modeling_assumption
    source_ids: list[str] = Field(
        default_factory=list,
        description="Required for literature_supported: evidence/synthesis artifact ids",
    )
    purpose: str | None = Field(default=None)
    restrictiveness: str = Field(default="medium", description="low | medium | high")

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("assumption statement must be non-empty")
        return v

    @field_validator("restrictiveness")
    @classmethod
    def validate_restrictiveness(cls, v: str) -> str:
        if v not in ("low", "medium", "high"):
            raise ValueError(f"restrictiveness must be low|medium|high, got {v!r}")
        return v

    model_config = {"extra": "forbid"}


class PayoffFunction(BaseModel):
    actor_id: str
    objective_type: str = Field(
        default="utility", description="e.g. profit, utility, welfare, cost"
    )
    expression: Expression
    decision_variables: list[str] = Field(
        default_factory=list, description="Symbols of decision variables this actor controls"
    )
    parameters: list[str] = Field(default_factory=list, description="Parameter symbols used")
    constraints: list[Expression] = Field(default_factory=list)

    @field_validator("objective_type")
    @classmethod
    def validate_objective(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("objective type must be non-empty")
        return v

    model_config = {"extra": "forbid"}


class ModelStatus(str, Enum):
    draft = "draft"
    critiqued = "critiqued"
    revised = "revised"
    approved = "approved"


class FormalAnalyticalModel(BaseModel):
    """A complete, structurally validated analytical model specification.

    References all components explicitly; no free-form equation block.
    Immutable; revisions create superseding artifacts.
    """

    selected_mechanism_id: str
    gap_id: str | None = Field(default=None)
    title: str
    description: str
    game_type: str | None = Field(
        default=None, description="e.g. static complete information, dynamic private information"
    )
    actors: list[ModelActor] = Field(default_factory=list)
    variables: list[ModelVariable] = Field(default_factory=list)
    parameters: list[ModelParameter] = Field(default_factory=list)
    assumptions: list[ModelAssumption] = Field(default_factory=list)
    timing: list[ModelTimingStage] = Field(default_factory=list)
    information_structure: InformationStructure = Field(default_factory=InformationStructure)
    payoffs: list[PayoffFunction] = Field(default_factory=list)
    status: ModelStatus = ModelStatus.draft
    revision_notes: list[str] = Field(default_factory=list)
    model_role: str = Field(default="reasoning")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "description")
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


class ModelCritiqueCategory(str, Enum):
    mechanism_model_mismatch = "mechanism_model_mismatch"
    undefined_concept = "undefined_concept"
    inconsistent_timing = "inconsistent_timing"
    impossible_information = "impossible_information"
    redundant_assumption = "redundant_assumption"
    missing_strategic_actor = "missing_strategic_actor"
    payoff_inconsistency = "payoff_inconsistency"
    poor_tractability = "poor_tractability"
    unjustified_restriction = "unjustified_restriction"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class ModelCritiqueIssue(BaseModel):
    category: ModelCritiqueCategory
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


class ModelSpecificationCritique(BaseModel):
    """Independent critique of a FormalAnalyticalModel (durable)."""

    model_id: str
    selected_mechanism_id: str | None = Field(default=None)
    issues: list[ModelCritiqueIssue] = Field(default_factory=list)
    overall_assessment: str
    verdict: str = Field(default="revise", description="keep | revise | reject")
    revision_recommendations: list[str] = Field(default_factory=list)
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

    @field_validator("verdict")
    @classmethod
    def validate_verdict(cls, v: str) -> str:
        if v not in ("keep", "revise", "reject"):
            raise ValueError(f"verdict must be keep|revise|reject, got {v!r}")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class ModelSpecificationExecution(BaseModel):
    """Operational record of a model build run."""

    selected_mechanism_id: str
    model_created: bool = False
    rejected: bool = False
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
