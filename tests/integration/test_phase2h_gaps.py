"""Phase 2H offline integration — LiteratureSynthesis + EvidenceCorpus -> gap analysis
with contradiction gap and mechanism/context gap. Fake model, no network."""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evidence import EvidenceCategory, EvidenceItem, Locator
from research_harness.research.schemas.evidence_extraction import (
    EvidenceCorpus,
    EvidenceExtractionExecution,
)
from research_harness.research.schemas.gap import (
    GapAnalysis,
    GapAnalysisExecution,
    GapStrength,
    GapType,
    ResearchGap,
)
from research_harness.research.schemas.research_profile import PaperResearchProfile, ProfileClaim
from research_harness.research.schemas.synthesis import (
    LiteratureSynthesis,
    SupportType,
    SynthesisStatement,
    SynthesisStatementType,
    SynthesisTheme,
)


class FakeRouter:
    def __init__(self, content: str):
        self.content = content
        self.calls = 0
        self.last_role = None

    async def complete(self, role, request):
        self.calls += 1
        self.last_role = role
        return ModelResponse(
            message=Message(role="assistant", content=self.content),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


async def _evidence(store, doc_id, statement, cat, pages) -> str:
    ev = EvidenceItem(
        statement=statement,
        source_artifact_id=doc_id,
        category=EvidenceCategory(cat),
        locator=Locator(page=pages[0], pages=pages),
        extraction_method="model-assisted",
        confidence=0.9,
    )
    env = ArtifactEnvelope.create(payload=ev, artifact_type="evidence_item", producer="test")
    await store.put(env)
    return env.artifact_id


@pytest.mark.asyncio
async def test_phase2h_gap_analysis_integration(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.gap_analyzer.plugin import GapAnalyzerService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")

    # 4 papers: 2 support pricing increase, 1 decrease (contradiction), 1 boundary
    ev = {
        "p1a": await _evidence(
            store, "doc1", "Pricing increases with network effects", "finding", [3]
        ),
        "p2a": await _evidence(
            store, "doc2", "Pricing increases in platform markets", "finding", [2]
        ),
        "p3a": await _evidence(
            store, "doc3", "Pricing decreases in competitive platforms", "finding", [1]
        ),
        "p4a": await _evidence(
            store, "doc4", "Effect only holds for large platforms", "boundary_condition", [4]
        ),
        "p4b": await _evidence(store, "doc4", "Mechanism remains unexplained", "limitation", [5]),
    }

    profiles = {}
    for key, (pi, doc, ev_ids) in {
        "p1": ("pi1", "doc1", ["p1a"]),
        "p2": ("pi2", "doc2", ["p2a"]),
        "p3": ("pi3", "doc3", ["p3a"]),
        "p4": ("pi4", "doc4", ["p4a", "p4b"]),
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

    # Synthesis statements + theme
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
    st_bound = SynthesisStatement(
        statement="Effect limited to large platforms; mechanism unexplained",
        type=SynthesisStatementType.boundary_condition,
        supporting_evidence_ids=[ev["p4a"], ev["p4b"]],
        supporting_paper_identity_ids=["pi4"],
        papers_supporting=1,
        evidence_items_supporting=2,
        support_type=SupportType.single_paper,
        confidence=0.75,
    )
    stmt_ids = {}
    for stmt in (st_cons, st_contra, st_bound):
        s_env = ArtifactEnvelope.create(
            payload=stmt, artifact_type="synthesis_statement", producer="test"
        )
        await store.put(s_env)
        stmt_ids[stmt.statement] = s_env.artifact_id

    theme = SynthesisTheme(
        title="Pricing and mechanisms",
        dimension="findings",
        statements=[st_cons, st_contra, st_bound],
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

    # Fake model returns 2 gaps: contradiction + mechanism
    resp = json.dumps(
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
                    "title": "Mechanism linking platform size to pricing unexplained",
                    "gap_type": "mechanism_gap",
                    "description": "Few included studies examine the mechanism through which platform size affects pricing; the reviewed corpus provides limited evidence on the causal pathway.",
                    "why_it_matters": "Causal mechanism needed for analytical modeling",
                    "supporting_synthesis_statement_ids": [
                        stmt_ids["Effect limited to large platforms; mechanism unexplained"]
                    ],
                    "supporting_evidence_ids": [ev["p4a"], ev["p4b"]],
                    "confidence": 0.75,
                    "evidence_strength": 0.6,
                    "research_importance": 0.9,
                    "theoretical_relevance": 0.9,
                    "analytical_model_potential": 0.9,
                    "tractability": 0.7,
                    "model_domains": ["platform behavior", "mechanism design"],
                },
            ]
        }
    )
    router = FakeRouter(resp)
    svc = GapAnalyzerService(model_router=router, artifact_store=store, model_role="reasoning")
    exec_id = await svc.run(syn_env.artifact_id, c_env.artifact_id, research_question_id="rq1")
    assert router.last_role == "reasoning"

    rec = (await store.get(exec_id)).parse_payload(GapAnalysisExecution)
    assert rec.themes_processed == 1
    assert rec.statements_processed == 3
    assert rec.gaps_created == 2
    assert rec.gaps_rejected == 0
    assert rec.research_question_id == "rq1"

    gaps = [
        env.parse_payload(ResearchGap) for env in await store.list(artifact_type="research_gap")
    ]
    assert len(gaps) == 2
    contra = next(g for g in gaps if g.gap_type == GapType.contradiction_gap)
    mech = next(g for g in gaps if g.gap_type == GapType.mechanism_gap)

    # Contradiction gap: both sides preserved, deterministic counts
    assert set(contra.contradiction_statement_ids) == {
        stmt_ids["One study finds pricing decreases"]
    }
    assert contra.supporting_papers == 3
    assert contra.supporting_evidence_items == 3
    assert contra.contradicting_papers == 2
    assert contra.strength == GapStrength.strongly_supported
    assert set(contra.relevant_paper_identity_ids) >= {"pi1", "pi2", "pi3"}

    # Mechanism gap
    assert mech.supporting_papers == 1
    assert mech.strength == GapStrength.tentative
    assert "reviewed corpus" in mech.description

    # Ranking: contradiction gap higher composite
    analyses = [
        env.parse_payload(GapAnalysis) for env in await store.list(artifact_type="gap_analysis")
    ]
    a = analyses[0]
    assert len(a.ranked_gap_ids) == 2
    assert a.ranked_gap_ids[0] == next(
        env.artifact_id
        for env in await store.list(artifact_type="research_gap")
        if env.parse_payload(ResearchGap).gap_type == GapType.contradiction_gap
    )
    assert "doc_no_text" in a.coverage_limitations
    assert a.literature_synthesis_id == syn_env.artifact_id

    # Provenance after reopen
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")
    gap_envs = await store2.list(artifact_type="research_gap")
    for env in gap_envs:
        parents = await store2.get_parents(env.artifact_id)
        assert any(p.source_artifact_id in stmt_ids.values() for p in parents)
        assert any(p.source_artifact_id in ev.values() for p in parents)
    a_envs2 = await store2.list(artifact_type="gap_analysis")
    a_parents = await store2.get_parents(a_envs2[0].artifact_id)
    assert any(p.source_artifact_id == exec_id for p in a_parents)
    assert any(p.source_artifact_id == syn_env.artifact_id for p in a_parents)
    await store2.close()
