import json
import pathlib

import httpx
import pytest

from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.common import ExternalIdentifier
from research_harness.research.schemas.identity import (
    IdentityEvidence,
    PaperIdentity,
    ResolutionMethod,
)
from research_harness.research.schemas.paper import PaperRecord


def _make_identity_with_doi(store, doi="10.1234/test") -> tuple[str, str]:
    # Returns (identity_id, paper_id)
    paper = PaperRecord(title="T", doi=doi)
    p_env = ArtifactEnvelope.create(payload=paper, artifact_type="paper_record", producer="test")
    # Need to put after async? We'll do in test; this is helper sync not used
    raise NotImplementedError


@pytest.mark.asyncio
async def test_unpaywall_best_oa_direct_pdf(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.locator_unpaywall.plugin import UnpaywallLocatorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    paper = PaperRecord(title="T", doi="10.1234/test")
    p_env = ArtifactEnvelope.create(payload=paper, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[ExternalIdentifier(scheme="doi", value="10.1234/test")],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[
            IdentityEvidence(
                identifier_scheme="doi",
                normalized_value="10.1234/test",
                member_artifact_ids=[p_env.artifact_id],
            )
        ],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)

    raw = {
        "doi": "10.1234/test",
        "is_oa": True,
        "best_oa_location": {
            "url": "https://example.com/paper.pdf",
            "url_for_pdf": "https://example.com/paper.pdf",
            "url_for_landing_page": "https://example.com/landing",
            "host_type": "publisher",
            "version": "publishedVersion",
            "license": "cc-by",
        },
        "oa_locations": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert "10.1234" in str(request.url)
        assert "email" in str(request.url)
        return httpx.Response(200, json=raw)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = UnpaywallLocatorService(
        artifact_store=store, http_client=client, email="test@example.com"
    )
    loc_ids = await svc.resolve(ident_env.artifact_id)
    assert len(loc_ids) == 1
    from research_harness.research.schemas.document_location import DocumentLocation

    loc = (await store.get(loc_ids[0])).parse_payload(DocumentLocation)
    assert loc.url == "https://example.com/paper.pdf"
    assert loc.is_direct_download is True
    assert loc.license == "cc-by"
    assert loc.version.value == "publishedVersion"
    assert loc.host_type.value == "publisher"
    # Snapshot preserved
    snaps = await store.list(artifact_type="provider_record_snapshot")
    assert len(snaps) == 1
    from research_harness.research.schemas.provider_snapshot import ProviderRecordSnapshot

    snap = snaps[0].parse_payload(ProviderRecordSnapshot)
    assert snap.provider == "unpaywall"
    assert snap.raw_payload["doi"] == "10.1234/test"
    assert "test@example.com" not in json.dumps(snap.model_dump(mode="json"))
    await store.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_unpaywall_multiple_oa_locations(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.locator_unpaywall.plugin import UnpaywallLocatorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    paper = PaperRecord(title="T", doi="10.1234/test2")
    p_env = ArtifactEnvelope.create(payload=paper, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[ExternalIdentifier(scheme="doi", value="10.1234/test2")],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)

    raw = {
        "doi": "10.1234/test2",
        "is_oa": True,
        "best_oa_location": {
            "url_for_pdf": "https://example.com/best.pdf",
            "url": "https://example.com/best.pdf",
            "url_for_landing_page": "https://example.com/best",
            "host_type": "publisher",
            "version": "publishedVersion",
        },
        "oa_locations": [
            {
                "url_for_pdf": "https://example.com/other.pdf",
                "url": "https://example.com/other.pdf",
                "host_type": "repository",
                "version": "acceptedVersion",
            },
            {
                "url_for_pdf": None,
                "url": "https://example.com/landing",
                "url_for_landing_page": "https://example.com/landing",
                "host_type": "repository",
                "version": "submittedVersion",
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=raw)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = UnpaywallLocatorService(
        artifact_store=store, http_client=client, email="test@example.com"
    )
    loc_ids = await svc.resolve(ident_env.artifact_id)
    # Should create 3 locations in priority order: best direct, other direct, landing
    assert len(loc_ids) == 3
    from research_harness.research.schemas.document_location import DocumentLocation

    locs = [(await store.get(lid)).parse_payload(DocumentLocation) for lid in loc_ids]
    # First should be best direct
    assert locs[0].url == "https://example.com/best.pdf"
    assert locs[0].is_direct_download is True
    await store.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_unpaywall_landing_page_only(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.locator_unpaywall.plugin import UnpaywallLocatorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    paper = PaperRecord(title="T", doi="10.1234/landing")
    p_env = ArtifactEnvelope.create(payload=paper, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[ExternalIdentifier(scheme="doi", value="10.1234/landing")],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)

    raw = {
        "doi": "10.1234/landing",
        "is_oa": True,
        "best_oa_location": {
            "url_for_pdf": None,
            "url": "https://example.com/landing",
            "url_for_landing_page": "https://example.com/landing",
            "host_type": "publisher",
            "version": "publishedVersion",
        },
        "oa_locations": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=raw)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = UnpaywallLocatorService(
        artifact_store=store, http_client=client, email="test@example.com"
    )
    loc_ids = await svc.resolve(ident_env.artifact_id)
    assert len(loc_ids) == 1
    from research_harness.research.schemas.document_location import DocumentLocation

    loc = (await store.get(loc_ids[0])).parse_payload(DocumentLocation)
    assert loc.is_direct_download is False
    assert loc.url == "https://example.com/landing"
    await store.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_unpaywall_404_no_location(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.locator_unpaywall.plugin import UnpaywallLocatorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    paper = PaperRecord(title="T", doi="10.1234/notfound")
    p_env = ArtifactEnvelope.create(payload=paper, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[ExternalIdentifier(scheme="doi", value="10.1234/notfound")],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = UnpaywallLocatorService(
        artifact_store=store, http_client=client, email="test@example.com"
    )
    loc_ids = await svc.resolve(ident_env.artifact_id)
    assert loc_ids == []
    snaps = await store.list(artifact_type="provider_record_snapshot")
    assert len(snaps) == 1
    await store.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_unpaywall_429_and_5xx(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.locator_unpaywall.plugin import UnpaywallLocatorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    paper = PaperRecord(title="T", doi="10.1234/err")
    p_env = ArtifactEnvelope.create(payload=paper, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[ExternalIdentifier(scheme="doi", value="10.1234/err")],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)

    for status in [429, 500]:

        def handler(request: httpx.Request, s=status) -> httpx.Response:
            return httpx.Response(s, json={})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        svc = UnpaywallLocatorService(
            artifact_store=store, http_client=client, email="test@example.com"
        )
        loc_ids = await svc.resolve(ident_env.artifact_id)
        assert loc_ids == []
        await client.aclose()
    await store.close()


@pytest.mark.asyncio
async def test_unpaywall_timeout_and_malformed(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.locator_unpaywall.plugin import UnpaywallLocatorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    paper = PaperRecord(title="T", doi="10.1234/timeout")
    p_env = ArtifactEnvelope.create(payload=paper, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[ExternalIdentifier(scheme="doi", value="10.1234/timeout")],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timeout")

    client = httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler))
    svc = UnpaywallLocatorService(
        artifact_store=store, http_client=client, email="test@example.com"
    )
    loc_ids = await svc.resolve(ident_env.artifact_id)
    assert loc_ids == []
    await client.aclose()

    # Malformed JSON
    def malformed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json")

    client2 = httpx.AsyncClient(transport=httpx.MockTransport(malformed_handler))
    svc2 = UnpaywallLocatorService(
        artifact_store=store, http_client=client2, email="test@example.com"
    )
    loc_ids2 = await svc2.resolve(ident_env.artifact_id)
    assert loc_ids2 == []
    await client2.aclose()
    await store.close()


@pytest.mark.asyncio
async def test_unpaywall_no_doi(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.locator_unpaywall.plugin import UnpaywallLocatorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    paper = PaperRecord(title="T")  # no doi
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

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    )
    svc = UnpaywallLocatorService(
        artifact_store=store, http_client=client, email="test@example.com"
    )
    loc_ids = await svc.resolve(ident_env.artifact_id)
    assert loc_ids == []
    await client.aclose()
    await store.close()


@pytest.mark.asyncio
async def test_unpaywall_email_not_persisted(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.locator_unpaywall.plugin import UnpaywallLocatorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    paper = PaperRecord(title="T", doi="10.1234/emailtest")
    p_env = ArtifactEnvelope.create(payload=paper, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[ExternalIdentifier(scheme="doi", value="10.1234/emailtest")],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)

    raw = {
        "doi": "10.1234/emailtest",
        "is_oa": True,
        "best_oa_location": {
            "url_for_pdf": "https://example.com/a.pdf",
            "url": "https://example.com/a.pdf",
        },
        "oa_locations": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        # Ensure email param is sent but not stored in snapshot
        assert "email" in str(request.url)
        return httpx.Response(200, json=raw)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = UnpaywallLocatorService(
        artifact_store=store, http_client=client, email="secret@example.com"
    )
    await svc.resolve(ident_env.artifact_id)
    # Check snapshot does not contain email
    snaps = await store.list(artifact_type="provider_record_snapshot")
    import json

    dumped = json.dumps([s.model_dump(mode="json") for s in snaps])
    assert "secret@example.com" not in dumped
    # Also check DocumentLocation metadata
    locs = await store.list(artifact_type="document_location")
    dumped2 = json.dumps([loc.model_dump(mode="json") for loc in locs])
    assert "secret@example.com" not in dumped2
    await store.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_unpaywall_version_and_license_mapping(tmp_path: pathlib.Path):
    from research_harness.plugins.documents.locator_unpaywall.plugin import UnpaywallLocatorService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    paper = PaperRecord(title="T", doi="10.1234/mapping")
    p_env = ArtifactEnvelope.create(payload=paper, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[ExternalIdentifier(scheme="doi", value="10.1234/mapping")],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)

    raw = {
        "doi": "10.1234/mapping",
        "is_oa": True,
        "best_oa_location": {
            "url_for_pdf": "https://example.com/a.pdf",
            "url": "https://example.com/a.pdf",
            "host_type": "publisher",
            "version": "submittedVersion",
            "license": "cc-by-nc",
        },
        "oa_locations": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=raw)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    svc = UnpaywallLocatorService(
        artifact_store=store, http_client=client, email="test@example.com"
    )
    loc_ids = await svc.resolve(ident_env.artifact_id)
    from research_harness.research.schemas.document_location import DocumentLocation

    loc = (await store.get(loc_ids[0])).parse_payload(DocumentLocation)
    assert loc.version.value == "submittedVersion"
    assert loc.license == "cc-by-nc"
    assert loc.host_type.value == "publisher"
    await store.close()
    await client.aclose()
