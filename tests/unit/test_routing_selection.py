"""Phase 7C unit tests — routing selection algorithm + policies."""

from __future__ import annotations

import pytest

from research_harness.research.routing.policies import get_policy, list_policies
from research_harness.research.routing.selection import (
    build_assessments,
    filter_eligible,
    select,
)
from research_harness.research.schemas.routing import (
    RoutingCandidateAssessment,
    RoutingRequest,
)
from research_harness.research.schemas.tournament import LeaderboardEntry, RoleLeaderboard


def _entry(candidate_id: str, **kw) -> LeaderboardEntry:
    defaults = {
        "candidate_id": candidate_id,
        "model": {
            "candidate_id": candidate_id,
            "provider": "openrouter",
            "requested_model": f"m-{candidate_id}",
        },
        "resolved_model": f"m-{candidate_id}",
        "rank": 1,
        "eligibility": "eligible",
        "deterministic_pass_rate": 0.95,
        "benchmark_pass_rate": 0.95,
        "case_pass_rate": 0.95,
        "structured_output_success_rate": 1.0,
        "model_error_rate": 0.0,
        "retry_rate": 0.0,
    }
    defaults.update(kw)
    return LeaderboardEntry.model_validate(defaults)


def _board(entries: list[LeaderboardEntry]) -> RoleLeaderboard:
    return RoleLeaderboard(
        role="reasoning", plan_id="p", tournament_run_id="r", plan_hash="h", entries=entries
    )


def _request(**kw) -> RoutingRequest:
    defaults = {"role": "reasoning"}
    defaults.update(kw)
    return RoutingRequest(**defaults)


def test_quality_first_selects_highest_correctness():
    board = _board(
        [
            _entry(
                "good",
                deterministic_pass_rate=0.98,
                latency_ms_p50=120.0,
                cost_per_successful_case=0.02,
            ),
            _entry(
                "cheap",
                deterministic_pass_rate=0.86,
                latency_ms_p50=5.0,
                cost_per_successful_case=0.0002,
            ),
        ]
    )
    assessments = build_assessments(board)
    eligible, rejected = filter_eligible(assessments, _request())
    assert len(eligible) == 2
    primary, fallback = select(eligible, get_policy("quality_first"))
    assert primary.candidate_id == "good"
    assert fallback.candidate_id == "cheap"


def test_cheap_failing_model_rejected():
    board = _board(
        [
            _entry("good", deterministic_pass_rate=0.95),
            _entry("failing", deterministic_pass_rate=0.7),
        ]
    )
    assessments = build_assessments(board)
    eligible, rejected = filter_eligible(
        assessments, _request(required_deterministic_pass_rate=0.9)
    )
    assert [a.candidate_id for a in eligible] == ["good"]
    assert [a.candidate_id for a in rejected] == ["failing"]
    assert "eligibility" in (rejected[0].rejection_reason or "")


def test_cost_constrained_chooses_cheapest_qualified():
    board = _board(
        [
            _entry("premium", deterministic_pass_rate=0.99, cost_per_successful_case=0.12),
            _entry("mid", deterministic_pass_rate=0.9, cost_per_successful_case=0.02),
            _entry("cheap-low", deterministic_pass_rate=0.82, cost_per_successful_case=0.002),
        ]
    )
    assessments = build_assessments(board)
    eligible, rejected = filter_eligible(
        assessments, _request(required_deterministic_pass_rate=0.85)
    )
    primary, _ = select(eligible, get_policy("cost_constrained"))
    assert primary.candidate_id == "mid"
    assert "cheap-low" in {a.candidate_id for a in rejected}


def test_latency_constrained_chooses_fastest_eligible():
    board = _board(
        [
            _entry("fast", deterministic_pass_rate=0.87, latency_ms_p50=12.0),
            _entry("slow", deterministic_pass_rate=0.99, latency_ms_p50=400.0),
        ]
    )
    assessments = build_assessments(board)
    eligible, _ = filter_eligible(assessments, _request(required_deterministic_pass_rate=0.85))
    primary, _ = select(eligible, get_policy("latency_constrained"))
    assert primary.candidate_id == "fast"


def test_structured_output_capability_rejects():
    board = _board(
        [
            _entry(
                "good",
                model={
                    "candidate_id": "good",
                    "provider": "openrouter",
                    "requested_model": "m-good",
                },
            ),
            _entry(
                "legacy",
                model={
                    "candidate_id": "legacy",
                    "provider": "legacy",
                    "requested_model": "m-legacy",
                },
            ),
        ]
    )
    assessments = build_assessments(board, capability_ok={"good": True, "legacy": False})
    eligible, rejected = filter_eligible(assessments, _request(require_structured_output=True))
    assert [a.candidate_id for a in eligible] == ["good"]
    assert [a.candidate_id for a in rejected] == ["legacy"]
    assert "capability" in (rejected[0].rejection_reason or "")


