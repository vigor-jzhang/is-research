"""Evaluation harness schemas — Phase 6A.

Immutable, versioned benchmark definitions and evaluation artifacts. Schemas
carry only domain fields; evaluation logic lives in evaluator plugins, never
in these models.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EvaluatorCategory(str, Enum):
    deterministic = "deterministic"
    model_assisted = "model_assisted"


class EvaluatorStatus(str, Enum):
    passed = "passed"
    failed = "failed"
    skipped = "skipped"
    error = "error"


class EvaluationCaseStatus(str, Enum):
    passed = "passed"
    failed = "failed"
    error = "error"
    # An evaluator declined to judge (e.g. nothing was produced to evaluate).
    # Distinct from ``passed``: "not evaluated" is not evidence of success.
    skipped = "skipped"


class EvaluationReportStatus(str, Enum):
    passed = "passed"
    failed = "failed"
    error = "error"


class EvaluationMetricKind(str, Enum):
    rate = "rate"
    quantity = "quantity"
    cost = "cost"
    latency = "latency"
    score = "score"


class Benchmark(BaseModel):
    """Immutable benchmark definition referencing immutable cases."""

    id: str = Field(description="Benchmark id (e.g. novelty-threat-v1)")
    version: int = Field(default=1, ge=1)
    name: str
    description: str = ""
    category: str = Field(default="research", description="e.g. novelty_threat")
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Evaluation configuration carried by the benchmark (evaluators, thresholds)",
    )
    case_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class BenchmarkCase(BaseModel):
    """One benchmark case: input + expected reference, no evaluation logic."""

    id: str = Field(description="Case id (unique within a benchmark)")
    benchmark_id: str
    version: int = Field(default=1, ge=1)
    name: str
    description: str = ""
    input: dict[str, Any] = Field(
        default_factory=dict,
        description="Workflow input (manuscript, fixture sources, scripted model responses)",
    )
    reference: dict[str, Any] = Field(
        default_factory=dict,
        description="Expected outputs interpreted by evaluators",
    )
    evaluation_dimensions: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class EvaluatorResult(BaseModel):
    """Structured outcome of one evaluator on one case."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    case_id: str
    evaluator_id: str
    evaluator_version: str = "0.1.0"
    category: EvaluatorCategory
    score: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Normalized 0..1 where applicable"
    )
    value: dict[str, Any] = Field(
        default_factory=dict, description="Detailed structured outputs (counts, verdicts)"
    )
    status: EvaluatorStatus
    explanation: str = ""
    evidence_artifact_ids: list[str] = Field(
        default_factory=list, description="Produced research artifacts used as evidence"
    )
    model_metadata: dict[str, Any] | None = Field(
        default=None, description="Role/model/tokens/latency if a model was used"
    )
    created_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class EvaluationCaseResult(BaseModel):
    """Per-case outcome: status, evaluator results, produced artifacts."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    case_id: str
    case_name: str
    case_version: int = 1
    case_content_hash: str
    status: EvaluationCaseStatus
    evaluator_result_ids: list[str] = Field(default_factory=list)
    produced_artifact_ids: list[str] = Field(default_factory=list)
    error: str | None = Field(default=None)
    metrics: dict[str, float] = Field(
        default_factory=dict, description="Dimension -> normalized score"
    )
    created_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class EvaluationMetric(BaseModel):
    """One aggregated metric across a benchmark run."""

    metric_id: str
    dimension: str
    kind: EvaluationMetricKind
    value: float
    count: int = Field(default=0, description="Denominator / sample size")
    definition: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class EvaluationReport(BaseModel):
    """Aggregated, immutable report for one evaluation run."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    benchmark_id: str
    benchmark_version: int = 1
    status: EvaluationReportStatus
    cases_total: int
    cases_passed: int
    cases_failed: int
    cases_error: int
    # Invariant: cases_passed + cases_failed + cases_error + cases_skipped
    # == cases_total.
    cases_skipped: int = 0
    metrics: list[EvaluationMetric] = Field(default_factory=list)
    case_results: list[EvaluationCaseResult] = Field(default_factory=list)
    false_positive_counts: dict[str, int] = Field(default_factory=dict)
    false_negative_counts: dict[str, int] = Field(default_factory=dict)
    execution_cost_usd: float = Field(default=0.0, ge=0.0)
    execution_latency_ms: int = Field(default=0, ge=0)
    evaluator_versions: dict[str, str] = Field(default_factory=dict)
    model_roles: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class EvaluationRun(BaseModel):
    """Operational record of one benchmark execution (inputs, counts, cost)."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    benchmark_id: str
    benchmark_version: int = 1
    benchmark_content_hash: str
    case_hashes: dict[str, str] = Field(
        default_factory=dict, description="case_id -> artifact content hash"
    )
    evaluation_config: dict[str, Any] = Field(default_factory=dict)
    evaluator_ids: list[str] = Field(default_factory=list)
    evaluator_versions: dict[str, str] = Field(default_factory=dict)
    model_roles: dict[str, str] = Field(default_factory=dict)
    produced_artifact_ids: list[str] = Field(default_factory=list)
    evaluator_result_ids: list[str] = Field(default_factory=list)
    case_result_ids: list[str] = Field(default_factory=list)
    report_id: str
    token_usage: dict[str, int] = Field(
        default_factory=lambda: {"prompt_tokens": 0, "completion_tokens": 0}
    )
    cost_usd: float = Field(default=0.0, ge=0.0)
    latency_ms: int = Field(default=0, ge=0)
    cases_total: int = 0
    cases_passed: int = 0
    cases_failed: int = 0
    cases_error: int = 0
    cases_skipped: int = 0
    failures: list[str] = Field(default_factory=list)
    status: EvaluationReportStatus
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
