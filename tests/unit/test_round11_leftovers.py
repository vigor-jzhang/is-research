"""Regression tests for the round-11 leftovers (round 32).

M83 — a metric that read a key nothing ever writes.
M84 — a reported count that was hard-coded to zero.
M85 — configured budgets and roles that were accepted and never read.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.research.schemas.evaluation import BenchmarkCase


def _case(reference: dict[str, Any], inp: dict[str, Any] | None = None) -> BenchmarkCase:
    return BenchmarkCase(
        id="c1",
        benchmark_id="b1",
        name="c1",
        description="c1",
        input=inp or {},
        reference=reference,
    )


# ---------------------------------------------------------------------------
# M83 — task_completion_rate was a constant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_completion_rate_is_not_reported_as_measured() -> None:
    """M83: the metric read `_completed`, which nothing in the repo ever writes.

    So it was always 1.0 with denominator 1 — a fabricated number presented as
    a measured rate. It is gone rather than left green.
    """
    from research_harness.plugins.research.evaluator_live_quality_reasoning.plugin import (
        LiveQualityReasoningEvaluator,
    )

    ev = LiveQualityReasoningEvaluator()
    ctx = EvaluatorContext(
        case=_case({"task": "gap_analysis"}), case_envelope=None, produced_artifacts=[]
    )
    result = await ev.evaluate(ctx)
    metrics = result.value.get("metrics", {})
    assert "task_completion_rate" not in metrics, (
        "a metric nothing can ever fail has come back"
    )
    assert "task_completion_rate" not in result.value.get("dimension_scores", {})


def test_coverage_matrix_no_longer_declares_it() -> None:
    """M83: the declaration and the evaluator must agree — both dropped it."""
    from research_harness.research.evaluation_coverage import (
        COVERAGE_MATRIX,
    )

    for entry in COVERAGE_MATRIX:
        if entry.evaluator == "evaluator.live_quality_reasoning":
            assert "task_completion_rate" not in entry.metrics
            return
    pytest.fail("evaluator.live_quality_reasoning not found in the coverage matrix")


def test_other_reasoning_metrics_survived() -> None:
    """Guard: removing the fake metric did not take real ones with it."""
    from research_harness.research.evaluation_coverage import COVERAGE_MATRIX

    for entry in COVERAGE_MATRIX:
        if entry.evaluator == "evaluator.live_quality_reasoning":
            assert "structured_output_success" in entry.metrics
            assert "grounding_correctness" in entry.metrics
            return
    pytest.fail("evaluator.live_quality_reasoning not found in the coverage matrix")


# ---------------------------------------------------------------------------
# M84 — missing_abstract was hard-coded to 0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_abstract_is_counted(tmp_path: pathlib.Path) -> None:
    """M84: the orchestrator reported 0 no matter how many papers lacked one.

    screening_view_builder sets metadata["missing_abstract"] = True and nothing
    read it back, so the report read as "no paper lacked an abstract" whenever
    the flag simply had not been aggregated.
    """
    import json

    from research_harness.plugins.literature.screening_orchestrator.plugin import (
        ScreeningOrchestratorService,
    )
    from research_harness.plugins.literature.screening_view_builder.plugin import (
        ScreeningViewBuilderService,
    )
    from research_harness.plugins.literature.title_abstract_screener.plugin import (
        TitleAbstractScreenerService,
    )
    from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
    from research_harness.research.envelope import ArtifactEnvelope
    from research_harness.research.schemas.execution import (
        LiteratureSearchExecution,
    )
    from research_harness.research.schemas.identity import (
        PaperIdentity,
        ResolutionMethod,
    )
    from research_harness.research.schemas.paper import PaperRecord
    from research_harness.research.schemas.screening_execution import ScreeningExecution
    from research_harness.research.schemas.screening_protocol import (
        ProtocolStatus,
        ScreeningCriterion,
        ScreeningProtocol,
    )

    store = SQLiteArtifactStore(path=tmp_path / "a.db")

    include_json = json.dumps(
        {
            "decision": "include",
            "matched_inclusion_criteria": ["I1"],
            "matched_exclusion_criteria": [],
            "reason_codes": [],
            "rationale_summary": "yes",
            "confidence": 0.9,
            "information_sufficiency": "sufficient",
        }
    )

    class _Router:
        async def complete(self, role: str, request: Any) -> Any:
            class _Msg:
                content = include_json

            class _Resp:
                message = _Msg()

            return _Resp()

    pi_ids: list[str] = []
    for i, abstract in enumerate([None, None, "has one", "has one too"]):
        paper = PaperRecord(title=f"T{i}", abstract=abstract)
        p_env = ArtifactEnvelope.create(
            payload=paper, artifact_type="paper_record", producer="test"
        )
        await store.put(p_env)
        pi = PaperIdentity(
            member_paper_artifact_ids=[p_env.artifact_id],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.exact_identifier,
            resolution_evidence=[],
        )
        pi_env = ArtifactEnvelope.create(
            payload=pi, artifact_type="paper_identity", producer="test"
        )
        await store.put(pi_env)
        pi_ids.append(pi_env.artifact_id)

    proto = ScreeningProtocol(
        research_question_id="rq",
        objective="obj",
        inclusion_criteria=[
            ScreeningCriterion(criterion_id="I1", kind="inclusion", description="d")
        ],
        exclusion_criteria=[
            ScreeningCriterion(criterion_id="E1", kind="exclusion", description="e")
        ],
        status=ProtocolStatus.approved,
    )
    proto_env = ArtifactEnvelope.create(
        payload=proto, artifact_type="screening_protocol", producer="test"
    )
    await store.put(proto_env)

    search = LiteratureSearchExecution(
        strategy_artifact_id="s",
        query_artifact_ids=[],
        search_record_artifact_ids=[],
        paper_artifact_ids=[],
        paper_identity_artifact_ids=pi_ids,
        counts={},
        provider_failures=[],
    )
    search_env = ArtifactEnvelope.create(
        payload=search,
        artifact_type="literature_search_execution",
        producer="test",
    )
    await store.put(search_env)

    orchestrator = ScreeningOrchestratorService(
        artifact_store=store,
        view_builder=ScreeningViewBuilderService(artifact_store=store),
        screener=TitleAbstractScreenerService(
            model_router=_Router(), artifact_store=store, model_role="fast"
        ),
    )
    out_id = await orchestrator.screen(search_env.artifact_id, proto_env.artifact_id)
    execution = (await store.get(out_id)).parse_payload(ScreeningExecution)
    await store.close()

    assert execution.counts.get("missing_abstract") == 2, (
        f"counted {execution.counts.get('missing_abstract')} of 2 missing abstracts"
    )


# ---------------------------------------------------------------------------
# M85 — configured knobs that did nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_results_assembler_honours_max_llm_calls(  # type: ignore[no-untyped-def]
    tmp_path: pathlib.Path, monkeypatch
) -> None:
    """M85: max_llm_calls=1 still spent three calls.

    The retry loop used a module constant and never consulted the configured
    budget, so the knob did nothing at all.
    """
    from research_harness.plugins.research.results_assembler.plugin import (
        ResultsAssemblerService,
    )
    from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore

    store = SQLiteArtifactStore(path=tmp_path / "a.db")
    svc = ResultsAssemblerService(
        model_router=None, artifact_store=store, max_llm_calls=1
    )
    calls: list[int] = []

    async def always_invalid(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return {"findings": "not-a-list"}  # fails _AssemblyResponse validation

    monkeypatch.setattr(svc, "_load_context", lambda *a, **k: _ctx())
    monkeypatch.setattr(svc, "_call_model", always_invalid)

    async def _ctx() -> dict[str, Any]:
        return {
            "equilibrium_analysis_id": "e",
            "model_id": "m",
            "novelty_normalized": 0,
            "started_at": None,
        }

    import contextlib

    with contextlib.suppress(Exception):
        await svc.assemble("num-1")
    await store.close()

    assert len(calls) == 1, f"budget of 1 allowed {len(calls)} model calls"


def test_equilibrium_deriver_uses_the_revision_role() -> None:
    """M85: revisions ran under the derivation role; revision_role was inert.

    The sibling critic plugins honour theirs, so this was an inconsistency as
    well as a dead knob.
    """
    from research_harness.plugins.research.equilibrium_deriver.plugin import (
        EquilibriumDeriverService,
    )

    used: list[str] = []

    class _Router:
        async def complete(self, role: str, request: Any) -> Any:
            used.append(role)
            raise RuntimeError("stop here")

    svc = EquilibriumDeriverService(
        model_router=_Router(),
        artifact_store=None,
        verifier=None,
        model_role="fast",
        revision_role="critic",
    )

    import asyncio

    asyncio.run(svc._llm_candidate_call("prompt", role=svc._revision_role))
    assert used == ["critic"], f"revision ran under {used}"
    assert svc._revision_role == "critic"


def test_initial_proposal_still_uses_the_derivation_role() -> None:
    """Guard: only revisions moved role; the first proposal is unchanged."""
    from research_harness.plugins.research.equilibrium_deriver.plugin import (
        EquilibriumDeriverService,
    )

    used: list[str] = []

    class _Router:
        async def complete(self, role: str, request: Any) -> Any:
            used.append(role)
            raise RuntimeError("stop here")

    svc = EquilibriumDeriverService(
        model_router=_Router(),
        artifact_store=None,
        verifier=None,
        model_role="fast",
        revision_role="critic",
    )

    import asyncio

    asyncio.run(svc._llm_candidate_call("prompt"))
    assert used == ["fast"], f"initial proposal ran under {used}"


@pytest.mark.parametrize(
    "module,cls,kwarg",
    [
        (
            "research_harness.plugins.research.proposition_generator.plugin",
            "PropositionGeneratorService",
            "max_llm_calls",
        ),
        (
            "research_harness.plugins.research.mechanism_generator.plugin",
            "MechanismGeneratorService",
            "max_model_calls",
        ),
        (
            "research_harness.plugins.literature.gap_analyzer.plugin",
            "GapAnalyzerService",
            "max_model_calls",
        ),
    ],
)
def test_inert_budgets_are_gone(module: str, cls: str, kwarg: str) -> None:
    """M85: three budgets were accepted and never read.

    None of these services has a call loop to bound — each makes one model call
    per run — so the knob could never do anything. It was removed rather than
    left as decoration that reads like a working control.
    """
    import importlib

    mod = importlib.import_module(module)
    service_cls = getattr(mod, cls)
    import inspect

    assert kwarg not in inspect.signature(service_cls.__init__).parameters
