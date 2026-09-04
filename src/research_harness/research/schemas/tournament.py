"""Model tournament + role leaderboard schemas — Phase 7B.

Immutable, versioned artifacts produced by the model-tournament service.
Schemas carry only domain fields; aggregation/ranking logic lives in
`research/tournament/` modules, never in these models. Cost is never
invented: provider-returned usage/pricing is recorded, and unknown values
stay null.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TournamentFailureKind(str, Enum):
    structured_output_failure = "structured_output_failure"
    timeout = "timeout"
    provider_error = "provider_error"
    rate_limit = "rate_limit"
    validation_failure = "validation_failure"


class TournamentPricing(BaseModel):
    """Configured pricing used to calculate cost when the provider returns
    none. Cost is only calculated when both rates are present."""

    source: str | None = Field(default=None, description="Pricing source, e.g. openrouter-catalog")
    version: str | None = Field(default=None, description="Pricing source version / date")
    input_per_million: float | None = Field(
        default=None, ge=0.0, description="USD per 1M input tokens"
    )
    output_per_million: float | None = Field(
        default=None, ge=0.0, description="USD per 1M output tokens"
    )

    model_config = {"extra": "forbid"}


class TournamentModelConfig(BaseModel):
    """One candidate model evaluated for a logical role."""

    candidate_id: str = Field(description="Unique candidate key within the plan")
    provider: str = Field(
        default="openrouter", description="Provider service id (model_provider.<provider>)"
    )
    requested_model: str = Field(description="Exact model slug requested")
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    structured_output: bool = Field(
        default=True, description="Request strict structured output when a schema is present"
    )
    pricing: TournamentPricing | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class ModelCallRecord(BaseModel):
    """One candidate model call measured at the model boundary."""

    call_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: str
    model: str | None = Field(
        default=None, description="Resolved model id returned by the provider"
    )
    requested_model: str | None = Field(default=None)
    provider: str | None = Field(default=None)
    temperature: float | None = None
    max_tokens: int | None = None
    structured: bool = Field(default=False, description="Request carried a response_schema")
    latency_ms: float | None = Field(
        default=None, description="Model latency at the boundary (not wall-clock)"
    )
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    provider_cost: float | None = Field(
        default=None, description="Cost returned by the provider (if any)"
    )
    calculated_cost: float | None = Field(
        default=None, description="Cost computed from configured pricing (if any)"
    )
    cost_source: str | None = Field(default=None, description="'provider' | 'pricing' | None")
    status: str = Field(
        default="success", description="success | error | structured_output_failure"
    )
    failure: TournamentFailureKind | None = None
    retries: int = Field(default=0)
    benchmark_id: str = ""
    case_id: str = ""

    model_config = {"extra": "forbid"}


class BenchmarkRunRef(BaseModel):
    """Immutable reference to one persisted EvaluationRun + report."""

    benchmark_id: str
    benchmark_version: int
    repetition: int
    run_id: str
    report_id: str
    report_status: str
    cases_total: int
    cases_passed: int
    cases_failed: int
    cases_error: int
    latency_ms: int = Field(default=0, description="Benchmark total duration")
    cost_usd: float = Field(default=0.0)

    model_config = {"extra": "forbid"}


class TournamentModelResult(BaseModel):
    """Aggregated result for one candidate across benchmarks x repetitions.

    Individual dimensions stay visible; no opaque blended score."""

    candidate_id: str
    config: TournamentModelConfig
    resolved_model: str | None = Field(default=None, description="Model id actually used, if known")
    role: str
    benchmark_runs: list[BenchmarkRunRef] = Field(default_factory=list)
    calls: list[ModelCallRecord] = Field(default_factory=list)
    deterministic_pass_rate: float | None = None
    benchmark_pass_rate: float | None = None
    case_pass_rate: float | None = None
    # Share of cases that errored. Kept separate from deterministic_pass_rate,
    # which deliberately measures quality among completed cases only: a model
    # must clear both to qualify.
    case_error_rate: float | None = None
    # Share of repetitions that crashed before producing a report.
    repetition_failure_rate: float | None = None
    structured_output_success_rate: float | None = None
    model_error_rate: float | None = None
    retry_rate: float | None = None
    latency_ms_mean: float | None = None
    latency_ms_p50: float | None = None
    latency_ms_p95: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = Field(default=None, description="None when cost is unknown")
    cost_per_successful_case: float | None = None
    cost_per_successful_benchmark: float | None = None
    advisory_score: float | None = None
    eligibility: str | None = Field(default=None, description="eligible | not_eligible")
    eligibility_reason: str = ""
    failure_counts: dict[str, int] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class TournamentPlan(BaseModel):
    """The reproducible specification of one tournament."""

    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    role: str = Field(description="Logical role evaluated: fast | reasoning | critic")
    benchmark_ids: list[str] = Field(default_factory=list)
    models: list[TournamentModelConfig] = Field(default_factory=list)
    repetitions: int = Field(
        default=1, ge=1, le=10, description="Stochastic-reliability repetitions per model/benchmark"
    )
    timeout_seconds: float = Field(default=120.0, ge=1.0)
    retries: int = Field(
        default=2, ge=0, le=5, description="Retry policy for transient model failures"
    )
    deterministic_pass_threshold: float = Field(
        default=0.85, ge=0.0, le=1.0, description="Eligibility gate on deterministic_pass_rate"
    )
    evaluator_ids: list[str] | None = Field(
        default=None, description="Override evaluator set (default: each benchmark's config)"
    )
    advisory_evaluators: list[str] = Field(
        default_factory=list, description="Optional advisory evaluators appended to each run"
    )
    ranking_rules: dict[str, Any] = Field(
        default_factory=dict,
        description="Snapshot of the deterministic ranking hierarchy applied (for reproducibility)",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class TournamentRun(BaseModel):
    """Immutable record of one executed tournament."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    plan_id: str
    plan_hash: str
    plan_snapshot: dict[str, Any] = Field(
        default_factory=dict, description="Full plan dump for reproducibility"
    )
    role: str
    benchmark_ids: list[str] = Field(default_factory=list)
    benchmark_versions: dict[str, int] = Field(default_factory=dict)
    repetitions: int = 1
    ranking_rules: dict[str, Any] = Field(default_factory=dict)
    model_results: list[TournamentModelResult] = Field(default_factory=list)
    leaderboard_id: str | None = None
    status: str = "completed"
    failures: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class LeaderboardEntry(BaseModel):
    """One row in a RoleLeaderboard exposing every raw dimension."""

    candidate_id: str
    model: dict[str, Any] = Field(default_factory=dict, description="TournamentModelConfig dump")
    resolved_model: str | None = None
    rank: int
    eligibility: str
    eligibility_reason: str = ""
    deterministic_pass_rate: float | None = None
    benchmark_pass_rate: float | None = None
    case_pass_rate: float | None = None
    case_error_rate: float | None = None
    repetition_failure_rate: float | None = None
    structured_output_success_rate: float | None = None
    model_error_rate: float | None = None
    retry_rate: float | None = None
    latency_ms_mean: float | None = None
    latency_ms_p50: float | None = None
    latency_ms_p95: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
    cost_per_successful_case: float | None = None
    cost_per_successful_benchmark: float | None = None
    advisory_score: float | None = None
    caveats: list[str] = Field(
        default_factory=list, description="E.g. cost unknown; structured output unsupported"
    )

    model_config = {"extra": "forbid"}


class RoleLeaderboard(BaseModel):
    """Immutable leaderboard for one role from one tournament run."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: str
    plan_id: str
    tournament_run_id: str
    plan_hash: str
    ranking_rules: dict[str, Any] = Field(default_factory=dict)
    entries: list[LeaderboardEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="e.g. repetitions used for this leaderboard"
    )
    evidence_type: str = Field(
        default="fixture_evidence",
        description="fixture_evidence (offline tournaments) | live_quality_evidence "
        "(live-quality runs). Production routing requires live_quality_evidence.",
    )

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
