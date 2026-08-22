"""Phase 2F unit tests — chunking, grounding, dedup, profiles, budgets, idempotency, provenance.

Uses fake models only; no network, no keys.
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
from research_harness.research.schemas.research_profile import PaperResearchProfile


class FakeRouter:
    """Returns configured JSON per call; can fail or vary."""

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


def _ok_response(
    category="finding", statement="The platform reduces cost", pages=(1, 2), confidence=0.9
):
    return json.dumps(
        {
            "items": [
                {
                    "category": category,
                    "statement": statement,
                    "page_numbers": list(pages),
                    "confidence": confidence,
                    "excerpt": "excerpt",
                }
            ]
        }
    )


def _pages(n: int) -> list[dict]:
    return [{"page": i + 1, "text": f"Page {i + 1} content text."} for i in range(n)]


async def _make_doc(
    store,
    blobs,
    pages,
    status: TextStatus = TextStatus.extracted,
    text_blob=None,
    pi="pi1",
    doc_id=None,
) -> str:
    pi = await _get_or_create_identity(store, pi)
    if text_blob is None:
        data = json.dumps({"schema_version": 1, "pages": pages}, sort_keys=True).encode()
        text_blob = await blobs.put_bytes(data, media_type="application/json")
    pdf_blob = await blobs.put_bytes(b"%PDF- test", media_type="application/pdf")
    acq = DocumentAcquisition(
        paper_identity_id=pi,
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
    doc = FullTextDocument(
        paper_identity_id=pi,
        document_acquisition_id=a_env.artifact_id,
        source_blob=pdf_blob,
        text_blob=text_blob,
        extractor="documents.extractor.pypdf",
        extractor_version="0.1.0",
        page_count=len(pages),
        pages_with_text=len(pages),
        character_count=sum(len(p["text"]) for p in pages),
        text_status=status,
        quality_metrics={},
    )
    d_env = ArtifactEnvelope.create(
        payload=doc, artifact_type="full_text_document", producer="test"
    )
    await store.put(d_env)
    if doc_id:
        return doc_id
    return d_env.artifact_id


async def _get_or_create_identity(store, pi: str) -> str:
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod

    envs = await store.list(artifact_type="paper_identity")
    for env in envs:
        ident = env.parse_payload(PaperIdentity)
        if ident.member_paper_artifact_ids and ident.member_paper_artifact_ids[0] == pi:
            return env.artifact_id
    ident = PaperIdentity(
        member_paper_artifact_ids=[pi],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    env = ArtifactEnvelope.create(payload=ident, artifact_type="paper_identity", producer="test")
    await store.put(env)
    return env.artifact_id


async def _make_corpus(store, doc_ids: list[str], unavailable: list[str] | None = None) -> str:
    exec_rec = EvidenceExtractionExecution(full_text_corpus_id="c1")
    e_env = ArtifactEnvelope.create(
        payload=exec_rec, artifact_type="evidence_extraction_execution", producer="test"
    )
    await store.put(e_env)
    corpus = FullTextCorpus(
        document_acquisition_execution_id=e_env.artifact_id,
        screened_literature_set_id="set1",
        available_document_ids=doc_ids,
        unavailable_identity_ids=[],
        restricted_identity_ids=[],
        failed_identity_ids=[],
        metadata={},
    )
    c_env = ArtifactEnvelope.create(
        payload=corpus, artifact_type="full_text_corpus", producer="test"
    )
    await store.put(c_env)
    return c_env.artifact_id


# 1. page-bounded chunking
def test_chunking_page_bounded():
    from research_harness.plugins.literature.evidence_extractor.plugin import chunk_pages

    pages = _pages(9)
    chunks = chunk_pages("doc1", pages, 4)
    assert [c.chunk_index for c in chunks] == [0, 1, 2]
    assert [(c.start_page, c.end_page) for c in chunks] == [(1, 4), (5, 8), (9, 9)]
    assert chunks[0].document_id == "doc1"
    assert chunks[0].pages == [1, 2, 3, 4]
    assert chunks[2].end_page == 9
    assert "Page 1" in chunks[0].text
    # No overlap, no gap
    all_pages = [p for c in chunks for p in c.pages]
    assert all_pages == list(range(1, 10))


# 2. valid grounded evidence
@pytest.mark.asyncio
async def test_extract_chunk_valid(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractionChunk,
        EvidenceExtractorService,
    )

    router = FakeRouter([_ok_response()])
    svc = EvidenceExtractorService(
        model_router=router, artifact_store=None, blob_store=None, model_role="reasoning"
    )
    chunk = EvidenceExtractionChunk(
        document_id="doc1", chunk_index=0, start_page=1, end_page=4, page_texts=_pages(4)
    )
    cands = await svc.extract_chunk(chunk)
    assert len(cands) == 1
    assert cands[0].category == "finding"
    assert cands[0].page_numbers == [1, 2]
    assert cands[0].confidence == 0.9
    assert router.last_role == "reasoning"
    # Prompt must not request chain-of-thought (only prohibits it)
    user_content = router.last_request.messages[1].content.lower()
    assert "explain your reasoning" not in user_content
    assert "show your work" not in user_content
    assert "return json only" in user_content


# 3. invalid page (not in chunk) rejected
@pytest.mark.asyncio
async def test_extract_chunk_page_outside_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractionChunk,
        EvidenceExtractorService,
    )

    router = FakeRouter([_ok_response(pages=(9,))])  # page 9 outside 1-4
    svc = EvidenceExtractorService(model_router=router, artifact_store=None, blob_store=None)
    chunk = EvidenceExtractionChunk(
        document_id="doc1", chunk_index=0, start_page=1, end_page=4, page_texts=_pages(4)
    )
    with pytest.raises(ValueError, match="outside"):
        await svc.extract_chunk(chunk)


# 4. malformed output / missing pages / empty statement / invalid category rejected
@pytest.mark.asyncio
async def test_extract_chunk_malformed_and_invalid(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractionChunk,
        EvidenceExtractorService,
    )

    chunk = EvidenceExtractionChunk(
        document_id="doc1", chunk_index=0, start_page=1, end_page=4, page_texts=_pages(4)
    )

    # Not JSON
    svc = EvidenceExtractorService(
        model_router=FakeRouter(["not json"]), artifact_store=None, blob_store=None
    )
    with pytest.raises(ValueError, match="invalid JSON"):
        await svc.extract_chunk(chunk)

    # Missing page_numbers
    bad = json.dumps({"items": [{"category": "finding", "statement": "x", "confidence": 0.9}]})
    svc2 = EvidenceExtractorService(
        model_router=FakeRouter([bad]), artifact_store=None, blob_store=None
    )
    with pytest.raises(ValueError, match="invalid evidence candidate"):
        await svc2.extract_chunk(chunk)

    # Empty statement
    bad2 = json.dumps(
        {
            "items": [
                {"category": "finding", "statement": "   ", "page_numbers": [1], "confidence": 0.9}
            ]
        }
    )
    svc3 = EvidenceExtractorService(
        model_router=FakeRouter([bad2]), artifact_store=None, blob_store=None
    )
    with pytest.raises(ValueError, match="invalid evidence candidate"):
        await svc3.extract_chunk(chunk)

    # Invalid category
    bad3 = json.dumps(
        {
            "items": [
                {"category": "nonsense", "statement": "x", "page_numbers": [1], "confidence": 0.9}
            ]
        }
    )
    svc4 = EvidenceExtractorService(
        model_router=FakeRouter([bad3]), artifact_store=None, blob_store=None
    )
    with pytest.raises(ValueError, match="invalid evidence category"):
        await svc4.extract_chunk(chunk)


# 5. orchestrator full flow: 2 docs -> evidence -> profiles -> corpus
@pytest.mark.asyncio
async def test_orchestrator_full_flow(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractorService,
    )
    from research_harness.plugins.literature.evidence_orchestrator.plugin import (
        EvidenceOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    d1 = await _make_doc(store, blobs, _pages(4), pi="pi1")
    d2 = await _make_doc(store, blobs, _pages(2), pi="pi2")
    corpus_id = await _make_corpus(store, [d1, d2])

    router = FakeRouter(
        [
            _ok_response(category="finding", statement="Finding one", pages=(1,)),
            _ok_response(category="method", statement="Method two", pages=(3, 4)),
            _ok_response(category="limitation", statement="Limitation three", pages=(1, 2)),
        ]
    )
    extractor = EvidenceExtractorService(
        model_router=router, artifact_store=store, blob_store=blobs, model_role="reasoning"
    )
    orch = EvidenceOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        extractor=extractor,
        pages_per_chunk=2,
        max_chunks_per_document=10,
        max_model_calls=500,
    )
    exec_id = await orch.run(corpus_id)
    rec = (await store.get(exec_id)).parse_payload(EvidenceExtractionExecution)
    assert rec.documents_attempted == 2
    assert rec.documents_completed == 2
    assert rec.evidence_items_created == 3
    assert rec.profiles_created == 2
    assert rec.chunks_processed == 3  # 4 pages/2 + 2 pages/2

    # Evidence items grounded
    items = await store.list(artifact_type="evidence_item")
    assert len(items) == 3
    for env in items:
        ev = env.parse_payload(EvidenceItem)
        assert ev.source_artifact_id in (d1, d2)
        assert ev.locator is not None and ev.locator.pages
        assert all(p >= 1 for p in ev.locator.pages)
        assert ev.extraction_method == "model-assisted"

    # Profiles
    profiles = await store.list(artifact_type="paper_research_profile")
    assert len(profiles) == 2
    p = profiles[0].parse_payload(PaperResearchProfile)
    assert p.evidence_item_ids
    # profile references evidence ids
    for claim in p.main_findings + p.methodology + p.limitations:
        assert claim.evidence_item_ids

    # EvidenceCorpus
    corpora = await store.list(artifact_type="evidence_corpus")
    assert len(corpora) == 1
    ec = corpora[0].parse_payload(EvidenceCorpus)
    assert len(ec.paper_profile_ids) == 2
    assert len(ec.evidence_item_ids) == 3
    assert ec.documents_without_evidence == []


# 6. partial chunk failure preserves earlier evidence
@pytest.mark.asyncio
async def test_partial_chunk_failure(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractorService,
    )
    from research_harness.plugins.literature.evidence_orchestrator.plugin import (
        EvidenceOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    d1 = await _make_doc(store, blobs, _pages(6), pi="pi1")
    corpus_id = await _make_corpus(store, [d1])

    # 3 chunks (2 pages each); first ok, second malformed (raises), third ok
    router = FakeRouter(
        [
            _ok_response(category="finding", statement="Chunk one finding", pages=(1,)),
            "garbage not json",
            _ok_response(category="result", statement="Chunk three result", pages=(5, 6)),
        ]
    )
    extractor = EvidenceExtractorService(
        model_router=router, artifact_store=store, blob_store=blobs
    )
    orch = EvidenceOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        extractor=extractor,
        pages_per_chunk=2,
        max_chunks_per_document=10,
        max_model_calls=500,
    )
    exec_id = await orch.run(corpus_id)
    rec = (await store.get(exec_id)).parse_payload(EvidenceExtractionExecution)
    assert rec.chunks_processed == 2
    assert rec.chunks_failed == 1
    assert rec.evidence_items_created == 2  # earlier + later preserved
    items = await store.list(artifact_type="evidence_item")
    statements = {env.parse_payload(EvidenceItem).statement for env in items}
    assert "Chunk one finding" in statements
    assert "Chunk three result" in statements


# 7. duplicate evidence collapsed within paper
@pytest.mark.asyncio
async def test_duplicate_evidence_dedup(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractorService,
    )
    from research_harness.plugins.literature.evidence_orchestrator.plugin import (
        EvidenceOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    d1 = await _make_doc(store, blobs, _pages(4), pi="pi1")
    corpus_id = await _make_corpus(store, [d1])

    dup = _ok_response(category="finding", statement="Same finding.", pages=(1,))
    router = FakeRouter([dup, dup])
    extractor = EvidenceExtractorService(
        model_router=router, artifact_store=store, blob_store=blobs
    )
    orch = EvidenceOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        extractor=extractor,
        pages_per_chunk=2,
        max_chunks_per_document=10,
        max_model_calls=500,
    )
    await orch.run(corpus_id)
    items = await store.list(artifact_type="evidence_item")
    assert len(items) == 1  # collapsed
    ev = items[0].parse_payload(EvidenceItem)
    assert ev.statement == "Same finding."


# 8. insufficient-text / encrypted / failed documents skipped
@pytest.mark.asyncio
async def test_unavailable_documents_skipped(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractorService,
    )
    from research_harness.plugins.literature.evidence_orchestrator.plugin import (
        EvidenceOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    d_good = await _make_doc(store, blobs, _pages(3), pi="pi1")
    d_insuff = await _make_doc(
        store, blobs, _pages(1), status=TextStatus.insufficient_text, pi="pi2"
    )
    d_enc = await _make_doc(store, blobs, _pages(1), status=TextStatus.encrypted, pi="pi3")
    d_fail = await _make_doc(store, blobs, _pages(1), status=TextStatus.extraction_failed, pi="pi4")
    corpus_id = await _make_corpus(store, [d_good, d_insuff, d_enc, d_fail])

    router = FakeRouter([_ok_response(category="finding", statement="Only good doc", pages=(1,))])
    extractor = EvidenceExtractorService(
        model_router=router, artifact_store=store, blob_store=blobs
    )
    orch = EvidenceOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        extractor=extractor,
        pages_per_chunk=4,
        max_chunks_per_document=10,
        max_model_calls=500,
    )
    exec_id = await orch.run(corpus_id)
    rec = (await store.get(exec_id)).parse_payload(EvidenceExtractionExecution)
    assert rec.evidence_items_created == 1
    assert rec.documents_completed == 1
    assert rec.counts["documents_without_evidence"] == 3
    corpora = await store.list(artifact_type="evidence_corpus")
    ec = corpora[0].parse_payload(EvidenceCorpus)
    assert {d_insuff, d_enc, d_fail} == set(ec.documents_without_evidence)


# 9. budgets enforced
@pytest.mark.asyncio
async def test_execution_budgets(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractorService,
    )
    from research_harness.plugins.literature.evidence_orchestrator.plugin import (
        EvidenceOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    d1 = await _make_doc(store, blobs, _pages(8), pi="pi1")
    corpus_id = await _make_corpus(store, [d1])

    # max_model_calls=1 -> only first chunk processed
    router = FakeRouter([_ok_response(category="finding", statement="First chunk", pages=(1,))])
    extractor = EvidenceExtractorService(
        model_router=router, artifact_store=store, blob_store=blobs
    )
    orch = EvidenceOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        extractor=extractor,
        pages_per_chunk=4,
        max_chunks_per_document=50,
        max_model_calls=1,
    )
    exec_id = await orch.run(corpus_id)
    rec = (await store.get(exec_id)).parse_payload(EvidenceExtractionExecution)
    assert rec.counts["model_calls"] == 1
    assert rec.chunks_processed == 1
    assert any("budget" in f.get("error", "") for f in rec.failures)

    # max_chunks_per_document=1 on a 2-chunk doc
    store2 = SQLiteArtifactStore(path=tmp_path / "art2.db")
    blobs2 = FilesystemBlobStore(root=tmp_path / "blobs2")
    d2 = await _make_doc(store2, blobs2, _pages(6), pi="pi1")
    corpus2 = await _make_corpus(store2, [d2])
    router2 = FakeRouter([_ok_response(category="finding", statement="A", pages=(1,))])
    extractor2 = EvidenceExtractorService(
        model_router=router2, artifact_store=store2, blob_store=blobs2
    )
    orch2 = EvidenceOrchestratorService(
        artifact_store=store2,
        blob_store=blobs2,
        extractor=extractor2,
        pages_per_chunk=2,
        max_chunks_per_document=1,
        max_model_calls=500,
    )
    exec2 = await orch2.run(corpus2)
    rec2 = (await store2.get(exec2)).parse_payload(EvidenceExtractionExecution)
    assert rec2.chunks_processed == 1  # only 1 of 3 chunks


# 10. idempotent rerun
@pytest.mark.asyncio
async def test_idempotent_rerun(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractorService,
    )
    from research_harness.plugins.literature.evidence_orchestrator.plugin import (
        EvidenceOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    d1 = await _make_doc(store, blobs, _pages(4), pi="pi1")
    corpus_id = await _make_corpus(store, [d1])

    router = FakeRouter([_ok_response(category="finding", statement="Stable", pages=(1,))])
    extractor = EvidenceExtractorService(
        model_router=router, artifact_store=store, blob_store=blobs
    )
    orch = EvidenceOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        extractor=extractor,
        pages_per_chunk=4,
        max_chunks_per_document=10,
        max_model_calls=500,
    )
    exec1 = await orch.run(corpus_id)
    exec2 = await orch.run(corpus_id)
    assert exec1 == exec2  # reuses execution
    items = await store.list(artifact_type="evidence_item")
    assert len(items) == 1


# 11. provenance after reopen
@pytest.mark.asyncio
async def test_provenance_after_reopen(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractorService,
    )
    from research_harness.plugins.literature.evidence_orchestrator.plugin import (
        EvidenceOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    d1 = await _make_doc(store, blobs, _pages(3), pi="pi1")
    corpus_id = await _make_corpus(store, [d1])
    router = FakeRouter([_ok_response(category="finding", statement="Grounded", pages=(1, 2))])
    extractor = EvidenceExtractorService(
        model_router=router, artifact_store=store, blob_store=blobs
    )
    orch = EvidenceOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        extractor=extractor,
        pages_per_chunk=4,
        max_chunks_per_document=10,
        max_model_calls=500,
    )
    exec_id = await orch.run(corpus_id)
    await store.close()

    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs2 = FilesystemBlobStore(root=tmp_path / "blobs")
    items = await store2.list(artifact_type="evidence_item")
    assert len(items) == 1
    ev = items[0].parse_payload(EvidenceItem)
    assert ev.locator.pages == [1, 2]  # page locators survive reopen
    parents = await store2.get_parents(items[0].artifact_id)
    assert any(p.source_artifact_id == d1 for p in parents)
    profiles = await store2.list(artifact_type="paper_research_profile")
    p_parents = await store2.get_parents(profiles[0].artifact_id)
    assert any(p.source_artifact_id == items[0].artifact_id for p in p_parents)
    ecs = await store2.list(artifact_type="evidence_corpus")
    ec_parents = await store2.get_parents(ecs[0].artifact_id)
    assert any(p.source_artifact_id == exec_id for p in ec_parents)
    assert (
        await blobs2.exists(ev.source_artifact_id) is False
    )  # sanity: doc ids are artifact ids, not blobs
    await store2.close()


# ============================ Phase 2F.5 verification ============================


# 2F.5-1: model abstraction — configurable logical role used, not hard-coded model
@pytest.mark.asyncio
async def test_model_abstraction_uses_configured_role():
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractionChunk,
        EvidenceExtractorService,
    )

    router = FakeRouter([_ok_response()])
    svc = EvidenceExtractorService(
        model_router=router, artifact_store=None, blob_store=None, model_role="critic"
    )
    chunk = EvidenceExtractionChunk(
        document_id="doc1", chunk_index=0, start_page=1, end_page=2, page_texts=_pages(2)
    )
    await svc.extract_chunk(chunk)
    assert router.last_role == "critic"
    # Structured output schema must be attached to the request (no free-form fallback)
    schema = router.last_request.response_schema
    assert schema is not None
    assert schema["type"] == "object"
    assert schema["required"] == ["items"]
    assert schema["properties"]["items"]["items"]["required"] == [
        "category",
        "statement",
        "page_numbers",
        "confidence",
    ]


# 2F.5-2: chunking edge cases — short doc, final partial chunk, empty pages, 1-based
def test_chunking_edge_cases():
    from research_harness.plugins.literature.evidence_extractor.plugin import chunk_pages

    # short document (2 pages, ppc 4) -> single chunk 1-2
    chunks = chunk_pages("doc", _pages(2), 4)
    assert len(chunks) == 1
    assert (chunks[0].start_page, chunks[0].end_page) == (1, 2)

    # empty page list -> no chunks
    assert chunk_pages("doc", [], 4) == []

    # final partial chunk (5 pages, ppc 2) -> 1-2, 3-4, 5-5
    chunks = chunk_pages("doc", _pages(5), 2)
    assert [(c.start_page, c.end_page) for c in chunks] == [(1, 2), (3, 4), (5, 5)]

    # page numbering remains 1-based even when page dicts are unsorted
    pages = [{"page": 7, "text": "x"}, {"page": 5, "text": "y"}, {"page": 6, "text": "z"}]
    chunks = chunk_pages("doc", pages, 10)
    assert len(chunks) == 1
    assert chunks[0].start_page == 5
    assert chunks[0].end_page == 7
    assert chunks[0].pages == [5, 6, 7]

    # empty page text inside a chunk is preserved as a page boundary
    pages = [{"page": 1, "text": "has text"}, {"page": 2, "text": ""}]
    chunks = chunk_pages("doc", pages, 4)
    assert chunks[0].pages == [1, 2]
    assert "Page 2" in chunks[0].text


# 2F.5-3: partial failure — technical failure creates no fake evidence
@pytest.mark.asyncio
async def test_partial_failure_no_fake_evidence(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractorService,
    )
    from research_harness.plugins.literature.evidence_orchestrator.plugin import (
        EvidenceOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    d1 = await _make_doc(store, blobs, _pages(6), pi="pi1")
    corpus_id = await _make_corpus(store, [d1])

    # chunk 0 ok, chunk 1 raises (model failure), chunk 2 ok
    router = FakeRouter(
        [
            _ok_response(category="finding", statement="Chunk one ok", pages=(1,)),
            "malformed",
            _ok_response(category="finding", statement="Chunk three ok", pages=(5, 6)),
        ]
    )
    extractor = EvidenceExtractorService(
        model_router=router, artifact_store=store, blob_store=blobs
    )
    orch = EvidenceOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        extractor=extractor,
        pages_per_chunk=2,
        max_chunks_per_document=10,
        max_model_calls=500,
    )
    await orch.run(corpus_id)
    rec = next(
        env.parse_payload(EvidenceExtractionExecution)
        for env in await store.list(artifact_type="evidence_extraction_execution")
        if env.parse_payload(EvidenceExtractionExecution).full_text_corpus_id == corpus_id
    )
    assert rec.chunks_failed == 1
    items = await store.list(artifact_type="evidence_item")
    statements = {env.parse_payload(EvidenceItem).statement for env in items}
    assert statements == {"Chunk one ok", "Chunk three ok"}
    # No evidence from failed chunk 2; no fabricated content
    assert (
        "malformed" not in str([env.parse_payload(EvidenceItem).statement for env in items]).lower()
    )


# 2F.5-4: profile claims reference valid EvidenceItem artifacts
@pytest.mark.asyncio
async def test_profile_claims_reference_existing_evidence(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractorService,
    )
    from research_harness.plugins.literature.evidence_orchestrator.plugin import (
        EvidenceOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    d1 = await _make_doc(store, blobs, _pages(4), pi="pi1")
    corpus_id = await _make_corpus(store, [d1])
    router = FakeRouter(
        [
            _ok_response(category="theory", statement="Theory claim", pages=(1, 2)),
            _ok_response(category="finding", statement="Finding claim", pages=(3, 4)),
        ]
    )
    extractor = EvidenceExtractorService(
        model_router=router, artifact_store=store, blob_store=blobs
    )
    orch = EvidenceOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        extractor=extractor,
        pages_per_chunk=2,
        max_chunks_per_document=10,
        max_model_calls=500,
    )
    await orch.run(corpus_id)
    profiles = await store.list(artifact_type="paper_research_profile")
    prof = profiles[0].parse_payload(PaperResearchProfile)
    # Every non-inference claim references valid, existing EvidenceItem ids
    sections = [
        prof.theories,
        prof.main_findings,
        prof.research_question,
        prof.methodology,
        prof.limitations,
        prof.boundary_conditions,
    ]
    for section in sections:
        for claim in section:
            assert claim.inference is False
            assert claim.evidence_item_ids
            for eid in claim.evidence_item_ids:
                env = await store.get(eid)
                assert env.artifact_type == "evidence_item"
                assert env.parse_payload(EvidenceItem).source_artifact_id == d1
    # All profile claim evidence ids are within the profile's aggregate list
    assert set(prof.evidence_item_ids) == {
        eid for section in sections for claim in section for eid in claim.evidence_item_ids
    }


# 2F.5-5: idempotency — model role change creates a new run, does not reuse
@pytest.mark.asyncio
async def test_idempotency_model_change_creates_new_run(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractorService,
    )
    from research_harness.plugins.literature.evidence_orchestrator.plugin import (
        EvidenceOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    d1 = await _make_doc(store, blobs, _pages(4), pi="pi1")
    corpus_id = await _make_corpus(store, [d1])

    router = FakeRouter([_ok_response(category="finding", statement="Stable", pages=(1, 2))])
    extractor = EvidenceExtractorService(
        model_router=router, artifact_store=store, blob_store=blobs, model_role="reasoning"
    )
    orch = EvidenceOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        extractor=extractor,
        model_role="reasoning",
        pages_per_chunk=4,
        max_chunks_per_document=10,
        max_model_calls=500,
    )
    exec1 = await orch.run(corpus_id)
    exec2 = await orch.run(corpus_id)
    assert exec1 == exec2  # same role+chunk config -> reuse

    # Same corpus but different model role -> new run (material config change)
    router2 = FakeRouter([_ok_response(category="finding", statement="Stable", pages=(1, 2))])
    extractor2 = EvidenceExtractorService(
        model_router=router2, artifact_store=store, blob_store=blobs, model_role="critic"
    )
    orch2 = EvidenceOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        extractor=extractor2,
        model_role="critic",
        pages_per_chunk=4,
        max_chunks_per_document=10,
        max_model_calls=500,
    )
    exec3 = await orch2.run(corpus_id)
    assert exec3 != exec1  # new run for changed model

    # Same corpus but different pages_per_chunk -> new run
    router3 = FakeRouter([_ok_response(category="finding", statement="Stable", pages=(1, 2))])
    extractor3 = EvidenceExtractorService(
        model_router=router3, artifact_store=store, blob_store=blobs, model_role="reasoning"
    )
    orch3 = EvidenceOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        extractor=extractor3,
        model_role="reasoning",
        pages_per_chunk=2,
        max_chunks_per_document=10,
        max_model_calls=500,
    )
    exec4 = await orch3.run(corpus_id)
    assert exec4 != exec1
    executions = [
        env.parse_payload(EvidenceExtractionExecution)
        for env in await store.list(artifact_type="evidence_extraction_execution")
        if env.parse_payload(EvidenceExtractionExecution).full_text_corpus_id == corpus_id
    ]
    assert len(executions) == 3


# 2F.5-6: exact reuse semantics — unchanged rerun reuses execution AND evidence ids
@pytest.mark.asyncio
async def test_idempotency_exact_reuse_semantics(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractorService,
    )
    from research_harness.plugins.literature.evidence_orchestrator.plugin import (
        EvidenceOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    d1 = await _make_doc(store, blobs, _pages(4), pi="pi1")
    corpus_id = await _make_corpus(store, [d1])

    router = FakeRouter([_ok_response(category="finding", statement="Same", pages=(1, 2))])
    extractor = EvidenceExtractorService(
        model_router=router, artifact_store=store, blob_store=blobs
    )
    orch = EvidenceOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        extractor=extractor,
        pages_per_chunk=4,
        max_chunks_per_document=10,
        max_model_calls=500,
    )
    exec1 = await orch.run(corpus_id)
    items1 = [env.artifact_id for env in await store.list(artifact_type="evidence_item")]
    profiles1 = [
        env.artifact_id for env in await store.list(artifact_type="paper_research_profile")
    ]

    # Second run with the same everything: router not called at all, same ids
    router2 = FakeRouter(
        [_ok_response(category="finding", statement="Should not be called", pages=(1, 2))]
    )
    extractor2 = EvidenceExtractorService(
        model_router=router2, artifact_store=store, blob_store=blobs
    )
    orch2 = EvidenceOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        extractor=extractor2,
        pages_per_chunk=4,
        max_chunks_per_document=10,
        max_model_calls=500,
    )
    exec2 = await orch2.run(corpus_id)
    assert exec1 == exec2
    assert router2.calls == 0  # no model calls on reuse
    items2 = [env.artifact_id for env in await store.list(artifact_type="evidence_item")]
    profiles2 = [
        env.artifact_id for env in await store.list(artifact_type="paper_research_profile")
    ]
    assert items1 == items2
    assert profiles1 == profiles2
