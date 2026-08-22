"""Phase 3A offline integration — full chain with fake models and no network:

EvidenceCorpus + LiteratureSynthesis
  -> gap analysis (2 gaps)
  -> gap selection (with autonomy policy, interactive)
  -> 3 mechanism candidates (grounding validated)
  -> independent critique
  -> SelectedMechanism (revised)
  -> provenance chain reaches EvidenceItem
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
from research_harness.research.schemas.evidence_extraction import (
    EvidenceCorpus,
    EvidenceExtractionExecution,
)
from research_harness.research.schemas.gap import GapType, ResearchGap
from research_harness.research.schemas.research_profile import PaperResearchProfile, ProfileClaim
from research_harness.research.schemas.synthesis import (
    LiteratureSynthesis,
    SupportType,
    SynthesisStatement,
    SynthesisStatementType,
    SynthesisTheme,
)


class FakeRouter:
    """Scripted responses per logical role."""

    def __init__(self, by_role: dict[str, list[str]]):
        self.by_role = {k: list(v) for k, v in by_role.items()}
        self.calls: list[str] = []

    async def complete(self, role, request):
        self.calls.append(role)
        pool = self.by_role.get(role) or [""]
        idx = min(self.calls.count(role) - 1, len(pool) - 1)
        return ModelResponse(
            message=Message(role="assistant", content=pool[idx]),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


class FakeAutonomy:
    async def requires_approval(self, checkpoint: str) -> bool:
        return checkpoint == "research_gap"

    async def request_approval(self, request):
        from research_harness.contracts.autonomy import ApprovalDecision

        return ApprovalDecision(
            request_id=request.request_id,
            approved=True,
            reason="integration test approved",
            decided_by="policy:interactive",
        )


@pytest.mark.asyncio
async def test_phase3a_full_chain(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.gap_analyzer.plugin import GapAnalyzerService
    from research_harness.plugins.research.gap_selection.plugin import GapSelectionService
    from research_harness.plugins.research.mechanism_critic.plugin import MechanismCriticService
    from research_harness.plugins.research.mechanism_generator.plugin import (
        MechanismGeneratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")

    # ---- Phase 2 inputs: 4 papers -> evidence -> synthesis -------------------
    async def _evidence(doc_id, statement, pages) -> str:
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

    ev = {
        "p1a": await _evidence("doc1", "Pricing increases with network effects", [3]),
        "p2a": await _evidence("doc2", "Pricing increases in platform markets", [2]),
        "p3a": await _evidence("doc3", "Pricing decreases in competitive platforms", [1]),
        "p4a": await _evidence("doc4", "Mechanism remains unexplained", [5]),
    }
    profiles = {}
    for key, (pi, doc, ev_ids) in {
        "p1": ("pi1", "doc1", ["p1a"]),
        "p2": ("pi2", "doc2", ["p2a"]),
        "p3": ("pi3", "doc3", ["p3a"]),
        "p4": ("pi4", "doc4", ["p4a"]),
    }.items():
        prof = PaperResearchProfile(
            paper_identity_id=pi,
            full_text_document_id=doc,
            main_findings=[
                ProfileClaim(
                    text=(await store.get(ev[ev_id])).parse_payload(EvidenceItem).statement,
                    evidence_item_ids=[ev[ev_id]],
                    inference=False,
                )
                for ev_id in ev_ids
            ],
            evidence_item_ids=[ev[ev_id] for ev_id in ev_ids],
            model_role="reasoning",
        )
        p_env = ArtifactEnvelope.create(
            payload=prof, artifact_type="paper_research_profile", producer="test"
        )
        await store.put(p_env)
        profiles[key] = p_env.artifact_id

    seed = EvidenceExtractionExecution(full_text_corpus_id="seed")
    e_env = ArtifactEnvelope.create(
        payload=seed, artifact_type="evidence_extraction_execution", producer="test"
    )
    await store.put(e_env)
    corpus = EvidenceCorpus(
        evidence_extraction_execution_id=e_env.artifact_id,
        full_text_corpus_id="c1",
        paper_profile_ids=list(profiles.values()),
        evidence_item_ids=list(ev.values()),
        documents_without_evidence=["doc_no_text"],
        failed_document_ids=[],
    )
    c_env = ArtifactEnvelope.create(
        payload=corpus, artifact_type="evidence_corpus", producer="test"
    )
    await store.put(c_env)

    st_cons = SynthesisStatement(
        statement="Two studies find pricing increases",
        type=SynthesisStatementType.consensus,
        supporting_evidence_ids=[ev["p1a"], ev["p2a"]],
        supporting_paper_identity_ids=["pi1", "pi2"],
        papers_supporting=2,
        evidence_items_supporting=2,
        support_type=SupportType.multi_paper,
        confidence=0.85,
    )
    st_contra = SynthesisStatement(
        statement="One study finds pricing decreases",
        type=SynthesisStatementType.contradiction,
        supporting_evidence_ids=[ev["p3a"]],
        conflicting_evidence_ids=[ev["p1a"], ev["p2a"]],
        supporting_paper_identity_ids=["pi3"],
        conflicting_paper_identity_ids=["pi1", "pi2"],
        papers_supporting=1,
        evidence_items_supporting=1,
        papers_conflicting=2,
        evidence_items_conflicting=2,
        support_type=SupportType.single_paper,
        confidence=0.8,
    )
    st_lim = SynthesisStatement(
        statement="Mechanism remains unexplained",
        type=SynthesisStatementType.limitation_pattern,
        supporting_evidence_ids=[ev["p4a"]],
        supporting_paper_identity_ids=["pi4"],
        papers_supporting=1,
        evidence_items_supporting=1,
        support_type=SupportType.single_paper,
        confidence=0.7,
    )
    stmt_ids = {}
    for stmt in (st_cons, st_contra, st_lim):
        s_env = ArtifactEnvelope.create(
            payload=stmt, artifact_type="synthesis_statement", producer="test"
        )
        await store.put(s_env)
        stmt_ids[stmt.statement] = s_env.artifact_id
        for eid in stmt.supporting_evidence_ids + stmt.conflicting_evidence_ids:
            await store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=eid,
                    target_artifact_id=s_env.artifact_id,
                    producer="test",
                )
            )

    theme = SynthesisTheme(
        title="Pricing and mechanisms",
        dimension="findings",
        statements=[st_cons, st_contra, st_lim],
        evidence_item_ids=list(ev.values()),
        paper_identity_ids=["pi1", "pi2", "pi3", "pi4"],
        metadata={"statement_ids": list(stmt_ids.values())},
    )
    t_env = ArtifactEnvelope.create(payload=theme, artifact_type="synthesis_theme", producer="test")
    await store.put(t_env)
    synthesis = LiteratureSynthesis(
        evidence_corpus_id=c_env.artifact_id,
        theme_ids=[t_env.artifact_id],
        statement_ids=list(stmt_ids.values()),
        counts={"themes": 1, "statements": 3},
    )
    syn_env = ArtifactEnvelope.create(
        payload=synthesis, artifact_type="literature_synthesis", producer="test"
    )
    await store.put(syn_env)

    # ---- 1. Gap analysis (Phase 2H service) ---------------------------------
    gap_resp = json.dumps(
        {
            "gaps": [
                {
                    "title": "Conflicting pricing direction in platform markets",
                    "gap_type": "contradiction_gap",
                    "description": "Within the reviewed corpus, two studies report pricing increases while one reports decreases; the reviewed literature provides no reconciliation of the direction.",
                    "why_it_matters": "Direction of platform pricing effect remains unresolved",
                    "supporting_synthesis_statement_ids": [
                        stmt_ids["Two studies find pricing increases"],
                        stmt_ids["One study finds pricing decreases"],
                    ],
                    "supporting_evidence_ids": [ev["p1a"], ev["p2a"], ev["p3a"]],
                    "contradiction_statement_ids": [stmt_ids["One study finds pricing decreases"]],
                    "confidence": 0.85,
                    "evidence_strength": 0.9,
                    "research_importance": 0.9,
                    "theoretical_relevance": 0.8,
                    "analytical_model_potential": 0.9,
                    "tractability": 0.8,
                    "model_domains": ["pricing", "platform behavior", "competition"],
                },
                {
                    "title": "Mechanism linking competition to pricing unexplained",
                    "gap_type": "mechanism_gap",
                    "description": "Few included studies examine the mechanism through which platform competition shapes pricing; the reviewed corpus provides limited evidence on the causal pathway.",
                    "why_it_matters": "Causal mechanism needed for analytical modeling",
                    "supporting_synthesis_statement_ids": [
                        stmt_ids["Mechanism remains unexplained"],
                        stmt_ids["Two studies find pricing increases"],
                    ],
                    "supporting_evidence_ids": [ev["p4a"], ev["p1a"], ev["p2a"]],
                    "contradiction_statement_ids": [stmt_ids["One study finds pricing decreases"]],
                    "confidence": 0.75,
                    "evidence_strength": 0.5,
                    "research_importance": 0.9,
                    "theoretical_relevance": 0.9,
                    "analytical_model_potential": 0.95,
                    "tractability": 0.7,
                    "model_domains": ["platform behavior", "mechanism design"],
                },
            ]
        }
    )
    gap_router = FakeRouter({"reasoning": [gap_resp]})
    gap_svc = GapAnalyzerService(model_router=gap_router, artifact_store=store)
    await gap_svc.run(syn_env.artifact_id, c_env.artifact_id)
    a_env = (await store.list(artifact_type="gap_analysis"))[0]
    a_id = a_env.artifact_id
    gaps = [
        env.parse_payload(ResearchGap) for env in await store.list(artifact_type="research_gap")
    ]
    assert len(gaps) == 2

    # ---- 2. Gap selection (model picks the mechanism gap, not rank #1) -------
    mech_gap_id = next(
        env.artifact_id
        for env in await store.list(artifact_type="research_gap")
        if env.parse_payload(ResearchGap).gap_type == GapType.mechanism_gap
    )
    sel_resp = json.dumps(
        {
            "selected_gap_id": mech_gap_id,
            "evidence_synthesis_basis": "one paper reports the mechanism is unexplained",
            "research_importance": 0.9,
            "theoretical_relevance": 0.95,
            "analytical_model_suitability": 0.95,
            "tractability": 0.75,
            "selection_rationale": "Mechanism development adds the most theoretical value here",
        }
    )
    sel_svc = GapSelectionService(
        model_router=FakeRouter({"reasoning": [sel_resp]}),
        artifact_store=store,
        autonomy_mode="interactive",
        autonomy=FakeAutonomy(),
    )
    sel_id = await sel_svc.select(a_id)
    from research_harness.research.schemas.mechanism import GapSelection, SelectionStatus

    sel = (await store.get(sel_id)).parse_payload(GapSelection)
    assert sel.selected_gap_id == mech_gap_id
    assert sel.status == SelectionStatus.approved
    assert sel.approval_required is True
    assert len(sel.alternative_gap_ids) == 1

    # ---- 3. Mechanism generation (3 candidates) ------------------------------
    gen_resp = json.dumps(
        {
            "candidates": [
                {
                    "name": "Competition-driven fee adjustment",
                    "description": "Platforms adjust seller fees as competitive pressure changes.",
                    "actors": ["platform", "sellers", "entrants"],
                    "strategic_interactions": ["fee setting", "entry"],
                    "information_structure": "sellers observe current fees",
                    "incentives": ["profit maximization"],
                    "causal_logic": "Competition increases entry; entry shifts platform pricing.",
                    "key_assumptions": ["price-taking sellers"],
                    "expected_outcomes": ["lower fees with more competition"],
                    "boundary_conditions": ["two-sided markets"],
                    "literature_support_ids": [stmt_ids["Two studies find pricing increases"]],
                    "grounding": [
                        {
                            "element": "competition relates to pricing",
                            "basis": "literature_supported",
                            "source_ids": [
                                stmt_ids["One study finds pricing decreases"],
                                ev["p3a"],
                            ],
                        },
                        {
                            "element": "entry mediates the effect",
                            "basis": "new_hypothesis",
                            "source_ids": [],
                        },
                    ],
                    "analytical_model_potential": {
                        "suitable": True,
                        "domains": ["pricing", "competition"],
                    },
                    "evaluation": {
                        "gap_alignment": 0.9,
                        "theoretical_coherence": 0.8,
                        "novelty_within_reviewed_corpus": 0.7,
                        "analytical_tractability": 0.8,
                        "managerial_economic_relevance": 0.7,
                        "is_relevance": 0.9,
                    },
                },
                {
                    "name": "Information-driven pricing mechanism",
                    "description": "Platform pricing responds to information asymmetries about demand.",
                    "actors": ["platform", "buyers"],
                    "strategic_interactions": ["signaling"],
                    "information_structure": "platform observes demand signals",
                    "incentives": ["rent extraction"],
                    "causal_logic": "Asymmetric demand information lets platforms price discriminate.",
                    "key_assumptions": ["demand heterogeneity"],
                    "expected_outcomes": ["price dispersion"],
                    "boundary_conditions": ["large platforms"],
                    "literature_support_ids": [ev["p1a"], ev["p2a"]],
                    "grounding": [
                        {
                            "element": "pricing increases with network effects",
                            "basis": "literature_supported",
                            "source_ids": [ev["p1a"], ev["p2a"]],
                        }
                    ],
                    "analytical_model_potential": {
                        "suitable": True,
                        "domains": ["information asymmetry"],
                    },
                    "evaluation": {
                        "gap_alignment": 0.8,
                        "theoretical_coherence": 0.9,
                        "novelty_within_reviewed_corpus": 0.9,
                        "analytical_tractability": 0.9,
                        "managerial_economic_relevance": 0.8,
                        "is_relevance": 0.95,
                    },
                },
                {
                    "name": "Entry-threshold mechanism",
                    "description": "Pricing flips when competitive entry crosses a threshold.",
                    "actors": ["platform", "entrants"],
                    "strategic_interactions": ["entry deterrence"],
                    "information_structure": "entrants observe platform size",
                    "incentives": ["survival"],
                    "causal_logic": "Below a size threshold entrants avoid the market.",
                    "key_assumptions": ["fixed entry costs"],
                    "expected_outcomes": ["threshold pricing pattern"],
                    "boundary_conditions": ["nascent platforms"],
                    "literature_support_ids": [stmt_ids["Mechanism remains unexplained"]],
                    "grounding": [
                        {
                            "element": "mechanism is unexplained",
                            "basis": "literature_supported",
                            "source_ids": [stmt_ids["Mechanism remains unexplained"], ev["p4a"]],
                        },
                        {"element": "entry threshold", "basis": "new_hypothesis", "source_ids": []},
                    ],
                    "analytical_model_potential": {
                        "suitable": True,
                        "domains": ["platform behavior"],
                    },
                    "evaluation": {
                        "gap_alignment": 0.85,
                        "theoretical_coherence": 0.75,
                        "novelty_within_reviewed_corpus": 0.8,
                        "analytical_tractability": 0.7,
                        "managerial_economic_relevance": 0.8,
                        "is_relevance": 0.85,
                    },
                },
            ]
        }
    )
    gen_svc = MechanismGeneratorService(
        model_router=FakeRouter({"reasoning": [gen_resp]}), artifact_store=store
    )
    gen_exec = await gen_svc.generate(sel_id)
    from research_harness.research.schemas.mechanism import (
        MechanismAnalysisExecution,
        MechanismCandidate,
    )

    rec = (await store.get(gen_exec)).parse_payload(MechanismAnalysisExecution)
    assert rec.candidates_created == 3
    assert rec.candidates_rejected == 0
    cands = [
        env.parse_payload(MechanismCandidate)
        for env in await store.list(artifact_type="mechanism_candidate")
    ]
    assert len(cands) == 3
    assert all(c.gap_selection_id == sel_id for c in cands)

    # ---- 4. Independent critique ----------------------------------------------
    crit_resp = json.dumps(
        {
            "overall_assessment": "The direction is plausible but the entry mediation is ungrounded.",
            "verdict": "revise",
            "revision_recommendations": ["ground the mediation claim or relabel as hypothesis"],
            "issues": [
                {
                    "category": "unsupported_assumption",
                    "description": "entry mediation is asserted without corpus support",
                    "severity": "high",
                    "location": "causal_logic",
                },
                {
                    "category": "alternative_explanation",
                    "description": "network effects could explain the pattern",
                    "severity": "medium",
                    "location": "description",
                },
            ],
        }
    )
    rev_resp = json.dumps(
        {
            "name": "Competition-driven fee adjustment (revised)",
            "description": "Platforms adjust seller fees as competitive pressure changes; entry is a candidate mediator requiring empirical confirmation.",
            "actors": ["platform", "sellers", "entrants"],
            "strategic_interactions": ["fee setting", "entry"],
            "information_structure": "sellers observe current fees",
            "incentives": ["profit maximization"],
            "causal_logic": "Competition increases entry; entry is hypothesized to shift platform pricing.",
            "key_assumptions": ["price-taking sellers"],
            "expected_outcomes": ["lower fees with more competition"],
            "boundary_conditions": ["two-sided markets"],
            "grounding": [
                {
                    "element": "competition relates to pricing",
                    "basis": "literature_supported",
                    "source_ids": [stmt_ids["One study finds pricing decreases"], ev["p3a"]],
                },
                {"element": "entry mediates", "basis": "new_hypothesis", "source_ids": []},
            ],
            "revision_notes": ["relabeled mediation as hypothesis per critique"],
            "analytical_model_potential": {"suitable": True, "domains": ["pricing", "competition"]},
            "evaluation": {
                "gap_alignment": 0.9,
                "theoretical_coherence": 0.85,
                "novelty_within_reviewed_corpus": 0.7,
                "analytical_tractability": 0.8,
                "managerial_economic_relevance": 0.7,
                "is_relevance": 0.9,
            },
        }
    )
    critic_svc = MechanismCriticService(
        model_router=FakeRouter({"critic": [crit_resp], "reasoning": [rev_resp]}),
        artifact_store=store,
    )
    cand_id = next(env.artifact_id for env in await store.list(artifact_type="mechanism_candidate"))
    crit_id = await critic_svc.critique(cand_id)
    from research_harness.research.schemas.mechanism import MechanismCritique

    crit = (await store.get(crit_id)).parse_payload(MechanismCritique)
    assert crit.verdict.value == "revise"
    assert any(i.category.value == "unsupported_assumption" for i in crit.issues)

    # ---- 5. Selection / revision ----------------------------------------------
    sm_id = await critic_svc.select(cand_id)
    from research_harness.research.schemas.mechanism import (
        MechanismAnalysis,
        SelectedMechanism,
    )

    sm = (await store.get(sm_id)).parse_payload(SelectedMechanism)
    assert sm.name == "Competition-driven fee adjustment (revised)"
    assert sm.mechanism_candidate_id == cand_id
    assert len(sm.critique_ids) == 1
    assert any("hypothesis" in note for note in sm.revision_notes)
    # novel element stays labeled as hypothesis, not fact
    hyp = next(g for g in sm.grounding if g.basis.value == "new_hypothesis")
    assert "entry mediates" in hyp.element

    # ---- 6. Provenance chain to Phase 2 evidence ------------------------------
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")

    sm_parents = await store2.get_parents(sm_id)
    assert any(p.source_artifact_id == cand_id for p in sm_parents)
    assert any(p.source_artifact_id == crit_id for p in sm_parents)

    cand_parents = await store2.get_parents(cand_id)
    assert any(p.source_artifact_id == mech_gap_id for p in cand_parents)
    assert any(p.source_artifact_id == ev["p3a"] for p in cand_parents)
    assert any(
        p.source_artifact_id == stmt_ids["One study finds pricing decreases"] for p in cand_parents
    )

    # SelectedMechanism -> candidate -> gap -> statement -> evidence
    gap_parents = await store2.get_parents(mech_gap_id)
    assert any(p.source_artifact_id in stmt_ids.values() for p in gap_parents)
    stmt_parents = await store2.get_parents(stmt_ids["One study finds pricing decreases"])
    assert any(p.source_artifact_id == ev["p3a"] for p in stmt_parents)

    # analysis chain: generated -> critiqued -> selected (supersedes)
    analysis_envs = await store2.list(artifact_type="mechanism_analysis")
    assert len(analysis_envs) == 3
    leaves = []
    for env in analysis_envs:
        children = await store2.get_children(env.artifact_id)
        if not any(p.relation.value == "supersedes" for p in children):
            leaves.append(env)
    latest = max(leaves, key=lambda e: e.created_at).parse_payload(MechanismAnalysis)
    assert latest.status.value == "selected"
    assert latest.selected_mechanism_id == sm_id
    await store2.close()
