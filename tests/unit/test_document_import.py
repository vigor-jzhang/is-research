import io
import pathlib

import pytest
from reportlab.pdfgen import canvas

from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.plugins.storage.blobs_filesystem.plugin import FilesystemBlobStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
from research_harness.research.schemas.paper import PaperRecord


def _make_pdf(text="import test") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(100, 750, text)
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.mark.asyncio
async def test_import_valid_local_pdf(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.acquisition_orchestrator.plugin import (
        DocumentAcquisitionOrchestratorService,
    )
    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    # Create identity
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

    pdf_bytes = _make_pdf("valid import")
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(pdf_bytes)

    extractor = PypdfExtractorService(artifact_store=store, blob_store=blobs)
    svc = DocumentAcquisitionOrchestratorService(
        artifact_store=store, blob_store=blobs, extractor=extractor
    )
    acq_id = await svc.import_local(ident_env.artifact_id, str(pdf_path))
    from research_harness.research.schemas.document_acquisition import DocumentAcquisition

    acq = (await store.get(acq_id)).parse_payload(DocumentAcquisition)
    assert acq.status.value == "imported"
    assert acq.source_type == "user_provided"
    assert acq.blob is not None
    assert await blobs.exists(acq.blob)
    # Provenance
    parents = await store.get_parents(acq_id)
    assert any(p.source_artifact_id == ident_env.artifact_id for p in parents)
    await store.close()


@pytest.mark.asyncio
async def test_import_invalid_non_pdf(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.acquisition_orchestrator.plugin import (
        DocumentAcquisitionOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
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

    txt_path = tmp_path / "notpdf.txt"
    txt_path.write_text("not a pdf")
    svc = DocumentAcquisitionOrchestratorService(artifact_store=store, blob_store=blobs)
    with pytest.raises(ValueError, match="not a PDF"):
        await svc.import_local(ident_env.artifact_id, str(txt_path))
    await store.close()


@pytest.mark.asyncio
async def test_import_same_pdf_twice_blob_reused(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.acquisition_orchestrator.plugin import (
        DocumentAcquisitionOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
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

    pdf_bytes = _make_pdf("same content")
    pdf_path = tmp_path / "same.pdf"
    pdf_path.write_bytes(pdf_bytes)

    svc = DocumentAcquisitionOrchestratorService(artifact_store=store, blob_store=blobs)
    id1 = await svc.import_local(ident_env.artifact_id, str(pdf_path))
    id2 = await svc.import_local(ident_env.artifact_id, str(pdf_path))
    assert id1 == id2  # idempotent reuse
    from research_harness.research.schemas.document_acquisition import DocumentAcquisition

    acq1 = (await store.get(id1)).parse_payload(DocumentAcquisition)
    acq2 = (await store.get(id2)).parse_payload(DocumentAcquisition)
    assert acq1.sha256 == acq2.sha256
    # Only one blob file
    count = sum(1 for _ in (tmp_path / "blobs").rglob("*") if _.is_file())
    assert count == 1
    await store.close()


@pytest.mark.asyncio
async def test_import_provenance_and_path_traversal_irrelevant(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.acquisition_orchestrator.plugin import (
        DocumentAcquisitionOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
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

    pdf_bytes = _make_pdf("traversal test")
    # Try path with traversal in name - should not affect blob storage (uses digest not filename)
    pdf_path = tmp_path / "subdir" / "my paper (1).pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(pdf_bytes)

    svc = DocumentAcquisitionOrchestratorService(artifact_store=store, blob_store=blobs)
    acq_id = await svc.import_local(ident_env.artifact_id, str(pdf_path))
    from research_harness.research.schemas.document_acquisition import DocumentAcquisition

    acq = (await store.get(acq_id)).parse_payload(DocumentAcquisition)
    # Blob key should not contain original filename or traversal
    assert acq.blob is not None
    assert ".." not in acq.blob.storage_key
    assert "my paper" not in acq.blob.storage_key
    assert acq.blob.digest not in str(pdf_path)
    # Provenance correct
    parents = await store.get_parents(acq_id)
    assert any(p.source_artifact_id == ident_env.artifact_id for p in parents)
    await store.close()
