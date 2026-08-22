"""Phase 3A unit tests — gap selection with autonomy checkpoint.

Fake models only, offline. Covers: selection not auto-rank-#1, autonomy
approval recording (high + interactive), explicit operator override,
invalid gap rejection, deterministic fallback, idempotency, provenance.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.gap import (
    AnalyticalModelOpportunity,
    GapAnalysis,
    GapRankDimension,
    GapStatus,
    GapStrength,
    GapType,
    ResearchGap,
)


class FakeRouter:
    def __init__(self, responses: list[str] | None = None, fail: bool = False):
        self.responses = responses or []
        self.fail = fail
        self.calls = 0
        self.last_role = None

    async def complete(self, role, request):
        self.calls += 1
        self.last_role = role
        if self.fail:
            raise RuntimeError("model failure")
        idx = min(self.calls - 1, len(self.responses) - 1)
        content = self.responses[idx] if self.responses else "{}"
        return ModelResponse(
            message=Message(role="assistant", content=content),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


class FakeAutonomy:
    """Records approval requests; auto-approves unless configured otherwise."""

    def __init__(self, approved: bool = True, mode: str = "high"):
        self.approved = approved
        self.mode = mode
        self.requests: list = []

    @property
    def autonomy_mode(self) -> str:
        return self.mode

    async def requires_approval(self, checkpoint: str) -> bool:
        return checkpoint == "research_gap"

    async def request_approval(self, request):
        self.requests.append(request)
        from research_harness.contracts.autonomy import ApprovalDecision

        return ApprovalDecision(
            request_id=request.request_id,
            approved=self.approved,
            reason="approved by fake policy" if self.approved else "rejected by fake policy",
            decided_by=f"policy:{self.mode}",
        )


def _gap(title: str, gid: str, rank: float) -> ResearchGap:
    return ResearchGap(
        title=title,
        gap_type=GapType.mechanism_gap,
        description=f"Within the reviewed corpus, {title.lower()} needs study.",
        why_it_matters="matters",
        supporting_papers=2,
        supporting_evidence_items=3,
        strength=GapStrength.strongly_supported,
        status=GapStatus.candidate,
        ranking=GapRankDimension(
            evidence_strength=rank,
            research_importance=rank,
            theoretical_relevance=rank,
            analytical_model_potential=rank,
            tractability=rank,
        ),
        analytical_model_opportunity=AnalyticalModelOpportunity(
            suitable=True, domains=["pricing"], rationale="r"
        ),
    )


async def _gap_analysis_env(
    store, gaps: list[ResearchGap], ranked: list[str] | None = None
) -> tuple[str, dict[str, str]]:
    ids = {}
    for g in gaps:
        env = ArtifactEnvelope.create(payload=g, artifact_type="research_gap", producer="test")
        await store.put(env)
        ids[g.title] = env.artifact_id
    analysis = GapAnalysis(
        literature_synthesis_id="syn1",
        evidence_corpus_id="corp1",
        gap_ids=list(ids.values()),
        ranked_gap_ids=ranked or list(ids.values()),
    )
    a_env = ArtifactEnvelope.create(payload=analysis, artifact_type="gap_analysis", producer="test")
    await store.put(a_env)
    return a_env.artifact_id, ids


def _selection_resp(gid: str) -> str:
    return json.dumps(
        {
            "selected_gap_id": gid,
            "evidence_synthesis_basis": "2 supporting papers across the corpus",
            "research_importance": 0.9,
            "theoretical_relevance": 0.85,
            "analytical_model_suitability": 0.8,
            "tractability": 0.7,
            "selection_rationale": "Most theoretical headroom for mechanism development",
        }
    )


@pytest.mark.asyncio
async def test_gap_selection_model_can_pick_beyond_rank_one(tmp_path: pathlib.Path):
    from research_harness.plugins.research.gap_selection.plugin import GapSelectionService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    a_id, ids = await _gap_analysis_env(
        store,
        [_gap("Rank one gap", "g1", 0.9), _gap("Rank two gap", "g2", 0.6)],
    )
    # ranked default = insertion order; model picks the SECOND gap
    router = FakeRouter([_selection_resp(ids["Rank two gap"])])
    svc = GapSelectionService(
        model_router=router, artifact_store=store, model_role="reasoning", autonomy_mode="high"
    )
    sel_id = await svc.select(a_id)
    assert router.last_role == "reasoning"
    from research_harness.research.schemas.mechanism import GapSelection, SelectionStatus

    sel = (await store.get(sel_id)).parse_payload(GapSelection)
    assert sel.selected_gap_id == ids["Rank two gap"]
    assert sel.selected_by == "model"
    assert sel.status == SelectionStatus.approved
    assert sel.autonomy_mode == "high"
    assert sel.approval_required is False
    assert ids["Rank one gap"] in sel.alternative_gap_ids
    assert sel.research_importance == 0.9


@pytest.mark.asyncio
async def test_gap_selection_model_proposal_outside_analysis_falls_back(
    tmp_path: pathlib.Path,
):
    from research_harness.plugins.research.gap_selection.plugin import GapSelectionService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    a_id, ids = await _gap_analysis_env(store, [_gap("Only gap", "g1", 0.8)], ranked=None)
    router = FakeRouter([_selection_resp("hallucinated-gap-id")])
    svc = GapSelectionService(model_router=router, artifact_store=store, autonomy_mode="high")
    sel_id = await svc.select(a_id)
    from research_harness.research.schemas.mechanism import GapSelection

    sel = (await store.get(sel_id)).parse_payload(GapSelection)
    assert sel.selected_gap_id == ids["Only gap"]
    assert "fallback" in sel.selection_rationale


@pytest.mark.asyncio
async def test_gap_selection_interactive_approval_recorded(tmp_path: pathlib.Path):
    from research_harness.plugins.research.gap_selection.plugin import GapSelectionService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    a_id, ids = await _gap_analysis_env(store, [_gap("Gap A", "g1", 0.8)])
    autonomy = FakeAutonomy(approved=True, mode="interactive")
    router = FakeRouter([_selection_resp(ids["Gap A"])])
    svc = GapSelectionService(
        model_router=router,
        artifact_store=store,
        autonomy_mode="interactive",
        autonomy=autonomy,
    )
    sel_id = await svc.select(a_id)
    assert len(autonomy.requests) == 1
    assert autonomy.requests[0].checkpoint == "research_gap"
    from research_harness.research.schemas.mechanism import GapSelection, SelectionStatus

    sel = (await store.get(sel_id)).parse_payload(GapSelection)
    assert sel.status == SelectionStatus.approved
    assert sel.approval_required is True
    assert sel.approval_decided_by == "policy:interactive"
    assert sel.autonomy_mode == "interactive"


@pytest.mark.asyncio
async def test_gap_selection_interactive_rejection_records_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.research.gap_selection.plugin import GapSelectionService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    a_id, ids = await _gap_analysis_env(store, [_gap("Gap B", "g1", 0.8)])
    autonomy = FakeAutonomy(approved=False, mode="interactive")
    router = FakeRouter([_selection_resp(ids["Gap B"])])
    svc = GapSelectionService(
        model_router=router,
        artifact_store=store,
        autonomy_mode="interactive",
        autonomy=autonomy,
    )
    sel_id = await svc.select(a_id)
    from research_harness.research.schemas.mechanism import GapSelection, SelectionStatus

    sel = (await store.get(sel_id)).parse_payload(GapSelection)
    assert sel.status == SelectionStatus.rejected
    assert sel.approval_reason == "rejected by fake policy"


@pytest.mark.asyncio
async def test_gap_selection_operator_override(tmp_path: pathlib.Path):
    from research_harness.plugins.research.gap_selection.plugin import GapSelectionService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    a_id, ids = await _gap_analysis_env(
        store,
        [_gap("First", "g1", 0.9), _gap("Second", "g2", 0.5)],
    )
    router = FakeRouter([])  # no model call should happen
    svc = GapSelectionService(model_router=router, artifact_store=store, autonomy_mode="high")
    sel_id = await svc.select(a_id, selected_gap_id=ids["Second"])
    assert router.calls == 0
    from research_harness.research.schemas.mechanism import GapSelection

    sel = (await store.get(sel_id)).parse_payload(GapSelection)
    assert sel.selected_gap_id == ids["Second"]
    assert sel.selected_by == "operator"


@pytest.mark.asyncio
async def test_gap_selection_invalid_operator_override_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.research.gap_selection.plugin import GapSelectionService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    a_id, ids = await _gap_analysis_env(store, [_gap("Only", "g1", 0.8)])
    svc = GapSelectionService(model_router=FakeRouter(), artifact_store=store, autonomy_mode="high")
    with pytest.raises(ValueError, match="not among gaps"):
        await svc.select(a_id, selected_gap_id="unknown-id")


@pytest.mark.asyncio
async def test_gap_selection_deterministic_fallback_on_model_failure(
    tmp_path: pathlib.Path,
):
    from research_harness.plugins.research.gap_selection.plugin import GapSelectionService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    a_id, ids = await _gap_analysis_env(
        store,
        [_gap("Top", "g1", 0.95), _gap("Lower", "g2", 0.3)],
    )
    svc = GapSelectionService(model_router=FakeRouter(fail=True), artifact_store=store)
    sel_id = await svc.select(a_id)
    from research_harness.research.schemas.mechanism import GapSelection

    sel = (await store.get(sel_id)).parse_payload(GapSelection)
    assert sel.selected_gap_id == ids["Top"]
    assert "fallback" in sel.selection_rationale


@pytest.mark.asyncio
async def test_gap_selection_idempotent(tmp_path: pathlib.Path):
    from research_harness.plugins.research.gap_selection.plugin import GapSelectionService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    a_id, ids = await _gap_analysis_env(store, [_gap("Gap C", "g1", 0.8)])
    router = FakeRouter([_selection_resp(ids["Gap C"])])
    svc = GapSelectionService(model_router=router, artifact_store=store, autonomy_mode="high")
    first = await svc.select(a_id)
    second = await svc.select(a_id)
    assert first == second
    assert router.calls == 1


@pytest.mark.asyncio
async def test_gap_selection_provenance_and_gap_marked_selected(tmp_path: pathlib.Path):
    from research_harness.plugins.research.gap_selection.plugin import GapSelectionService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    a_id, ids = await _gap_analysis_env(store, [_gap("Gap D", "g1", 0.8)])
    svc = GapSelectionService(
        model_router=FakeRouter([_selection_resp(ids["Gap D"])]), artifact_store=store
    )
    sel_id = await svc.select(a_id)
    sel_env = await store.get(sel_id)
    parents = await store.get_parents(sel_id)
    assert any(p.source_artifact_id == a_id for p in parents)
    assert any(p.source_artifact_id == ids["Gap D"] for p in parents)

    # The selected gap is superseded by a status=selected artifact
    gap_envs = await store.list(artifact_type="research_gap")
    assert len(gap_envs) == 2
    selected_gap = next(env for env in gap_envs if env.artifact_id != ids["Gap D"]).parse_payload(
        ResearchGap
    )
    assert selected_gap.status == GapStatus.selected
    supersede_links = await store.get_children(ids["Gap D"])
    assert any(p.relation.value == "supersedes" for p in supersede_links)
