"""Regression tests for routing semantics (round 26).

Batch 6 of the §9 triage: M18, M21, M23, M25, M28, M30, M31.
"""

from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from research_harness.research.schemas.qualification import QualificationCandidateResult
from research_harness.research.schemas.tournament import RoleLeaderboard


def _entry(candidate_id: str = "m", **kw):
    """A leaderboard entry that satisfies the router's gate."""
    return {
        "candidate_id": candidate_id,
        "model": {"provider": "openrouter", "requested_model": candidate_id},
        "rank": 1,
        "eligibility": "eligible",
        "deterministic_pass_rate": 0.95,
        "case_error_rate": 0.0,
        "structured_output_success_rate": 0.95,
        "model_error_rate": 0.0,
        "latency_ms_p50": 100.0,
        "estimated_cost": 0.01,
        **kw,
    }


def _candidate(candidate_id: str, *, qualified: bool = True, stability: str = "stable"):
    return QualificationCandidateResult(
        candidate_id=candidate_id,
        role="reasoning",
        benchmark_id="b",
        qualified=qualified,
        stability=stability,
        deterministic_pass_rate_mean=0.92,
        structured_output_success_rate=0.95,
        provider_error_frequency=0.0,
        latency_ms_p50=100.0,
        estimated_cost=0.01,
    )


# ---------------------------------------------------------------------------
# M18 — eligibility must not be a side effect of build_role_summary
# ---------------------------------------------------------------------------


def test_standalone_matrix_computes_eligibility():
    """M18: only build_role_summary set the flags; the matrix inherited defaults.

    build_qualification_matrix computes primary/fallback itself but never set
    eligibility, so unless the same candidate list had already been through
    build_role_summary every row reported primary_eligible=False — including the
    row for the candidate it had just named primary.
    """
    from research_harness.research.routing.qualification import (
        build_qualification_matrix,
    )
    from research_harness.research.routing.readiness import criteria_for_role

    candidates = [_candidate("m-a"), _candidate("m-b")]
    matrix = build_qualification_matrix(
        candidates,
        role="reasoning",
        benchmark_id="b",
        repetitions=3,
        criteria=criteria_for_role("reasoning"),
    )
    assert matrix.status == "qualified"
    assert candidates[0].primary_eligible, "the selected primary was not eligible"

    by_id = {row.candidate: row for row in matrix.rows}
    assert by_id["m-a"].primary_eligible is True, "row inherited the default False"
    # The primary is never its own fallback.
    assert by_id["m-a"].fallback_eligible is False
    assert by_id["m-b"].fallback_eligible is True


def test_unstable_candidate_is_not_primary_eligible():
    from research_harness.research.routing.qualification import (
        build_qualification_matrix,
    )
    from research_harness.research.routing.readiness import criteria_for_role

    candidates = [_candidate("m-a", stability="unstable")]
    matrix = build_qualification_matrix(
        candidates,
        role="reasoning",
        benchmark_id="b",
        repetitions=3,
        criteria=criteria_for_role("reasoning"),
    )
    assert matrix.rows[0].primary_eligible is False, "an unstable candidate was eligible"
    assert matrix.rows[0].stability == "unstable"


# ---------------------------------------------------------------------------
# M21 — "fallback model selected" must name a model
# ---------------------------------------------------------------------------


def test_single_eligible_candidate_is_not_reported_as_a_fallback():
    """M21: status was `fallback` while select() returned (None, None)."""
    from research_harness.research.routing.selection import decide_status
    from research_harness.research.schemas.routing import RoutingDecisionStatus

    board = RoleLeaderboard(
        role="reasoning", plan_id="p", tournament_run_id="r", plan_hash="h", entries=[_entry()]
    )
    one = [object()]  # one eligible candidate
    status, reason = decide_status(
        board, one, False, False, use_fallback=True
    )
    assert status == RoutingDecisionStatus.no_eligible_model
    assert "no fallback" in reason
    assert "selected" not in reason


def test_two_eligible_candidates_still_report_fallback():
    from research_harness.research.routing.selection import decide_status
    from research_harness.research.schemas.routing import RoutingDecisionStatus

    board = RoleLeaderboard(
        role="reasoning", plan_id="p", tournament_run_id="r", plan_hash="h", entries=[_entry()]
    )
    status, reason = decide_status(board, [object(), object()], False, False, use_fallback=True)
    assert status == RoutingDecisionStatus.fallback
    assert "fallback model selected" in reason


# ---------------------------------------------------------------------------
# M23 — the unsupported-claim bucket never fired
# ---------------------------------------------------------------------------


