"""Regression tests for routing / qualification numeric correctness (round 19).

Batch 1 of the §9 triage: eight findings where a number is computed from the
wrong inputs, so the result looks plausible and is read as fact. M17, M19,
M20, M22, M24, M26, M27, M29.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from research_harness.contracts.common import Usage
from research_harness.contracts.model import Message, ModelResponse

# ---------------------------------------------------------------------------
# shared fixtures
# ---------------------------------------------------------------------------


def _result(
    candidate_id: str,
    *,
    det: float = 0.9,
    worst: float | None = None,
    variance: float = 0.0,
    structured: float = 0.9,
    provider_error: float = 0.0,
    grounding: int = 0,
    repetitions: int = 3,
    role: str = "reasoning",
    excluded_attribution: dict[str, int] | None = None,
    task_performance: list | None = None,
) -> object:
    from research_harness.research.schemas.live_quality import LiveQualityModelResult

    return LiveQualityModelResult(
        candidate_id=candidate_id,
        model={"candidate_id": candidate_id, "requested_model": f"m-{candidate_id}"},
        resolved_model=f"m-{candidate_id}",
        role=role,
        benchmark_id=f"live-quality-{role}-v1",
        repetitions=repetitions,
        deterministic_pass_rate_mean=det,
        deterministic_pass_rate_worst=worst if worst is not None else det,
        deterministic_pass_rate_variance=variance,
        structured_output_success_rate=structured,
        provider_error_frequency=provider_error,
        critical_grounding_failures=grounding,
        excluded_failure_attribution=excluded_attribution or {},
        failure_attribution={},
        task_performance=list(task_performance or []),
        task_results=[
            {
                "repetition": i,
                "run_id": f"r{i}",
                "report_id": f"p{i}",
                "report_status": "passed",
                "cases_total": 1,
                "cases_passed": 1,
                "cases_failed": 0,
                "cases_error": 0,
                "task_pass_rate": 1.0,
                "task_completed": True,
            }
            for i in range(repetitions)
        ],
    )


class _StructuredProbeProvider:
    """Reachable, but the schema-carrying probe fails in the requested way.

    The reachability probe sends no `response_schema`, so this passes the first
    probe and exercises the structured-output branch specifically.
    """

    def __init__(self, *, error: str | None = None, content: str = "ok") -> None:
        self.error = error
        self.content = content
        self.model = "resolved-model"

    async def complete(self, request):  # noqa: ANN201
        if request.response_schema is None:
            return self._ok("ok")
        if self.error is not None:
            raise RuntimeError(self.error)
        return self._ok(self.content)

    def _ok(self, content: str) -> ModelResponse:
        return ModelResponse(
            message=Message(role="assistant", content=content),
            model=self.model,
            provider="openrouter",
            usage=Usage(prompt_tokens=1, completion_tokens=1),
            latency_ms=1.0,
        )


def _probe_lookup(provider) -> object:
    def lookup(name: str):
        if name == "model_provider.openrouter":
            return provider
        raise LookupError(name)

    return lookup


async def _preflight(provider, **kw) -> object:
    from research_harness.research.routing.preflight import run_candidate_preflight
    from research_harness.research.schemas.tournament import TournamentModelConfig

    return await run_candidate_preflight(
        role="reasoning",
        candidate=TournamentModelConfig(
            candidate_id="fake/model", provider="openrouter", requested_model="fake/model"
        ),
        service_lookup=_probe_lookup(provider),
        base_router=None,
        timeout_seconds=5.0,
        retries=0,
        **kw,
    )


# ---------------------------------------------------------------------------
# M17 — stability must apply the same confirmed-defect exclusion as the gate
# ---------------------------------------------------------------------------


def test_stability_applies_the_confirmed_defect_exclusion():
    """M17: a model exonerated by the ledger must not be called unstable.

    `qualify_model` subtracts failures attributable to confirmed benchmark or
    evaluator defects; `stability_status` did not, so a model whose every
    grounding failure was a known defect came out `qualified` and `unstable`
    from the same evidence.
    """
    from research_harness.research.routing.qualification import stability_status
    from research_harness.research.routing.readiness import criteria_for_role, qualify_model
    from research_harness.research.schemas.live_quality import FailureAttributionKind

    criteria = criteria_for_role("reasoning")
    genuine = _result("genuine", det=0.95, worst=0.95, grounding=2)
    exonerated = _result(
        "exonerated",
        det=0.95,
        worst=0.95,
        grounding=2,
        excluded_attribution={FailureAttributionKind.benchmark_reference_defect.value: 2},
    )

    assert stability_status(genuine, criteria) == "unstable"
    ok, reasons = qualify_model(exonerated, criteria)
    assert ok, f"the gate exonerates this model, but reported {reasons}"
    assert stability_status(exonerated, criteria) == "stable", (
        "stability ignores the confirmed-defect exclusion: the same model is "
        "qualified by one verdict and unstable by the other"
    )


# ---------------------------------------------------------------------------
# M19 — a transient failure is not a missing capability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_structured_probe_rate_limit_is_not_a_capability_mismatch():
    """M19: a rate limit must be recorded as temporarily_unavailable."""
    from research_harness.research.schemas.qualification import PreflightStatus

    result = await _preflight(_StructuredProbeProvider(error="rate limit 429"))
    assert result.status == PreflightStatus.temporarily_unavailable, (
        "a structured-output probe that hit a rate limit was recorded as a "
        "capability mismatch, blaming the model for a provider outage"
    )


@pytest.mark.asyncio
async def test_structured_probe_hard_error_is_a_provider_error():
    from research_harness.research.schemas.qualification import PreflightStatus

    result = await _preflight(_StructuredProbeProvider(error="provider 500 gateway failure"))
    assert result.status == PreflightStatus.provider_error


@pytest.mark.asyncio
async def test_unparseable_structured_output_is_a_capability_mismatch():
    """An unparseable response with no error is genuinely a capability gap."""
    from research_harness.research.schemas.qualification import PreflightStatus

    result = await _preflight(_StructuredProbeProvider(content="not json at all"))
    assert result.status == PreflightStatus.capability_mismatch


# ---------------------------------------------------------------------------
# M20 — context requirement is one case, not all of them
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_sizing_is_the_largest_case_not_the_sum():
    """M20: the probe holds one case at a time, so the requirement is the max."""
    from research_harness.research.routing.preflight import preflight_required_context_chars

    cases = [
        {"input": {"documents": [{"text": "a" * 100}]}},
        {"input": {"documents": [{"text": "b" * 250}]}},
    ]
    got = await preflight_required_context_chars(role="reasoning", benchmark_cases=cases)
    assert got == 250, f"expected the largest case (250), got {got} — summing overstates it"


# ---------------------------------------------------------------------------
# M22 — preflight status is per (candidate, role)
# ---------------------------------------------------------------------------


def test_preflight_status_is_not_shared_across_roles():
    """M22: the newest preflight from any role was applied to all three.

    A preflight is a role-specific probe. Keying on the candidate alone let a
    critic probe report reasoning availability.
    """
    from research_harness.research.routing.qualification import build_remaining_task_coverage
    from research_harness.research.schemas.qualification import (
        ModelPreflight,
        PreflightStatus,
        TaskQualificationMatrix,
        TaskQualificationResult,
    )

    now = datetime.now(UTC)
    row = TaskQualificationResult(
        role="fast",
        task="screening",
        candidate_id="m-x",
        model={},
        benchmark_id="live-quality-fast-v1",
        repetitions=3,
        deterministic_pass_rate_mean=0.4,
        deterministic_pass_rate_worst=0.4,
        deterministic_pass_rate_variance=0.0,
        structured_output_success_rate=0.9,
        provider_error_frequency=0.0,
        critical_grounding_failures=0,
        qualified=False,
        rejection_reasons=["task deterministic_pass_rate 0.4 < 0.9"],
    )
    matrix = TaskQualificationMatrix(
        role="fast",
        benchmark_id="live-quality-fast-v1",
        tasks=["screening"],
        rows=[row],
        qualified_models_by_task={"screening": []},
        ranked_models_by_task={"screening": []},
        qualified_tasks_by_model={"m-x": []},
        role_qualified_models=[],
    )
    # This candidate is available for `fast` (older) but unavailable for
    # `critic` (newer). The newest-wins key used to pick the critic probe.
    fast_pf = ModelPreflight(
        role="fast",
        candidate_id="m-x",
        provider="openrouter",
        requested_model="m-x",
        status=PreflightStatus.available,
        created_at=now - timedelta(hours=1),
    )
    critic_pf = ModelPreflight(
        role="critic",
        candidate_id="m-x",
        provider="openrouter",
        requested_model="m-x",
        status=PreflightStatus.temporarily_unavailable,
        created_at=now,
    )

    class _Campaign:
        role = "fast"
        candidates = [type("C", (), {"candidate_id": "m-x"})()]

    coverage = build_remaining_task_coverage(
        [matrix], preflights=[fast_pf, critic_pf], campaigns=[_Campaign()]
    )
    assert any(r.tested_model_count == 1 for r in coverage.rows), "the fast row is missing"
    assert all(r.provider_unavailable_count == 0 for r in coverage.rows), (
        "the critic probe was applied to the fast role"
    )


# ---------------------------------------------------------------------------
# M24 — a truncated token total must not look complete
# ---------------------------------------------------------------------------


def _call(**kw):
    from research_harness.research.schemas.tournament import ModelCallRecord

    defaults = {
        "role": "reasoning",
        "model": "m",
        "requested_model": "m",
        "status": "success",
        "latency_ms": 50.0,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": None,
    }
    defaults.update(kw)
    return ModelCallRecord(**defaults)


def test_partial_token_reporting_is_unknown_not_truncated():
    """M24: one call reporting 1000 next to one reporting None is not 1000."""
    from research_harness.research.tournament.accounting import aggregate_calls

    partial = aggregate_calls(
        [_call(prompt_tokens=1000, completion_tokens=100), _call(prompt_tokens=None, completion_tokens=None)]
    )
    assert partial["input_tokens"] is None, (
        "a sum over the calls that happened to report usage looks like a "
        "complete total but silently omits the rest"
    )
    assert partial["total_tokens"] is None

    complete = aggregate_calls(
        [_call(prompt_tokens=100, completion_tokens=50), _call(prompt_tokens=200, completion_tokens=100)]
    )
    assert complete["input_tokens"] == 300
    assert complete["total_tokens"] == 450


# ---------------------------------------------------------------------------
# M26 — task rows keep the run they came from
# ---------------------------------------------------------------------------


def test_task_rows_keep_their_live_quality_run_id():
    """M26: `live_quality_run_id=None` was hardcoded, so provenance was lost.

    `build_task_aware_decision` collects `qualification_result_ids` from these
    rows; with every row carrying None it was always empty.
    """
    from research_harness.research.routing.qualification import build_task_matrix
    from research_harness.research.routing.tasks import tasks_for_role
    from research_harness.research.schemas.live_quality import LiveQualityTaskPerformance

    task_id = tasks_for_role("reasoning")[0]
    tp = LiveQualityTaskPerformance(
        task_id=task_id,
        task_name=task_id,
        repetitions=3,
        pass_rate_mean=0.95,
        pass_rate_worst=0.95,
        pass_rate_variance=0.0,
        pass_rates=[0.95, 0.95, 0.95],
        structured_output_success_rate=0.95,
        provider_error_frequency=0.0,
        critical_grounding_failures=0,
    )
    result = _result("m-a", role="reasoning", det=0.95, task_performance=[tp])
    _matrix, rows = build_task_matrix(
        {"m-a": result},
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        repetitions=3,
        live_quality_run_ids={"m-a": "run-42"},
    )
    assert rows, "expected at least one task row"
    assert [r.live_quality_run_id for r in rows] == ["run-42"] * len(rows)


# ---------------------------------------------------------------------------
# M27 — an unmeasured baseline is not a baseline of zero
# ---------------------------------------------------------------------------


def _trow(*, candidate_id: str, task: str, det, latency=None, cost=None) -> object:
    from research_harness.research.schemas.qualification import TaskQualificationResult

    return TaskQualificationResult(
        role="reasoning",
        task=task,
        candidate_id=candidate_id,
        model={"provider": "openrouter", "requested_model": candidate_id},
        resolved_model=candidate_id,
        benchmark_id="live-quality-reasoning-v1",
        repetitions=3,
        deterministic_pass_rate_mean=det,
        deterministic_pass_rate_worst=det,
        deterministic_pass_rate_variance=0.0,
        structured_output_success_rate=0.9,
        provider_error_frequency=0.0,
        critical_grounding_failures=0,
        qualified=True,
        rejection_reasons=[],
        latency_ms_p50=latency,
        estimated_cost=cost,
    )


def _decide(*, task: str, rows: list) -> object:
    from research_harness.research.routing.task_aware import build_task_aware_decision
    from research_harness.research.schemas.qualification import TaskQualificationMatrix

    tasks = sorted({r.task for r in rows})
    matrix = TaskQualificationMatrix(
        id="matrix-1",
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        tasks=tasks,
        rows=rows,
        qualified_models_by_task={t: [r.candidate_id for r in rows if r.task == t] for t in tasks},
        ranked_models_by_task={},
        qualified_tasks_by_model={},
        role_qualified_models=[],
        repetitions=3,
    )
    return build_task_aware_decision(
        role="reasoning",
        task=task,
        matrix=matrix,
        static_model="static/model",
        static_provider="openrouter",
    )


def test_unmeasured_static_baseline_is_not_reported_as_improvement():
    """M27: subtracting `or 0.0` invented a +0.95 gain over a model never measured."""
    rows = [
        _trow(
            candidate_id="m-a",
            task="evidence_extraction",
            det=0.95,
            latency=100.0,
            cost=1.0,
        ),
        # The static model was never measured on any of these dimensions.
        _trow(candidate_id="static/model", task="evidence_extraction", det=None),
    ]
    d = _decide(task="evidence_extraction", rows=rows)
    assert d.expected_quality_delta is None, (
        "an unknown baseline was treated as zero, fabricating an improvement"
    )
    assert d.expected_latency_delta is None
    assert d.expected_cost_delta is None


def test_measured_baseline_still_yields_deltas():
    rows = [
        _trow(
            candidate_id="m-a",
            task="evidence_extraction",
            det=0.9,
            latency=40.0,
            cost=1.0,
        ),
        _trow(
            candidate_id="static/model",
            task="evidence_extraction",
            det=0.85,
            latency=100.0,
            cost=0.5,
        ),
    ]
    d = _decide(task="evidence_extraction", rows=rows)
    assert d.expected_quality_delta == pytest.approx(0.05)
    assert d.expected_latency_delta == pytest.approx(-60.0)
    assert d.expected_cost_delta == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# M29 — an unresolvable provider is not a missing capability
# ---------------------------------------------------------------------------


def test_unresolvable_provider_raises_instead_of_blaming_the_model():
    """M29: a lookup fault was reported as "the model lacks the capability"."""
    from research_harness.kernel.errors import PluginError
    from research_harness.plugins.routing.policy_router.plugin import PolicyModelRouterService
    from research_harness.research.schemas.routing import RoutingRequest

    def lookup(name: str):
        raise LookupError(name)

    svc = PolicyModelRouterService(artifact_store=None, service_lookup=lookup)
    with pytest.raises(PluginError, match="cannot resolve model provider"):
        svc._capability_ok("not-a-registered-provider", RoutingRequest(role="reasoning"))


def test_provider_without_capabilities_is_a_capability_gap():
    """A resolved provider that declares no capabilities genuinely cannot be checked."""
    from research_harness.plugins.routing.policy_router.plugin import PolicyModelRouterService
    from research_harness.research.schemas.routing import RoutingRequest

    class _NoCaps:
        pass

    svc = PolicyModelRouterService(
        artifact_store=None, service_lookup=lambda name: _NoCaps()
    )
    assert svc._capability_ok("openrouter", RoutingRequest(role="reasoning")) is False
