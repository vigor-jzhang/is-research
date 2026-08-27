"""Live-quality model validation + routing-readiness schemas — Phase 7D.0/7D.2.

Durable artifacts recording live-quality benchmark runs, per-model results
(with repetitions/variance), structured failure attribution, task-level
performance, and deterministic routing-readiness assessments. Production
routing is never enabled automatically; these artifacts only gate whether a
role MAY be considered ready for Phase 7D controlled activation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FailureAttributionKind(str, Enum):
    """Structured attribution for failed live-quality cases (Phase 7D.2).

    Only genuine model/provider outcomes count toward qualification after
    benchmark validity is confirmed; `benchmark_reference_defect` and
    `evaluator_defect` are excluded from qualification."""

    model_reasoning_failure = "model_reasoning_failure"
    structured_output_failure = "structured_output_failure"
    grounding_failure = "grounding_failure"
    instruction_following_failure = "instruction_following_failure"
    provider_error = "provider_error"
    timeout = "timeout"
    rate_limit = "rate_limit"
    benchmark_reference_defect = "benchmark_reference_defect"
    evaluator_defect = "evaluator_defect"
    infrastructure_failure = "infrastructure_failure"


EXCLUDED_ATTRIBUTION_KINDS = frozenset(
    {
        FailureAttributionKind.benchmark_reference_defect.value,
        FailureAttributionKind.evaluator_defect.value,
    }
)


class QualificationCriteria(BaseModel):
    """Deterministic, role-specific routing-qualification criteria."""

    role: str
    min_deterministic_pass_rate: float = Field(default=0.85, ge=0.0, le=1.0)
    min_structured_output_success_rate: float = Field(default=0.85, ge=0.0, le=1.0)
    max_provider_error_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    min_repetitions: int = Field(default=3, ge=1, description="Runs per model/task")
    require_no_critical_grounding_failures: bool = True
    min_cases: int = Field(default=1, ge=1, description="Minimum benchmark cases exercised")
    leaderboard_max_age_seconds: float | None = Field(
        default=None, description="Live evidence freshness limit (None = no limit)"
    )
    description: str = ""

    model_config = {"extra": "forbid"}


class LiveQualityTaskResult(BaseModel):
    """One repetition of one live-quality benchmark (one EvaluationRun)."""

    repetition: int
    run_id: str
    report_id: str
    report_status: str
    cases_total: int
    cases_passed: int
    cases_failed: int
    cases_error: int
    task_pass_rate: float = Field(default=0.0, description="passed / total")
    task_completed: bool = Field(default=False, description="no case errors")
    critical_grounding_failures: int = 0
    latency_ms: int = 0
    failure_count: int = 0

    model_config = {"extra": "forbid"}


class LiveQualityTaskPerformance(BaseModel):
    """Per-task (per-case) live-quality performance across repetitions (Phase 7D.2/7D.3)."""

    task_id: str
    task_name: str = ""
    repetitions: int = 0
    pass_rate_mean: float | None = None
    pass_rate_worst: float | None = None
    pass_rate_variance: float | None = None
    pass_rates: list[float] = Field(
        default_factory=list, description="per-repetition task pass rates (Phase 7D.3)"
    )
    structured_output_success_rate: float | None = Field(
        default=None, description="per-task structured-output success across reps (Phase 7D.3)"
    )
    provider_error_frequency: float | None = Field(
        default=None, description="per-task provider-error frequency across reps (Phase 7D.3)"
    )
    critical_grounding_failures: int = 0
    failure_attribution: dict[str, int] = Field(default_factory=dict)
    excluded_failure_attribution: dict[str, int] = Field(default_factory=dict)
    evidence_diagnostics: dict[str, int] = Field(
        default_factory=dict,
        description="evidence-extraction failure diagnostics (Phase 7D.3)",
    )

    model_config = {"extra": "forbid"}


class LiveQualityModelResult(BaseModel):
    """Aggregated live-quality result for one model on one benchmark.

    Individual dimensions stay visible; no opaque blended score."""

    candidate_id: str
    model: dict[str, Any] = Field(default_factory=dict, description="TournamentModelConfig dump")
    resolved_model: str | None = None
    role: str
    benchmark_id: str
    benchmark_version: int = 1
    repetitions: int = 0
    task_results: list[LiveQualityTaskResult] = Field(default_factory=list)
    deterministic_pass_rate_mean: float | None = None
    deterministic_pass_rate_worst: float | None = None
    deterministic_pass_rate_variance: float | None = None
    case_pass_rate_mean: float | None = None
    structured_output_success_rate: float | None = None
    structured_output_failure_frequency: float | None = None
    provider_error_frequency: float | None = None
    model_error_rate: float | None = None
    critical_grounding_failures: int = 0
    latency_ms_p50_mean: float | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = Field(default=None, description="None when cost is unknown")
    qualification: bool | None = Field(default=None, description="Qualified for production routing")
    qualification_reasons: list[str] = Field(default_factory=list)
    failure_counts: dict[str, int] = Field(default_factory=dict)
    failure_attribution: dict[str, int] = Field(
        default_factory=dict,
        description="Case-failure kind -> count for genuine model/provider outcomes (Phase 7D.2)",
    )
    excluded_failure_attribution: dict[str, int] = Field(
        default_factory=dict,
        description="Case-failure kind -> count for confirmed benchmark/evaluator defects, "
        "excluded from qualification (Phase 7D.2)",
    )
    task_performance: list[LiveQualityTaskPerformance] = Field(
        default_factory=list,
        description="Per-task pass rates + failure attribution across repetitions (Phase 7D.2)",
    )
    stability: str | None = Field(
        default=None, description="stable | borderline | unstable (Phase 7D.2)"
    )
    evidence_timestamp: datetime = Field(
        default_factory=_utcnow, description="When this evidence was collected"
    )

    model_config = {"extra": "forbid"}


class LiveQualityRun(BaseModel):
    """Immutable record of one live-quality run for one model on one benchmark."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: str
    benchmark_id: str
    benchmark_version: int = 1
    model: dict[str, Any] = Field(default_factory=dict)
    repetitions: int = 1
    result: LiveQualityModelResult
    leaderboard_id: str | None = None
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class RoutingReadinessAssessment(BaseModel):
    """Immutable per-role production-routing readiness verdict.

    `unsafe_production_qualification` must always be False — any attempt to
    qualify production routing from insufficient/unsafe evidence is a bug."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: str
    created_at: datetime = Field(default_factory=_utcnow)
    criteria: QualificationCriteria
    qualified: bool
    reasons: list[str] = Field(default_factory=list)
    qualified_models: list[str] = Field(default_factory=list)
    fallback_qualified: bool = False
    fallback_model: str | None = None
    configured_model: str | None = None
    evidence: dict[str, Any] = Field(
        default_factory=dict, description="Model -> live-quality summary"
    )
    leaderboard_id: str | None = None
    unsafe_production_qualification: bool = Field(default=False)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}
