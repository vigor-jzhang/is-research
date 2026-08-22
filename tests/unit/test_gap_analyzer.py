"""Phase 2H unit tests — gap grounding, sweeping-claim normalization, deterministic
support counts, ranking, idempotency, provenance. Fake models only, offline."""

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


def _resp_gaps(gaps: list[dict]) -> str:
    return json.dumps({"gaps": gaps})


def _gap(title, gap_type, description, **kw) -> dict:
    g = {
        "title": title,
        "gap_type": gap_type,
        "description": description,
        "supporting_synthesis_statement_ids": kw.get("stmts", []),
        "supporting_evidence_ids": kw.get("evs", []),
        "contradiction_statement_ids": kw.get("contra", []),
        "confidence": kw.get("confidence", 0.8),
        "evidence_strength": kw.get("evidence_strength", 0.6),
        "research_importance": kw.get("research_importance", 0.7),
        "theoretical_relevance": kw.get("theoretical_relevance", 0.6),
        "analytical_model_potential": kw.get("analytical_model_potential", 0.5),
        "tractability": kw.get("tractability", 0.7),
        "model_domains": kw.get("domains", []),
    }
    return g


async def _evidence(store, doc_id: str, statement: str, cat: str, pages) -> str:
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


async def _synthesis_env(
    store,
    corpus_id: str,
    statements: list[tuple[str, SynthesisStatementType, list[str], list[str], list[str], int]],
) -> tuple[str, dict[str, str]]:
    """Create a LiteratureSynthesis with themes + persisted statements.
    statements: (text, type, supporting_ev, conflicting_ev, supporting_paper_ids, evidence_supporting)
    Returns (synthesis_env_id, {text: statement_id})
    """
    stmt_id_by_text: dict[str, str] = {}
    theme_statements: dict[str, list[SynthesisStatement]] = {}
    theme_order: list[str] = []
    for text, stype, sup_ev, con_ev, sup_papers, evs in statements:
        stmt = SynthesisStatement(
            statement=text,
            type=stype,
            supporting_evidence_ids=sup_ev,
            conflicting_evidence_ids=con_ev,
            supporting_paper_identity_ids=sup_papers,
            conflicting_paper_identity_ids=sup_papers[: len(con_ev)],
            papers_supporting=len(sup_papers),
            evidence_items_supporting=evs,
            support_type=SupportType.multi_paper
            if len(sup_papers) >= 2
            else SupportType.single_paper,
            confidence=0.9,
        )
        s_env = ArtifactEnvelope.create(
            payload=stmt, artifact_type="synthesis_statement", producer="test"
        )
        await store.put(s_env)
        stmt_id_by_text[text] = s_env.artifact_id
        title = f"Theme {len(theme_order) + 1}"
        if title not in theme_statements:
            theme_order.append(title)
            theme_statements[title] = []
        theme_statements[title].append(stmt)

    theme_ids: list[str] = []
    stmt_ids: list[str] = []
    for title in theme_order:
        stmts = theme_statements[title]
        theme = SynthesisTheme(
            title=title,
            dimension="findings",
            statements=stmts,
            evidence_item_ids=[
                e for s in stmts for e in s.supporting_evidence_ids + s.conflicting_evidence_ids
            ],
            paper_identity_ids=[f"pi{i}" for i in range(1, 4)],
            metadata={"statement_ids": [stmt_id_by_text[s.statement] for s in stmts]},
        )
        t_env = ArtifactEnvelope.create(
            payload=theme, artifact_type="synthesis_theme", producer="test"
        )
        await store.put(t_env)
        theme_ids.append(t_env.artifact_id)
        stmt_ids.extend(stmt_id_by_text[s.statement] for s in stmts)

    synthesis = LiteratureSynthesis(
        evidence_corpus_id=corpus_id,
        theme_ids=theme_ids,
        statement_ids=stmt_ids,
        counts={"themes": len(theme_ids), "statements": len(stmt_ids)},
    )
    s_env = ArtifactEnvelope.create(
        payload=synthesis, artifact_type="literature_synthesis", producer="test"
    )
    await store.put(s_env)
    return s_env.artifact_id, stmt_id_by_text


async def _corpus_env(store, profile_ids: list[str], evidence_ids: list[str]) -> str:
    seed = EvidenceExtractionExecution(full_text_corpus_id="seed")
    e_env = ArtifactEnvelope.create(
        payload=seed, artifact_type="evidence_extraction_execution", producer="test"
    )
    await store.put(e_env)
    corpus = EvidenceCorpus(
        evidence_extraction_execution_id=e_env.artifact_id,
        full_text_corpus_id="c1",
        paper_profile_ids=profile_ids,
        evidence_item_ids=evidence_ids,
        documents_without_evidence=["doc_missing"],
        failed_document_ids=[],
    )
    c_env = ArtifactEnvelope.create(
        payload=corpus, artifact_type="evidence_corpus", producer="test"
    )
    await store.put(c_env)
    return c_env.artifact_id


