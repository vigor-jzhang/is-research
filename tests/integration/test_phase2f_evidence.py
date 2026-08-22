"""Phase 2F offline integration — FullTextCorpus -> 2 docs -> fake model -> EvidenceItems -> Profiles -> EvidenceCorpus.

No network, no keys, no LLM.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.plugins.storage.blobs_filesystem.plugin import FilesystemBlobStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.document_acquisition import (
    AcquisitionStatus,
    DocumentAcquisition,
)
from research_harness.research.schemas.evidence import EvidenceItem
from research_harness.research.schemas.evidence_extraction import (
    EvidenceCorpus,
    EvidenceExtractionExecution,
)
from research_harness.research.schemas.full_text import (
    FullTextCorpus,
    FullTextDocument,
    TextStatus,
)
from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
from research_harness.research.schemas.paper import PaperRecord
from research_harness.research.schemas.research_profile import PaperResearchProfile


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


def _resp(category, statement, pages, confidence=0.9):
    return json.dumps(
        {
            "items": [
                {
                    "category": category,
                    "statement": statement,
                    "page_numbers": pages,
                    "confidence": confidence,
                }
            ]
        }
    )


async def _make_full_doc(
    store, blobs, pi_id: str, pages_texts: list[str], status=TextStatus.extracted
) -> str:
    pdf_blob = await blobs.put_bytes(b"%PDF- x", media_type="application/pdf")
    acq = DocumentAcquisition(
        paper_identity_id=pi_id,
        document_location_id=None,
        status=AcquisitionStatus.downloaded,
        blob=pdf_blob,
        sha256=pdf_blob.digest,
        size_bytes=pdf_blob.size_bytes,
        media_type="application/pdf",
        source_type="http",
    )
    a_env = ArtifactEnvelope.create(
        payload=acq, artifact_type="document_acquisition", producer="test"
    )
    await store.put(a_env)

    pages = [{"page": i + 1, "text": t} for i, t in enumerate(pages_texts)]
    blob_bytes = json.dumps({"schema_version": 1, "pages": pages}, sort_keys=True).encode()
    text_blob = await blobs.put_bytes(blob_bytes, media_type="application/json")

    doc = FullTextDocument(
        paper_identity_id=pi_id,
        document_acquisition_id=a_env.artifact_id,
        source_blob=pdf_blob,
        text_blob=text_blob,
        extractor="documents.extractor.pypdf",
        extractor_version="0.1.0",
        page_count=len(pages),
        pages_with_text=len(pages),
        character_count=sum(len(t) for t in pages_texts),
        text_status=status,
        quality_metrics={},
    )
    d_env = ArtifactEnvelope.create(
        payload=doc, artifact_type="full_text_document", producer="test"
    )
    await store.put(d_env)
    # Provenance doc -> acquisition -> identity
    from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation

    await store.add_provenance(
        ProvenanceLink(
            relation=ProvenanceRelation.derived_from,
            source_artifact_id=a_env.artifact_id,
            target_artifact_id=d_env.artifact_id,
            producer="test",
        )
    )
    await store.add_provenance(
        ProvenanceLink(
            relation=ProvenanceRelation.derived_from,
            source_artifact_id=pi_id,
            target_artifact_id=d_env.artifact_id,
            producer="test",
        )
    )
    return d_env.artifact_id


@pytest.mark.asyncio
async def test_phase2f_evidence_integration(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractorService,
    )
    from research_harness.plugins.literature.evidence_orchestrator.plugin import (
        EvidenceOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")

    # Two PaperIdentities with PaperRecords (provenance chain to ResearchQuestion)
    p1 = PaperRecord(title="Paper One")
    p1_env = ArtifactEnvelope.create(payload=p1, artifact_type="paper_record", producer="test")
    await store.put(p1_env)
    pi1 = PaperIdentity(
        member_paper_artifact_ids=[p1_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    pi1_env = ArtifactEnvelope.create(payload=pi1, artifact_type="paper_identity", producer="test")
    await store.put(pi1_env)

    p2 = PaperRecord(title="Paper Two")
    p2_env = ArtifactEnvelope.create(payload=p2, artifact_type="paper_record", producer="test")
    await store.put(p2_env)
    pi2 = PaperIdentity(
        member_paper_artifact_ids=[p2_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    pi2_env = ArtifactEnvelope.create(payload=pi2, artifact_type="paper_identity", producer="test")
    await store.put(pi2_env)

    # Two FullTextDocuments (one 5 pages, one 3 pages)
    d1 = await _make_full_doc(
        store,
        blobs,
        pi1_env.artifact_id,
        [
            "Intro page one",
            "Theory page two",
            "Method page three",
            "Results page four",
            "Limits page five",
        ],
    )
    d2 = await _make_full_doc(
        store,
        blobs,
        pi2_env.artifact_id,
        ["Intro two", "Data two", "Findings two"],
    )

    # FullTextCorpus
    exec_rec = EvidenceExtractionExecution(full_text_corpus_id="seed")
    e_env = ArtifactEnvelope.create(
        payload=exec_rec, artifact_type="evidence_extraction_execution", producer="test"
    )
    await store.put(e_env)
    corpus = FullTextCorpus(
        document_acquisition_execution_id=e_env.artifact_id,
        screened_literature_set_id="set1",
        available_document_ids=[d1, d2],
        unavailable_identity_ids=[],
        restricted_identity_ids=[],
        failed_identity_ids=[],
    )
    c_env = ArtifactEnvelope.create(
        payload=corpus, artifact_type="full_text_corpus", producer="test"
    )
    await store.put(c_env)

    # Fake structured model: 3 chunks for doc1 (2ppc) + 2 chunks for doc2
    responses = [
        _resp("research_question", "RQ: how platforms affect pricing", [1]),
        _resp("theory", "Theory of network effects", [3, 4]),
        _resp("finding", "Pricing decreases by 10 percent", [5]),
        _resp("method", "Used regression analysis", [1]),
        _resp("finding", "Data shows adoption growth", [3]),
    ]
    router = FakeRouter(responses)
    extractor = EvidenceExtractorService(
        model_router=router, artifact_store=store, blob_store=blobs, model_role="reasoning"
    )
    orch = EvidenceOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        extractor=extractor,
        model_role="reasoning",
        pages_per_chunk=2,
        max_chunks_per_document=10,
        max_model_calls=500,
    )
    exec_id = await orch.run(c_env.artifact_id)
    assert router.last_role == "reasoning"

    rec = (await store.get(exec_id)).parse_payload(EvidenceExtractionExecution)
    assert rec.documents_attempted == 2
    assert rec.documents_completed == 2
    assert rec.evidence_items_created == 5
    assert rec.profiles_created == 2
    assert rec.chunks_processed == 5

    # EvidenceItems all grounded
    items = await store.list(artifact_type="evidence_item")
    assert len(items) == 5
    for env in items:
        ev = env.parse_payload(EvidenceItem)
        assert ev.source_artifact_id in (d1, d2)
        assert ev.locator is not None and ev.locator.pages
        assert all(p >= 1 for p in ev.locator.pages)
        assert ev.confidence is not None

    # Profiles reference evidence
    profiles = await store.list(artifact_type="paper_research_profile")
    assert len(profiles) == 2
    for p_env in profiles:
        prof = p_env.parse_payload(PaperResearchProfile)
        assert prof.evidence_item_ids
        # every claim references an evidence item id
        all_ids = set(prof.evidence_item_ids)
        for section in [
            prof.research_question,
            prof.theories,
            prof.main_findings,
            prof.results,
            prof.methodology,
        ]:
            for claim in section:
                assert claim.evidence_item_ids
                assert all(eid in all_ids for eid in claim.evidence_item_ids)

    # EvidenceCorpus
    ec_envs = await store.list(artifact_type="evidence_corpus")
    assert len(ec_envs) == 1
    ec = ec_envs[0].parse_payload(EvidenceCorpus)
    assert len(ec.paper_profile_ids) == 2
    assert len(ec.evidence_item_ids) == 5
    assert ec.documents_without_evidence == []

    # Provenance chain survives reopen: EvidenceCorpus -> Execution -> Corpus
    # and Profile -> EvidenceItem -> FullTextDocument
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs2 = FilesystemBlobStore(root=tmp_path / "blobs")

    ec_envs2 = await store2.list(artifact_type="evidence_corpus")
    ec2 = ec_envs2[0].parse_payload(EvidenceCorpus)
    ec_parents = await store2.get_parents(ec_envs2[0].artifact_id)
    assert any(p.source_artifact_id == exec_id for p in ec_parents)

    items2 = await store2.list(artifact_type="evidence_item")
    for env in items2:
        ev = env.parse_payload(EvidenceItem)
        assert ev.locator.pages  # page locators survive reopen
        parents = await store2.get_parents(env.artifact_id)
        assert any(p.source_artifact_id == ev.source_artifact_id for p in parents)

    profiles2 = await store2.list(artifact_type="paper_research_profile")
    for p_env in profiles2:
        prof = p_env.parse_payload(PaperResearchProfile)
        prof_parents = await store2.get_parents(p_env.artifact_id)
        # derived from at least one evidence item
        assert any(p.source_artifact_id in ec2.evidence_item_ids for p in prof_parents)

    # text blob still readable
    docs = await store2.list(artifact_type="full_text_document")
    for d_env in docs:
        doc = d_env.parse_payload(FullTextDocument)
        assert doc.text_blob is not None
        assert await blobs2.exists(doc.text_blob)
    await store2.close()
