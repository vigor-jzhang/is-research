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


class PreflightStatus(str, Enum):
    """Provider/model preflight classification (Phase 7D.3B).

    A provider-unavailable model is never interpreted as academically
    incapable and is never qualified from a failed probe."""

    available = "available"
    temporarily_unavailable = "temporarily_unavailable"
    capability_mismatch = "capability_mismatch"
    provider_error = "provider_error"


class PreflightCheckKind(str, Enum):
    reachability = "reachability"
    structured_output = "structured_output"
    context_size = "context_size"
    timeout_retry = "timeout_retry"


class ModelPreflightCheck(BaseModel):
    """One capability-probe result for a candidate model (Phase 7D.3B)."""

    kind: str
    passed: bool
    detail: str = ""
    latency_ms: float | None = None
    resolved_model: str | None = None

    model_config = {"extra": "forbid"}


class ModelPreflight(BaseModel):
    """Lightweight provider/model capability preflight (Phase 7D.3B).

    Runs BEFORE a candidate enters an expensive qualification campaign so
    provider/gateway availability failures stay distinct from model-quality
    failures. A model that fails preflight is never qualified; a
    temporarily_unavailable model is not interpreted as academically
    incapable."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: str | None = None
    candidate_id: str
    provider: str
    requested_model: str
    resolved_model: str | None = None
    status: PreflightStatus
    checks: list[ModelPreflightCheck] = Field(default_factory=list)
    required_context_chars: int = 0
    timeout_seconds: float = 0.0
    retries: int = 0
    error: str = ""
    created_at: datetime = Field(default_factory=_utcnow)

    @property
    def reachable(self) -> bool:
        return any(
            c.kind == PreflightCheckKind.reachability.value and c.passed for c in self.checks
        )

    model_config = {"extra": "forbid"}


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


class TaskQualificationResult(BaseModel):
    """One model's qualification verdict for one canonical research task (Phase 7D.3).

    Task qualification uses the SAME deterministic thresholds as the role
    criteria (never relaxed); a model may be `qualified_for_task` without being
    qualified for the entire role."""

    role: str
    task: str
    task_label: str = ""
    candidate_id: str
    model: dict[str, Any] = Field(default_factory=dict)
    resolved_model: str | None = None
    benchmark_id: str
    repetitions: int = 0
    deterministic_pass_rate_mean: float | None = None
    deterministic_pass_rate_worst: float | None = None
    deterministic_pass_rate_variance: float | None = None
    structured_output_success_rate: float | None = None
    provider_error_frequency: float | None = None
    critical_grounding_failures: int = 0
    critical_failure_frequency: float | None = None
    latency_ms_p50: float | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
    qualified: bool = False
    rejection_reasons: list[str] = Field(default_factory=list)
    evidence_diagnostics: dict[str, int] = Field(default_factory=dict)
    task_diagnostics: dict[str, int] = Field(
        default_factory=dict,
        description="Task-specific failure diagnostics (Phase 7D.3B); diagnostic "
        "only, never part of the pass criteria",
    )
    live_quality_run_id: str | None = None

    model_config = {"extra": "forbid"}


class TaskQualificationMatrix(BaseModel):
    """Per-role task-qualification matrix (Phase 7D.3).

    Rows are (model, task) verdicts. `qualified_tasks_by_model` and
    `qualified_models_by_task` summarize coverage; role qualification is kept
    separate (a task-qualified model is not role-qualified)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: str
    benchmark_id: str
    tasks: list[str] = Field(default_factory=list)
    rows: list[TaskQualificationResult] = Field(default_factory=list)
    qualified_models_by_task: dict[str, list[str]] = Field(default_factory=dict)
    ranked_models_by_task: dict[str, list[str]] = Field(
        default_factory=dict,
        description="qualified models per task ranked: correctness, reliability, "
        "structured-output, latency, cost, deterministic tie-break (Phase 7D.3)",
    )
    qualified_tasks_by_model: dict[str, list[str]] = Field(default_factory=dict)
    role_qualified_models: list[str] = Field(default_factory=list)
    criteria: Any = None
    repetitions: int = 0
    created_at: datetime = Field(default_factory=_utcnow)

    model_config = {"extra": "forbid"}


class ModelCapabilityProfile(BaseModel):
    """One model's production capability summary across tasks and roles (Phase 7D.3).

    `task_qualifications`: canonical task -> qualified_for_task |
    not_qualified_for_task. `role_qualified` reflects the separate role-level
    qualification. Raw dimensions (latency/tokens/cost) are preserved."""

    model: str
    resolved_model: str | None = None
    role: str
    benchmark_id: str
    task_qualifications: dict[str, str] = Field(default_factory=dict)
    role_qualified: bool = False
    stability: str | None = None
    repetitions: int = 0
    latency_ms_p50: float | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
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


class RemainingTaskCoverageRow(BaseModel):
    """One remaining-task coverage row (Phase 7D.3B).

    Distinguishes model capability from provider availability so remaining
    gaps can be attributed honestly: `provider_unavailable_count` counts
    candidates whose preflight classified them unavailable/provider-error
    (never interpreted as academically incapable)."""

    task: str
    qualified_primary: str | None = None
    qualified_fallback: str | None = None
    qualified_model_count: int = 0
    tested_model_count: int = 0
    provider_unavailable_count: int = 0
    dominant_failure_reason: str = ""

    model_config = {"extra": "forbid"}


class RemainingTaskCoverage(BaseModel):
    """Coverage of the tasks that were unqualified at the start of Phase 7D.3B.

    `provider_unavailable` models are never counted as qualified and their
    failures are attributed to availability, not capability."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    rows: list[RemainingTaskCoverageRow] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=_utcnow)

    model_config = {"extra": "forbid"}