def test_claim_is_unsupported_when_most_terms_are_absent():
    """M23: `not any(...)` required EVERY term to be absent."""
    from research_harness.research.routing.qualification import (
        evidence_extraction_diagnostics,
    )

    # "Prices" appears in the source, so `not any(...)` said "supported"; four of
    # the five substantive terms are absent, so MOST of the claim is unsupported.
    source = "Prices fall with entry in the platform market."
    diag = evidence_extraction_diagnostics(
        [],
        produced_evidence=[{"statement": "Prices collapse dramatically in generic markets."}],
        source_text=source,
    )
    assert diag["unsupported_claims"] == 1, (
        "a claim sharing one word with the source was treated as supported"
    )


def test_supported_claim_is_not_flagged():
    from research_harness.research.routing.qualification import (
        evidence_extraction_diagnostics,
    )

    source = "Prices fall with entry in the platform market and margins shrink."
    diag = evidence_extraction_diagnostics(
        [],
        produced_evidence=[{"statement": "Prices fall with entry in the platform market."}],
        source_text=source,
    )
    assert diag["unsupported_claims"] == 0


# ---------------------------------------------------------------------------
# M25 — task coverage and variance
# ---------------------------------------------------------------------------


def _task_perf(repetitions: int, rates: list[float]):
    from research_harness.research.schemas.live_quality import LiveQualityTaskPerformance

    return LiveQualityTaskPerformance(
        task_id="evidence_extraction",
        repetitions=repetitions,
        pass_rates=rates,
        pass_rate_mean=sum(rates) / len(rates),
    )


def test_task_repetitions_use_the_weakest_case():
    """M25: max() reported the best-covered case as the task's coverage."""
    from research_harness.research.routing.qualification import (
        aggregate_task_performance,
    )
    from research_harness.research.schemas.live_quality import LiveQualityModelResult

    result = LiveQualityModelResult(
        candidate_id="m",
        model={},
        resolved_model="m",
        role="reasoning",
        benchmark_id="b",
        repetitions=5,
        task_performance=[
            _task_perf(5, [1.0, 1.0, 1.0, 1.0, 1.0]),
            _task_perf(1, [0.8]),
        ],
    )
    tp = aggregate_task_performance(result, "evidence_extraction")
    assert tp is not None
    assert tp.repetitions == 1, f"reported {tp.repetitions}, hiding the 1-repetition case"


def test_single_sample_variance_is_unknown_not_perfectly_stable():
    """M25: pvariance over one sample reported 0.0, i.e. stable."""
    from research_harness.research.routing.qualification import (
        aggregate_task_performance,
    )
    from research_harness.research.schemas.live_quality import LiveQualityModelResult

    result = LiveQualityModelResult(
        candidate_id="m",
        model={},
        resolved_model="m",
        role="reasoning",
        benchmark_id="b",
        repetitions=1,
        task_performance=[_task_perf(1, [0.9])],
    )
    tp = aggregate_task_performance(result, "evidence_extraction")
    assert tp is not None
    assert tp.pass_rate_variance is None, "one sample cannot demonstrate stability"


def test_multiple_samples_still_report_variance():
    from research_harness.research.routing.qualification import (
        aggregate_task_performance,
    )
    from research_harness.research.schemas.live_quality import LiveQualityModelResult

    result = LiveQualityModelResult(
        candidate_id="m",
        model={},
        resolved_model="m",
        role="reasoning",
        benchmark_id="b",
        repetitions=3,
        task_performance=[_task_perf(3, [1.0, 0.8, 0.9])],
    )
    tp = aggregate_task_performance(result, "evidence_extraction")
    assert tp is not None
    assert tp.pass_rate_variance is not None and tp.pass_rate_variance > 0


# ---------------------------------------------------------------------------
# M28 — the router gate must not be looser than qualification
# ---------------------------------------------------------------------------


def _assessment(candidate_id: str, *, det: float = 0.95, structured: float | None = 0.6):
    from research_harness.research.schemas.routing import RoutingCandidateAssessment

    return RoutingCandidateAssessment(
        candidate_id=candidate_id,
        model={"provider": "openrouter"},
        provider="openrouter",
        requested_model=candidate_id,
        eligibility="eligible",
        deterministic_pass_rate=det,
        case_error_rate=0.0,
        structured_output_success_rate=structured,
        model_error_rate=0.0,
        capability_ok=True,
    )


