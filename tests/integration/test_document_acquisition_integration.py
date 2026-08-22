import io
import json
import pathlib

import httpx
import pytest
from reportlab.pdfgen import canvas

from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.plugins.storage.blobs_filesystem.plugin import FilesystemBlobStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.common import ExternalIdentifier
from research_harness.research.schemas.identity import (
    PaperIdentity,
    ResolutionMethod,
)
from research_harness.research.schemas.paper import Author, PaperRecord
from research_harness.research.schemas.project import ResearchQuestion
from research_harness.research.schemas.screening_execution import ScreenedLiteratureSet


def _pdf_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, text)
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.mark.asyncio
async def test_document_acquisition_integration_offline(tmp_path: pathlib.Path):
    # Setup stores
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")

    # Create RQ -> not needed for corpus but for provenance chain
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="RQ for doc acquisition"),
        artifact_type="research_question",
    )
    await store.put(rq)

    # Create 3 PaperIdentities with different OA scenarios
    # PI1: metadata direct PDF (semantic_scholar style open_access_url)
    p1 = PaperRecord(
        title="Paper One",
        open_access_url="https://example.com/p1.pdf",
        doi="10.1234/p1",
        authors=[Author(name="A")],
    )
    p1_env = ArtifactEnvelope.create(payload=p1, artifact_type="paper_record", producer="test")
    await store.put(p1_env)
    pi1 = PaperIdentity(
        member_paper_artifact_ids=[p1_env.artifact_id],
        canonical_identifiers=[ExternalIdentifier(scheme="doi", value="10.1234/p1")],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    pi1_env = ArtifactEnvelope.create(payload=pi1, artifact_type="paper_identity", producer="test")
    await store.put(pi1_env)

    # PI2: no metadata OA, but Unpaywall will have OA
    p2 = PaperRecord(title="Paper Two", doi="10.1234/p2", authors=[Author(name="B")])
    p2_env = ArtifactEnvelope.create(payload=p2, artifact_type="paper_record", producer="test")
    await store.put(p2_env)
    pi2 = PaperIdentity(
        member_paper_artifact_ids=[p2_env.artifact_id],
        canonical_identifiers=[ExternalIdentifier(scheme="doi", value="10.1234/p2")],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    pi2_env = ArtifactEnvelope.create(payload=pi2, artifact_type="paper_identity", producer="test")
    await store.put(pi2_env)

    # PI3: no OA anywhere
    p3 = PaperRecord(title="Paper Three", doi="10.1234/p3", authors=[Author(name="C")])
    p3_env = ArtifactEnvelope.create(payload=p3, artifact_type="paper_record", producer="test")
    await store.put(p3_env)
    pi3 = PaperIdentity(
        member_paper_artifact_ids=[p3_env.artifact_id],
        canonical_identifiers=[ExternalIdentifier(scheme="doi", value="10.1234/p3")],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    pi3_env = ArtifactEnvelope.create(payload=pi3, artifact_type="paper_identity", producer="test")
    await store.put(pi3_env)

    # Create ScreenedLiteratureSet with included = PI1, PI2, PI3
    # Need also a dummy ScreeningExecution etc., but for our orchestrator we just need the set
    # Create a minimal acquisition execution's screened set: we can directly create ScreenedLiteratureSet
    from research_harness.research.schemas.screening_execution import ScreeningExecution

    # Dummy execution for provenance
    screening_exec = ScreeningExecution(
        protocol_artifact_id="proto",
        search_execution_artifact_id="search",
        candidate_identity_ids=[pi1_env.artifact_id, pi2_env.artifact_id, pi3_env.artifact_id],
        screening_view_ids=[],
        decision_artifact_ids=[],
        counts={"included": 3},
        failures=[],
    )
    screening_exec_env = ArtifactEnvelope.create(
        payload=screening_exec, artifact_type="screening_execution", producer="test"
    )
    await store.put(screening_exec_env)

    screened_set = ScreenedLiteratureSet(
        screening_execution_id=screening_exec_env.artifact_id,
        screening_protocol_id="proto",
        included_identity_ids=[pi1_env.artifact_id, pi2_env.artifact_id, pi3_env.artifact_id],
        excluded_identity_ids=[],
        uncertain_identity_ids=[],
        decision_artifact_ids=[],
    )
    set_env = ArtifactEnvelope.create(
        payload=screened_set, artifact_type="screened_literature_set", producer="test"
    )
    await store.put(set_env)

    # Prepare mocked HTTP for both Unpaywall and PDF fetches - need >200 chars to be 'extracted'
    long_text1 = "Content of Paper One - extracted text. " * 15
    long_text2 = "Content of Paper Two - Unpaywall PDF. " * 15
    pdf1_bytes = _pdf_bytes(long_text1)
    pdf2_bytes = _pdf_bytes(long_text2)

    # Unpaywall mock: PI2's DOI returns best_oa_location with PDF, PI3 returns not OA
    def unpaywall_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "10.1234%2Fp2" in url or "10.1234/p2" in url:
            raw = {
                "doi": "10.1234/p2",
                "is_oa": True,
                "best_oa_location": {
                    "url_for_pdf": "https://example.com/p2_unpaywall.pdf",
                    "url": "https://example.com/p2_unpaywall.pdf",
                    "url_for_landing_page": "https://example.com/p2",
                    "host_type": "repository",
                    "version": "publishedVersion",
                    "license": "cc-by",
                },
                "oa_locations": [],
            }
            return httpx.Response(200, json=raw)
        if "10.1234%2Fp3" in url or "10.1234/p3" in url:
            return httpx.Response(
                200,
                json={
                    "doi": "10.1234/p3",
                    "is_oa": False,
                    "best_oa_location": None,
                    "oa_locations": [],
                },
            )
        # PI1 also may be queried but it has metadata location already; we still return something but orchestrator will prioritize metadata
        if "10.1234%2Fp1" in url or "10.1234/p1" in url:
            return httpx.Response(
                200,
                json={
                    "doi": "10.1234/p1",
                    "is_oa": True,
                    "best_oa_location": {
                        "url_for_pdf": "https://example.com/p1_unpaywall.pdf",
                        "url": "https://example.com/p1_unpaywall.pdf",
                    },
                    "oa_locations": [],
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    def fetcher_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://example.com/p1.pdf":
            return httpx.Response(
                200,
                content=pdf1_bytes,
                headers={"content-type": "application/pdf", "content-length": str(len(pdf1_bytes))},
            )
        if url == "https://example.com/p2_unpaywall.pdf":
            return httpx.Response(
                200,
                content=pdf2_bytes,
                headers={"content-type": "application/pdf", "content-length": str(len(pdf2_bytes))},
            )
        # Anything else 404
        return httpx.Response(404, content=b"not found")

    # Combined handler for a single client shared
    def combined_handler(request: httpx.Request) -> httpx.Response:
        if "api.unpaywall.org" in str(request.url):
            return unpaywall_handler(request)
        else:
            return fetcher_handler(request)

    combined_client = httpx.AsyncClient(transport=httpx.MockTransport(combined_handler))
    # For simplicity, use same client for both services (they share transport)
    # Create locators with same client
    from research_harness.plugins.documents.acquisition_orchestrator.plugin import (
        DocumentAcquisitionOrchestratorService,
    )
    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorService
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService
    from research_harness.plugins.documents.locator_metadata.plugin import MetadataLocatorService
    from research_harness.plugins.documents.locator_unpaywall.plugin import UnpaywallLocatorService

    meta_loc = MetadataLocatorService(artifact_store=store)
    unpaywall_loc = UnpaywallLocatorService(
        artifact_store=store, http_client=combined_client, email="test@example.com"
    )
    fetcher = HttpFetcherService(
        artifact_store=store,
        blob_store=blobs,
        http_client=combined_client,
        max_bytes=50 * 1024 * 1024,
    )
    extractor = PypdfExtractorService(artifact_store=store, blob_store=blobs)
    orchestrator = DocumentAcquisitionOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        fetcher=fetcher,
        extractor=extractor,
        metadata_locator=meta_loc,
        unpaywall_locator=unpaywall_loc,
    )

    exec_id = await orchestrator.run(set_env.artifact_id)
    assert exec_id
    from research_harness.research.schemas.full_text import (
        DocumentAcquisitionExecution,
        FullTextCorpus,
    )

    exec_rec = (await store.get(exec_id)).parse_payload(DocumentAcquisitionExecution)
    # Expected: total_included 3, downloaded 2, no_location 1, text_extracted 2
    assert exec_rec.counts["total_included"] == 3
    assert exec_rec.counts["downloaded"] == 2
    assert exec_rec.counts["no_location"] == 1
    assert exec_rec.counts["text_extracted"] == 2
    assert len(exec_rec.acquisition_artifact_ids) == 3  # 2 downloaded +1 not_available
    assert len(exec_rec.full_text_document_ids) == 2

    # Corpus
    corpora = await store.list(artifact_type="full_text_corpus")
    assert len(corpora) == 1
    corpus = corpora[0].parse_payload(FullTextCorpus)
    assert len(corpus.available_document_ids) == 2
    assert len(corpus.unavailable_identity_ids) == 1
    assert set(corpus.unavailable_identity_ids) == {pi3_env.artifact_id}
    assert set(corpus.available_document_ids) == set(exec_rec.full_text_document_ids)

    # Verify after reopen: PDF blob exists, text blob exists, page text retrievable, provenance survives
    await store.close()
    # Need to keep blobs on filesystem (same tmp_path)
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs2 = FilesystemBlobStore(root=tmp_path / "blobs")
    # Check PDF blobs
    docs = await store2.list(artifact_type="full_text_document")
    assert len(docs) == 2
    for doc_env in docs:
        from research_harness.research.schemas.full_text import FullTextDocument

        doc = doc_env.parse_payload(FullTextDocument)
        # PDF blob exists
        assert await blobs2.exists(doc.source_blob)
        pdf = await blobs2.get_bytes(doc.source_blob)
        assert pdf.startswith(b"%PDF-")
        # Text blob exists and retrievable
        assert doc.text_blob is not None
        assert await blobs2.exists(doc.text_blob)
        text_data = await blobs2.get_bytes(doc.text_blob)
        j = json.loads(text_data.decode("utf-8"))
        assert j["schema_version"] == 1
        assert len(j["pages"]) == 1
        # Check that page text contains expected content (either p1 or p2)
        page_text = j["pages"][0]["text"]
        assert "Content of Paper" in page_text
        # Provenance: FullTextDocument derived_from DocumentAcquisition
        parents = await store2.get_parents(doc_env.artifact_id)
        assert any(p.relation.value == "derived_from" for p in parents)
        # Corpus provenance
        corp_parents = await store2.get_parents(corpora[0].artifact_id)
        assert any(p.source_artifact_id == exec_id for p in corp_parents)

    # Also verify DocumentLocation -> PaperIdentity provenance
    locs = await store2.list(artifact_type="document_location")
    # Should have at least 2 (p1 metadata, p2 unpaywall)
    assert len(locs) >= 2
    for loc_env in locs:
        parents = await store2.get_parents(loc_env.artifact_id)
        # Should have derived_from PaperIdentity or snapshot
        assert len(parents) >= 1

    await store2.close()
    await combined_client.aclose()
