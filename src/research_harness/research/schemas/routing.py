"""Policy-constrained model routing schemas — Phase 7C (shadow/simulation only).

Immutable, versioned routing artifacts produced by the policy router. The
router is decision support + shadow evaluation only; it never replaces the
configured production role model. Schemas carry domain fields; selection
logic lives in `research/routing/`, never in these models. Cost is never
invented (unknown stays null).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RoutingDecisionStatus(str, Enum):
    selected = "selected"
    fallback = "fallback"
    insufficient_evidence = "insufficient_evidence"
    no_eligible_model = "no_eligible_model"


class RoutingPolicy(BaseModel):
    """A documented, explicit routing policy (not an opaque score)."""

    policy_id: str
    name: str
    version: str = "1"
    description: str = ""
    selection_rules: dict[str, Any] = Field(
        default_factory=dict,
        description="Exact decision rules snapshot, persisted with every decision",
    )

    model_config = {"extra": "forbid"}


class RoutingRequest(BaseModel):
    """What a caller asks the router to satisfy."""

    role: str = Field(description="Logical role: fast | reasoning | critic")
    required_deterministic_pass_rate: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Minimum deterministic quality (eligibility gate)"
    )
    max_estimated_cost: float | None = Field(default=None, ge=0.0)
    latency_limit_ms: float | None = Field(default=None, ge=0.0)
    require_structured_output: bool = Field(default=True)
    required_context_tokens: int | None = Field(default=None, ge=1)
    allowed_models: list[str] | None = Field(
        default=None, description="Allowed requested model slugs"
    )
    allowed_providers: list[str] | None = Field(default=None)
    require_fallback: bool = Field(default=True)
    min_structured_output_success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    max_model_error_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    max_case_error_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Maximum share of evaluation cases that may error. Errored cases are "
            "excluded from deterministic_pass_rate, so without this a model that "
            "errors on nearly every case can still score 1.0 on the few it finishes."
        ),
    )
    min_repetitions: int = Field(
        default=1, ge=1, description="Minimum leaderboard repetitions of evidence"
    )
    leaderboard_max_age_seconds: float | None = Field(default=None, ge=1.0)
    leaderboard_ids: list[str] | None = Field(
        default=None, description="Explicit leaderboard artifacts to use (default: latest for role)"
    )
    evidence_types: list[str] | None = Field(
        default=None,
        description="Required leaderboard evidence types, e.g. ['live_quality_evidence'] "
        "(default: any). Production routing requires live_quality_evidence.",
    )
    task_context: str = ""

    model_config = {"extra": "forbid"}


class RoutingCandidateAssessment(BaseModel):
    """One candidate considered for a routing decision (raw dimensions kept)."""

    candidate_id: str
    model: dict[str, Any] = Field(default_factory=dict, description="TournamentModelConfig dump")
    provider: str = ""
    requested_model: str = ""
    eligibility: str = Field(default="eligible", description="eligible | not_eligible")
    rejection_reason: str | None = None
    deterministic_pass_rate: float | None = None
    benchmark_pass_rate: float | None = None
    case_pass_rate: float | None = None
    case_error_rate: float | None = None
    repetition_failure_rate: float | None = None
    structured_output_success_rate: float | None = None
    model_error_rate: float | None = None
    retry_rate: float | None = None
    latency_ms_p50: float | None = None
    estimated_cost: float | None = None
    cost_per_successful_case: float | None = None
    advisory_score: float | None = None
    capability_ok: bool = True
    leaderboard_id: str | None = None

    model_config = {"extra": "forbid"}


class RoutingDecision(BaseModel):
    """One immutable routing decision with full rationale."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=_utcnow)
    policy_id: str
    policy_version: str = "1"
    policy_rules: dict[str, Any] = Field(
        default_factory=dict, description="Snapshot of the exact decision rules applied"
    )
    request: RoutingRequest
    role: str
    leaderboard_id: str | None = None
    leaderboard_age_seconds: float | None = None
    status: RoutingDecisionStatus
    selected_candidate_id: str | None = None
    selected_model: dict[str, Any] | None = Field(
        default=None, description="TournamentModelConfig dump of the selected candidate"
    )
    eligible_candidates: list[RoutingCandidateAssessment] = Field(default_factory=list)
    rejected_candidates: list[RoutingCandidateAssessment] = Field(default_factory=list)
    fallback_candidate_id: str | None = None
    expected_quality: float | None = Field(
        default=None, description="Expected deterministic_pass_rate"
    )
    expected_latency_ms: float | None = None
    expected_cost: float | None = None
    rationale: dict[str, Any] = Field(
        default_factory=dict, description="Structured decision fields"
    )
    shadow: dict[str, Any] = Field(
        default_factory=dict,
        description="Shadow-mode comparison (would_switch / deltas) when applicable",
    )

    model_config = {"extra": "forbid"}


