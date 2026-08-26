"""Live-model qualification campaign schemas — Phase 7D.1.

Durable artifacts recording per-role qualification campaigns over the
live-quality benchmarks: candidate results (with structured rejection kinds),
role summaries (primary/fallback/status), and the campaign record itself.
Production routing stays disabled; these artifacts only identify qualified
models for a future controlled-activation phase.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from research_harness.research.schemas.live_quality import QualificationCriteria


def _utcnow() -> datetime:
    return datetime.now(UTC)


class QualificationRejectionKind(str, Enum):
    below_quality_threshold = "below_quality_threshold"
    structured_output_failure = "structured_output_failure"
    critical_grounding_failure = "critical_grounding_failure"
    provider_error_rate = "provider_error_rate"
    insufficient_repetitions = "insufficient_repetitions"
    stale_evidence = "stale_evidence"
    capability_mismatch = "capability_mismatch"


class QualificationCandidateResult(BaseModel):
    """One candidate's live-quality result + qualification verdict (Phase 7D.1/7D.2)."""

    candidate_id: str
    model: dict[str, Any] = Field(default_factory=dict, description="TournamentModelConfig dump")
    resolved_model: str | None = None
    role: str
    benchmark_id: str
    qualified: bool
    rejection_kinds: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    live_quality_run_id: str | None = None
    leaderboard_id: str | None = None
    deterministic_pass_rate_mean: float | None = None
    deterministic_pass_rate_worst: float | None = None
    deterministic_pass_rate_variance: float | None = None
    critical_failure_frequency: float | None = None
    structured_output_success_rate: float | None = None
    provider_error_frequency: float | None = None
    latency_ms_p50: float | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
    stability: str | None = Field(
        default=None, description="stable | borderline | unstable (Phase 7D.2)"
    )
    repetitions: int = 0
    critical_grounding_failures: int = 0
    primary_eligible: bool = Field(
        default=False, description="qualified and not unstable (Phase 7D.2)"
    )
    fallback_eligible: bool = Field(
        default=False,
        description="qualified, not unstable, and not the selected primary (Phase 7D.2)",
    )
    failure_attribution: dict[str, int] = Field(default_factory=dict)
    excluded_failure_attribution: dict[str, int] = Field(default_factory=dict)
    task_performance: list[Any] = Field(
        default_factory=list, description="Per-task LiveQualityTaskPerformance snapshots"
    )

    model_config = {"extra": "forbid"}


class ProductionQualificationMatrixRow(BaseModel):
    """One (role, candidate) row of the production-qualification matrix (Phase 7D.2).

    Becomes the activation input for Phase 7D controlled routing."""

    role: str
    candidate: str
    qualified: bool
    stability: str | None = None
    primary_eligible: bool = False
    fallback_eligible: bool = False
    repetitions: int = 0
    rejection_kinds: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    live_quality_run_ids: list[str] = Field(default_factory=list)
    deterministic_pass_rate_mean: float | None = None
    deterministic_pass_rate_worst: float | None = None
    deterministic_pass_rate_variance: float | None = None
    structured_output_success_rate: float | None = None
    provider_error_frequency: float | None = None
    critical_grounding_failures: int = 0
    latency_ms_p50: float | None = None
    estimated_cost: float | None = None
    total_tokens: int | None = None

    model_config = {"extra": "forbid"}


class ProductionQualificationMatrix(BaseModel):
    """Per-role production-qualification matrix (Phase 7D.2).

    Primary/fallback eligibility require a qualified candidate that is not
    unstable. Rows preserve raw dimensions; unqualified candidates are never
    ranked above qualified ones."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: str
    status: str = Field(
        default="no_qualified_model",
        description="qualified | qualified_without_fallback | no_qualified_model",
    )
    primary: str | None = None
    fallback: str | None = None
    rows: list[ProductionQualificationMatrixRow] = Field(default_factory=list)
    benchmark_id: str
    repetitions: int = 0
    criteria: Any = None
    created_at: datetime = Field(default_factory=_utcnow)

    model_config = {"extra": "forbid"}


class RoleQualificationSummary(BaseModel):
    """Per-role qualification verdict (primary/fallback/status)."""

    role: str
    status: str = Field(
        default="no_qualified_model",
        description="qualified | qualified_without_fallback | no_qualified_model",
    )
    primary: str | None = None
    fallback: str | None = None
    qualified_models: list[str] = Field(default_factory=list)
    candidates: list[QualificationCandidateResult] = Field(default_factory=list)
    rejection_counts: dict[str, int] = Field(default_factory=dict)
    criteria: QualificationCriteria
    benchmark_id: str
    repetitions: int = 0
    created_at: datetime = Field(default_factory=_utcnow)

    model_config = {"extra": "forbid"}


class QualificationCampaign(BaseModel):
    """Immutable record of one role's qualification campaign."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: str
    benchmark_id: str
    repetitions: int = 3
    candidates: list[QualificationCandidateResult] = Field(default_factory=list)
    summary: RoleQualificationSummary
    live_quality_run_ids: list[str] = Field(default_factory=list)
    leaderboard_ids: list[str] = Field(default_factory=list)
    criteria: QualificationCriteria
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}
