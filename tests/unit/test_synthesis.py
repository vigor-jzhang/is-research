"""Phase 2G unit tests — synthesis grounding, consensus/contradiction, support metrics,
batch processing, idempotency, provenance. Fake models only, offline."""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evidence import EvidenceCategory, EvidenceItem, Locator
from research_harness.research.schemas.evidence_extraction import EvidenceCorpus
from research_harness.research.schemas.research_profile import PaperResearchProfile, ProfileClaim
from research_harness.research.schemas.synthesis import (
    LiteratureSynthesis,
    SupportType,
    SynthesisExecution,
    SynthesisStatementType,
    SynthesisTheme,
)


class FakeRouter:
    def __init__(self, responses: list[str] | None = None, fail_on_call: int | None = None):
        self.responses = responses or []
        self.fail_on_call = fail_on_call
        self.calls = 0
        self.last_role = None
        self.last_request = None

    async def complete(self, role, request):
        self.calls += 1
        self.last_role = role
        self.last_request = request
        if self.fail_on_call is not None and self.calls == self.fail_on_call:
            raise RuntimeError("model failure")
        idx = min(self.calls - 1, len(self.responses) - 1)
        content = self.responses[idx] if self.responses else "{}"
        return ModelResponse(
            message=Message(role="assistant", content=content),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


def _resp_themes(themes: list[dict]) -> str:
    return json.dumps({"themes": themes})


def _theme(title, dimension, statements) -> dict:
    return {"title": title, "dimension": dimension, "statements": statements}


def _stmt(text, stype, supporting, conflicting=None, confidence=0.9) -> dict:
    s = {
        "statement": text,
        "type": stype,
        "supporting_evidence_ids": supporting,
        "confidence": confidence,
    }
    if conflicting:
        s["conflicting_evidence_ids"] = conflicting
    return s


async def _make_evidence(
    store, pi_id: str, doc_id: str, statement: str, cat: str, pages: list[int]
) -> str:
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


async def _make_profile(store, pi_id: str, doc_id: str, evidence_ids: list[str]) -> str:
    prof = PaperResearchProfile(
        paper_identity_id=pi_id,
        full_text_document_id=doc_id,
        main_findings=[
            ProfileClaim(
                text=(await store.get(eid)).parse_payload(EvidenceItem).statement,
                evidence_item_ids=[eid],
                inference=False,
            )
            for eid in evidence_ids
        ],
        evidence_item_ids=list(evidence_ids),
        model_role="reasoning",
    )
    env = ArtifactEnvelope.create(
        payload=prof, artifact_type="paper_research_profile", producer="test"
    )
    await store.put(env)
    return env.artifact_id


async def _make_corpus(store, profile_ids: list[str], evidence_ids: list[str]) -> str:
    from research_harness.research.schemas.evidence_extraction import EvidenceExtractionExecution

    seed = EvidenceExtractionExecution(full_text_corpus_id="seed")
    s_env = ArtifactEnvelope.create(
        payload=seed, artifact_type="evidence_extraction_execution", producer="test"
    )
    await store.put(s_env)
    corpus = EvidenceCorpus(
        evidence_extraction_execution_id=s_env.artifact_id,
        full_text_corpus_id="c1",
        paper_profile_ids=profile_ids,
        evidence_item_ids=evidence_ids,
        documents_without_evidence=[],
        failed_document_ids=[],
    )
    c_env = ArtifactEnvelope.create(
        payload=corpus, artifact_type="evidence_corpus", producer="test"
    )
    await store.put(c_env)
    return c_env.artifact_id


def test_evidence_id_validation_hallucinated_rejected():
    from research_harness.plugins.literature.synthesis.plugin import LiteratureSynthesizerService

    # Unit-test the private grounding helper via a stub service
    svc = LiteratureSynthesizerService.__new__(LiteratureSynthesizerService)
    svc._store = None  # not needed for _build_statement
    svc._model_role = "reasoning"
    # Build minimal ev/paper maps
    from research_harness.research.schemas.evidence import EvidenceItem

    ev = EvidenceItem(
        statement="real evidence",
        source_artifact_id="doc1",
        category=EvidenceCategory.finding,
        locator=Locator(pages=[1]),
    )
    ev_by_id = {"e1": ev}
    paper_by_evidence = {"e1": "pi1"}
    cand = _StatementCandidateStub("Good statement", "consensus", ["e1"], [])
    stmt = svc._build_statement(cand, ev_by_id, paper_by_evidence)
    assert stmt.papers_supporting == 1
    assert stmt.support_type == SupportType.single_paper

    # Hallucinated supporting id
    bad = _StatementCandidateStub("Bad", "consensus", ["e999"], [])
    with pytest.raises(ValueError, match="hallucinated"):
        svc._build_statement(bad, ev_by_id, paper_by_evidence)

    # Hallucinated conflicting id
    bad2 = _StatementCandidateStub("Bad2", "contradiction", ["e1"], ["e999"])
    with pytest.raises(ValueError, match="hallucinated"):
        svc._build_statement(bad2, ev_by_id, paper_by_evidence)

    # Contradiction without conflicting evidence
    bad3 = _StatementCandidateStub("Bad3", "contradiction", ["e1"], [])
    with pytest.raises(ValueError, match="conflicting_evidence_ids"):
        svc._build_statement(bad3, ev_by_id, paper_by_evidence)

    # Invalid type
    bad4 = _StatementCandidateStub("Bad4", "not_a_type", ["e1"], [])
    with pytest.raises(ValueError, match="invalid synthesis statement type"):
        svc._build_statement(bad4, ev_by_id, paper_by_evidence)


class _StatementCandidateStub:
    def __init__(self, statement, stype, supporting, conflicting):
        self.statement = statement
        self.type = stype
        self.supporting_evidence_ids = supporting
        self.conflicting_evidence_ids = conflicting
        self.confidence = 0.9


@pytest.mark.asyncio
async def test_multi_paper_consensus_and_contradiction(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.synthesis.plugin import LiteratureSynthesizerService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    # 3 papers, 1 evidence each
    e1 = await _make_evidence(store, "pi1", "doc1", "Platform pricing increases", "finding", [1])
    e2 = await _make_evidence(store, "pi2", "doc2", "Platform pricing increases", "finding", [2])
    e3 = await _make_evidence(store, "pi3", "doc3", "Platform pricing decreases", "finding", [1])
    p1 = await _make_profile(store, "pi1", "doc1", [e1])
    p2 = await _make_profile(store, "pi2", "doc2", [e2])
    p3 = await _make_profile(store, "pi3", "doc3", [e3])
    corpus_id = await _make_corpus(store, [p1, p2, p3], [e1, e2, e3])

    resp = _resp_themes(
        [
            _theme(
                "Pricing direction",
                "findings",
                [
                    _stmt("Most studies find pricing increases", "consensus", [e1, e2]),
                    _stmt("One study finds pricing decreases", "contradiction", [e3], [e1, e2]),
                ],
            )
        ]
    )
    router = FakeRouter([resp])
    svc = LiteratureSynthesizerService(
        model_router=router,
        artifact_store=store,
        model_role="reasoning",
        batch_profiles=3,
        max_batches=10,
    )
    exec_id = await svc.run(corpus_id)
    rec = (await store.get(exec_id)).parse_payload(SynthesisExecution)
    assert rec.batches_processed == 1
    assert rec.statements_created == 2
    assert rec.themes_created == 1
    assert router.last_role == "reasoning"

    # Consensus statement: multi-paper, deterministic counts
    stmts = await store.list(artifact_type="synthesis_statement")
    assert len(stmts) == 2
    from research_harness.research.schemas.synthesis import SynthesisStatement

    parsed = [env.parse_payload(SynthesisStatement) for env in stmts]
    consensus = next(s for s in parsed if s.type == SynthesisStatementType.consensus)
    contradiction = next(s for s in parsed if s.type == SynthesisStatementType.contradiction)

    assert consensus.papers_supporting == 2
    assert consensus.evidence_items_supporting == 2
    assert consensus.support_type == SupportType.multi_paper
    assert set(consensus.supporting_paper_identity_ids) == {"pi1", "pi2"}

    # Contradiction: preserves both sides
    assert contradiction.evidence_items_supporting == 1
    assert contradiction.papers_supporting == 1
    assert contradiction.evidence_items_conflicting == 2
    assert contradiction.papers_conflicting == 2
    assert contradiction.type == SynthesisStatementType.contradiction
    assert set(contradiction.conflicting_paper_identity_ids) == {"pi1", "pi2"}
    assert set(contradiction.supporting_paper_identity_ids) == {"pi3"}

    # Theme aggregates
    themes = await store.list(artifact_type="synthesis_theme")
    theme = themes[0].parse_payload(SynthesisTheme)
    assert theme.title == "Pricing direction"
    assert set(theme.evidence_item_ids) == {e1, e2, e3}
    assert set(theme.paper_identity_ids) == {"pi1", "pi2", "pi3"}

    # LiteratureSynthesis + provenance
    synths = await store.list(artifact_type="literature_synthesis")
    syn = synths[0].parse_payload(LiteratureSynthesis)
    assert syn.statement_ids == [env.artifact_id for env in stmts]
    assert len(syn.theme_ids) == 1
    await store.close()


@pytest.mark.asyncio
async def test_single_paper_vs_multi_paper_distinction(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.synthesis.plugin import LiteratureSynthesizerService
    from research_harness.research.schemas.synthesis import SynthesisStatement

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    e1 = await _make_evidence(store, "pi1", "doc1", "Only paper claim", "finding", [1])
    p1 = await _make_profile(store, "pi1", "doc1", [e1])
    corpus_id = await _make_corpus(store, [p1], [e1])

    resp = _resp_themes(
        [
            _theme(
                "Single paper",
                "findings",
                [_stmt("The literature generally shows X", "consensus", [e1])],
            )
        ]
    )
    router = FakeRouter([resp])
    svc = LiteratureSynthesizerService(
        model_router=router, artifact_store=store, model_role="reasoning", batch_profiles=3
    )
    await svc.run(corpus_id)
    stmts = await store.list(artifact_type="synthesis_statement")
    s = stmts[0].parse_payload(SynthesisStatement)
    # One paper must NOT be represented as multi-paper consensus
    assert s.support_type == SupportType.single_paper
    assert s.papers_supporting == 1
    await store.close()


@pytest.mark.asyncio
async def test_partial_batch_failure_preserves_work(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.synthesis.plugin import LiteratureSynthesizerService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    evs = []
    profiles = []
    for i in range(1, 5):
        e = await _make_evidence(
            store, f"pi{i}", f"doc{i}", f"Claim from paper {i}", "finding", [1]
        )
        evs.append(e)
        profiles.append(await _make_profile(store, f"pi{i}", f"doc{i}", [e]))
    corpus_id = await _make_corpus(store, profiles, evs)

    # batch_profiles=2 -> 2 batches; first ok, second raises
    ok = _resp_themes(
        [_theme("Theme A", "findings", [_stmt("A claim", "pattern", [evs[0], evs[1]])])]
    )
    router = FakeRouter([ok], fail_on_call=2)
    svc = LiteratureSynthesizerService(
        model_router=router,
        artifact_store=store,
        model_role="reasoning",
        batch_profiles=2,
        max_batches=10,
    )
    exec_id = await svc.run(corpus_id)
    rec = (await store.get(exec_id)).parse_payload(SynthesisExecution)
    assert rec.batches_processed == 1
    assert rec.batches_failed == 1
    assert rec.statements_created == 1  # from successful batch only
    stmts = await store.list(artifact_type="synthesis_statement")
    assert len(stmts) == 1
    assert any(f.get("batch_index") == 1 for f in rec.failures)
    await store.close()


@pytest.mark.asyncio
async def test_cross_batch_consolidation(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.synthesis.plugin import LiteratureSynthesizerService
    from research_harness.research.schemas.synthesis import SynthesisTheme

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    evs = []
    profiles = []
    for i in range(1, 5):
        e = await _make_evidence(store, f"pi{i}", f"doc{i}", f"Claim {i}", "finding", [1])
        evs.append(e)
        profiles.append(await _make_profile(store, f"pi{i}", f"doc{i}", [e]))
    corpus_id = await _make_corpus(store, profiles, evs)

    b1 = _resp_themes(
        [
            _theme(
                "Same Theme",
                "findings",
                [_stmt("Statement from batch 1", "pattern", [evs[0], evs[1]])],
            )
        ]
    )
    b2 = _resp_themes(
        [
            _theme(
                "Same Theme",
                "findings",
                [_stmt("Statement from batch 2", "pattern", [evs[2], evs[3]])],
            )
        ]
    )
    router = FakeRouter([b1, b2])
    svc = LiteratureSynthesizerService(
        model_router=router,
        artifact_store=store,
        model_role="reasoning",
        batch_profiles=2,
        max_batches=10,
    )
    await svc.run(corpus_id)
    themes = await store.list(artifact_type="synthesis_theme")
    assert len(themes) == 1  # consolidated across batches
    theme = themes[0].parse_payload(SynthesisTheme)
    assert len(theme.statements) == 2
    assert set(theme.evidence_item_ids) == set(evs)
    assert set(theme.paper_identity_ids) == {"pi1", "pi2", "pi3", "pi4"}
    await store.close()


@pytest.mark.asyncio
async def test_deterministic_support_counts_not_model_invented(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.synthesis.plugin import LiteratureSynthesizerService
    from research_harness.research.schemas.synthesis import SynthesisStatement

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    # 3 papers, first paper has 2 evidence items
    e1 = await _make_evidence(store, "pi1", "doc1", "A1", "finding", [1])
    e1b = await _make_evidence(store, "pi1", "doc1", "A2", "finding", [2])
    e2 = await _make_evidence(store, "pi2", "doc2", "B", "finding", [1])
    p1 = await _make_profile(store, "pi1", "doc1", [e1, e1b])
    p2 = await _make_profile(store, "pi2", "doc2", [e2])
    corpus_id = await _make_corpus(store, [p1, p2], [e1, e1b, e2])

    resp = _resp_themes([_theme("T", "findings", [_stmt("S", "pattern", [e1, e1b, e2])])])
    router = FakeRouter([resp])
    svc = LiteratureSynthesizerService(
        model_router=router, artifact_store=store, model_role="reasoning"
    )
    await svc.run(corpus_id)
    stmts = await store.list(artifact_type="synthesis_statement")
    s = stmts[0].parse_payload(SynthesisStatement)
    assert s.evidence_items_supporting == 3
    assert s.papers_supporting == 2  # distinct papers, not evidence count
    assert s.support_type == SupportType.multi_paper
    await store.close()


@pytest.mark.asyncio
async def test_idempotency_and_model_change(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.synthesis.plugin import LiteratureSynthesizerService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    e1 = await _make_evidence(store, "pi1", "doc1", "X", "finding", [1])
    e2 = await _make_evidence(store, "pi2", "doc2", "Y", "finding", [1])
    p1 = await _make_profile(store, "pi1", "doc1", [e1])
    p2 = await _make_profile(store, "pi2", "doc2", [e2])
    corpus_id = await _make_corpus(store, [p1, p2], [e1, e2])

    resp = _resp_themes([_theme("T", "findings", [_stmt("S", "consensus", [e1, e2])])])
    router = FakeRouter([resp])
    svc = LiteratureSynthesizerService(
        model_router=router, artifact_store=store, model_role="reasoning", batch_profiles=3
    )
    exec1 = await svc.run(corpus_id)

    # Same config -> reuse, zero additional model calls
    router2 = FakeRouter([resp])
    svc2 = LiteratureSynthesizerService(
        model_router=router2, artifact_store=store, model_role="reasoning", batch_profiles=3
    )
    exec2 = await svc2.run(corpus_id)
    assert exec1 == exec2
    assert router2.calls == 0

    # Model role change -> new execution
    svc3 = LiteratureSynthesizerService(
        model_router=FakeRouter([resp]), artifact_store=store, model_role="critic", batch_profiles=3
    )
    exec3 = await svc3.run(corpus_id)
    assert exec3 != exec1

    # Batch config change -> new execution
    svc4 = LiteratureSynthesizerService(
        model_router=FakeRouter([resp]),
        artifact_store=store,
        model_role="reasoning",
        batch_profiles=1,
    )
    exec4 = await svc4.run(corpus_id)
    assert exec4 != exec1
    await store.close()


@pytest.mark.asyncio
async def test_provenance_after_reopen(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.synthesis.plugin import LiteratureSynthesizerService
    from research_harness.research.schemas.synthesis import LiteratureSynthesis, SynthesisStatement

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    e1 = await _make_evidence(store, "pi1", "doc1", "X", "finding", [1])
    e2 = await _make_evidence(store, "pi2", "doc2", "Y", "finding", [1])
    p1 = await _make_profile(store, "pi1", "doc1", [e1])
    p2 = await _make_profile(store, "pi2", "doc2", [e2])
    corpus_id = await _make_corpus(store, [p1, p2], [e1, e2])

    resp = _resp_themes([_theme("T", "findings", [_stmt("S", "consensus", [e1, e2])])])
    svc = LiteratureSynthesizerService(
        model_router=FakeRouter([resp]), artifact_store=store, model_role="reasoning"
    )
    exec_id = await svc.run(corpus_id)
    await store.close()

    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")
    stmts = await store2.list(artifact_type="synthesis_statement")
    assert len(stmts) == 1
    s = stmts[0].parse_payload(SynthesisStatement)
    # Statement -> evidence provenance survives reopen
    parents = await store2.get_parents(stmts[0].artifact_id)
    assert any(p.source_artifact_id in (e1, e2) for p in parents)
    # Theme -> statement
    themes = await store2.list(artifact_type="synthesis_theme")
    theme_parents = await store2.get_parents(themes[0].artifact_id)
    assert any(p.source_artifact_id == stmts[0].artifact_id for p in theme_parents)
    # Synthesis -> theme + execution
    synths = await store2.list(artifact_type="literature_synthesis")
    syn = synths[0].parse_payload(LiteratureSynthesis)
    syn_parents = await store2.get_parents(synths[0].artifact_id)
    assert any(p.source_artifact_id == themes[0].artifact_id for p in syn_parents)
    assert any(p.source_artifact_id == exec_id for p in syn_parents)
    assert syn.evidence_corpus_id == corpus_id
    await store2.close()