def test_router_rejects_a_structured_rate_qualification_would_reject():
    """M28: the router defaulted to 0.50 while the criteria require 0.85.

    A structured-output rate of 0.60 passed the router and failed qualification,
    so routing could select a model the qualification pass rejects.
    """
    from research_harness.research.routing.selection import filter_eligible
    from research_harness.research.schemas.routing import RoutingRequest

    request = RoutingRequest(role="reasoning")
    eligible, rejected = filter_eligible([_assessment("m", structured=0.6)], request)
    assert not eligible, "the router accepted a rate below the qualification minimum"
    assert any("structured_output_success_rate" in (a.rejection_reason or "") for a in rejected)


def test_router_still_accepts_a_rate_above_the_minimum():
    from research_harness.research.routing.selection import filter_eligible
    from research_harness.research.schemas.routing import RoutingRequest

    eligible, _ = filter_eligible(
        [_assessment("m", structured=0.9)], RoutingRequest(role="reasoning")
    )
    assert len(eligible) == 1


def test_explicit_request_threshold_still_wins():
    from research_harness.research.routing.selection import filter_eligible
    from research_harness.research.schemas.routing import RoutingRequest

    eligible, _ = filter_eligible(
        [_assessment("m", structured=0.6)],
        RoutingRequest(role="reasoning", min_structured_output_success_rate=0.5),
    )
    assert len(eligible) == 1, "an explicit request must override the role default"


# ---------------------------------------------------------------------------
# M30 — permissive router defaults
# ---------------------------------------------------------------------------


def test_min_repetitions_default_matches_the_criteria():
    """M30: defaulted to 1 while the criteria require 3."""
    from research_harness.research.schemas.routing import RoutingRequest

    assert RoutingRequest(role="reasoning").min_repetitions == 3


@pytest.mark.asyncio
async def test_fixture_evidence_is_not_treated_as_production_evidence(tmp_path: pathlib.Path):
    """M30: unset evidence_types accepted fixture_evidence.

    RoleLeaderboard.evidence_type documents that "Production routing requires
    live_quality_evidence", so deciding on an offline fixture tournament was
    never the intended behaviour.
    """
    from research_harness.plugins.routing.policy_router.plugin import (
        PolicyModelRouterService,
    )
    from research_harness.plugins.storage.artifacts_sqlite.plugin import (
        SQLiteArtifactStore,
    )
    from research_harness.research.envelope import ArtifactEnvelope
    from research_harness.research.schemas.routing import RoutingDecisionStatus

    store = SQLiteArtifactStore(path=tmp_path / "a.db")

    def _board(evidence_type: str) -> RoleLeaderboard:
        return RoleLeaderboard(
            role="reasoning",
            plan_id="p",
            tournament_run_id="r",
            plan_hash="h",
            evidence_type=evidence_type,
            entries=[_entry("m")],
            metadata={"repetitions": 3},
        )

    await store.put(
        ArtifactEnvelope.create(
            payload=_board("fixture_evidence"),
            artifact_type="role_leaderboard",
            producer="test",
        )
    )

    svc = PolicyModelRouterService(
        artifact_store=store,
        service_lookup=lambda name: _CapProvider(),
        current_roles={"reasoning": {"provider": "openrouter", "model": "m"}},
    )
    decision = await svc.decide("reasoning", "quality_first")
    # Old behaviour: the fixture board was accepted and a candidate selected from
    # an offline tournament. Production routing requires live_quality_evidence.
    assert decision.status == RoutingDecisionStatus.insufficient_evidence, (
        f"routing decided on fixture evidence: {decision.status.value}"
    )
    assert decision.selected_candidate_id is None
    await store.close()


class _CapProvider:
    capabilities = type(
        "Caps", (), {"structured_output": True, "context_length": 8192}
    )()


# ---------------------------------------------------------------------------
# M31 — naive datetimes and negative ages
# ---------------------------------------------------------------------------


def test_naive_timestamp_does_not_raise():
    """M31: subtracting a naive payload datetime raised TypeError."""
    from research_harness.research.timeutil import age_seconds

    naive = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=10)
    got = age_seconds(naive)
    assert got is not None and 9 <= got <= 11


def test_future_timestamp_yields_zero_not_negative_age():
    """M31: a negative age passed every freshness gate."""
    from research_harness.research.timeutil import age_seconds

    future = datetime.now(UTC) + timedelta(hours=1)
    assert age_seconds(future) == 0.0


def test_unreadable_timestamp_is_none():
    from research_harness.research.timeutil import age_seconds, aware_utc

    assert age_seconds(None) is None
    assert age_seconds("not a date") is None
    assert aware_utc(None) is None


def test_naive_datetime_becomes_aware_utc():
    from research_harness.research.timeutil import aware_utc

    naive = datetime(2026, 1, 1, 12, 0, 0)
    got = aware_utc(naive)
    assert got is not None and got.tzinfo is not None
    assert got == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
