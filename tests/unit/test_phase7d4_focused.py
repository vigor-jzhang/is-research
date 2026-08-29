"""Phase 7D.4 focused unit tests.

Covers the deterministic task-aware shadow routing logic: exact-task
qualification (never transferred), task specialization, uncovered-task static
fallback (no_qualified_task_model / stale_qualification), unqualified-cheaper
non-selection, qualified primary/fallback behavior, primary-without-qualified-
fallback, deterministic tie-breaks, no unsafe selection, decision idempotency,
and provenance after a store reopen.
"""

from __future__ import annotations

import pathlib

import pytest

from research_harness.research.schemas.qualification import (
    TaskQualificationMatrix,
    TaskQualificationResult,
)


def _row(
    *,
    candidate_id: str,
    task: str,
    role: str = "reasoning",
    qualified: bool = True,
    det: float = 0.9,
    worst: float | None = None,
    structured: float = 0.9,
    provider: float = 0.0,
    latency: float | None = None,
    cost: float | None = None,
    live_quality_run_id: str | None = None,
) -> TaskQualificationResult:
    return TaskQualificationResult(
        role=role,
        task=task,
        candidate_id=candidate_id,
        model={"provider": "openrouter", "requested_model": candidate_id},
        resolved_model=candidate_id,
        benchmark_id=f"live-quality-{role}-v1",
        repetitions=3,
        deterministic_pass_rate_mean=det,
        deterministic_pass_rate_worst=worst if worst is not None else det,
        deterministic_pass_rate_variance=0.0,
        structured_output_success_rate=structured,
        provider_error_frequency=provider,
        critical_grounding_failures=0,
        qualified=qualified,
        rejection_reasons=[] if qualified else ["task deterministic_pass_rate 0.4 < 0.85"],
        latency_ms_p50=latency,
        estimated_cost=cost,
        live_quality_run_id=live_quality_run_id,
    )


def _matrix(
    rows: list[TaskQualificationResult], role: str = "reasoning"
) -> TaskQualificationMatrix:
    tasks = sorted({r.task for r in rows})
    qualified: dict[str, list[str]] = {t: [] for t in tasks}
    for r in rows:
        if r.qualified:
            qualified.setdefault(r.task, []).append(r.candidate_id)
    return TaskQualificationMatrix(
        id="matrix-1",
        role=role,
        benchmark_id=f"live-quality-{role}-v1",
        tasks=tasks,
        rows=rows,
        qualified_models_by_task=qualified,
        ranked_models_by_task={t: sorted(v) for t, v in qualified.items()},
        qualified_tasks_by_model={},
        role_qualified_models=[],
        repetitions=3,
    )


def _decide(
    *,
    task: str,
    rows: list[TaskQualificationResult],
    role: str = "reasoning",
    static_model: str = "static/model",
    max_qualification_age_seconds: float | None = None,
    matrix_age_seconds: float | None = None,
):
    from research_harness.research.routing.task_aware import build_task_aware_decision

    return build_task_aware_decision(
        role=role,
        task=task,
        matrix=_matrix(rows, role),
        static_model=static_model,
        static_provider="openrouter",
        max_qualification_age_seconds=max_qualification_age_seconds,
        matrix_age_seconds=matrix_age_seconds,
    )


def test_exact_task_qualification_gate():
    """A model qualified for evidence_extraction is selected ONLY for that
    exact task; the same model does not qualify mechanism_generation."""
    rows = [_row(candidate_id="m-a", task="evidence_extraction", det=0.95)]
    d_evidence = _decide(task="evidence_extraction", rows=rows)
    assert d_evidence.status.value == "selected"
    assert d_evidence.primary_candidate_id == "m-a"
    d_mech = _decide(task="mechanism_generation", rows=rows)
    assert d_mech.status.value == "static_fallback"
    assert d_mech.reason == "no_qualified_task_model"
    assert d_mech.primary_candidate_id is None
    assert d_mech.shadow_selected_model is None
    assert d_mech.would_switch is False


def test_specialization_across_tasks():
    """Different tasks specialize on different qualified models."""
    rows = [
        _row(candidate_id="m-a", task="evidence_extraction", det=0.95),
        _row(candidate_id="m-b", task="gap_analysis", det=0.95),
    ]
    assert _decide(task="evidence_extraction", rows=rows).primary_candidate_id == "m-a"
    assert _decide(task="gap_analysis", rows=rows).primary_candidate_id == "m-b"


def test_uncovered_task_stays_static():
    """No qualified model -> static_fallback with the configured model."""
    rows = [_row(candidate_id="m-a", task="evidence_extraction", det=0.4, qualified=False)]
    d = _decide(task="evidence_extraction", rows=rows)
    assert d.status.value == "static_fallback"
    assert d.reason == "no_qualified_task_model"
    assert d.fallback_candidate_id == "static/model"
    assert d.fallback_not_live_qualified is True
    assert d.shadow_selected_model is None


