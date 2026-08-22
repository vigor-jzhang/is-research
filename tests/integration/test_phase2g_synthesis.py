"""Phase 2G offline integration — EvidenceCorpus -> 3+ profiles -> fake synthesis model
-> themes (consensus + contradiction) -> LiteratureSynthesis. No network, no keys."""

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
from research_harness.research.schemas.research_profile import PaperResearchProfile, ProfileClaim
from research_harness.research.schemas.synthesis import (
    LiteratureSynthesis,
    SupportType,
    SynthesisExecution,
    SynthesisStatement,
    SynthesisStatementType,
    SynthesisTheme,
)


class FakeRouter:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0
        self.last_role = None

    async def complete(self, role, request):
        self.calls += 1
        self.last_role = role
        idx = min(self.calls - 1, len(self.responses) - 1)
        return ModelResponse(
            message=Message(role="assistant", content=self.responses[idx]),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


def _resp(themes: list[dict]) -> str:
    return json.dumps({"themes": themes})


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


async def _profile(store, pi_id: str, doc_id: str, ev_ids: list[str], sections: dict) -> str:
    claims = [
        ProfileClaim(
            text=(await store.get(eid)).parse_payload(EvidenceItem).statement,
            evidence_item_ids=[eid],
            inference=False,
        )
        for eid in ev_ids
    ]
    prof = PaperResearchProfile(
        paper_identity_id=pi_id,
        full_text_document_id=doc_id,
        main_findings=sections.get("findings", claims),
        theories=sections.get("theories", []),
        mechanisms=sections.get("mechanisms", []),
        methodology=sections.get("methodology", []),
        evidence_item_ids=list(ev_ids),
        model_role="reasoning",
    )
    env = ArtifactEnvelope.create(
        payload=prof, artifact_type="paper_research_profile", producer="test"
    )
    await store.put(env)
    return env.artifact_id


@pytest.mark.asyncio
async def test_phase2g_synthesis_integration(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.synthesis.plugin import LiteratureSynthesizerService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")

    # 4 papers with evidence across dimensions
    ev = {}
    # Paper 1: platform pricing theory + finding
    ev["p1a"] = await _evidence(
        store, "doc1", "Network effects raise platform value", "theory", [1]
    )
    ev["p1b"] = await _evidence(
        store, "doc1", "Pricing increased in our platform study", "finding", [3]
    )
    # Paper 2: same theory + similar finding
    ev["p2a"] = await _evidence(
        store, "doc2", "Network effects raise platform value", "theory", [2]
    )
    ev["p2b"] = await _evidence(
        store, "doc2", "Pricing increased in our platform study", "finding", [4]
    )
    # Paper 3: conflicting finding
    ev["p3a"] = await _evidence(
        store, "doc3", "Pricing decreased in our platform study", "finding", [1]
    )
    # Paper 4: method + boundary
    ev["p4a"] = await _evidence(store, "doc4", "Used regression on panel data", "method", [2])
    ev["p4b"] = await _evidence(
        store, "doc4", "Effect only holds for large platforms", "boundary_condition", [5]
    )

    profiles = {
        "p1": await _profile(store, "pi1", "doc1", [ev["p1a"], ev["p1b"]], {}),
        "p2": await _profile(store, "pi2", "doc2", [ev["p2a"], ev["p2b"]], {}),
        "p3": await _profile(store, "pi3", "doc3", [ev["p3a"]], {}),
        "p4": await _profile(store, "pi4", "doc4", [ev["p4a"], ev["p4b"]], {}),
    }

    seed = EvidenceExtractionExecution(full_text_corpus_id="seed")
    s_env = ArtifactEnvelope.create(
        payload=seed, artifact_type="evidence_extraction_execution", producer="test"
    )
    await store.put(s_env)
    corpus = EvidenceCorpus(
        evidence_extraction_execution_id=s_env.artifact_id,
        full_text_corpus_id="c1",
        paper_profile_ids=list(profiles.values()),
        evidence_item_ids=list(ev.values()),
        documents_without_evidence=[],
        failed_document_ids=[],
    )
    c_env = ArtifactEnvelope.create(
        payload=corpus, artifact_type="evidence_corpus", producer="test"
    )
    await store.put(c_env)

    # Fake model: batch 1 (profiles 1-2... actually batch_profiles=3 -> batches [p1..p3], [p4])
    b1 = _resp(
        [
            {
                "title": "Network effects",
                "dimension": "theories",
                "statements": [
                    {
                        "statement": "Two papers apply network-effects theory to platforms",
                        "type": "theoretical_pattern",
                        "supporting_evidence_ids": [ev["p1a"], ev["p2a"]],
                        "confidence": 0.9,
                    }
                ],
            },
            {
                "title": "Pricing direction",
                "dimension": "findings",
                "statements": [
                    {
                        "statement": "Two studies find pricing increases",
                        "type": "consensus",
                        "supporting_evidence_ids": [ev["p1b"], ev["p2b"]],
                        "confidence": 0.85,
                    },
                    {
                        "statement": "One study finds pricing decreases",
                        "type": "contradiction",
                        "supporting_evidence_ids": [ev["p3a"]],
                        "conflicting_evidence_ids": [ev["p1b"], ev["p2b"]],
                        "confidence": 0.8,
                    },
                ],
            },
        ]
    )
    b2 = _resp(
        [
            {
                "title": "Methods and scope",
                "dimension": "methods",
                "statements": [
                    {
                        "statement": "Panel-data regression is used",
                        "type": "methodological_pattern",
                        "supporting_evidence_ids": [ev["p4a"]],
                        "confidence": 0.9,
                    },
                    {
                        "statement": "Effects limited to large platforms",
                        "type": "boundary_condition",
                        "supporting_evidence_ids": [ev["p4b"]],
                        "confidence": 0.85,
                    },
                ],
            }
        ]
    )
    router = FakeRouter([b1, b2])
    svc = LiteratureSynthesizerService(
        model_router=router,
        artifact_store=store,
        model_role="reasoning",
        batch_profiles=3,
        max_batches=10,
    )
    exec_id = await svc.run(c_env.artifact_id)
    assert router.last_role == "reasoning"

    rec = (await store.get(exec_id)).parse_payload(SynthesisExecution)
    assert rec.profiles_processed == 4
    assert rec.batches_processed == 2
    assert rec.themes_created == 3
    assert rec.statements_created == 5

    # Statements: consensus multi-paper, contradiction preserves both sides
    stmts = [
        env.parse_payload(SynthesisStatement)
        for env in await store.list(artifact_type="synthesis_statement")
    ]
    assert len(stmts) == 5
    consensus = next(s for s in stmts if s.type == SynthesisStatementType.consensus)
    assert consensus.support_type == SupportType.multi_paper
    assert consensus.papers_supporting == 2
    assert consensus.evidence_items_supporting == 2
    assert set(consensus.supporting_paper_identity_ids) == {"pi1", "pi2"}
    contradiction = next(s for s in stmts if s.type == SynthesisStatementType.contradiction)
    assert contradiction.evidence_items_conflicting == 2
    assert contradiction.papers_conflicting == 2
    assert set(contradiction.supporting_paper_identity_ids) == {"pi3"}

    # Theme with contradiction holds evidence from both sides
    themes = [
        env.parse_payload(SynthesisTheme)
        for env in await store.list(artifact_type="synthesis_theme")
    ]
    pricing_theme = next(t for t in themes if t.title == "Pricing direction")
    assert {ev["p3a"], ev["p1b"], ev["p2b"]} <= set(pricing_theme.evidence_item_ids)
    assert set(pricing_theme.paper_identity_ids) == {"pi1", "pi2", "pi3"}

    # Synthesis artifact + counts
    synths = [
        env.parse_payload(LiteratureSynthesis)
        for env in await store.list(artifact_type="literature_synthesis")
    ]
    syn = synths[0]
    assert syn.evidence_corpus_id == c_env.artifact_id
    assert len(syn.theme_ids) == 3
    assert len(syn.statement_ids) == 5
    assert syn.counts["statements_rejected"] == 0

    # Single-paper statements remain single_paper
    method_stmt = next(s for s in stmts if s.type == SynthesisStatementType.methodological_pattern)
    assert method_stmt.support_type == SupportType.single_paper

    # Provenance after reopen: statement -> evidence; synthesis -> theme -> statement
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")
    stmt_envs = await store2.list(artifact_type="synthesis_statement")
    for env in stmt_envs:
        parents = await store2.get_parents(env.artifact_id)
        assert any(p.relation.value == "derived_from" for p in parents)
    theme_envs = await store2.list(artifact_type="synthesis_theme")
    t_parents = await store2.get_parents(theme_envs[0].artifact_id)
    assert any(p.source_artifact_id in [e.artifact_id for e in stmt_envs] for p in t_parents)
    syn_envs = await store2.list(artifact_type="literature_synthesis")
    syn_parents = await store2.get_parents(syn_envs[0].artifact_id)
    assert any(p.source_artifact_id == exec_id for p in syn_parents)
    await store2.close()