def test_unknown_cost_rejected_under_max_cost_constraint():
    board = _board(
        [
            _entry("priced", estimated_cost=0.01),
            _entry("unknown", estimated_cost=None),
        ]
    )
    assessments = build_assessments(board)
    eligible, rejected = filter_eligible(assessments, _request(max_estimated_cost=0.05))
    assert [a.candidate_id for a in eligible] == ["priced"]
    assert "cost unknown" in (rejected[0].rejection_reason or "")


def test_latency_limit_rejects_unknown_and_slow():
    board = _board(
        [
            _entry("fast", latency_ms_p50=10.0),
            _entry("slow", latency_ms_p50=500.0),
            _entry("unknown-lat", latency_ms_p50=None),
        ]
    )
    assessments = build_assessments(board)
    eligible, rejected = filter_eligible(assessments, _request(latency_limit_ms=100.0))
    assert [a.candidate_id for a in eligible] == ["fast"]
    assert {a.candidate_id for a in rejected} == {"slow", "unknown-lat"}


def test_allowed_models_and_providers():
    board = _board(
        [
            _entry(
                "allowed",
                model={
                    "candidate_id": "allowed",
                    "provider": "openrouter",
                    "requested_model": "m-allowed",
                },
            ),
            _entry(
                "other",
                model={
                    "candidate_id": "other",
                    "provider": "openrouter",
                    "requested_model": "m-other",
                },
            ),
        ]
    )
    assessments = build_assessments(board)
    eligible, rejected = filter_eligible(assessments, _request(allowed_models=["m-allowed"]))
    assert [a.candidate_id for a in eligible] == ["allowed"]
    assert "allowed_models" in (rejected[0].rejection_reason or "")


def test_reliability_gate():
    board = _board(
        [
            _entry("unreliable", model_error_rate=0.9, structured_output_success_rate=0.1),
            _entry("solid", model_error_rate=0.01, structured_output_success_rate=0.99),
        ]
    )
    assessments = build_assessments(board)
    eligible, rejected = filter_eligible(assessments, _request(require_structured_output=True))
    assert [a.candidate_id for a in eligible] == ["solid"]
    assert "reliability" in (rejected[0].rejection_reason or "")


def test_deterministic_tiebreak():
    board = _board(
        [
            _entry(
                "beta",
                deterministic_pass_rate=1.0,
                latency_ms_p50=10.0,
                cost_per_successful_case=0.02,
            ),
            _entry(
                "alpha",
                deterministic_pass_rate=1.0,
                latency_ms_p50=10.0,
                cost_per_successful_case=0.02,
            ),
        ]
    )
    assessments = build_assessments(board)
    eligible, _ = filter_eligible(assessments, _request())
    primary, _ = select(eligible, get_policy("quality_first"))
    assert primary.candidate_id == "alpha"


def test_no_eligible():
    board = _board(
        [_entry("a", deterministic_pass_rate=0.6), _entry("b", deterministic_pass_rate=0.7)]
    )
    assessments = build_assessments(board)
    eligible, rejected = filter_eligible(
        assessments, _request(required_deterministic_pass_rate=0.95)
    )
    assert eligible == []
    assert len(rejected) == 2


def test_insufficient_evidence_no_selection_ok():
    # a leaderboard entry without deterministic evidence is never selected
    board = _board(
        [_entry("no-evidence", deterministic_pass_rate=None, eligibility="not_eligible")]
    )
    assessments = build_assessments(board)
    eligible, rejected = filter_eligible(assessments, _request())
    assert eligible == []
    assert "no deterministic" in (rejected[0].rejection_reason or "")


def test_policies_documented_and_deterministic():
    specs = list_policies()
    ids = {s.policy_id for s in specs}
    assert ids == {"quality_first", "balanced", "cost_constrained", "latency_constrained"}
    for spec in specs:
        assert "gate" in spec.selection_rules
        assert "rank" in spec.selection_rules
        # same inputs -> same ordering
        r1 = [
            a.candidate_id
            for a in sorted(
                [
                    RoutingCandidateAssessment(
                        candidate_id="x", model={}, provider="o", requested_model="mx"
                    ),
                    RoutingCandidateAssessment(
                        candidate_id="y", model={}, provider="o", requested_model="my"
                    ),
                ],
                key=spec.rank_key(),
            )
        ]
        r2 = [
            a.candidate_id
            for a in sorted(
                [
                    RoutingCandidateAssessment(
                        candidate_id="x", model={}, provider="o", requested_model="mx"
                    ),
                    RoutingCandidateAssessment(
                        candidate_id="y", model={}, provider="o", requested_model="my"
                    ),
                ],
                key=spec.rank_key(),
            )
        ]
        assert r1 == r2


def test_unknown_policy_raises():
    with pytest.raises(Exception):
        get_policy("does-not-exist")


def test_explicit_zero_reliability_threshold_is_enforced():
    board = _board([_entry("has-errors", model_error_rate=0.01)])
    eligible, rejected = filter_eligible(
        build_assessments(board), _request(max_model_error_rate=0.0)
    )
    assert eligible == []
    assert rejected[0].candidate_id == "has-errors"
    assert "model_error_rate" in (rejected[0].rejection_reason or "")
