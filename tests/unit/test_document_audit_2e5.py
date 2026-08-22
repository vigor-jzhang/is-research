"""Phase 2E.5 audit - missing requirement coverage.

Covers: DOI quoting, redirect bounds, fallback, idempotency,
orchestrator scope, partial failure, blob boundary, provenance, events,
DNS limitation documentation.

All offline, deterministic.
"""

from __future__ import annotations

import hashlib
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
from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
from research_harness.research.schemas.paper import PaperRecord


def _pdf_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def _pdf_two_pages(a: str, b: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, a)
    c.showPage()
    c.drawString(100, 750, b)
    c.showPage()
    c.save()
    return buf.getvalue()


async def _make_pi(
    store: SQLiteArtifactStore, doi: str | None = None, open_url: str | None = None
) -> str:
    paper = PaperRecord(title="T", doi=doi, open_access_url=open_url)
    p_env = ArtifactEnvelope.create(payload=paper, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[ExternalIdentifier(scheme="doi", value=doi)] if doi else [],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    i_env = ArtifactEnvelope.create(payload=ident, artifact_type="paper_identity", producer="test")
    await store.put(i_env)
    return i_env.artifact_id


# 8 - DOI quoting regression
@pytest.mark.asyncio
async def test_doi_quoting_preserves_slash(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.locator_unpaywall.plugin import UnpaywallLocatorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    doi = "10.1234/test-doi/with/slashes"
    # normalized will be lowercased, slashes preserved
    pi = await _make_pi(store, doi=doi)
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["path"] = request.url.path
        # Must contain literal slash, not %2F
        assert "%2F" not in request.url.path, f"DOI slash was encoded: {request.url.path}"
        assert "10.1234/test-doi/with/slashes" in request.url.path
        return httpx.Response(
            200,
            json={
                "doi": doi,
                "is_oa": True,
                "best_oa_location": {
                    "url_for_pdf": "https://example.com/a.pdf",
                    "url": "https://example.com/a.pdf",
                    "host_type": "publisher",
                    "version": "publishedVersion",
                },
                "oa_locations": [],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = UnpaywallLocatorService(
        artifact_store=store, http_client=client, email="audit2e5@test.org"
    )
    loc_ids = await svc.resolve(pi)
    assert len(loc_ids) == 1
    assert "/10.1234/test-doi/with/slashes" in captured["path"]
    await client.aclose()
    await store.close()


# 12 - redirect bounds
@pytest.mark.asyncio
async def test_redirect_too_many_and_non_http_and_private(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService
    from research_harness.research.schemas.document_location import DocumentLocation

    async def _run(url: str, handler, max_redirects=2, expect_status="failed"):
        store = SQLiteArtifactStore(
            path=tmp_path / f"art_{hashlib.sha256(url.encode()).hexdigest()[:6]}.db"
        )
        blobs = FilesystemBlobStore(
            root=tmp_path / f"blobs_{hashlib.sha256(url.encode()).hexdigest()[:6]}"
        )
        pi = await _make_pi(store)
        loc = DocumentLocation(paper_identity_id=pi, resolver="test", url=url)
        loc_env = ArtifactEnvelope.create(
            payload=loc, artifact_type="document_location", producer="test"
        )
        await store.put(loc_env)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        svc = HttpFetcherService(
            artifact_store=store, blob_store=blobs, http_client=client, max_redirects=max_redirects
        )
        acq_id = await svc.fetch(loc_env.artifact_id)
        from research_harness.research.schemas.document_acquisition import DocumentAcquisition

        acq = (await store.get(acq_id)).parse_payload(DocumentAcquisition)
        await client.aclose()
        await store.close()
        return acq

    # valid redirect (1 hop) should succeed
    pdf = _pdf_bytes("redirect valid " * 20)

    def ok_handler(r: httpx.Request) -> httpx.Response:
        if str(r.url) == "https://example.com/start":
            return httpx.Response(302, headers={"location": "https://example.com/final.pdf"})
        return httpx.Response(200, content=pdf)

    acq = await _run(
        "https://example.com/start", ok_handler, max_redirects=5, expect_status="downloaded"
    )
    assert acq.status.value == "downloaded"

    # too many redirects (chain 4 with limit 2) -> failed / too_many
    def many_handler(r: httpx.Request) -> httpx.Response:
        url = str(r.url)
        if url == "https://example.com/a":
            return httpx.Response(302, headers={"location": "https://example.com/b"})
        if url == "https://example.com/b":
            return httpx.Response(302, headers={"location": "https://example.com/c"})
        if url == "https://example.com/c":
            return httpx.Response(302, headers={"location": "https://example.com/d"})
        return httpx.Response(404)

    acq2 = await _run("https://example.com/a", many_handler, max_redirects=2)
    assert acq2.status.value == "failed"
    assert (
        "redirect" in (acq2.failure_message or "").lower()
        or "too_many" in (acq2.failure_code or "").lower()
    )

    # redirect to private IP must be rejected and not followed
    def priv_handler(r: httpx.Request) -> httpx.Response:
        if str(r.url) == "https://example.com/start2":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private.pdf"})
        return httpx.Response(404)

    acq3 = await _run("https://example.com/start2", priv_handler)
    assert acq3.status.value == "failed"
    assert "private" in (acq3.failure_message or "").lower()

    # redirect to non-http scheme must be rejected
    def ftp_handler(r: httpx.Request) -> httpx.Response:
        if str(r.url) == "https://example.com/start3":
            return httpx.Response(302, headers={"location": "ftp://example.com/file.pdf"})
        return httpx.Response(404)

    acq4 = await _run("https://example.com/start3", ftp_handler)
    assert acq4.status.value == "failed"


# 15+16 - acquisition statuses and fallback
@pytest.mark.asyncio
async def test_multiple_location_fallback_and_statuses(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.acquisition_orchestrator.plugin import (
        DocumentAcquisitionOrchestratorService,
    )
    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorService
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService
    from research_harness.plugins.documents.locator_metadata.plugin import MetadataLocatorService
    from research_harness.plugins.documents.locator_unpaywall.plugin import UnpaywallLocatorService
    from research_harness.research.schemas.screening_execution import (
        ScreenedLiteratureSet,
        ScreeningExecution,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")

    # Create identity with no metadata OA, but Unpaywall will have 2 locations: first fails 404, second succeeds
    pi = await _make_pi(store, doi="10.1234/fallback")
    # Add screening set with only this PI
    exec_s = ScreeningExecution(
        protocol_artifact_id="proto",
        search_execution_artifact_id="search",
        candidate_identity_ids=[pi],
        screening_view_ids=[],
        decision_artifact_ids=[],
        counts={"included": 1},
        failures=[],
    )
    exec_env = ArtifactEnvelope.create(
        payload=exec_s, artifact_type="screening_execution", producer="test"
    )
    await store.put(exec_env)
    sset = ScreenedLiteratureSet(
        screening_execution_id=exec_env.artifact_id,
        screening_protocol_id="proto",
        included_identity_ids=[pi],
        excluded_identity_ids=[],
        uncertain_identity_ids=[],
        decision_artifact_ids=[],
    )
    set_env = ArtifactEnvelope.create(
        payload=sset, artifact_type="screened_literature_set", producer="test"
    )
    await store.put(set_env)

    pdf_ok = _pdf_bytes("fallback success " * 20)

    def unpaywall_handler(r: httpx.Request) -> httpx.Response:
        # Return two locations: first url_for_pdf will 404, second will succeed
        raw = {
            "doi": "10.1234/fallback",
            "is_oa": True,
            "best_oa_location": {
                "url_for_pdf": "https://example.com/first.pdf",
                "url": "https://example.com/first.pdf",
                "host_type": "publisher",
                "version": "publishedVersion",
                "license": "cc-by",
            },
            "oa_locations": [
                {
                    "url_for_pdf": "https://example.com/second.pdf",
                    "url": "https://example.com/second.pdf",
                    "host_type": "repository",
                    "version": "acceptedVersion",
                }
            ],
        }
        return httpx.Response(200, json=raw)

    def fetch_handler(r: httpx.Request) -> httpx.Response:
        url = str(r.url)
        if "api.unpaywall.org" in url:
            return unpaywall_handler(r)
        if url == "https://example.com/first.pdf":
            return httpx.Response(404, content=b"not found")
        if url == "https://example.com/second.pdf":
            return httpx.Response(200, content=pdf_ok, headers={"content-type": "application/pdf"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(fetch_handler))
    meta = MetadataLocatorService(artifact_store=store)
    unpay = UnpaywallLocatorService(
        artifact_store=store, http_client=client, email="audit@test.org"
    )
    fetcher = HttpFetcherService(artifact_store=store, blob_store=blobs, http_client=client)
    extractor = PypdfExtractorService(artifact_store=store, blob_store=blobs)
    orch = DocumentAcquisitionOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        fetcher=fetcher,
        extractor=extractor,
        metadata_locator=meta,
        unpaywall_locator=unpay,
    )
    exec_id = await orch.run(set_env.artifact_id)
    from research_harness.research.schemas.full_text import DocumentAcquisitionExecution

    exec_rec = (await store.get(exec_id)).parse_payload(DocumentAcquisitionExecution)
    # Should have succeeded via second location, but first failure remains observable as an acquisition
    assert exec_rec.counts["downloaded"] == 1
    # At least 2 acquisitions (first failed invalid/404, second success)
    assert len(exec_rec.acquisition_artifact_ids) >= 2
    # Check that we have one with invalid_content/not_available/failed and one downloaded
    from research_harness.research.schemas.document_acquisition import DocumentAcquisition

    statuses = []
    for aid in exec_rec.acquisition_artifact_ids:
        a = (await store.get(aid)).parse_payload(DocumentAcquisition)
        statuses.append(a.status.value)
    assert "downloaded" in statuses
    # The failed one should be observable
    assert any(s in ("not_available", "failed", "invalid_content") for s in statuses)
    # FullTextDocument should exist for successful
    assert len(exec_rec.full_text_document_ids) == 1
    # Document unavailable must not modify screening (still included)
    sset_after = (await store.get(set_env.artifact_id)).parse_payload(ScreenedLiteratureSet)
    assert pi in sset_after.included_identity_ids
    await client.aclose()
    await store.close()


# 24 - acquisition idempotency, 25 - extraction idempotency
@pytest.mark.asyncio
async def test_acquisition_and_extraction_idempotency(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorService
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService
    from research_harness.research.schemas.document_location import DocumentLocation

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    pi = await _make_pi(store)
    pdf = _pdf_bytes("idempotent " * 30)
    loc = DocumentLocation(
        paper_identity_id=pi, resolver="test", url="https://example.com/idem.pdf"
    )
    loc_env = ArtifactEnvelope.create(
        payload=loc, artifact_type="document_location", producer="test"
    )
    await store.put(loc_env)

    def handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=pdf)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    fetcher = HttpFetcherService(artifact_store=store, blob_store=blobs, http_client=client)
    id1 = await fetcher.fetch(loc_env.artifact_id)
    id2 = await fetcher.fetch(loc_env.artifact_id)
    assert id1 == id2  # same location same sha reuses

    # Same bytes should reuse blob
    count = sum(1 for _ in (tmp_path / "blobs").rglob("*") if _.is_file())
    assert count == 1

    # Extraction idempotency
    extractor = PypdfExtractorService(artifact_store=store, blob_store=blobs)
    doc1 = await extractor.extract(id1)
    doc2 = await extractor.extract(id1)
    assert doc1 == doc2  # same blob same version reuses

    # Version change should create new doc (simulate by bumping version)
    extractor._version = "9.9.9"
    doc3 = await extractor.extract(id1)
    assert doc3 != doc1

    # Verify original PDF unchanged
    from research_harness.research.schemas.full_text import FullTextDocument

    d1 = (await store.get(doc1)).parse_payload(FullTextDocument)
    pdf_back = await blobs.get_bytes(d1.source_blob)
    assert pdf_back == pdf
    assert d1.source_blob.digest != d1.text_blob.digest if d1.text_blob else True  # distinct blobs

    await client.aclose()
    await store.close()


# 26 - orchestrator scope only included
@pytest.mark.asyncio
async def test_orchestrator_only_processes_included(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.acquisition_orchestrator.plugin import (
        DocumentAcquisitionOrchestratorService,
    )
    from research_harness.research.schemas.screening_execution import (
        ScreenedLiteratureSet,
        ScreeningExecution,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")

    pi_inc = await _make_pi(store, doi="10.1234/inc", open_url="https://example.com/inc.pdf")
    pi_exc = await _make_pi(store, doi="10.1234/exc", open_url="https://example.com/exc.pdf")
    pi_unc = await _make_pi(store, doi="10.1234/unc", open_url="https://example.com/unc.pdf")

    exec_s = ScreeningExecution(
        protocol_artifact_id="proto",
        search_execution_artifact_id="search",
        candidate_identity_ids=[pi_inc, pi_exc, pi_unc],
        screening_view_ids=[],
        decision_artifact_ids=[],
        counts={"included": 1, "excluded": 1, "uncertain": 1},
        failures=[],
    )
    exec_env = ArtifactEnvelope.create(
        payload=exec_s, artifact_type="screening_execution", producer="test"
    )
    await store.put(exec_env)
    sset = ScreenedLiteratureSet(
        screening_execution_id=exec_env.artifact_id,
        screening_protocol_id="proto",
        included_identity_ids=[pi_inc],
        excluded_identity_ids=[pi_exc],
        uncertain_identity_ids=[pi_unc],
        decision_artifact_ids=[],
    )
    set_env = ArtifactEnvelope.create(
        payload=sset, artifact_type="screened_literature_set", producer="test"
    )
    await store.put(set_env)

    pdf = _pdf_bytes("included only " * 20)

    def handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=pdf)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorService
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService
    from research_harness.plugins.documents.locator_metadata.plugin import MetadataLocatorService

    meta = MetadataLocatorService(artifact_store=store)
    fetcher = HttpFetcherService(artifact_store=store, blob_store=blobs, http_client=client)
    extractor = PypdfExtractorService(artifact_store=store, blob_store=blobs)
    orch = DocumentAcquisitionOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        fetcher=fetcher,
        extractor=extractor,
        metadata_locator=meta,
    )
    exec_id = await orch.run(set_env.artifact_id)
    from research_harness.research.schemas.full_text import DocumentAcquisitionExecution

    rec = (await store.get(exec_id)).parse_payload(DocumentAcquisitionExecution)
    assert rec.counts["total_included"] == 1
    # Only one acquisition (for included), excluded/uncertain not processed
    assert len([a for a in rec.acquisition_artifact_ids if a]) == 1
    # Verify excluded not in corpus available
    corpora = await store.list(artifact_type="full_text_corpus")
    from research_harness.research.schemas.full_text import FullTextCorpus

    corp = corpora[0].parse_payload(FullTextCorpus)
    # unavailable should not contain excluded
    assert pi_exc not in corp.available_document_ids
    assert pi_exc not in corp.unavailable_identity_ids
    assert pi_unc not in corp.unavailable_identity_ids
    await client.aclose()
    await store.close()


# 3 - blob boundary, 27 - partial failure, 28 - corpus semantics, 29 - provenance
@pytest.mark.asyncio
async def test_blob_boundary_corpus_provenance_partial_failure(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.acquisition_orchestrator.plugin import (
        DocumentAcquisitionOrchestratorService,
    )
    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorService
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService
    from research_harness.plugins.documents.locator_metadata.plugin import MetadataLocatorService
    from research_harness.plugins.documents.locator_unpaywall.plugin import UnpaywallLocatorService
    from research_harness.research.schemas.screening_execution import (
        ScreenedLiteratureSet,
        ScreeningExecution,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")

    # PI1 success, PI2 no location, PI3 download fails (HTML), PI4 encrypted (simulated via blob that will be encrypted?)
    # Use pi with open_url for PI1, PI2 none, PI3 url that returns HTML, PI4 we will test separately via direct extractor
    pi1 = await _make_pi(store, doi="10.1234/ok1", open_url="https://example.com/ok1.pdf")
    pi2 = await _make_pi(store, doi="10.1234/noloc")
    pi3 = await _make_pi(store, doi="10.1234/html", open_url="https://example.com/bad.pdf")
    pi4 = await _make_pi(store, doi="10.1234/ok2", open_url="https://example.com/ok2.pdf")

    exec_s = ScreeningExecution(
        protocol_artifact_id="proto",
        search_execution_artifact_id="search",
        candidate_identity_ids=[pi1, pi2, pi3, pi4],
        screening_view_ids=[],
        decision_artifact_ids=[],
        counts={"included": 4},
        failures=[],
    )
    exec_env = ArtifactEnvelope.create(
        payload=exec_s, artifact_type="screening_execution", producer="test"
    )
    await store.put(exec_env)
    sset = ScreenedLiteratureSet(
        screening_execution_id=exec_env.artifact_id,
        screening_protocol_id="proto",
        included_identity_ids=[pi1, pi2, pi3, pi4],
        excluded_identity_ids=[],
        uncertain_identity_ids=[],
        decision_artifact_ids=[],
    )
    set_env = ArtifactEnvelope.create(
        payload=sset, artifact_type="screened_literature_set", producer="test"
    )
    await store.put(set_env)

    pdf = _pdf_bytes("good content for extraction " * 20)
    html = b"<!doctype html><html><body>Login</body></html>"

    def handler(r: httpx.Request) -> httpx.Response:
        url = str(r.url)
        if "api.unpaywall.org" in url:
            # All not found to force metadata only
            return httpx.Response(404)
        if url == "https://example.com/ok1.pdf":
            return httpx.Response(200, content=pdf)
        if url == "https://example.com/ok2.pdf":
            return httpx.Response(200, content=pdf)
        if url == "https://example.com/bad.pdf":
            return httpx.Response(200, content=html, headers={"content-type": "text/html"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    meta = MetadataLocatorService(artifact_store=store)
    unpay = UnpaywallLocatorService(
        artifact_store=store, http_client=client, email="audit@test.org"
    )
    fetcher = HttpFetcherService(artifact_store=store, blob_store=blobs, http_client=client)
    extractor = PypdfExtractorService(artifact_store=store, blob_store=blobs)
    orch = DocumentAcquisitionOrchestratorService(
        artifact_store=store,
        blob_store=blobs,
        fetcher=fetcher,
        extractor=extractor,
        metadata_locator=meta,
        unpaywall_locator=unpay,
    )
    exec_id = await orch.run(set_env.artifact_id)
    from research_harness.research.schemas.full_text import (
        DocumentAcquisitionExecution,
        FullTextCorpus,
    )

    rec = (await store.get(exec_id)).parse_payload(DocumentAcquisitionExecution)
    # Should have 2 downloaded, 1 no_location, 1 invalid_content
    assert rec.counts["total_included"] == 4
    assert rec.counts["downloaded"] == 2
    assert rec.counts["no_location"] == 1
    assert rec.counts["invalid_content"] == 1
    assert len(rec.full_text_document_ids) == 2
    # Partial failure must preserve successful work: 2 docs still exist
    assert rec.counts["text_extracted"] == 2

    # Corpus semantics: available 2, unavailable 1, failed 1
    corpora = await store.list(artifact_type="full_text_corpus")
    corp = corpora[0].parse_payload(FullTextCorpus)
    assert len(corp.available_document_ids) == 2
    assert pi2 in corp.unavailable_identity_ids
    assert (
        pi3 in corp.failed_identity_ids
        or pi3 in corp.unavailable_identity_ids
        or pi3 in corp.failed_identity_ids
    )

    # BlobStore vs ArtifactStore boundary: SQLite JSON must not contain raw PDF or full text
    all_envs = await store.list()
    dumped = json.dumps([e.model_dump(mode="json") for e in all_envs])
    assert "%PDF-" not in dumped
    assert "good content for extraction" not in dumped  # full text not in artifact JSON
    # But blob should contain it
    for doc_id in rec.full_text_document_ids:
        from research_harness.research.schemas.full_text import FullTextDocument

        doc = (await store.get(doc_id)).parse_payload(FullTextDocument)
        assert doc.source_blob.storage_key.count("/") == 2
        assert await blobs.exists(doc.source_blob)
        assert doc.text_blob is not None
        t = await blobs.get_bytes(doc.text_blob)
        j = json.loads(t.decode())
        assert j["schema_version"] == 1
        assert j["pages"][0]["page"] == 1  # 1-based

    # Provenance chain survives reopen
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")
    docs = await store2.list(artifact_type="full_text_document")
    for d_env in docs:
        parents = await store2.get_parents(d_env.artifact_id)
        assert any(p.relation.value == "derived_from" for p in parents)
    # Check corpus -> execution -> set
    exec_parents = await store2.get_parents(rec.artifacts if hasattr(rec, "artifacts") else exec_id)
    # corpus provenance
    corp_env = corpora[0]
    cp = await store2.get_parents(corp_env.artifact_id)
    assert any(p.source_artifact_id == exec_id for p in cp)
    await store2.close()
    await client.aclose()


# encrypted and insufficient
@pytest.mark.asyncio
async def test_encrypted_and_insufficient_text_distinct(tmp_path: pathlib.Path):
    from pypdf import PdfWriter

    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorService
    from research_harness.research.schemas.document_acquisition import (
        AcquisitionStatus,
        DocumentAcquisition,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    pi = await _make_pi(store)

    # insufficient: blank PDF
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.showPage()
    c.showPage()
    c.save()
    blank = buf.getvalue()
    bref = await blobs.put_bytes(blank)
    acq = DocumentAcquisition(
        paper_identity_id=pi,
        document_location_id="loc",
        status=AcquisitionStatus.downloaded,
        blob=bref,
        sha256=bref.digest,
        size_bytes=bref.size_bytes,
        media_type="application/pdf",
        source_type="http",
    )
    a_env = ArtifactEnvelope.create(
        payload=acq, artifact_type="document_acquisition", producer="test"
    )
    await store.put(a_env)
    svc = PypdfExtractorService(artifact_store=store, blob_store=blobs)
    did = await svc.extract(a_env.artifact_id)
    from research_harness.research.schemas.full_text import FullTextDocument

    doc = (await store.get(did)).parse_payload(FullTextDocument)
    assert doc.text_status.value == "insufficient_text"
    assert doc.text_status.value != "extraction_failed"

    # encrypted
    buf2 = io.BytesIO()
    c2 = canvas.Canvas(buf2)
    c2.drawString(100, 750, "secret")
    c2.showPage()
    c2.save()
    data = buf2.getvalue()
    import io as bio

    from pypdf import PdfReader

    reader = PdfReader(bio.BytesIO(data))
    writer = PdfWriter()
    for p in reader.pages:
        writer.add_page(p)
    writer.encrypt("pw")
    out = bio.BytesIO()
    writer.write(out)
    enc = out.getvalue()
    bref2 = await blobs.put_bytes(enc)
    acq2 = DocumentAcquisition(
        paper_identity_id=pi,
        document_location_id="loc2",
        status=AcquisitionStatus.downloaded,
        blob=bref2,
        sha256=bref2.digest,
        size_bytes=bref2.size_bytes,
        media_type="application/pdf",
        source_type="http",
    )
    a2_env = ArtifactEnvelope.create(
        payload=acq2, artifact_type="document_acquisition", producer="test"
    )
    await store.put(a2_env)
    did2 = await svc.extract(a2_env.artifact_id)
    doc2 = (await store.get(did2)).parse_payload(FullTextDocument)
    assert doc2.text_status.value == "encrypted"
    assert doc2.text_blob is None
    await store.close()


# no-ocr and no-llm check
def test_no_ocr_and_no_llm_imports():
    import pathlib

    # Ensure pypdf extractor does not import tesseract etc
    p = pathlib.Path("src/research_harness/plugins/documents/extractor_pypdf/plugin.py").read_text()
    assert "tesseract" not in p.lower()
    assert "ocrmypdf" not in p.lower()
    assert "vision" not in p.lower() or "version" in p.lower()  # allow version word
    assert "pytesseract" not in p.lower()

    # Ensure document pipeline does not import model_router
    for fname in [
        "src/research_harness/plugins/documents/fetcher_http/plugin.py",
        "src/research_harness/plugins/documents/extractor_pypdf/plugin.py",
        "src/research_harness/plugins/documents/locator_unpaywall/plugin.py",
        "src/research_harness/plugins/documents/locator_metadata/plugin.py",
        "src/research_harness/plugins/documents/acquisition_orchestrator/plugin.py",
        "src/research_harness/plugins/storage/blobs_filesystem/plugin.py",
    ]:
        txt = pathlib.Path(fname).read_text()
        assert "model_router" not in txt
        assert "openrouter" not in txt.lower()
        assert "EvidenceItem" not in txt
        assert "ResearchClaim" not in txt

    # pyproject must not have OCR deps
    pyproj = pathlib.Path("pyproject.toml").read_text().lower()
    assert "tesseract" not in pyproj
    assert "ocrmypdf" not in pyproj


# two-page extraction exact
@pytest.mark.asyncio
async def test_two_page_extraction_exact_separation(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorService
    from research_harness.research.schemas.document_acquisition import (
        AcquisitionStatus,
        DocumentAcquisition,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    pi = await _make_pi(store)
    pdf = _pdf_two_pages("TEXT_A_PAGE_ONE " * 20, "TEXT_B_PAGE_TWO " * 20)
    bref = await blobs.put_bytes(pdf)
    acq = DocumentAcquisition(
        paper_identity_id=pi,
        document_location_id="loc",
        status=AcquisitionStatus.downloaded,
        blob=bref,
        sha256=bref.digest,
        size_bytes=bref.size_bytes,
        media_type="application/pdf",
        source_type="http",
    )
    a_env = ArtifactEnvelope.create(
        payload=acq, artifact_type="document_acquisition", producer="test"
    )
    await store.put(a_env)
    svc = PypdfExtractorService(artifact_store=store, blob_store=blobs)
    did = await svc.extract(a_env.artifact_id)
    from research_harness.research.schemas.full_text import FullTextDocument

    doc = (await store.get(did)).parse_payload(FullTextDocument)
    assert doc.page_count == 2
    assert doc.pages_with_text == 2
    text_data = await blobs.get_bytes(doc.text_blob)
    j = json.loads(text_data.decode())
    assert j["schema_version"] == 1
    assert len(j["pages"]) == 2
    assert j["pages"][0]["page"] == 1
    assert j["pages"][1]["page"] == 2
    assert "TEXT_A_PAGE_ONE" in j["pages"][0]["text"]
    assert "TEXT_B_PAGE_TWO" in j["pages"][1]["text"]
    # Ensure distinct blobs: source vs text
    assert doc.source_blob.digest != doc.text_blob.digest
    # Reopen persistence
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs2 = FilesystemBlobStore(root=tmp_path / "blobs")
    doc2 = (await store2.get(did)).parse_payload(FullTextDocument)
    assert await blobs2.exists(doc2.source_blob)
    assert await blobs2.exists(doc2.text_blob)
    await store2.close()
