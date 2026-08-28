"""Phase 7D.3D gap-workflow repair regression tests.

The live-quality gap workflow used stable case-scoped fixture ids with
idempotent _put_explicit, so repeated live runs reused the same fixture
artifacts and the grounding check flagged every correct gap as referencing
ids "not produced by this run". The repair adds a run-scoped ID mapping layer:
live (real-model) runs get run-unique fixture ids with fresh puts; offline
(scripted) runs keep stable ids, so research-gap-analysis-v1 is unchanged.
Production GapAnalyzerService is untouched and evaluator grounding rules are
not weakened.
"""

from __future__ import annotations

import json
import re

import pytest

from research_harness.contracts.common import Usage
from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import LIVE_QUALITY_REASONING_V1
from research_harness.research.benchmarks.workflows import run_gap_workflow
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase


class _GapEchoRouter:
    """Real-model stand-in: echoes the statement ids shown in the prompt."""

    def __init__(self, *, hallucinate: bool = False) -> None:
        self.hallucinate = hallucinate
        self.calls = 0

    async def complete(self, role: str, request):  # noqa: ANN001
        self.calls += 1
        prompt = request.messages[-1].content or ""
        stmt_ids = re.findall(r"\[([A-Za-z0-9_\-:]+)\]", prompt)
        if not stmt_ids:
            raise RuntimeError("no statement ids in prompt")
        gap = {
            "title": "Mechanism gap",
            "gap_type": "mechanism_gap",
            "description": "The mechanism linking algorithmic pricing to welfare is unclear.",
            "supporting_synthesis_statement_ids": (
                ["lq-ghost-statement"] if self.hallucinate else stmt_ids[:1]
            ),
            "supporting_evidence_ids": [],
            "contradiction_statement_ids": [],
            "confidence": 0.8,
            "scope": "within the reviewed corpus",
            "limitations": [],
        }
        return ModelResponse(
            message=Message(role="assistant", content=json.dumps({"gaps": [gap]})),
            model="fake",
            provider="fake",
            usage=Usage(prompt_tokens=10, completion_tokens=5),
            latency_ms=1.0,
        )


def _lq_gap_case() -> BenchmarkCase:
    case = [c for c in LIVE_QUALITY_REASONING_V1.cases if c.id == "lq-gap-analysis"][0]
    return BenchmarkCase(
        id=case.id,
        benchmark_id=LIVE_QUALITY_REASONING_V1.benchmark_id,
        name=case.name,
        description=case.description,
        input=case.input,
        reference=case.reference,
        evaluation_dimensions=case.evaluation_dimensions,
        tags=case.tags,
    )


async def _live_gap_run(store, router) -> list:  # noqa: ANN001
    return await run_gap_workflow(
        artifact_store=store, case=_lq_gap_case(), producer="test", model_router=router
    )


def _fixture_ids(produced) -> dict:  # noqa: ANN001
    ids = {"evidence": [], "statements": [], "gaps": []}
    for e in produced:
        if e.artifact_type == "evidence_item":
            ids["evidence"].append(e.artifact_id)
        elif e.artifact_type == "synthesis_statement":
            ids["statements"].append(e.artifact_id)
        elif e.artifact_type == "research_gap":
            ids["gaps"].append(e.artifact_id)
    return ids


@pytest.mark.asyncio
async def test_live_gap_repetitions_use_distinct_fixture_ids(tmp_path):
    store = SQLiteArtifactStore(path=tmp_path / "a.db")
    r1 = await _live_gap_run(store, _GapEchoRouter())
    r2 = await _live_gap_run(store, _GapEchoRouter())
    ids1, ids2 = _fixture_ids(r1), _fixture_ids(r2)
    assert ids1["statements"] and ids1["evidence"]
    assert ids1["statements"] != ids2["statements"]
    assert ids1["evidence"] != ids2["evidence"]
    await store.close()


@pytest.mark.asyncio
async def test_live_gap_no_stale_reuse_and_grounded(tmp_path):
    store = SQLiteArtifactStore(path=tmp_path / "b.db")
    router = _GapEchoRouter()
    r1 = await _live_gap_run(store, router)
    r2 = await _live_gap_run(store, router)
    # second run's produced set must not contain the first run's fixture ids
    produced2 = {e.artifact_id for e in r2}
    ids1 = set(_fixture_ids(r1)["statements"]) | set(_fixture_ids(r1)["evidence"])
    assert not (ids1 & produced2), "stale fixture artifact reused across live repetitions"
    # the produced gap must reference a statement produced in the SAME run
    stmts2 = set(_fixture_ids(r2)["statements"])
    gaps2 = [e.payload for e in r2 if e.artifact_type == "research_gap"]
    assert gaps2, "expected a produced research_gap"
    refs = gaps2[0].get("supporting_synthesis_statement_ids") or []
    assert refs, "gap must reference a produced statement"
    assert set(refs) <= stmts2, "gap references an id not produced by this run"
    await store.close()


