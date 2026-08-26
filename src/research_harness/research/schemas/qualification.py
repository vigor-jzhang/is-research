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
    """One candidate's live-quality result + qualification verdict."""

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