def test_unqualified_cheaper_model_never_selected():
    """A cheaper but unqualified model is never selected."""
    rows = [
        _row(
            candidate_id="m-cheap", task="evidence_extraction", det=0.4, qualified=False, cost=0.0
        ),
        _row(candidate_id="m-good", task="evidence_extraction", det=0.9, cost=1.0),
    ]
    d = _decide(task="evidence_extraction", rows=rows)
    assert d.primary_candidate_id == "m-good"
    assert d.shadow_selected_model == "m-good"


def test_qualified_primary_plus_fallback():
    """Two qualified models: primary + live-qualified fallback recorded."""
    rows = [
        _row(candidate_id="m-a", task="evidence_extraction", det=0.95),
        _row(candidate_id="m-b", task="evidence_extraction", det=0.9),
    ]
    d = _decide(task="evidence_extraction", rows=rows)
    assert d.primary_candidate_id == "m-a"
    assert d.fallback_candidate_id == "m-b"
    assert d.fallback_is_qualified is True
    assert d.fallback_not_live_qualified is False


def test_primary_without_qualified_fallback():
    """Only a primary qualified: fallback is the static model, marked not
    live qualified (never presented as qualified)."""
    rows = [_row(candidate_id="m-a", task="evidence_extraction", det=0.9)]
    d = _decide(task="evidence_extraction", rows=rows)
    assert d.primary_candidate_id == "m-a"
    assert d.fallback_candidate_id == "static/model"
    assert d.fallback_is_qualified is False
    assert d.fallback_not_live_qualified is True


def test_stale_qualification_rejected():
    """Qualification older than max age is rejected -> static_fallback."""
    rows = [_row(candidate_id="m-a", task="evidence_extraction", det=0.9)]
    d = _decide(
        task="evidence_extraction",
        rows=rows,
        max_qualification_age_seconds=60,
        matrix_age_seconds=3600,
    )
    assert d.status.value == "static_fallback"
    assert d.reason == "stale_qualification"
    assert d.primary_candidate_id is None


def test_deterministic_tie_break():
    """Identical qualified candidates resolve deterministically by candidate_id."""
    rows = [
        _row(candidate_id="m-b", task="evidence_extraction", det=0.9),
        _row(candidate_id="m-a", task="evidence_extraction", det=0.9),
    ]
    d = _decide(task="evidence_extraction", rows=rows)
    assert d.primary_candidate_id == "m-a"
    assert d.fallback_candidate_id == "m-b"


def test_no_unsafe_selection_invariant():
    """A selected primary is always exact-task qualified; static fallback never
    switches; deltas computed against the static model's own qualified row."""
    rows = [
        _row(candidate_id="m-a", task="evidence_extraction", det=0.9, cost=1.0),
        _row(candidate_id="static/model", task="evidence_extraction", det=0.85, cost=0.5),
    ]
    d = _decide(task="evidence_extraction", rows=rows)
    qualified_ids = {c.candidate_id for c in d.qualified_candidates}
    assert d.primary_candidate_id in qualified_ids
    assert d.would_switch is True  # m-a != static/model
    assert d.expected_quality_delta == pytest.approx(0.05)
    assert d.expected_cost_delta == pytest.approx(0.5)


def test_qualification_result_ids_captured():
    rows = [
        _row(candidate_id="m-a", task="evidence_extraction", det=0.9, live_quality_run_id="run-1"),
    ]
    d = _decide(task="evidence_extraction", rows=rows)
    assert d.qualification_result_ids == ["run-1"]
    assert d.matrix_id == "matrix-1"


@pytest.mark.asyncio
async def test_service_decide_idempotent_and_provenance_after_reopen(tmp_path: pathlib.Path):
    """Deciding the same (role, task) twice is idempotent in content, and the
    immutable decision + matrix survive a store reopen (provenance intact)."""
    from research_harness.plugins.routing.task_aware_router.plugin import TaskAwareRouterService
    from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore

    db = tmp_path / "art.db"
    store = SQLiteArtifactStore(path=db)
    matrix = _matrix([_row(candidate_id="m-a", task="evidence_extraction", det=0.9)])
    from research_harness.research.envelope import ArtifactEnvelope

    await store.put(
        ArtifactEnvelope.create(payload=matrix, artifact_type="task_qualification_matrix")
    )
    svc = TaskAwareRouterService(
        artifact_store=store,
        current_roles={"reasoning": {"model": "static/model", "provider": "openrouter"}},
    )

    d1 = await svc.decide("reasoning", "evidence_extraction")
    d2 = await svc.decide("reasoning", "evidence_extraction")
    assert d1.status == d2.status
    assert d1.primary_candidate_id == d2.primary_candidate_id == "m-a"
    assert d1.would_switch == d2.would_switch == True  # noqa: E712

    # provenance after reopen: decisions + matrix still readable and identical
    reopened = SQLiteArtifactStore(path=db)
    svc2 = TaskAwareRouterService(artifact_store=reopened, current_roles={})
    decisions = await svc2.list_decisions()
    assert len(decisions) == 2
    assert decisions[0].primary_candidate_id == "m-a"
    matrix_reopened = await svc2.latest_matrix("reasoning")
    assert matrix_reopened is not None
    assert matrix_reopened.id == matrix.id
    assert any(r.candidate_id == "m-a" and r.qualified for r in matrix_reopened.rows)