@pytest.mark.asyncio
async def test_offline_gap_stable_ids_unchanged(tmp_path):
    from research_harness.research.benchmarks import RESEARCH_GAP_ANALYSIS_V1

    case_def = RESEARCH_GAP_ANALYSIS_V1.cases[0]
    case = BenchmarkCase(
        id=case_def.id,
        benchmark_id=RESEARCH_GAP_ANALYSIS_V1.benchmark_id,
        name=case_def.name,
        description=case_def.description,
        input=case_def.input,
        reference=case_def.reference,
        evaluation_dimensions=case_def.evaluation_dimensions,
        tags=case_def.tags,
    )
    store = SQLiteArtifactStore(path=tmp_path / "c.db")
    r1 = await run_gap_workflow(artifact_store=store, case=case, producer="test")
    r2 = await run_gap_workflow(artifact_store=store, case=case, producer="test")
    ids1 = _fixture_ids(r1)
    # offline (scripted) keeps STABLE case-scoped ids (not run-unique)
    assert all(sid.startswith(f"{case.id}-statement-") for sid in ids1["statements"])
    assert all(eid.startswith(f"{case.id}-evidence-") for eid in ids1["evidence"])
    # second run is idempotent: it does not create new/run-unique fixture ids
    assert _fixture_ids(r2)["statements"] == []
    assert _fixture_ids(r2)["evidence"] == []
    await store.close()


@pytest.mark.asyncio
async def test_provenance_survives_sqlite_reopen(tmp_path):
    store = SQLiteArtifactStore(path=tmp_path / "d.db")
    r1 = await _live_gap_run(store, _GapEchoRouter())
    gap_ids = _fixture_ids(r1)["gaps"]
    assert gap_ids, "expected a produced research_gap"
    target = gap_ids[0]
    # ancestors of the produced gap include its fixture context
    lineage = await store.get_lineage(target, direction="ancestors")
    lineage_ids = {e.artifact_id for e in lineage}
    assert any(aid.startswith("lq-gap-analysis-") for aid in lineage_ids), (
        "produced gap must be linked to its run-scoped fixture ancestors"
    )
    await store.close()
    reopened = SQLiteArtifactStore(path=tmp_path / "d.db")
    env = await reopened.get(target)
    assert env.artifact_type == "research_gap"
    lineage2 = await reopened.get_lineage(target, direction="ancestors")
    assert len(lineage2) >= len(lineage)
    await reopened.close()


@pytest.mark.asyncio
async def test_correct_gap_passes_grounding():
    """A correct gap referencing a produced statement passes the live-quality
    reasoning evaluator (grounding rules unchanged)."""
    from research_harness.contracts.evaluator import EvaluatorContext
    from research_harness.plugins.research.evaluator_live_quality_reasoning.plugin import (
        LiveQualityReasoningEvaluator,
    )
    from research_harness.research.schemas.gap import ResearchGap
    from research_harness.research.schemas.synthesis import (
        SynthesisStatement,
        SynthesisStatementType,
    )

    stmt = SynthesisStatement(
        statement="Algorithmic pricing affects consumer welfare in online markets.",
        type=SynthesisStatementType.consensus,
        supporting_evidence_ids=["ev-1"],
    )
    stmt_env = ArtifactEnvelope.create(
        payload=stmt, artifact_type="synthesis_statement", artifact_id="stmt-1"
    )
    gap = ResearchGap(
        title="Mechanism gap",
        gap_type="mechanism_gap",
        description="The mechanism linking algorithmic pricing to welfare is unclear.",
        supporting_synthesis_statement_ids=["stmt-1"],
    )
    gap_env = ArtifactEnvelope.create(payload=gap, artifact_type="research_gap")
    case = _lq_gap_case()
    ctx = EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(payload=case, artifact_type="benchmark_case"),
        produced_artifacts=[stmt_env, gap_env],
    )
    result = await LiveQualityReasoningEvaluator().evaluate(ctx)
    assert result.status.value == "passed"
    assert (result.value or {}).get("unsupported_references") == 0


@pytest.mark.asyncio
async def test_hallucinated_gap_id_still_fails():
    """A gap referencing an id NOT produced by the run still fails grounding —
    the repair never weakens the grounding rules."""
    from research_harness.contracts.evaluator import EvaluatorContext
    from research_harness.plugins.research.evaluator_live_quality_reasoning.plugin import (
        LiveQualityReasoningEvaluator,
    )
    from research_harness.research.schemas.gap import ResearchGap
    from research_harness.research.schemas.synthesis import (
        SynthesisStatement,
        SynthesisStatementType,
    )

    stmt = SynthesisStatement(
        statement="Algorithmic pricing affects consumer welfare in online markets.",
        type=SynthesisStatementType.consensus,
        supporting_evidence_ids=["ev-1"],
    )
    stmt_env = ArtifactEnvelope.create(
        payload=stmt, artifact_type="synthesis_statement", artifact_id="stmt-1"
    )
    gap = ResearchGap(
        title="Mechanism gap",
        gap_type="mechanism_gap",
        description="The mechanism linking algorithmic pricing to welfare is unclear.",
        supporting_synthesis_statement_ids=["hallucinated-99"],
    )
    gap_env = ArtifactEnvelope.create(payload=gap, artifact_type="research_gap")
    case = _lq_gap_case()
    ctx = EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(payload=case, artifact_type="benchmark_case"),
        produced_artifacts=[stmt_env, gap_env],
    )
    result = await LiveQualityReasoningEvaluator().evaluate(ctx)
    assert result.status.value == "failed"
    assert (result.value or {}).get("critical_grounding_failures") >= 1
