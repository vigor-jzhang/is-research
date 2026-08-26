"""Live-quality benchmark calibration audit schemas — Phase 7D.2.

A durable artifact recording a model-independent audit of a live-quality
benchmark: whether each case has a valid reference, achievable schema, no
fixture leakage, no impossible evidence requirement, deterministic evaluator
correctness, realistic context size, valid grounding ids/pages, and no
provider-specific assumptions. Confirmed defects are excluded from
qualification (only genuine model/provider outcomes count).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class CalibrationSeverity(str, Enum):
    info = "info"
    defect = "defect"
    repair = "repair"


class CalibrationCheck(BaseModel):
    """One model-independent audit check result."""

    name: str
    passed: bool
    details: str = ""

    model_config = {"extra": "forbid"}


class CalibrationFinding(BaseModel):
    """A concrete audit finding tied to a benchmark/case."""

    severity: CalibrationSeverity
    message: str
    benchmark_id: str = ""
    case_id: str = ""
    check: str = ""
    attributed_kind: str | None = Field(
        default=None,
        description="FailureAttributionKind when the finding is a confirmed defect "
        "(benchmark_reference_defect | evaluator_defect)",
    )

    model_config = {"extra": "forbid"}


class ConfirmedDefect(BaseModel):
    """A benchmark/evaluator defect confirmed by the audit (excluded from qualification)."""

    benchmark_id: str
    case_id: str
    kind: str
    message: str = ""

    model_config = {"extra": "forbid"}


class BenchmarkCalibrationAudit(BaseModel):
    """Immutable record of a live-quality benchmark calibration audit."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    benchmark_id: str
    benchmark_version: int = 1
    verdict: str = Field(default="ok", description="ok | repair_needed")
    checks: list[CalibrationCheck] = Field(default_factory=list)
    findings: list[CalibrationFinding] = Field(default_factory=list)
    confirmed_defects: list[ConfirmedDefect] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}
