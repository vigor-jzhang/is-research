"""Deterministic, documented ranking for role leaderboards (Phase 7B).

Ranking is lexicographic (a Pareto-style hierarchy), never an opaque blended
score. Correctness gates first; cost/latency never outrank correctness.

Hierarchy (documented, reproduced in every leaderboard's ranking_rules):
1. eligibility          — deterministic_pass_rate >= plan threshold
2. deterministic quality — deterministic_pass_rate desc
3. benchmark quality    — benchmark_pass_rate desc
4. reliability          — model_error_rate asc, then structured_output_success_rate desc,
                           then retry_rate asc
5. latency              — latency_ms_p50 asc
6. cost/token efficiency — cost_per_successful_case asc, then total_tokens asc
7. advisory quality     — advisory_score desc (only when deterministic ties remain)
Tie-break: candidate_id asc (fully deterministic).

None means "unknown / worst" and sorts last for every dimension.
"""

from __future__ import annotations

from typing import Any

from research_harness.research.schemas.tournament import (
    LeaderboardEntry,
    TournamentModelResult,
)

# The ordering hierarchy as data, so it is persisted with every leaderboard.
RANKING_RULES: dict[str, Any] = {
    "priority": [
        "eligibility",
        "deterministic_pass_rate",
        "benchmark_pass_rate",
        "model_error_rate",
        "structured_output_success_rate",
        "retry_rate",
        "latency_ms_p50",
        "cost_per_successful_case",
        "total_tokens",
        "advisory_score",
    ],
    "notes": (
        "lexicographic: earlier keys strictly dominate; lower is better for "
        "model_error_rate, retry_rate, latency_ms_p50, cost_per_successful_case, "
        "total_tokens; higher is better for the rest; None sorts last; "
        "deterministic tie-break by candidate_id"
    ),
}


def _key(entry: LeaderboardEntry, field: str, desc: bool) -> tuple[float, int]:
    value = getattr(entry, field, None)
    if value is None:
        # None (unknown) always sorts last regardless of direction
        return (float("-inf") if desc else float("inf"), 1)
    return (float(value), 0)


def build_entries(results: list[TournamentModelResult], threshold: float) -> list[LeaderboardEntry]:
    entries: list[LeaderboardEntry] = []
    for r in results:
        eligible = r.deterministic_pass_rate is not None and r.deterministic_pass_rate >= threshold
        reason = ""
        if r.deterministic_pass_rate is None:
            reason = "no deterministic outcome (all cases errored or no runs)"
        elif not eligible:
            reason = (
                f"deterministic_pass_rate {r.deterministic_pass_rate:.3f} < threshold {threshold}"
            )
        else:
            reason = (
                f"deterministic_pass_rate {r.deterministic_pass_rate:.3f} >= threshold {threshold}"
            )
        caveats: list[str] = []
        if r.estimated_cost is None:
            caveats.append("cost unknown (no provider usage cost and no configured pricing)")
        entries.append(
            LeaderboardEntry(
                candidate_id=r.candidate_id,
                model=r.config.model_dump(mode="json"),
                resolved_model=r.resolved_model,
                rank=0,
                eligibility="eligible" if eligible else "not_eligible",
                eligibility_reason=reason,
                deterministic_pass_rate=r.deterministic_pass_rate,
                benchmark_pass_rate=r.benchmark_pass_rate,
                case_pass_rate=r.case_pass_rate,
                structured_output_success_rate=r.structured_output_success_rate,
                model_error_rate=r.model_error_rate,
                retry_rate=r.retry_rate,
                latency_ms_mean=r.latency_ms_mean,
                latency_ms_p50=r.latency_ms_p50,
                latency_ms_p95=r.latency_ms_p95,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                total_tokens=r.total_tokens,
                estimated_cost=r.estimated_cost,
                cost_per_successful_case=r.cost_per_successful_case,
                cost_per_successful_benchmark=r.cost_per_successful_benchmark,
                advisory_score=r.advisory_score,
                caveats=caveats,
            )
        )
    return entries


def _cmp_fields() -> list[tuple[str, bool]]:
    return [
        ("deterministic_pass_rate", True),
        ("benchmark_pass_rate", True),
        ("model_error_rate", False),
        ("structured_output_success_rate", True),
        ("retry_rate", False),
        ("latency_ms_p50", False),
        ("cost_per_successful_case", False),
        ("total_tokens", False),
        ("advisory_score", True),
    ]


def rank_entries(entries: list[LeaderboardEntry]) -> list[LeaderboardEntry]:
    """Sort deterministically: eligible first, then the documented hierarchy."""

    def sort_key(entry: LeaderboardEntry) -> tuple[int, tuple[tuple[float | int, int], ...]]:
        eligible = 0 if entry.eligibility == "eligible" else 1
        keys = [_key(entry, field, desc) for field, desc in _cmp_fields()]
        # deterministic tie-break on candidate_id
        tie = (int.from_bytes(entry.candidate_id.encode("utf-8"), "big"), 0)
        return (eligible, tuple(keys + [tie]))

    ranked = sorted(entries, key=sort_key)
    for i, entry in enumerate(ranked, start=1):
        entry.rank = i
    return ranked


def build_leaderboard_entries(
    results: list[TournamentModelResult], threshold: float
) -> list[LeaderboardEntry]:
    return rank_entries(build_entries(results, threshold))
