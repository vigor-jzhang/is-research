"""Phase 3A unit tests — mechanism generation, grounding validation, critic,
revision/selection, provenance, idempotency, model-role change.

Fake models only, offline.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.evidence import EvidenceCategory, EvidenceItem, Locator
from research_harness.research.schemas.gap import (
    GapAnalysis,
    GapRankDimension,
    GapStatus,
    GapStrength,
    GapType,
    ResearchGap,
)
from research_harness.research.schemas.mechanism import (
    GapSelection,
    KnowledgeBasis,
    MechanismAnalysis,
    MechanismAnalysisExecution,
    MechanismCandidate,
    MechanismCritique,
    SelectedMechanism,
    SelectionStatus,
)
from research_harness.research.schemas.synthesis import (
    LiteratureSynthesis,
    SupportType,
    SynthesisStatement,
    SynthesisStatementType,
    SynthesisTheme,
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


async def _evidence(store, doc_id: str, statement: str, pages) -> str:
    ev = EvidenceItem(
        statement=statement,
        source_artifact_id=doc_id,
        category=EvidenceCategory.finding,
        locator=Locator(page=pages[0], pages=pages),
        extraction_method="model-assisted",
        confidence=0.9,
    )
    env = ArtifactEnvelope.create(payload=ev, artifact_type="evidence_item", producer="test")
    await store.put(env)
    return env.artifact_id


async def _scenario(store) -> dict[str, str]:
    """Evidence -> statement -> gap -> approved GapSelection scenario."""
    e1 = await _evidence(store, "doc1", "Pricing increases with network effects", [3])
    e2 = await _evidence(store, "doc2", "Pricing decreases in competitive platforms", [1])
    e3 = await _evidence(store, "doc3", "Mechanism remains unexplained", [5])

    st_cons = SynthesisStatement(
        statement="Two studies find pricing increases",
        type=SynthesisStatementType.consensus,
        supporting_evidence_ids=[e1, e2],
        supporting_paper_identity_ids=["pi1", "pi2"],
        papers_supporting=2,
        evidence_items_supporting=2,
        support_type=SupportType.multi_paper,
        confidence=0.85,
    )
    st_lim = SynthesisStatement(
        statement="Mechanism linking competition to pricing unexplained",
        type=SynthesisStatementType.limitation_pattern,
        supporting_evidence_ids=[e3],
        supporting_paper_identity_ids=["pi3"],
        papers_supporting=1,
        evidence_items_supporting=1,
        support_type=SupportType.single_paper,
        confidence=0.7,
    )
    stmt_envs = {}
    for stmt in (st_cons, st_lim):
        env = ArtifactEnvelope.create(
            payload=stmt, artifact_type="synthesis_statement", producer="test"
        )
        await store.put(env)
        stmt_envs[stmt.statement] = env.artifact_id
        for eid in stmt.supporting_evidence_ids:
            await store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=eid,
                    target_artifact_id=env.artifact_id,
                    producer="test",
                )
            )

    theme = SynthesisTheme(
        title="Pricing mechanisms",
        dimension="findings",
        statements=[st_cons, st_lim],
        evidence_item_ids=[e1, e2, e3],
        paper_identity_ids=["pi1", "pi2", "pi3"],
        metadata={"statement_ids": list(stmt_envs.values())},
    )
    t_env = ArtifactEnvelope.create(payload=theme, artifact_type="synthesis_theme", producer="test")
    await store.put(t_env)
    syn = LiteratureSynthesis(
        evidence_corpus_id="corp1",
        theme_ids=[t_env.artifact_id],
        statement_ids=list(stmt_envs.values()),
        counts={"themes": 1, "statements": 2},
    )
    syn_env = ArtifactEnvelope.create(
        payload=syn, artifact_type="literature_synthesis", producer="test"
    )
    await store.put(syn_env)

    gap = ResearchGap(
        title="Mechanism linking competition to pricing unexplained",
        gap_type=GapType.mechanism_gap,
        description=(
            "Within the reviewed corpus, few included studies examine the mechanism "
            "through which competition shapes platform pricing."
        ),
        why_it_matters="Needed for analytical modeling",
        supporting_synthesis_statement_ids=[
            stmt_envs["Two studies find pricing increases"],
            stmt_envs["Mechanism linking competition to pricing unexplained"],
        ],
        supporting_evidence_ids=[e1, e2, e3],
        contradiction_statement_ids=[],
        relevant_paper_identity_ids=["pi1", "pi2", "pi3"],
        supporting_papers=3,
        supporting_evidence_items=3,
        strength=GapStrength.strongly_supported,
        status=GapStatus.candidate,
        ranking=GapRankDimension(
            evidence_strength=0.8,
            research_importance=0.9,
            theoretical_relevance=0.9,
            analytical_model_potential=0.9,
            tractability=0.7,
        ),
    )
    gap_env = ArtifactEnvelope.create(payload=gap, artifact_type="research_gap", producer="test")
    await store.put(gap_env)
    for sid in gap.supporting_synthesis_statement_ids + gap.contradiction_statement_ids:
        await store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=sid,
                target_artifact_id=gap_env.artifact_id,
                producer="test",
            )
        )

    analysis = GapAnalysis(
        literature_synthesis_id=syn_env.artifact_id,
        evidence_corpus_id="corp1",
        gap_ids=[gap_env.artifact_id],
        ranked_gap_ids=[gap_env.artifact_id],
    )
    a_env = ArtifactEnvelope.create(payload=analysis, artifact_type="gap_analysis", producer="test")
    await store.put(a_env)

    selection = GapSelection(
        gap_analysis_id=a_env.artifact_id,
        selected_gap_id=gap_env.artifact_id,
        alternative_gap_ids=[],
        selection_rationale="Selected by test",
        status=SelectionStatus.approved,
        autonomy_mode="high",
    )
    sel_env = ArtifactEnvelope.create(
        payload=selection, artifact_type="gap_selection", producer="test"
    )
    await store.put(sel_env)

    return {
        "gap": gap_env.artifact_id,
        "analysis": a_env.artifact_id,
        "selection": sel_env.artifact_id,
        "st_cons": stmt_envs["Two studies find pricing increases"],
        "st_lim": stmt_envs["Mechanism linking competition to pricing unexplained"],
        "e1": e1,
        "e2": e2,
        "e3": e3,
    }


def _candidate_payload(
    name: str, stmts: list[str], evs: list[str], bad_ev: str | None = None, **kw
) -> dict:
    return {
        "name": name,
        "description": f"Mechanism description for {name}",
        "actors": ["platform", "sellers"],
        "strategic_interactions": ["price setting"],
        "information_structure": "sellers observe platform fees",
        "incentives": ["profit maximization"],
        "causal_logic": "Competition raises seller entry, which changes platform pricing.",
        "key_assumptions": ["linear demand"],
        "expected_outcomes": ["lower platform fees"],
        "boundary_conditions": ["two-sided markets"],
        "literature_support_ids": list(stmts) + list(evs),
        "grounding": [
            {
                "element": "platform pricing responds to competition",
                "basis": "literature_supported",
                "source_ids": list(stmts) + list(evs),
            },
            {
                "element": "seller entry mediates the effect",
                "basis": "new_hypothesis",
                "source_ids": [],
            },
            {"element": "linear demand", "basis": "modeling_assumption", "source_ids": []},
        ],
        "analytical_model_potential": {"suitable": True, "domains": ["pricing", "competition"]},
        "evaluation": {
            "gap_alignment": 0.9,
            "theoretical_coherence": 0.8,
            "novelty_within_reviewed_corpus": 0.7,
            "analytical_tractability": 0.8,
            "managerial_economic_relevance": 0.7,
            "is_relevance": 0.9,
        },
        **kw,
    }


def _generation_resp(*cands: dict) -> str:
    return json.dumps({"candidates": list(cands)})


@pytest.mark.asyncio
async def test_generate_multiple_grounded_candidates(tmp_path: pathlib.Path):
    from research_harness.plugins.research.mechanism_generator.plugin import (
        MechanismGeneratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _scenario(store)
    resp = _generation_resp(
        _candidate_payload("Competition pricing mechanism", [ids["st_cons"]], [ids["e1"]]),
        _candidate_payload("Entry mechanism", [ids["st_lim"]], [ids["e2"], ids["e3"]]),
        _candidate_payload("Signaling mechanism", [ids["st_cons"], ids["st_lim"]], [ids["e3"]]),
    )
    router = FakeRouter([resp])
    svc = MechanismGeneratorService(
        model_router=router, artifact_store=store, model_role="reasoning"
    )
    exec_id = await svc.generate(ids["selection"])
    assert router.last_role == "reasoning"

    rec = (await store.get(exec_id)).parse_payload(MechanismAnalysisExecution)
    assert rec.candidates_created == 3
    assert rec.candidates_rejected == 0

    cands = [
        env.parse_payload(MechanismCandidate)
        for env in await store.list(artifact_type="mechanism_candidate")
    ]
    assert len(cands) == 3
    for c in cands:
        assert c.gap_selection_id == ids["selection"]
        assert c.gap_id == ids["gap"]
        assert c.literature_support_papers >= 1
        assert c.literature_support_evidence_items >= 1
        assert c.evaluation is not None
        assert 0.0 <= c.evaluation.composite <= 1.0
        bases = {g.basis for g in c.grounding}
        assert KnowledgeBasis.literature_supported in bases
        assert KnowledgeBasis.new_hypothesis in bases
        assert KnowledgeBasis.modeling_assumption in bases
        # novel hypotheses not presented as facts
        hyp = next(g for g in c.grounding if g.basis == KnowledgeBasis.new_hypothesis)
        assert not hyp.source_ids

    # Deterministic counts match referenced sets
    c0 = cands[0]
    assert c0.literature_support_evidence_items == 1
    assert c0.literature_support_papers == 2  # st_cons -> pi1, pi2

    analyses = [
        env.parse_payload(MechanismAnalysis)
        for env in await store.list(artifact_type="mechanism_analysis")
    ]
    assert len(analyses) == 1
    assert analyses[0].status.value == "generated"
    assert len(analyses[0].candidate_ids) == 3


@pytest.mark.asyncio
async def test_generate_rejects_unsupported_evidence_id(tmp_path: pathlib.Path):
    from research_harness.plugins.research.mechanism_generator.plugin import (
        MechanismGeneratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _scenario(store)
    resp = _generation_resp(
        _candidate_payload("Good mechanism", [ids["st_cons"]], [ids["e1"]]),
        _candidate_payload(
            "Bad mechanism", [ids["st_cons"]], [ids["e1"]], bad_ev="hallucinated-ev"
        ),
    )
    # inject a hallucinated id via literature_support_ids
    bad = _candidate_payload("Bad mechanism", [ids["st_cons"]], [ids["e1"]])
    bad["literature_support_ids"] = [ids["st_cons"], "hallucinated-ev-id"]
    resp = _generation_resp(
        _candidate_payload("Good mechanism", [ids["st_cons"]], [ids["e1"]]),
        bad,
    )
    router = FakeRouter([resp])
    svc = MechanismGeneratorService(model_router=router, artifact_store=store)
    exec_id = await svc.generate(ids["selection"])
    rec = (await store.get(exec_id)).parse_payload(MechanismAnalysisExecution)
    assert rec.candidates_created == 1
    assert rec.candidates_rejected == 1
    assert any("unsupported literature ids" in f["error"] for f in rec.failures)
    cands = [
        env.parse_payload(MechanismCandidate)
        for env in await store.list(artifact_type="mechanism_candidate")
    ]
    assert len(cands) == 1
    assert cands[0].name == "Good mechanism"


@pytest.mark.asyncio
async def test_generate_rejects_literature_element_with_unknown_source(tmp_path: pathlib.Path):
    from research_harness.plugins.research.mechanism_generator.plugin import (
        MechanismGeneratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _scenario(store)
    bad = _candidate_payload("Bad grounding", [ids["st_cons"]], [ids["e1"]])
    bad["grounding"] = [
        {
            "element": "platform pricing responds to competition",
            "basis": "literature_supported",
            "source_ids": ["unknown-artifact-id"],
        }
    ]
    router = FakeRouter([_generation_resp(bad)])
    svc = MechanismGeneratorService(model_router=router, artifact_store=store)
    exec_id = await svc.generate(ids["selection"])
    rec = (await store.get(exec_id)).parse_payload(MechanismAnalysisExecution)
    assert rec.candidates_created == 0
    assert rec.candidates_rejected == 1
    assert any("unknown artifact ids" in f["error"] for f in rec.failures)


@pytest.mark.asyncio
async def test_generate_requires_approved_selection(tmp_path: pathlib.Path):
    from research_harness.plugins.research.mechanism_generator.plugin import (
        MechanismGeneratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _scenario(store)
    sel_env = await store.get(ids["selection"])
    pending = sel_env.parse_payload(GapSelection).model_copy(
        update={"status": SelectionStatus.pending_approval}
    )
    pend_env = ArtifactEnvelope.create(
        payload=pending, artifact_type="gap_selection", producer="test"
    )
    await store.put(pend_env)

    svc = MechanismGeneratorService(model_router=FakeRouter(), artifact_store=store)
    with pytest.raises(ValueError, match="must be approved"):
        await svc.generate(pend_env.artifact_id)


@pytest.mark.asyncio
async def test_generate_idempotent_and_model_role_change(tmp_path: pathlib.Path):
    from research_harness.plugins.research.mechanism_generator.plugin import (
        MechanismGeneratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _scenario(store)
    resp = _generation_resp(
        _candidate_payload("First mechanism", [ids["st_cons"]], [ids["e1"]]),
        _candidate_payload("Second mechanism", [ids["st_lim"]], [ids["e2"]]),
    )
    router = FakeRouter([resp, resp])
    svc = MechanismGeneratorService(
        model_router=router, artifact_store=store, model_role="reasoning"
    )
    first = await svc.generate(ids["selection"])
    second = await svc.generate(ids["selection"])
    assert first == second
    assert router.calls == 1

    # model-role change -> new execution + new candidates
    svc2 = MechanismGeneratorService(
        model_router=router, artifact_store=store, model_role="long_context"
    )
    third = await svc2.generate(ids["selection"])
    assert third != first
    rec = (await store.get(third)).parse_payload(MechanismAnalysisExecution)
    assert rec.generator_role == "long_context"
    assert rec.candidates_created == 2


@pytest.mark.asyncio
async def test_critique_output_and_idempotency(tmp_path: pathlib.Path):
    from research_harness.plugins.research.mechanism_critic.plugin import MechanismCriticService
    from research_harness.plugins.research.mechanism_generator.plugin import (
        MechanismGeneratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _scenario(store)
    gen = MechanismGeneratorService(
        model_router=FakeRouter(
            [
                _generation_resp(
                    _candidate_payload("Target mechanism", [ids["st_cons"]], [ids["e1"]])
                )
            ]
        ),
        artifact_store=store,
    )
    await gen.generate(ids["selection"])
    cand_id = (await store.list(artifact_type="mechanism_candidate"))[0].artifact_id

    crit_resp = json.dumps(
        {
            "overall_assessment": "Plausible but the causal direction is unclear and a key assumption is unsupported.",
            "verdict": "revise",
            "revision_recommendations": ["Clarify causal direction", "Justify linear demand"],
            "issues": [
                {
                    "category": "unclear_causal_direction",
                    "description": "Competition may follow pricing rather than precede it.",
                    "severity": "high",
                    "location": "causal_logic",
                },
                {
                    "category": "unsupported_assumption",
                    "description": "Linear demand is not grounded in the corpus.",
                    "severity": "medium",
                    "location": "key_assumptions",
                },
            ],
        }
    )
    router = FakeRouter([crit_resp])
    svc = MechanismCriticService(model_router=router, artifact_store=store, critic_role="critic")
    crit_id = await svc.critique(cand_id)
    assert router.last_role == "critic"

    crit = (await store.get(crit_id)).parse_payload(MechanismCritique)
    assert crit.verdict.value == "revise"
    assert len(crit.issues) == 2
    assert crit.issues[0].category.value == "unclear_causal_direction"
    assert crit.issues[0].severity.value == "high"
    assert crit.model_role == "critic"

    # idempotent
    assert await svc.critique(cand_id) == crit_id
    assert router.calls == 1

    # critic-role change -> new critique
    svc2 = MechanismCriticService(model_router=router, artifact_store=store, critic_role="fast")
    crit2 = await svc2.critique(cand_id)
    assert crit2 != crit_id
    assert (await store.get(crit2)).parse_payload(MechanismCritique).model_role == "fast"


@pytest.mark.asyncio
async def test_select_revises_and_preserves_original(tmp_path: pathlib.Path):
    from research_harness.plugins.research.mechanism_critic.plugin import MechanismCriticService
    from research_harness.plugins.research.mechanism_generator.plugin import (
        MechanismGeneratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _scenario(store)
    gen = MechanismGeneratorService(
        model_router=FakeRouter(
            [
                _generation_resp(
                    _candidate_payload("Target mechanism", [ids["st_cons"]], [ids["e1"]])
                )
            ]
        ),
        artifact_store=store,
    )
    await gen.generate(ids["selection"])
    cand_id = (await store.list(artifact_type="mechanism_candidate"))[0].artifact_id
    cand_orig = (await store.get(cand_id)).parse_payload(MechanismCandidate)

    crit_resp = json.dumps(
        {
            "overall_assessment": "Causal direction unclear.",
            "verdict": "revise",
            "revision_recommendations": ["Clarify direction"],
            "issues": [
                {
                    "category": "unclear_causal_direction",
                    "description": "Direction unclear.",
                    "severity": "high",
                    "location": "causal_logic",
                }
            ],
        }
    )
    rev_resp = json.dumps(
        {
            "name": "Revised competition pricing mechanism",
            "description": "Revised description addressing the critique.",
            "actors": ["platform", "sellers", "entrants"],
            "strategic_interactions": ["price setting", "entry"],
            "information_structure": "sellers observe platform fees",
            "incentives": ["profit maximization"],
            "causal_logic": "Competition induces seller entry; entry precedes pricing changes.",
            "key_assumptions": ["linear demand"],
            "expected_outcomes": ["lower platform fees"],
            "boundary_conditions": ["two-sided markets"],
            "grounding": [
                {
                    "element": "platform pricing responds to competition",
                    "basis": "literature_supported",
                    "source_ids": [ids["st_cons"], ids["e1"]],
                }
            ],
            "revision_notes": ["Clarified causal direction per critique"],
            "analytical_model_potential": {"suitable": True, "domains": ["pricing"]},
            "evaluation": {
                "gap_alignment": 0.95,
                "theoretical_coherence": 0.9,
                "novelty_within_reviewed_corpus": 0.7,
                "analytical_tractability": 0.85,
                "managerial_economic_relevance": 0.7,
                "is_relevance": 0.9,
            },
        }
    )
    router = FakeRouter([crit_resp, rev_resp])
    svc = MechanismCriticService(
        model_router=router, artifact_store=store, critic_role="critic", revision_role="reasoning"
    )
    sm_id = await svc.select(cand_id)
    assert router.calls == 2  # critique then revision

    sm = (await store.get(sm_id)).parse_payload(SelectedMechanism)
    assert sm.mechanism_candidate_id == cand_id
    assert sm.name == "Revised competition pricing mechanism"
    assert sm.revision_notes
    assert len(sm.critique_ids) == 1
    assert sm.causal_logic != cand_orig.causal_logic
    assert sm.model_role == "reasoning"

    # original candidate untouched
    cand_now = (await store.get(cand_id)).parse_payload(MechanismCandidate)
    assert cand_now.name == cand_orig.name
    assert cand_now.causal_logic == cand_orig.causal_logic

    # idempotent
    assert await svc.select(cand_id) == sm_id

    # analysis aggregate updated: generated -> critiqued -> selected
    analyses = [
        env.parse_payload(MechanismAnalysis)
        for env in await store.list(artifact_type="mechanism_analysis")
    ]
    # latest = leaf of the supersedes chain
    envs = await store.list(artifact_type="mechanism_analysis")
    leaves = []
    for env in envs:
        children = await store.get_children(env.artifact_id)
        if not any(p.relation.value == "supersedes" for p in children):
            leaves.append(env)
    latest = max(leaves, key=lambda e: e.created_at).parse_payload(MechanismAnalysis)
    assert latest.status.value == "selected"
    assert latest.selected_mechanism_id == sm_id
    assert len(latest.critique_ids) == 1
    # supersedes chain exists
    chain = await store.get_children(
        next(env.artifact_id for env in await store.list(artifact_type="mechanism_analysis"))
    )
    assert any(p.relation.value == "supersedes" for p in chain)
    assert len(analyses) == 3


@pytest.mark.asyncio
async def test_select_falls_back_when_revision_model_fails(tmp_path: pathlib.Path):
    from research_harness.plugins.research.mechanism_critic.plugin import MechanismCriticService
    from research_harness.plugins.research.mechanism_generator.plugin import (
        MechanismGeneratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _scenario(store)
    gen = MechanismGeneratorService(
        model_router=FakeRouter(
            [
                _generation_resp(
                    _candidate_payload("Target mechanism", [ids["st_cons"]], [ids["e1"]])
                )
            ]
        ),
        artifact_store=store,
    )
    await gen.generate(ids["selection"])
    cand_id = (await store.list(artifact_type="mechanism_candidate"))[0].artifact_id

    class FlakyRouter(FakeRouter):
        def __init__(self):
            super().__init__(
                [
                    json.dumps(
                        {
                            "overall_assessment": "ok",
                            "verdict": "keep",
                            "revision_recommendations": [],
                            "issues": [],
                        }
                    )
                ]
            )

        async def complete(self, role, request):
            if role == "reasoning":
                raise RuntimeError("revision model down")
            return await super().complete(role, request)

    router = FlakyRouter()
    svc = MechanismCriticService(
        model_router=router, artifact_store=store, critic_role="critic", revision_role="reasoning"
    )
    sm_id = await svc.select(cand_id)
    sm = (await store.get(sm_id)).parse_payload(SelectedMechanism)
    assert sm.name == "Target mechanism"
    assert any("unavailable" in note for note in sm.revision_notes)


@pytest.mark.asyncio
async def test_mechanism_provenance_chain_to_evidence(tmp_path: pathlib.Path):
    from research_harness.plugins.research.mechanism_critic.plugin import MechanismCriticService
    from research_harness.plugins.research.mechanism_generator.plugin import (
        MechanismGeneratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _scenario(store)
    gen = MechanismGeneratorService(
        model_router=FakeRouter(
            [
                _generation_resp(
                    _candidate_payload("Target mechanism", [ids["st_cons"]], [ids["e1"]])
                )
            ]
        ),
        artifact_store=store,
    )
    await gen.generate(ids["selection"])
    cand_id = (await store.list(artifact_type="mechanism_candidate"))[0].artifact_id

    svc = MechanismCriticService(
        model_router=FakeRouter(
            [
                json.dumps(
                    {
                        "overall_assessment": "ok",
                        "verdict": "keep",
                        "issues": [],
                        "revision_recommendations": [],
                    }
                ),
                json.dumps(
                    {
                        "name": "Target mechanism",
                        "description": "Same",
                        "actors": ["platform"],
                        "strategic_interactions": [],
                        "information_structure": None,
                        "incentives": [],
                        "causal_logic": "Same logic",
                        "key_assumptions": [],
                        "expected_outcomes": [],
                        "boundary_conditions": [],
                        "grounding": [],
                        "revision_notes": ["no change"],
                        "analytical_model_potential": {"suitable": True, "domains": ["pricing"]},
                        "evaluation": {
                            "gap_alignment": 0.9,
                            "theoretical_coherence": 0.8,
                            "novelty_within_reviewed_corpus": 0.7,
                            "analytical_tractability": 0.8,
                            "managerial_economic_relevance": 0.7,
                            "is_relevance": 0.9,
                        },
                    }
                ),
            ]
        ),
        artifact_store=store,
    )
    sm_id = await svc.select(cand_id)

    # reopen store to verify durable provenance
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")

    sm_parents = await store2.get_parents(sm_id)
    assert any(p.source_artifact_id == cand_id for p in sm_parents)

    cand_parents = await store2.get_parents(cand_id)
    assert any(p.source_artifact_id == ids["gap"] for p in cand_parents)
    assert any(p.source_artifact_id == ids["st_cons"] for p in cand_parents)
    assert any(p.source_artifact_id == ids["e1"] for p in cand_parents)

    # gap -> statements -> evidence chain exists
    st_parents = await store2.get_parents(ids["st_cons"])
    assert any(p.source_artifact_id == ids["e1"] for p in st_parents)
    await store2.close()
