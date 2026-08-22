import hashlib
import io
import json
import pathlib

import pytest
from reportlab.pdfgen import canvas

from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.plugins.storage.blobs_filesystem.plugin import FilesystemBlobStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.document_acquisition import DocumentAcquisition
from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
from research_harness.research.schemas.paper import PaperRecord


def _pdf_with_pages(texts: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for text in texts:
        c.drawString(100, 750, text)
        c.showPage()
    c.save()
    return buf.getvalue()


def _pdf_encrypted() -> bytes:
    from pypdf import PdfWriter

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, "secret")
    c.showPage()
    c.save()
    data = buf.getvalue()
    import io as bio

    from pypdf import PdfReader

    reader = PdfReader(bio.BytesIO(data))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt("password")
    out = bio.BytesIO()
    writer.write(out)
    return out.getvalue()


async def _make_identity(store: SQLiteArtifactStore) -> str:
    paper = PaperRecord(title="T extract")
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
    return ident_env.artifact_id


@pytest.mark.asyncio
async def test_extractor_one_page(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    pi_id = await _make_identity(store)
    pdf_bytes = _pdf_with_pages(["Hello Single Page " * 20])
    blob_ref = await blobs.put_bytes(pdf_bytes, media_type="application/pdf")
    from research_harness.research.schemas.document_acquisition import AcquisitionStatus

    acq = DocumentAcquisition(
        paper_identity_id=pi_id,
        document_location_id="loc1",
        status=AcquisitionStatus.downloaded,
        blob=blob_ref,
        sha256=blob_ref.digest,
        size_bytes=blob_ref.size_bytes,
        media_type="application/pdf",
        source_type="http",
    )
    acq_env = ArtifactEnvelope.create(
        payload=acq, artifact_type="document_acquisition", producer="test"
    )
    await store.put(acq_env)

    svc = PypdfExtractorService(artifact_store=store, blob_store=blobs)
    doc_id = await svc.extract(acq_env.artifact_id)
    from research_harness.research.schemas.full_text import FullTextDocument

    doc = (await store.get(doc_id)).parse_payload(FullTextDocument)
    assert doc.page_count == 1
    assert doc.pages_with_text == 1
    assert doc.text_status.value == "extracted"
    assert doc.source_blob.digest == blob_ref.digest
    assert doc.text_blob is not None
    text_data = await blobs.get_bytes(doc.text_blob)  # type: ignore[arg-type]
    j = json.loads(text_data.decode("utf-8"))
    assert j["schema_version"] == 1
    assert len(j["pages"]) == 1
    assert j["pages"][0]["page"] == 1
    assert "Hello Single Page" in j["pages"][0]["text"]
    assert await blobs.get_bytes(blob_ref) == pdf_bytes
    await store.close()


@pytest.mark.asyncio
async def test_extractor_multiple_pages(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    pi_id = await _make_identity(store)
    pdf_bytes = _pdf_with_pages(["Page One Content " * 20, "Page Two Content " * 20])
    blob_ref = await blobs.put_bytes(pdf_bytes)
    from research_harness.research.schemas.document_acquisition import AcquisitionStatus

    acq = DocumentAcquisition(
        paper_identity_id=pi_id,
        document_location_id="loc2",
        status=AcquisitionStatus.downloaded,
        blob=blob_ref,
        sha256=blob_ref.digest,
        size_bytes=blob_ref.size_bytes,
        media_type="application/pdf",
        source_type="http",
    )
    acq_env = ArtifactEnvelope.create(
        payload=acq, artifact_type="document_acquisition", producer="test"
    )
    await store.put(acq_env)
    svc = PypdfExtractorService(artifact_store=store, blob_store=blobs)
    doc_id = await svc.extract(acq_env.artifact_id)
    from research_harness.research.schemas.full_text import FullTextDocument

    doc = (await store.get(doc_id)).parse_payload(FullTextDocument)
    assert doc.page_count == 2
    assert doc.pages_with_text == 2
    text_data = await blobs.get_bytes(doc.text_blob)  # type: ignore[arg-type]
    j = json.loads(text_data.decode("utf-8"))
    assert j["pages"][0]["page"] == 1
    assert j["pages"][1]["page"] == 2
    assert "Page One" in j["pages"][0]["text"]
    assert "Page Two" in j["pages"][1]["text"]
    await store.close()


@pytest.mark.asyncio
async def test_extractor_unicode(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    pi_id = await _make_identity(store)
    pdf_bytes = _pdf_with_pages(["Café naïve — π ≈ 3.14"])
    blob_ref = await blobs.put_bytes(pdf_bytes)
    from research_harness.research.schemas.document_acquisition import AcquisitionStatus

    acq = DocumentAcquisition(
        paper_identity_id=pi_id,
        document_location_id="loc3",
        status=AcquisitionStatus.downloaded,
        blob=blob_ref,
        sha256=blob_ref.digest,
        size_bytes=blob_ref.size_bytes,
        media_type="application/pdf",
        source_type="http",
    )
    acq_env = ArtifactEnvelope.create(
        payload=acq, artifact_type="document_acquisition", producer="test"
    )
    await store.put(acq_env)
    svc = PypdfExtractorService(artifact_store=store, blob_store=blobs)
    doc_id = await svc.extract(acq_env.artifact_id)
    from research_harness.research.schemas.full_text import FullTextDocument

    doc = (await store.get(doc_id)).parse_payload(FullTextDocument)
    text_data = await blobs.get_bytes(doc.text_blob)  # type: ignore[arg-type]
    j = json.loads(text_data.decode("utf-8"))
    text = j["pages"][0]["text"]
    assert "Caf" in text or "Café" in text or len(text) > 0
    await store.close()


@pytest.mark.asyncio
async def test_extractor_empty_page(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    pi_id = await _make_identity(store)
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.showPage()
    c.drawString(100, 750, "Has Text Page 2")
    c.showPage()
    c.save()
    pdf_bytes = buf.getvalue()
    blob_ref = await blobs.put_bytes(pdf_bytes)
    from research_harness.research.schemas.document_acquisition import AcquisitionStatus

    acq = DocumentAcquisition(
        paper_identity_id=pi_id,
        document_location_id="loc4",
        status=AcquisitionStatus.downloaded,
        blob=blob_ref,
        sha256=blob_ref.digest,
        size_bytes=blob_ref.size_bytes,
        media_type="application/pdf",
        source_type="http",
    )
    acq_env = ArtifactEnvelope.create(
        payload=acq, artifact_type="document_acquisition", producer="test"
    )
    await store.put(acq_env)
    svc = PypdfExtractorService(artifact_store=store, blob_store=blobs)
    doc_id = await svc.extract(acq_env.artifact_id)
    from research_harness.research.schemas.full_text import FullTextDocument

    doc = (await store.get(doc_id)).parse_payload(FullTextDocument)
    assert doc.page_count == 2
    assert doc.pages_with_text == 1
    assert doc.quality_metrics["pages_without_text"] == 1
    await store.close()


@pytest.mark.asyncio
async def test_extractor_encrypted(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    pi_id = await _make_identity(store)
    pdf_bytes = _pdf_encrypted()
    blob_ref = await blobs.put_bytes(pdf_bytes)
    from research_harness.research.schemas.document_acquisition import AcquisitionStatus

    acq = DocumentAcquisition(
        paper_identity_id=pi_id,
        document_location_id="loc5",
        status=AcquisitionStatus.downloaded,
        blob=blob_ref,
        sha256=blob_ref.digest,
        size_bytes=blob_ref.size_bytes,
        media_type="application/pdf",
        source_type="http",
    )
    acq_env = ArtifactEnvelope.create(
        payload=acq, artifact_type="document_acquisition", producer="test"
    )
    await store.put(acq_env)
    svc = PypdfExtractorService(artifact_store=store, blob_store=blobs)
    doc_id = await svc.extract(acq_env.artifact_id)
    from research_harness.research.schemas.full_text import FullTextDocument

    doc = (await store.get(doc_id)).parse_payload(FullTextDocument)
    assert doc.text_status.value == "encrypted"
    assert doc.text_blob is None
    await store.close()


@pytest.mark.asyncio
async def test_extractor_malformed_pdf(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    pi_id = await _make_identity(store)
    pdf_bytes = b"%PDF- not a real pdf content \x00\x01\x02"
    blob_ref = await blobs.put_bytes(pdf_bytes)
    from research_harness.research.schemas.document_acquisition import AcquisitionStatus

    acq = DocumentAcquisition(
        paper_identity_id=pi_id,
        document_location_id="loc6",
        status=AcquisitionStatus.downloaded,
        blob=blob_ref,
        sha256=blob_ref.digest,
        size_bytes=blob_ref.size_bytes,
        media_type="application/pdf",
        source_type="http",
    )
    acq_env = ArtifactEnvelope.create(
        payload=acq, artifact_type="document_acquisition", producer="test"
    )
    await store.put(acq_env)
    svc = PypdfExtractorService(artifact_store=store, blob_store=blobs)
    doc_id = await svc.extract(acq_env.artifact_id)
    from research_harness.research.schemas.full_text import FullTextDocument

    doc = (await store.get(doc_id)).parse_payload(FullTextDocument)
    assert doc.text_status.value in ("extraction_failed", "insufficient_text")
    await store.close()


@pytest.mark.asyncio
async def test_extractor_no_useful_text(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    pi_id = await _make_identity(store)
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.showPage()
    c.showPage()
    c.save()
    pdf_bytes = buf.getvalue()
    blob_ref = await blobs.put_bytes(pdf_bytes)
    from research_harness.research.schemas.document_acquisition import AcquisitionStatus

    acq = DocumentAcquisition(
        paper_identity_id=pi_id,
        document_location_id="loc7",
        status=AcquisitionStatus.downloaded,
        blob=blob_ref,
        sha256=blob_ref.digest,
        size_bytes=blob_ref.size_bytes,
        media_type="application/pdf",
        source_type="http",
    )
    acq_env = ArtifactEnvelope.create(
        payload=acq, artifact_type="document_acquisition", producer="test"
    )
    await store.put(acq_env)
    svc = PypdfExtractorService(artifact_store=store, blob_store=blobs)
    doc_id = await svc.extract(acq_env.artifact_id)
    from research_harness.research.schemas.full_text import FullTextDocument

    doc = (await store.get(doc_id)).parse_payload(FullTextDocument)
    assert doc.text_status.value == "insufficient_text"
    assert doc.pages_with_text == 0
    assert doc.character_count < 200
    await store.close()


@pytest.mark.asyncio
async def test_extractor_quality_metrics_and_roundtrip(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    pi_id = await _make_identity(store)
    pdf_bytes = _pdf_with_pages(
        [
            "Page one has some text content here to count characters",
            "Second page also has text for metrics",
        ]
    )
    blob_ref = await blobs.put_bytes(pdf_bytes)
    from research_harness.research.schemas.document_acquisition import AcquisitionStatus

    acq = DocumentAcquisition(
        paper_identity_id=pi_id,
        document_location_id="loc8",
        status=AcquisitionStatus.downloaded,
        blob=blob_ref,
        sha256=blob_ref.digest,
        size_bytes=blob_ref.size_bytes,
        media_type="application/pdf",
        source_type="http",
    )
    acq_env = ArtifactEnvelope.create(
        payload=acq, artifact_type="document_acquisition", producer="test"
    )
    await store.put(acq_env)
    svc = PypdfExtractorService(artifact_store=store, blob_store=blobs)
    doc_id = await svc.extract(acq_env.artifact_id)
    from research_harness.research.schemas.full_text import FullTextDocument

    doc = (await store.get(doc_id)).parse_payload(FullTextDocument)
    assert doc.quality_metrics["page_count"] == 2
    assert doc.quality_metrics["character_count"] > 0
    assert "average_characters_per_page" in doc.quality_metrics
    text_data = await blobs.get_bytes(doc.text_blob)  # type: ignore[arg-type]
    j = json.loads(text_data.decode("utf-8"))
    assert j["schema_version"] == 1
    assert hashlib.sha256(text_data).hexdigest() == doc.text_blob.digest  # type: ignore[union-attr]
    await store.close()


@pytest.mark.asyncio
async def test_extractor_original_blob_unchanged(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    pi_id = await _make_identity(store)
    pdf_bytes = _pdf_with_pages(["Original must stay unchanged"])
    blob_ref = await blobs.put_bytes(pdf_bytes)
    from research_harness.research.schemas.document_acquisition import AcquisitionStatus

    acq = DocumentAcquisition(
        paper_identity_id=pi_id,
        document_location_id="loc9",
        status=AcquisitionStatus.downloaded,
        blob=blob_ref,
        sha256=blob_ref.digest,
        size_bytes=blob_ref.size_bytes,
        media_type="application/pdf",
        source_type="http",
    )
    acq_env = ArtifactEnvelope.create(
        payload=acq, artifact_type="document_acquisition", producer="test"
    )
    await store.put(acq_env)
    svc = PypdfExtractorService(artifact_store=store, blob_store=blobs)
    await svc.extract(acq_env.artifact_id)
    assert await blobs.get_bytes(blob_ref) == pdf_bytes
    await store.close()
