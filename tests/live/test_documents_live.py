"""Live document test — opt-in, requires UNPAYWALL_EMAIL."""

import os
import pathlib

import pytest

pytestmark = pytest.mark.live_documents


@pytest.mark.asyncio
async def test_live_documents_smoke(tmp_path: pathlib.Path):
    email = os.getenv("UNPAYWALL_EMAIL")
    if not email:
        pytest.skip("UNPAYWALL_EMAIL not set")

    doi = os.getenv("FULLTEXT_SMOKE_DOI", "10.1371/journal.pone.0151203")
    # Skip if DOI not relevant? Just use it

    from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
    from research_harness.plugins.storage.blobs_filesystem.plugin import FilesystemBlobStore
    from research_harness.research.envelope import ArtifactEnvelope
    from research_harness.research.schemas.common import ExternalIdentifier
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.paper import PaperRecord

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")

    paper = PaperRecord(title="Live doc smoke", doi=doi)
    p_env = ArtifactEnvelope.create(
        payload=paper, artifact_type="paper_record", producer="live_test"
    )
    await store.put(p_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[ExternalIdentifier(scheme="doi", value=doi)],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="live_test"
    )
    await store.put(ident_env)

    from research_harness.plugins.documents.locator_unpaywall.plugin import UnpaywallLocatorService

    # Use real HTTP client
    unpaywall_loc = UnpaywallLocatorService(artifact_store=store, email=email)
    try:
        loc_ids = await unpaywall_loc.resolve(ident_env.artifact_id)
    except Exception as e:
        pytest.skip(f"unpaywall resolve failed live: {e}")
    if not loc_ids:
        pytest.skip(f"No OA location found for DOI {doi} via Unpaywall (email {email})")

    # Should have at least one location
    assert len(loc_ids) >= 1
    from research_harness.research.schemas.document_location import DocumentLocation

    loc = (await store.get(loc_ids[0])).parse_payload(DocumentLocation)
    assert loc.url.startswith("http")
    # At least one should be direct PDF or landing

    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService

    fetcher = HttpFetcherService(artifact_store=store, blob_store=blobs)
    try:
        acq_id = await fetcher.fetch(loc_ids[0])
    except Exception as e:
        pytest.fail(f"fetch failed live for {loc.url}: {e}")

    from research_harness.research.schemas.document_acquisition import DocumentAcquisition

    acq = (await store.get(acq_id)).parse_payload(DocumentAcquisition)
    # May be downloaded or invalid_content etc., but we expect downloaded for OA DOI
    if acq.status.value != "downloaded":
        pytest.skip(
            f"acquisition not downloaded live, status {acq.status.value} message {acq.failure_message}"
        )

    assert acq.blob is not None
    assert await blobs.exists(acq.blob)
    pdf_bytes = await blobs.get_bytes(acq.blob)
    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 1000
    assert len(pdf_bytes) < 50 * 1024 * 1024

    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorService

    extractor = PypdfExtractorService(artifact_store=store, blob_store=blobs)
    doc_id = await extractor.extract(acq_id)
    from research_harness.research.schemas.full_text import FullTextDocument

    doc = (await store.get(doc_id)).parse_payload(FullTextDocument)
    assert doc.page_count >= 1
    assert doc.text_status.value in ("extracted", "insufficient_text", "encrypted")
    # If extracted, verify text blob
    if doc.text_status.value == "extracted":
        assert doc.text_blob is not None
        assert await blobs.exists(doc.text_blob)
        import json

        text_data = await blobs.get_bytes(doc.text_blob)
        j = json.loads(text_data.decode("utf-8"))
        assert j["schema_version"] == 1
        assert len(j["pages"]) >= 1
        assert j["pages"][0]["page"] == 1

    # Verify provenance
    parents = await store.get_parents(doc_id)
    assert any(p.source_artifact_id == acq_id for p in parents)

    print(
        f"DOI {doi} location {loc.url} PDF size {len(pdf_bytes)} pages {doc.page_count} status {doc.text_status.value}"
    )

    await store.close()
    try:
        await unpaywall_loc.close()
    except Exception:
        pass
    try:
        await fetcher.close()
    except Exception:
        pass
