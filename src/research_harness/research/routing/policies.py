"""Routing policies (Phase 7C) — explicit, documented policies, never opaque
scores. Every policy first applies the mandatory gate (capability, deterministic
eligibility, reliability, explicit constraints) and only then ranks by its
documented dimension priority. Cost/latency are never traded for correctness
unless the request lowers the required quality threshold.

Each policy exposes `selection_rules` (persisted with every decision) and a
deterministic rank-key builder over eligible `RoutingCandidateAssessment`s.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from research_harness.research.schemas.routing import RoutingCandidateAssessment

# (field, ascending): ascending=True means lower is better.
_QUALITY_FIELDS: list[tuple[str, bool]] = [
    ("deterministic_pass_rate", False),
    ("benchmark_pass_rate", False),
]
_RELIABILITY_FIELDS: list[tuple[str, bool]] = [
    ("model_error_rate", True),
    ("structured_output_success_rate", False),
    ("retry_rate", True),
]
_LATENCY_FIELDS: list[tuple[str, bool]] = [("latency_ms_p50", True)]
_COST_FIELDS: list[tuple[str, bool]] = [("cost_per_successful_case", True)]


def build_rank_key(
    fields: list[tuple[str, bool]],
) -> Callable[[RoutingCandidateAssessment], tuple[Any, ...]]:
    def _key(assessment: RoutingCandidateAssessment) -> tuple[Any, ...]:
        out: list[Any] = []
        for field, ascending in fields:
            value = getattr(assessment, field, None)
            if value is None:
                # Unknown evidence sorts last in BOTH directions. For a
                # "higher is better" field the value is negated below, so
                # -inf here would sort unknown evidence ahead of every real
                # value and let a candidate with no benchmark runs win.
                out.append(float("inf"))
            else:
                out.append(float(value) if ascending else -float(value))
        out.append(assessment.candidate_id)  # deterministic tie-break
        return tuple(out)

    return _key


@dataclass(frozen=True)
class PolicySpec:
    policy_id: str
    name: str
    description: str
    fields: list[tuple[str, bool]]
    selection_rules: dict[str, Any]

    def rank_key(self) -> Callable[[RoutingCandidateAssessment], tuple[Any, ...]]:
        return build_rank_key(self.fields)


_POLICIES: dict[str, PolicySpec] = {
    "quality_first": PolicySpec(
        policy_id="quality_first",
        name="Quality First",
        description=(
            "Gate first, then choose the highest deterministic quality; latency "
            "and cost break quality ties only. Correctness is never traded for cost."
        ),
        fields=[*_QUALITY_FIELDS, *_LATENCY_FIELDS, *_COST_FIELDS],
        selection_rules={
            "gate": "capability -> deterministic eligibility -> reliability -> constraints",
            "rank": "deterministic_pass_rate desc, benchmark_pass_rate desc, "
            "latency_ms_p50 asc, cost_per_successful_case asc, candidate_id asc",
        },
    ),
    "balanced": PolicySpec(
        policy_id="balanced",
        name="Balanced",
        description=(
            "Gate first, then a documented lexicographic blend: quality, "
            "reliability, latency, cost — quality strictly dominates the rest."
        ),
        fields=[*_QUALITY_FIELDS, *_RELIABILITY_FIELDS, *_LATENCY_FIELDS, *_COST_FIELDS],
        selection_rules={
            "gate": "capability -> deterministic eligibility -> reliability -> constraints",
            "rank": "deterministic_pass_rate desc, benchmark_pass_rate desc, "
            "model_error_rate asc, structured_output_success_rate desc, retry_rate asc, "
            "latency_ms_p50 asc, cost_per_successful_case asc, candidate_id asc",
        },
    ),
    "cost_constrained": PolicySpec(
        policy_id="cost_constrained",
        name="Cost Constrained",
        description=(
            "Gate by minimum deterministic quality + mandatory reliability first, "
            "then choose the lowest expected cost among eligible candidates. "
            "Cheaper-but-ineligible models are never chosen."
        ),
        fields=[*_COST_FIELDS, *_QUALITY_FIELDS, *_LATENCY_FIELDS],
        selection_rules={
            "gate": "capability -> deterministic eligibility (min quality) -> "
            "reliability (mandatory) -> constraints",
            "rank": "cost_per_successful_case asc, then deterministic_pass_rate desc, "
            "then latency_ms_p50 asc, candidate_id asc",
        },
    ),
    "latency_constrained": PolicySpec(
        policy_id="latency_constrained",
        name="Latency Constrained",
        description=(
            "Gate by minimum deterministic quality + mandatory reliability first, "
            "then choose the fastest eligible model; quality breaks latency ties."
        ),
        fields=[*_LATENCY_FIELDS, *_QUALITY_FIELDS, *_COST_FIELDS],
        selection_rules={
            "gate": "capability -> deterministic eligibility (min quality) -> "
            "reliability (mandatory) -> constraints",
            "rank": "latency_ms_p50 asc, then deterministic_pass_rate desc, "
            "then cost_per_successful_case asc, candidate_id asc",
        },
    ),
}

POLICY_IDS: tuple[str, ...] = tuple(sorted(_POLICIES))


def get_policy(policy_id: str) -> PolicySpec:
    spec = _POLICIES.get(policy_id)
    if spec is None:
        from research_harness.kernel.errors import ConfigurationError

        raise ConfigurationError(f"unknown routing policy {policy_id!r}; available: {POLICY_IDS}")
    return spec


def list_policies() -> tuple[PolicySpec, ...]:
    return tuple(_POLICIES[pid] for pid in POLICY_IDS)


def default_policy_id() -> str:
    return "quality_first"


def policy_rules_snapshot() -> dict[str, Any]:
    return {pid: dict(spec.selection_rules) for pid, spec in _POLICIES.items()}
