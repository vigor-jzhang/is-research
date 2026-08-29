import hashlib
import io
import pathlib

import httpx
import pytest
from reportlab.pdfgen import canvas

from research_harness.plugins.documents.fetcher_http import plugin as fetcher_http
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.plugins.storage.blobs_filesystem.plugin import FilesystemBlobStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.document_location import DocumentLocation


def _make_pdf_bytes(text="Hello PDF") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def _make_location(store, url, paper_id="pi1"):
    # Helper sync creation via async? We will create directly in test
    pass


@pytest.mark.asyncio
async def test_fetcher_valid_pdf(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    pdf_bytes = _make_pdf_bytes("valid pdf")
    # Create location
    loc = DocumentLocation(
        paper_identity_id="pi1",
        resolver="test",
        url="https://example.com/paper.pdf",
        is_direct_download=True,
    )
    loc_env = ArtifactEnvelope.create(
        payload=loc, artifact_type="document_location", producer="test"
    )
    await store.put(loc_env)
    # Create identity for provenance
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.paper import PaperRecord

    paper = PaperRecord(title="T")
    p_env = ArtifactEnvelope.create(payload=paper, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)
    # Need to recreate location with correct pi id
    loc2 = DocumentLocation(
        paper_identity_id=ident_env.artifact_id,
        resolver="test",
        url="https://example.com/paper.pdf",
        is_direct_download=True,
    )
    loc2_env = ArtifactEnvelope.create(
        payload=loc2, artifact_type="document_location", producer="test"
    )
    await store.put(loc2_env)

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.com/paper.pdf"
        return httpx.Response(
            200,
            content=pdf_bytes,
            headers={"content-type": "application/pdf", "content-length": str(len(pdf_bytes))},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = HttpFetcherService(
        artifact_store=store, blob_store=blobs, http_client=client, max_bytes=50 * 1024 * 1024
    )
    acq_id = await svc.fetch(loc2_env.artifact_id)
    from research_harness.research.schemas.document_acquisition import DocumentAcquisition

    acq = (await store.get(acq_id)).parse_payload(DocumentAcquisition)
    assert acq.status.value == "downloaded"
    assert acq.sha256 == hashlib.sha256(pdf_bytes).hexdigest()
    assert await blobs.exists(acq.blob)  # type: ignore[arg-type]
    assert (await blobs.get_bytes(acq.blob)) == pdf_bytes  # type: ignore[arg-type]
    await client.aclose()
    await store.close()


@pytest.mark.asyncio
async def test_fetcher_redirect_to_valid_pdf(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    pdf_bytes = _make_pdf_bytes("redirected")
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.paper import PaperRecord

    paper = PaperRecord(title="T")
    p_env = ArtifactEnvelope.create(payload=paper, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)
    loc = DocumentLocation(
        paper_identity_id=ident_env.artifact_id,
        resolver="test",
        url="https://example.com/redirect",
        is_direct_download=True,
    )
    loc_env = ArtifactEnvelope.create(
        payload=loc, artifact_type="document_location", producer="test"
    )
    await store.put(loc_env)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.com/redirect":
            return httpx.Response(302, headers={"location": "https://example.com/final.pdf"})
        if str(request.url) == "https://example.com/final.pdf":
            return httpx.Response(200, content=pdf_bytes)
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = HttpFetcherService(artifact_store=store, blob_store=blobs, http_client=client)
    acq_id = await svc.fetch(loc_env.artifact_id)
    from research_harness.research.schemas.document_acquisition import DocumentAcquisition

    acq = (await store.get(acq_id)).parse_payload(DocumentAcquisition)
    assert acq.status.value == "downloaded"
    assert acq.final_url == "https://example.com/final.pdf"
    await client.aclose()
    await store.close()


@pytest.mark.asyncio
async def test_fetcher_html_instead_of_pdf(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.paper import PaperRecord

    paper = PaperRecord(title="T")
    p_env = ArtifactEnvelope.create(payload=paper, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)
    loc = DocumentLocation(
        paper_identity_id=ident_env.artifact_id,
        resolver="test",
        url="https://example.com/paper.pdf",
    )
    loc_env = ArtifactEnvelope.create(
        payload=loc, artifact_type="document_location", producer="test"
    )
    await store.put(loc_env)

    html = b"<!doctype html><html><body>Login required</body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html, headers={"content-type": "text/html"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = HttpFetcherService(artifact_store=store, blob_store=blobs, http_client=client)
    acq_id = await svc.fetch(loc_env.artifact_id)
    from research_harness.research.schemas.document_acquisition import DocumentAcquisition

    acq = (await store.get(acq_id)).parse_payload(DocumentAcquisition)
    assert acq.status.value == "invalid_content"
    await client.aclose()
    await store.close()


@pytest.mark.asyncio
async def test_fetcher_oversized_content_length(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.paper import PaperRecord

    paper = PaperRecord(title="T")
    p_env = ArtifactEnvelope.create(payload=paper, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)
    loc = DocumentLocation(
        paper_identity_id=ident_env.artifact_id, resolver="test", url="https://example.com/big.pdf"
    )
    loc_env = ArtifactEnvelope.create(
        payload=loc, artifact_type="document_location", producer="test"
    )
    await store.put(loc_env)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x", headers={"content-length": str(100 * 1024 * 1024)})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = HttpFetcherService(
        artifact_store=store, blob_store=blobs, http_client=client, max_bytes=50 * 1024 * 1024
    )
    acq_id = await svc.fetch(loc_env.artifact_id)
    from research_harness.research.schemas.document_acquisition import DocumentAcquisition

    acq = (await store.get(acq_id)).parse_payload(DocumentAcquisition)
    assert acq.status.value == "too_large"
    await client.aclose()
    await store.close()


@pytest.mark.asyncio
async def test_fetcher_stream_exceeds_max_bytes(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.paper import PaperRecord

    paper = PaperRecord(title="T")
    p_env = ArtifactEnvelope.create(payload=paper, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)
    loc = DocumentLocation(
        paper_identity_id=ident_env.artifact_id,
        resolver="test",
        url="https://example.com/stream.pdf",
    )
    loc_env = ArtifactEnvelope.create(
        payload=loc, artifact_type="document_location", producer="test"
    )
    await store.put(loc_env)

    # No content-length, stream will exceed
    big_content = b"%PDF-" + b"x" * 1000

    def handler(request: httpx.Request) -> httpx.Response:
        # Return without content-length, but body is big
        return httpx.Response(200, content=big_content)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = HttpFetcherService(
        artifact_store=store, blob_store=blobs, http_client=client, max_bytes=500
    )
    acq_id = await svc.fetch(loc_env.artifact_id)
    from research_harness.research.schemas.document_acquisition import DocumentAcquisition

    acq = (await store.get(acq_id)).parse_payload(DocumentAcquisition)
    assert acq.status.value == "too_large"
    await client.aclose()
    await store.close()


@pytest.mark.asyncio
async def test_fetcher_404_and_429(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.paper import PaperRecord

    for status in [404, 429]:
        paper = PaperRecord(title="T")
        p_env = ArtifactEnvelope.create(
            payload=paper, artifact_type="paper_record", producer="test"
        )
        await store.put(p_env)
        ident = PaperIdentity(
            member_paper_artifact_ids=[p_env.artifact_id],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.exact_identifier,
            resolution_evidence=[],
        )
        ident_env = ArtifactEnvelope.create(
            payload=ident, artifact_type="paper_identity", producer="test"
        )
        await store.put(ident_env)
        loc = DocumentLocation(
            paper_identity_id=ident_env.artifact_id,
            resolver="test",
            url=f"https://example.com/{status}.pdf",
        )
        loc_env = ArtifactEnvelope.create(
            payload=loc, artifact_type="document_location", producer="test"
        )
        await store.put(loc_env)

        def handler(request: httpx.Request, s=status) -> httpx.Response:
            return httpx.Response(s, content=b"not found")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        svc = HttpFetcherService(artifact_store=store, blob_store=blobs, http_client=client)
        acq_id = await svc.fetch(loc_env.artifact_id)
        from research_harness.research.schemas.document_acquisition import DocumentAcquisition

        acq = (await store.get(acq_id)).parse_payload(DocumentAcquisition)
        # 404 -> not_available or failed, 429 -> failed
        assert acq.status.value in ("not_available", "failed")
        await client.aclose()
    await store.close()


@pytest.mark.asyncio
async def test_fetcher_private_ip_rejection(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.paper import PaperRecord

    cases = [
        "http://127.0.0.1/paper.pdf",
        "http://localhost/paper.pdf",
        "http://10.0.0.5/paper.pdf",
        "http://192.168.1.1/paper.pdf",
        "http://172.16.0.5/paper.pdf",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/paper.pdf",
        "https://10.1.2.3/file.pdf",
    ]
    for url in cases:
        paper = PaperRecord(title="T")
        p_env = ArtifactEnvelope.create(
            payload=paper, artifact_type="paper_record", producer="test"
        )
        await store.put(p_env)
        ident = PaperIdentity(
            member_paper_artifact_ids=[p_env.artifact_id],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.exact_identifier,
            resolution_evidence=[],
        )
        ident_env = ArtifactEnvelope.create(
            payload=ident, artifact_type="paper_identity", producer="test"
        )
        await store.put(ident_env)
        loc = DocumentLocation(paper_identity_id=ident_env.artifact_id, resolver="test", url=url)
        loc_env = ArtifactEnvelope.create(
            payload=loc, artifact_type="document_location", producer="test"
        )
        await store.put(loc_env)

        # Even with client that would succeed, fetcher should reject before request
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=_make_pdf_bytes())

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        svc = HttpFetcherService(artifact_store=store, blob_store=blobs, http_client=client)
        acq_id = await svc.fetch(loc_env.artifact_id)
        from research_harness.research.schemas.document_acquisition import DocumentAcquisition

        acq = (await store.get(acq_id)).parse_payload(DocumentAcquisition)
        assert acq.status.value == "failed"
        assert (
            "private" in (acq.failure_message or "").lower()
            or "private" in (acq.failure_code or "").lower()
            or acq.failure_code == "invalid_url"
        )
        await client.aclose()
    await store.close()


@pytest.mark.asyncio
async def test_fetcher_redirect_to_private_ip_rejection(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.paper import PaperRecord

    paper = PaperRecord(title="T")
    p_env = ArtifactEnvelope.create(payload=paper, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)
    loc = DocumentLocation(
        paper_identity_id=ident_env.artifact_id,
        resolver="test",
        url="https://example.com/start.pdf",
    )
    loc_env = ArtifactEnvelope.create(
        payload=loc, artifact_type="document_location", producer="test"
    )
    await store.put(loc_env)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://example.com/start.pdf":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private.pdf"})
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = HttpFetcherService(artifact_store=store, blob_store=blobs, http_client=client)
    acq_id = await svc.fetch(loc_env.artifact_id)
    from research_harness.research.schemas.document_acquisition import DocumentAcquisition

    acq = (await store.get(acq_id)).parse_payload(DocumentAcquisition)
    assert acq.status.value == "failed"
    assert "private" in (acq.failure_message or "").lower()
    await client.aclose()
    await store.close()


@pytest.mark.asyncio
async def test_fetcher_non_http_scheme_rejection(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.paper import PaperRecord

    for url in ["file:///etc/passwd", "ftp://example.com/file.pdf"]:
        paper = PaperRecord(title="T")
        p_env = ArtifactEnvelope.create(
            payload=paper, artifact_type="paper_record", producer="test"
        )
        await store.put(p_env)
        ident = PaperIdentity(
            member_paper_artifact_ids=[p_env.artifact_id],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.exact_identifier,
            resolution_evidence=[],
        )
        ident_env = ArtifactEnvelope.create(
            payload=ident, artifact_type="paper_identity", producer="test"
        )
        await store.put(ident_env)
        # DocumentLocation validation would reject non-http, but we test fetcher directly via creation bypassing validation?
        # Create via direct model without validator? Use object with url that bypasses? Instead test fetcher's _validate_url directly
        # Instead we test that creating DocumentLocation with file:// raises
        with pytest.raises(Exception):
            loc = DocumentLocation(
                paper_identity_id=ident_env.artifact_id, resolver="test", url=url
            )
            loc_env = ArtifactEnvelope.create(
                payload=loc, artifact_type="document_location", producer="test"
            )
            await store.put(loc_env)

            # If it somehow succeeded, fetcher should also reject
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, content=b"data")

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            svc = HttpFetcherService(artifact_store=store, blob_store=blobs, http_client=client)
            acq_id = await svc.fetch(loc_env.artifact_id)
            from research_harness.research.schemas.document_acquisition import DocumentAcquisition

            acq = (await store.get(acq_id)).parse_payload(DocumentAcquisition)
            assert acq.status.value == "failed"
            await client.aclose()
    await store.close()


@pytest.mark.asyncio
async def test_fetcher_timeout(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.paper import PaperRecord

    paper = PaperRecord(title="T")
    p_env = ArtifactEnvelope.create(payload=paper, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)
    loc = DocumentLocation(
        paper_identity_id=ident_env.artifact_id,
        resolver="test",
        url="https://example.com/timeout.pdf",
    )
    loc_env = ArtifactEnvelope.create(
        payload=loc, artifact_type="document_location", producer="test"
    )
    await store.put(loc_env)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timeout")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = HttpFetcherService(artifact_store=store, blob_store=blobs, http_client=client)
    acq_id = await svc.fetch(loc_env.artifact_id)
    from research_harness.research.schemas.document_acquisition import DocumentAcquisition

    acq = (await store.get(acq_id)).parse_payload(DocumentAcquisition)
    assert acq.status.value == "failed"
    assert acq.failure_code == "timeout"
    await client.aclose()
    await store.close()


def test_fetcher_rejects_hostname_resolving_to_private_ip(monkeypatch):
    monkeypatch.setattr(
        fetcher_http.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(ValueError, match="resolves to private"):
        fetcher_http._validate_url("https://public-looking.example/paper.pdf")


def test_fetcher_rejects_non_global_cgnat_address(monkeypatch):
    monkeypatch.setattr(
        fetcher_http.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("100.64.0.1", 443))],
    )
    with pytest.raises(ValueError, match="non-global"):
        fetcher_http._validate_url("https://public-looking.example/paper.pdf")