async def _profile_env(store, pi_id: str, doc_id: str, ev_ids: list[str]) -> str:
    prof = PaperResearchProfile(
        paper_identity_id=pi_id,
        full_text_document_id=doc_id,
        main_findings=[
            ProfileClaim(
                text=(await store.get(eid)).parse_payload(EvidenceItem).statement,
                evidence_item_ids=[eid],
                inference=False,
            )
            for eid in ev_ids
        ],
        evidence_item_ids=list(ev_ids),
        model_role="reasoning",
    )
    p_env = ArtifactEnvelope.create(
        payload=prof, artifact_type="paper_research_profile", producer="test"
    )
    await store.put(p_env)
    return p_env.artifact_id


async def _build_scenario(store):
    """Shared 3-paper scenario: consensus pricing, contradiction, limitation."""
    e1 = await _evidence(store, "doc1", "Pricing increases", "finding", [1])
    e2 = await _evidence(store, "doc2", "Pricing increases", "finding", [2])
    e3 = await _evidence(store, "doc3", "Pricing decreases", "finding", [1])
    e4 = await _evidence(store, "doc3", "Limited to one region", "limitation", [5])
    p1 = await _profile_env(store, "pi1", "doc1", [e1])
    p2 = await _profile_env(store, "pi2", "doc2", [e2])
    p3 = await _profile_env(store, "pi3", "doc3", [e3, e4])
    corpus_id = await _corpus_env(store, [p1, p2, p3], [e1, e2, e3, e4])
    syn_id, stmt_ids = await _synthesis_env(
        store,
        corpus_id,
        [
            (
                "Most studies find pricing increases",
                SynthesisStatementType.consensus,
                [e1, e2],
                [],
                ["pi1", "pi2"],
                2,
            ),
            (
                "One study finds pricing decreases",
                SynthesisStatementType.contradiction,
                [e3],
                [e1, e2],
                ["pi3"],
                1,
            ),
            (
                "Study limited to one region",
                SynthesisStatementType.limitation_pattern,
                [e4],
                [],
                ["pi3"],
                1,
            ),
        ],
    )
    return corpus_id, syn_id, stmt_ids, {"e1": e1, "e2": e2, "e3": e3, "e4": e4}