class RoutingExecution(BaseModel):
    """Immutable record of a routing shadow/decision batch."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    decision_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    mode: str = Field(default="shadow", description="shadow (Phase 7C) — production never switched")
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class TaskAwareRoutingStatus(str, Enum):
    """Task-aware shadow routing outcome (Phase 7D.4, shadow only).

    - selected: an exact-task-qualified model was selected (shadow).
    - static_fallback: no exact-task-qualified model; the task keeps the
      configured static role model. This is never an error and never a
      dynamic switch.
    """

    selected = "selected"
    static_fallback = "static_fallback"


class TaskAwareQualifiedCandidate(BaseModel):
    """One candidate considered by the task-aware router for a single task."""

    candidate_id: str
    resolved_model: str | None = None
    qualified: bool = False
    deterministic_pass_rate_mean: float | None = None
    deterministic_pass_rate_worst: float | None = None
    structured_output_success_rate: float | None = None
    provider_error_frequency: float | None = None
    critical_grounding_failures: int = 0
    latency_ms_p50: float | None = None
    estimated_cost: float | None = None
    live_quality_run_id: str | None = None

    model_config = {"extra": "forbid"}


class TaskAwareRoutingDecision(BaseModel):
    """One immutable task-aware shadow routing decision (Phase 7D.4).

    Shadow mode only: `current_static_model` is always what executes in
    production; `shadow_selected_model` is the advisory task-specialized
    choice. Qualification is exact-task (never transferred across tasks), and
    uncovered tasks are recorded as `static_fallback` with `reason`
    `no_qualified_task_model` (or `stale_qualification`). Historical decisions
    are immutable."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=_utcnow)
    policy_id: str = "task_aware_shadow_v1"
    policy_version: str = "1"
    decision_policy: dict[str, Any] = Field(
        default_factory=dict,
        description="Snapshot of the exact decision rules applied (gate + rank)",
    )
    role: str
    task: str
    task_label: str = ""
    status: TaskAwareRoutingStatus
    reason: str = Field(
        default="",
        description="no_qualified_task_model | stale_qualification | '' (covered)",
    )
    matrix_id: str | None = None
    matrix_age_seconds: float | None = None
    current_static_model: str | None = None
    static_model_provider: str | None = None
    qualified_candidates: list[TaskAwareQualifiedCandidate] = Field(default_factory=list)
    primary_candidate_id: str | None = None
    fallback_candidate_id: str | None = None
    fallback_is_qualified: bool = False
    fallback_not_live_qualified: bool = Field(
        default=False,
        description="True when the fallback is the static configured model (not "
        "exact-task live qualified); never presented as qualified",
    )
    would_switch: bool | None = None
    shadow_selected_model: str | None = None
    qualification_result_ids: list[str] = Field(
        default_factory=list,
        description="Live-quality run ids backing the qualified candidates",
    )
    expected_quality_delta: float | None = None
    expected_latency_delta: float | None = None
    expected_cost_delta: float | None = None
    shadow: dict[str, Any] = Field(
        default_factory=dict,
        description="Shadow-mode comparison (routing_mode / would_switch / deltas)",
    )

    model_config = {"extra": "forbid"}


class TaskAwareShadowCampaign(BaseModel):
    """Immutable record of a task-aware shadow routing campaign batch."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    decision_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    mode: str = Field(
        default="shadow", description="shadow (Phase 7D.4) — production never switched"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}
