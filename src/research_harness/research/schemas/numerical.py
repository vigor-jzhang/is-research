"""Phase 3E numerical experiment schemas — scenarios, sweeps, results,
robustness, welfare.

Numerical work is deterministic (SymPy/NumPy); the LLM never computes
authoritative numbers. No paper drafting / publication claims here (Phase 4).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SweepKind(str, Enum):
    baseline = "baseline"
    low_high = "low_high"
    sweep_1d = "sweep_1d"
    grid = "grid"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class SweepDimension(BaseModel):
    parameter: str
    start: float
    end: float
    steps: int = Field(default=11, ge=2, le=101, description="Inclusive point count")

    model_config = {"extra": "forbid"}


class ParameterSweep(BaseModel):
    """A parameter scenario: baseline point, low/high cases, 1-D sweep, or grid."""

    model_id: str
    equilibrium_candidate_id: str
    experiment_id: str | None = Field(default=None)
    name: str
    kind: SweepKind = SweepKind.sweep_1d
    dimensions: list[SweepDimension] = Field(default_factory=list)
    fixed_parameters: dict[str, float] = Field(default_factory=dict)
    total_points: int = Field(default=0)
    series_blob_ref: dict[str, Any] | None = Field(
        default=None,
        description="BlobStore reference when the full table exceeds the artifact threshold",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class NumericalResult(BaseModel):
    """One evaluated parameter point (structured, visualization-ready)."""

    model_id: str
    equilibrium_candidate_id: str
    experiment_id: str
    sweep_id: str | None = Field(default=None)
    scenario: str = Field(default="baseline", description="baseline | low | high | sweep | grid")
    group: str | None = Field(default=None, description="scenario/group label for plotting")
    x_parameter: str | None = Field(default=None, description="x axis for series")
    x_value: float | None = Field(default=None)
    parameter_values: dict[str, float] = Field(default_factory=dict)
    outcomes: dict[str, float] = Field(default_factory=dict)
    feasible: bool = True
    infeasible_reason: str | None = Field(default=None)
    conditions: list[str] = Field(
        default_factory=list, description="Conditions that hold at this point"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class RobustnessCheckType(str, Enum):
    parameter_range = "parameter_range"
    assumption_relaxation = "assumption_relaxation"
    boundary_condition = "boundary_condition"
    alternative_branch = "alternative_branch"
    sensitivity = "sensitivity"
    proposition_support = "proposition_support"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class RobustnessOutcome(str, Enum):
    supported = "supported"
    violated = "violated"
    not_testable = "not_testable"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class RobustnessViolation(BaseModel):
    parameter_values: dict[str, float]
    detail: str

    model_config = {"extra": "forbid"}


class RobustnessCheck(BaseModel):
    """A structured robustness result; never overwrites symbolic artifacts."""

    model_id: str
    equilibrium_candidate_id: str
    experiment_id: str
    proposition_id: str | None = Field(
        default=None, description="Proposition under test for proposition_support checks"
    )
    check_type: RobustnessCheckType
    description: str
    outcome: RobustnessOutcome
    admissible_points: int = 0
    violations: list[RobustnessViolation] = Field(default_factory=list)
    conclusion: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class WelfareMetric(BaseModel):
    name: str
    actor_id: str | None = Field(default=None)
    value: float
    definition: str = Field(default="evaluated model payoff at the equilibrium point")

    model_config = {"extra": "forbid"}


class WelfareAnalysis(BaseModel):
    """Welfare computed ONLY from metrics definable in the model."""

    model_id: str
    equilibrium_candidate_id: str
    experiment_id: str
    scenario: str = Field(default="baseline")
    parameter_values: dict[str, float] = Field(default_factory=dict)
    metrics: list[WelfareMetric] = Field(default_factory=list)
    total_welfare: float | None = Field(default=None)
    notes: list[str] = Field(default_factory=list)
    model_role: str = Field(default="reasoning")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class NumericalExperiment(BaseModel):
    """Durable aggregate of a numerical run over a verified equilibrium."""

    model_id: str
    equilibrium_candidate_id: str
    sweeps: list[str] = Field(default_factory=list)
    results: list[str] = Field(default_factory=list)
    robustness: list[str] = Field(default_factory=list)
    welfare: list[str] = Field(default_factory=list)
    status: str = Field(default="completed", description="completed | partial | failed")
    summary: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class NumericalExperimentExecution(BaseModel):
    """Operational record with full reproducibility metadata."""

    model_id: str
    equilibrium_candidate_id: str
    sweeps_created: int = 0
    results_created: int = 0
    results_infeasible: int = 0
    robustness_created: int = 0
    welfare_created: int = 0
    engine: str = Field(default="sympy+python")
    engine_version: str = Field(default="")
    seed: int | None = Field(
        default=0, description="No randomness is used; seed recorded for reproducibility"
    )
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