@pytest.mark.asyncio
async def test_contradiction_and_limitation_gaps(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.gap_analyzer.plugin import GapAnalyzerService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    corpus_id, syn_id, stmt_ids, ev = await _build_scenario(store)
    st_contra = stmt_ids["One study finds pricing decreases"]
    st_lim = stmt_ids["Study limited to one region"]
    st_cons = stmt_ids["Most studies find pricing increases"]

    resp = _resp_gaps(
        [
            _gap(
                "Conflicting pricing findings",
                "contradiction_gap",
                "Within the reviewed corpus, two studies find pricing increases while one finds decreases; the reviewed literature provides no reconciliation.",
                stmts=[st_contra, st_cons],
                evs=[ev["e3"], ev["e1"], ev["e2"]],
                contra=[st_contra],
                domains=["pricing"],
            ),
            _gap(
                "Context-limited evidence",
                "context_gap",
                "Few included studies examine settings beyond one region; the reviewed literature provides limited evidence on other contexts.",
                stmts=[st_lim],
                evs=[ev["e4"]],
            ),
        ]
    )
    router = FakeRouter([resp])
    svc = GapAnalyzerService(model_router=router, artifact_store=store, model_role="reasoning")
    exec_id = await svc.run(syn_id, corpus_id)
    rec = (await store.get(exec_id)).parse_payload(GapAnalysisExecution)
    assert rec.gaps_created == 2
    assert rec.gaps_rejected == 0
    assert router.last_role == "reasoning"

    gaps = [
        env.parse_payload(ResearchGap) for env in await store.list(artifact_type="research_gap")
    ]
    contra_gap = next(g for g in gaps if g.gap_type == GapType.contradiction_gap)
    assert contra_gap.contradiction_statement_ids == [st_contra]
    assert contra_gap.supporting_papers == 3  # pi1, pi2, pi3 from both stmts+evs
    assert contra_gap.supporting_evidence_items == 3
    assert contra_gap.strength == GapStrength.strongly_supported
    assert contra_gap.analytical_model_opportunity is not None
    assert contra_gap.analytical_model_opportunity.suitable is True
    assert "pricing" in contra_gap.analytical_model_opportunity.domains
    # corpus-bounded language preserved
    assert (
        "reviewed corpus" in contra_gap.description
        or "reviewed literature" in contra_gap.description
    )

    ctx_gap = next(g for g in gaps if g.gap_type == GapType.context_gap)
    assert ctx_gap.supporting_papers == 1
    assert ctx_gap.strength == GapStrength.tentative

    # Coverage limitation exposed, not a gap
    analyses = [
        env.parse_payload(GapAnalysis) for env in await store.list(artifact_type="gap_analysis")
    ]
    a = analyses[0]
    assert "doc_missing" in a.coverage_limitations
    assert a.literature_synthesis_id == syn_id
    await store.close()


@pytest.mark.asyncio
async def test_hallucinated_ids_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.gap_analyzer.plugin import GapAnalyzerService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    corpus_id, syn_id, stmt_ids, ev = await _build_scenario(store)

    resp = _resp_gaps(
        [
            _gap(
                "Hallucinated",
                "mechanism_gap",
                "Missing mechanism explanation",
                stmts=[stmt_ids["Most studies find pricing increases"], "bogus-statement-id"],
                evs=[ev["e1"], "bogus-ev-id"],
            ),
            _gap(
                "Valid",
                "mechanism_gap",
                "Within the reviewed corpus, mechanism linking pricing to platform size is unexplored",
                stmts=[stmt_ids["Most studies find pricing increases"]],
                evs=[ev["e1"]],
            ),
        ]
    )
    router = FakeRouter([resp])
    svc = GapAnalyzerService(model_router=router, artifact_store=store, model_role="reasoning")
    exec_id = await svc.run(syn_id, corpus_id)
    rec = (await store.get(exec_id)).parse_payload(GapAnalysisExecution)
    assert rec.gaps_created == 1
    assert rec.gaps_rejected == 1
    assert any("hallucinated" in f.get("error", "") for f in rec.failures)
    gaps = [
        env.parse_payload(ResearchGap) for env in await store.list(artifact_type="research_gap")
    ]
    assert len(gaps) == 1
    assert gaps[0].title == "Valid"
    await store.close()


@pytest.mark.asyncio
async def test_sweeping_claim_normalized(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.gap_analyzer.plugin import (
        GapAnalyzerService,
        _has_sweeping_claim,
        _normalize_sweeping_claim,
    )

    # Unit-level: the normalizer rewrites absolute absence claims
    assert _has_sweeping_claim("No research has studied platform pricing.")
    assert _has_sweeping_claim("no studies exist on this topic")
    assert not _has_sweeping_claim(
        "The reviewed corpus provides limited evidence on platform pricing."
    )
    out = _normalize_sweeping_claim("No research has studied X.")
    assert "no research has studied" not in out.lower()
    assert "reviewed corpus" in out.lower()

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    corpus_id, syn_id, stmt_ids, ev = await _build_scenario(store)
    resp = _resp_gaps(
        [
            _gap(
                "Sweeping",
                "empirical_gap",
                "No research has studied the effect in other regions.",
                stmts=[stmt_ids["Study limited to one region"]],
                evs=[ev["e4"]],
            )
        ]
    )
    router = FakeRouter([resp])
    svc = GapAnalyzerService(model_router=router, artifact_store=store, model_role="reasoning")
    await svc.run(syn_id, corpus_id)
    gap = (await store.list(artifact_type="research_gap"))[0].parse_payload(ResearchGap)
    assert "no research has studied" not in gap.description.lower()
    assert "reviewed corpus" in gap.description.lower()
    await store.close()


@pytest.mark.asyncio
async def test_ranking_deterministic(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.gap_analyzer.plugin import GapAnalyzerService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    corpus_id, syn_id, stmt_ids, ev = await _build_scenario(store)
    resp = _resp_gaps(
        [
            _gap(
                "Lower",
                "mechanism_gap",
                "Within the reviewed corpus, mechanism unexplored",
                stmts=[stmt_ids["Most studies find pricing increases"]],
                evs=[ev["e1"]],
                evidence_strength=0.3,
                research_importance=0.3,
                theoretical_relevance=0.3,
                analytical_model_potential=0.3,
                tractability=0.3,
            ),
            _gap(
                "Higher",
                "contradiction_gap",
                "Within the reviewed corpus, conflicting pricing findings",
                stmts=[
                    stmt_ids["One study finds pricing decreases"],
                    stmt_ids["Most studies find pricing increases"],
                ],
                evs=[ev["e3"]],
                contra=[stmt_ids["One study finds pricing decreases"]],
                evidence_strength=0.9,
                research_importance=0.9,
                theoretical_relevance=0.9,
                analytical_model_potential=0.9,
                tractability=0.9,
            ),
        ]
    )
    svc = GapAnalyzerService(
        model_router=FakeRouter([resp]), artifact_store=store, model_role="reasoning"
    )
    await svc.run(syn_id, corpus_id)
    analyses = [
        env.parse_payload(GapAnalysis) for env in await store.list(artifact_type="gap_analysis")
    ]
    a = analyses[0]
    gaps = {
        g.artifact_id: g.parse_payload(ResearchGap)
        for g in await store.list(artifact_type="research_gap")
    }
    ranked = a.ranked_gap_ids
    assert len(ranked) == 2
    # Highest composite first
    assert gaps[ranked[0]].title == "Higher"
    assert gaps[ranked[1]].title == "Lower"
    # Raw scores kept separate
    high = gaps[ranked[0]]
    assert high.ranking.evidence_strength == 0.9
    assert high.ranking.research_importance == 0.9
    assert high.ranking.composite == pytest.approx(0.9)
    await store.close()


@pytest.mark.asyncio
async def test_partial_model_failure(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.gap_analyzer.plugin import GapAnalyzerService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    corpus_id, syn_id, stmt_ids, ev = await _build_scenario(store)
    svc = GapAnalyzerService(
        model_router=FakeRouter([], fail=True), artifact_store=store, model_role="reasoning"
    )
    exec_id = await svc.run(syn_id, corpus_id)
    rec = (await store.get(exec_id)).parse_payload(GapAnalysisExecution)
    assert rec.gaps_created == 0
    assert any("model call failed" in f.get("error", "") for f in rec.failures)
    assert await store.list(artifact_type="research_gap") == []
    await store.close()


@pytest.mark.asyncio
async def test_idempotency_and_model_change(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.gap_analyzer.plugin import GapAnalyzerService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    corpus_id, syn_id, stmt_ids, ev = await _build_scenario(store)
    resp = _resp_gaps(
        [
            _gap(
                "Gap A",
                "mechanism_gap",
                "Within the reviewed corpus, mechanism unexplored",
                stmts=[stmt_ids["Most studies find pricing increases"]],
                evs=[ev["e1"]],
            )
        ]
    )
    svc = GapAnalyzerService(
        model_router=FakeRouter([resp]), artifact_store=store, model_role="reasoning"
    )
    exec1 = await svc.run(syn_id, corpus_id)

    router2 = FakeRouter([resp])
    svc2 = GapAnalyzerService(model_router=router2, artifact_store=store, model_role="reasoning")
    exec2 = await svc2.run(syn_id, corpus_id)
    assert exec1 == exec2
    assert router2.calls == 0

    svc3 = GapAnalyzerService(
        model_router=FakeRouter([resp]), artifact_store=store, model_role="critic"
    )
    exec3 = await svc3.run(syn_id, corpus_id)
    assert exec3 != exec1
    await store.close()


@pytest.mark.asyncio
async def test_provenance_after_reopen(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.gap_analyzer.plugin import GapAnalyzerService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    corpus_id, syn_id, stmt_ids, ev = await _build_scenario(store)
    st_contra = stmt_ids["One study finds pricing decreases"]
    resp = _resp_gaps(
        [
            _gap(
                "Contradiction gap",
                "contradiction_gap",
                "Within the reviewed corpus, conflicting pricing findings persist",
                stmts=[st_contra],
                evs=[ev["e3"]],
                contra=[st_contra],
            )
        ]
    )
    svc = GapAnalyzerService(
        model_router=FakeRouter([resp]), artifact_store=store, model_role="reasoning"
    )
    exec_id = await svc.run(syn_id, corpus_id)
    await store.close()

    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")
    gaps = await store2.list(artifact_type="research_gap")
    assert len(gaps) == 1
    gap = gaps[0].parse_payload(ResearchGap)
    parents = await store2.get_parents(gaps[0].artifact_id)
    # gap derived_from synthesis statement + evidence
    assert any(p.source_artifact_id == st_contra for p in parents)
    assert any(p.source_artifact_id == ev["e3"] for p in parents)
    # analysis derived_from gap
    analyses = await store2.list(artifact_type="gap_analysis")
    a_parents = await store2.get_parents(analyses[0].artifact_id)
    assert any(p.source_artifact_id == gaps[0].artifact_id for p in a_parents)
    assert any(p.source_artifact_id == exec_id for p in a_parents)
    assert gap.relevant_paper_identity_ids  # papers mapped
    await store2.close()
