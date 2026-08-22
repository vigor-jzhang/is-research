import pathlib

import pytest

from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
from research_harness.research.schemas.paper import Author, PaperRecord


@pytest.mark.asyncio
async def test_metadata_locator_finds_open_access_url(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.locator_metadata.plugin import MetadataLocatorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = MetadataLocatorService(artifact_store=store)

    paper = PaperRecord(
        title="T", open_access_url="https://example.com/paper.pdf", authors=[Author(name="A")]
    )
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

    loc_ids = await svc.resolve(ident_env.artifact_id)
    assert len(loc_ids) == 1
    from research_harness.research.schemas.document_location import DocumentLocation

    loc = (await store.get(loc_ids[0])).parse_payload(DocumentLocation)
    assert loc.url == "https://example.com/paper.pdf"
    assert loc.is_direct_download is True
    assert loc.resolver == "documents.locator.metadata"
    await store.close()


@pytest.mark.asyncio
async def test_metadata_locator_multiple_members(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.locator_metadata.plugin import MetadataLocatorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = MetadataLocatorService(artifact_store=store)

    p1 = PaperRecord(title="T", open_access_url="https://example.com/a.pdf")
    p2 = PaperRecord(title="T", open_access_url="https://example.com/b.pdf")
    p1_env = ArtifactEnvelope.create(payload=p1, artifact_type="paper_record", producer="test")
    p2_env = ArtifactEnvelope.create(payload=p2, artifact_type="paper_record", producer="test")
    await store.put(p1_env)
    await store.put(p2_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p1_env.artifact_id, p2_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)

    loc_ids = await svc.resolve(ident_env.artifact_id)
    assert len(loc_ids) == 2
    from research_harness.research.schemas.document_location import DocumentLocation

    urls = sorted([(await store.get(lid)).parse_payload(DocumentLocation).url for lid in loc_ids])
    assert urls == ["https://example.com/a.pdf", "https://example.com/b.pdf"]
    await store.close()


@pytest.mark.asyncio
async def test_metadata_locator_no_oa(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.locator_metadata.plugin import MetadataLocatorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = MetadataLocatorService(artifact_store=store)

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

    loc_ids = await svc.resolve(ident_env.artifact_id)
    assert loc_ids == []
    await store.close()


@pytest.mark.asyncio
async def test_metadata_locator_duplicate_url_suppression(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.locator_metadata.plugin import MetadataLocatorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = MetadataLocatorService(artifact_store=store)

    url = "https://example.com/same.pdf"
    p1 = PaperRecord(title="T", open_access_url=url)
    p2 = PaperRecord(title="T", open_access_url=url)
    p1_env = ArtifactEnvelope.create(payload=p1, artifact_type="paper_record", producer="test")
    p2_env = ArtifactEnvelope.create(payload=p2, artifact_type="paper_record", producer="test")
    await store.put(p1_env)
    await store.put(p2_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p1_env.artifact_id, p2_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)

    loc_ids = await svc.resolve(ident_env.artifact_id)
    assert len(loc_ids) == 1
    await store.close()


@pytest.mark.asyncio
async def test_metadata_locator_deterministic_ordering(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.locator_metadata.plugin import MetadataLocatorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = MetadataLocatorService(artifact_store=store)

    p1 = PaperRecord(title="T", open_access_url="https://example.com/z.pdf")
    p2 = PaperRecord(title="T", open_access_url="https://example.com/a.pdf")
    p1_env = ArtifactEnvelope.create(payload=p1, artifact_type="paper_record", producer="test")
    p2_env = ArtifactEnvelope.create(payload=p2, artifact_type="paper_record", producer="test")
    await store.put(p1_env)
    await store.put(p2_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p1_env.artifact_id, p2_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)

    loc_ids = await svc.resolve(ident_env.artifact_id)
    from research_harness.research.schemas.document_location import DocumentLocation

    urls = [(await store.get(lid)).parse_payload(DocumentLocation).url for lid in loc_ids]
    assert urls == sorted(urls)  # deterministic sorted by url
    await store.close()
