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
    min_repetitions: int = Field(
        default=1, ge=1, description="Minimum leaderboard repetitions of evidence"
    )
    leaderboard_max_age_seconds: float | None = Field(default=None, ge=1.0)
    leaderboard_ids: list[str] | None = Field(
        default=None, description="Explicit leaderboard artifacts to use (default: latest for role)"
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
