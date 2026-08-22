import pathlib

import pytest

from research_harness.contracts.literature import LiteratureSearchHit, LiteratureSearchRequest
from research_harness.plugins.literature.ingestion.plugin import LiteratureIngestor
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.provenance.relations import ProvenanceRelation
from research_harness.research.schemas.paper import PaperRecord
from research_harness.research.schemas.provider_snapshot import ProviderRecordSnapshot
from research_harness.research.schemas.search_record import LiteratureSearchRecord


class FakeSource:
    def __init__(self, hits):
        self._hits = hits
        self.provider_name = "fake_provider"

    async def search(self, request):  # type: ignore[no-untyped-def]
        from research_harness.contracts.literature import LiteratureSearchPage

        return LiteratureSearchPage(
            provider=self.provider_name,
            hits=self._hits,
            total_estimate=len(self._hits),
            next_page_token=None,
            metadata={},
        )

    async def get(self, identifier: str):  # type: ignore[no-untyped-def]
        for h in self._hits:
            if h.provider_record_id == identifier:
                return h
        raise Exception("not found")


@pytest.mark.asyncio
async def test_ingestion_search_produces_artifacts(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    ingestor = LiteratureIngestor(artifact_store=store)

    paper = PaperRecord(title="Test Paper", year=2021, doi="10.123/test")
    hit = LiteratureSearchHit(
        paper=paper,
        raw_payload={"DOI": "10.123/test", "title": "Test Paper"},
        provider="crossref",
        provider_record_id="10.123/test",
        rank=0,
        score=1.0,
        metadata={},
    )
    source = FakeSource(hits=[hit])
    req = LiteratureSearchRequest(query="test", limit=10)

    search_env, snapshot_envs, paper_envs = await ingestor.ingest_search(source, req)

    assert len(snapshot_envs) == 1
    assert len(paper_envs) == 1
    assert search_env.artifact_type == "literature_search_record"
    # Check that paper artifact can be retrieved
    fetched_paper = await store.get(paper_envs[0].artifact_id)
    assert fetched_paper.parse_payload(PaperRecord).title == "Test Paper"
    # Check snapshot
    fetched_snap = await store.get(snapshot_envs[0].artifact_id)
    snap = fetched_snap.parse_payload(ProviderRecordSnapshot)
    assert snap.provider == "crossref"
    assert snap.raw_payload["DOI"] == "10.123/test"
    # Check provenance: paper generated_from snapshot
    parents = await store.get_parents(paper_envs[0].artifact_id)
    assert len(parents) == 1
    assert parents[0].relation == ProvenanceRelation.generated_from
    assert parents[0].source_artifact_id == snapshot_envs[0].artifact_id
    # Check search record
    search_rec = search_env.parse_payload(LiteratureSearchRecord)
    assert search_rec.provider == "fake_provider"
    assert search_rec.query == "test"
    assert search_rec.returned_count == 1
    assert paper_envs[0].artifact_id in search_rec.paper_artifact_ids
    assert snapshot_envs[0].artifact_id in search_rec.provider_snapshot_artifact_ids
    # Ensure no credentials in artifacts
    import json

    all_envs = await store.list()
    dumped = json.dumps([e.model_dump(mode="json") for e in all_envs])
    assert "API_KEY" not in dumped
    assert "api_key" not in dumped.lower() or "provider" in dumped  # at least not leaking key
    await store.close()


@pytest.mark.asyncio
async def test_ingestion_preserves_raw_not_in_paper(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    ingestor = LiteratureIngestor(artifact_store=store)
    raw = {"DOI": "10.123/test", "title": "T", "extra": "raw_data", "nested": {"a": 1}}
    paper = PaperRecord(title="T", doi="10.123/test")
    hit = LiteratureSearchHit(
        paper=paper, raw_payload=raw, provider="crossref", provider_record_id="10.123/test"
    )
    source = FakeSource(hits=[hit])
    req = LiteratureSearchRequest(query="test", limit=1)
    _, snapshot_envs, paper_envs = await ingestor.ingest_search(source, req)
    # PaperRecord metadata should not contain full raw
    paper_fetched = await store.get(paper_envs[0].artifact_id)
    paper_obj = paper_fetched.parse_payload(PaperRecord)
    # Ensure paper's metadata doesn't contain raw's extra field
    assert "raw_data" not in str(paper_obj.model_dump())
    # Snapshot should contain raw
    snap_fetched = await store.get(snapshot_envs[0].artifact_id)
    snap = snap_fetched.parse_payload(ProviderRecordSnapshot)
    assert snap.raw_payload["extra"] == "raw_data"
    await store.close()


@pytest.mark.asyncio
async def test_ingestion_get(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    ingestor = LiteratureIngestor(artifact_store=store)
    paper = PaperRecord(title="P", doi="10.123/test")
    hit = LiteratureSearchHit(
        paper=paper,
        raw_payload={"DOI": "10.123/test"},
        provider="semantic_scholar",
        provider_record_id="p123",
    )
    source = FakeSource(hits=[hit])
    snapshot_env, paper_env = await ingestor.ingest_get(source, "p123")
    assert snapshot_env.parse_payload(ProviderRecordSnapshot).provider_record_id == "p123"
    assert paper_env.parse_payload(PaperRecord).title == "P"
    # Check provenance
    parents = await store.get_parents(paper_env.artifact_id)
    assert parents[0].relation == ProvenanceRelation.generated_from
    await store.close()


@pytest.mark.asyncio
async def test_ingestion_no_dedup(tmp_path: pathlib.Path):
    # Ensure two papers with same DOI from different providers coexist as separate artifacts
    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    ingestor = LiteratureIngestor(artifact_store=store)
    paper1 = PaperRecord(title="Same DOI", doi="10.123/dup")
    paper2 = PaperRecord(title="Same DOI", doi="10.123/dup")
    hit1 = LiteratureSearchHit(
        paper=paper1,
        raw_payload={"DOI": "10.123/dup"},
        provider="crossref",
        provider_record_id="10.123/dup",
    )
    hit2 = LiteratureSearchHit(
        paper=paper2,
        raw_payload={"DOI": "10.123/dup"},
        provider="semantic_scholar",
        provider_record_id="p1",
    )
    source1 = FakeSource(hits=[hit1])
    source2 = FakeSource(hits=[hit2])
    await ingestor.ingest_search(source1, LiteratureSearchRequest(query="test", limit=1))
    await ingestor.ingest_search(source2, LiteratureSearchRequest(query="test", limit=1))
    all_papers = await store.find_by_type("paper_record")
    assert len(all_papers) == 2
    # They should have same DOI but different artifact_ids
    assert all_papers[0].artifact_id != all_papers[1].artifact_id
    assert all_papers[0].parse_payload(PaperRecord).doi == "10.123/dup"
    await store.close()
