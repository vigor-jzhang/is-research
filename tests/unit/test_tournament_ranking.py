"""Unit tests for deterministic leaderboard ranking (Phase 7B): eligibility
gate, correctness-first lexicographic ordering, None handling, and
deterministic tie-breaking."""

from __future__ import annotations

from research_harness.research.schemas.tournament import TournamentModelResult
from research_harness.research.tournament.ranking import RANKING_RULES, build_leaderboard_entries


def _result(candidate_id: str, **kw) -> TournamentModelResult:
    defaults = {
        "candidate_id": candidate_id,
        "config": {"candidate_id": candidate_id, "requested_model": f"m-{candidate_id}"},
        "role": "reasoning",
    }
    defaults.update(kw)
    return TournamentModelResult.model_validate(defaults)


def test_correctness_is_the_primary_gate():
    # cheap/fast model fails deterministic gates -> ranks below a correct one
    correct = _result(
        "correct",
        deterministic_pass_rate=1.0,
        benchmark_pass_rate=1.0,
        latency_ms_p50=500.0,
        estimated_cost=0.01,
    )
    failing = _result(
        "failing",
        deterministic_pass_rate=0.5,
        benchmark_pass_rate=0.5,
        latency_ms_p50=5.0,
        estimated_cost=0.0001,
    )
    entries = build_leaderboard_entries([failing, correct], threshold=0.85)
    assert entries[0].candidate_id == "correct"
    assert entries[1].candidate_id == "failing"
    assert entries[0].eligibility == "eligible"
    assert entries[1].eligibility == "not_eligible"
    assert entries[0].rank == 1
    assert entries[1].rank == 2


def test_latency_breaks_correctness_tie():
    a = _result("a", deterministic_pass_rate=1.0, latency_ms_p50=100.0)
    b = _result("b", deterministic_pass_rate=1.0, latency_ms_p50=10.0)
    entries = build_leaderboard_entries([a, b], threshold=0.85)
    assert entries[0].candidate_id == "b"  # faster wins the tie
    assert entries[1].candidate_id == "a"


def test_cost_only_after_correctness_and_latency():
    a = _result("a", deterministic_pass_rate=1.0, latency_ms_p50=10.0, cost_per_successful_case=0.5)
    b = _result("b", deterministic_pass_rate=1.0, latency_ms_p50=10.0, cost_per_successful_case=0.1)
    entries = build_leaderboard_entries([a, b], threshold=0.85)
    assert entries[0].candidate_id == "b"


def test_none_unknown_sorts_last():
    known = _result("known", deterministic_pass_rate=1.0, latency_ms_p50=10.0)
    unknown = _result("unknown", deterministic_pass_rate=1.0, latency_ms_p50=None)
    entries = build_leaderboard_entries([unknown, known], threshold=0.85)
    assert entries[0].candidate_id == "known"
    assert entries[1].candidate_id == "unknown"


def test_no_deterministic_outcome_ineligible():
    errored = _result("errored", deterministic_pass_rate=None, benchmark_pass_rate=None)
    good = _result("good", deterministic_pass_rate=1.0)
    entries = build_leaderboard_entries([good, errored], threshold=0.85)
    assert entries[0].candidate_id == "good"
    assert entries[1].candidate_id == "errored"
    assert entries[1].eligibility == "not_eligible"


def test_tie_break_by_candidate_id():
    x = _result("x", deterministic_pass_rate=1.0)
    y = _result("y", deterministic_pass_rate=1.0)
    entries = build_leaderboard_entries([y, x], threshold=0.85)
    assert entries[0].candidate_id == "x"
    assert entries[1].candidate_id == "y"


def test_ranking_rules_documented():
    assert "deterministic_pass_rate" in RANKING_RULES["priority"]
    assert "latency_ms_p50" in RANKING_RULES["priority"]
    assert "cost_per_successful_case" in RANKING_RULES["priority"]
    # correctness precedes cost in the hierarchy
    assert RANKING_RULES["priority"].index("deterministic_pass_rate") < RANKING_RULES[
        "priority"
    ].index("cost_per_successful_case")
