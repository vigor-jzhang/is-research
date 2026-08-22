import pathlib

import httpx
import pytest
import respx

from research_harness.app.bootstrap import build_runtime
from research_harness.config.loader import load_config_from_dict
from research_harness.research.schemas.paper import PaperRecord
from research_harness.research.schemas.provider_snapshot import ProviderRecordSnapshot
from research_harness.research.schemas.search_record import LiteratureSearchRecord


@pytest.mark.asyncio
@respx.mock
async def test_crossref_ingestion_e2e(tmp_path: pathlib.Path):
    # Mock Crossref search
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "message-type": "work-list",
                "message": {
                    "items": [
                        {
                            "title": ["Crossref Paper One"],
                            "DOI": "10.1234/cross1",
                            "author": [{"given": "A", "family": "B"}],
                            "published-print": {"date-parts": [[2020]]},
                            "container-title": ["Journal"],
                            "abstract": "<jats:p>Abstract 1.</jats:p>",
                            "URL": "https://doi.org/10.1234/cross1",
                            "type": "journal-article",
                        },
                        {
                            "title": ["Crossref Paper Two"],
                            "DOI": "10.1234/cross2",
                            "author": [{"given": "C", "family": "D"}],
                            "published-print": {"date-parts": [[2021]]},
                            "container-title": ["Journal"],
                            "abstract": "Abstract 2",
                            "URL": "https://doi.org/10.1234/cross2",
                            "type": "journal-article",
                        },
                    ],
                    "total-results": 2,
                },
            },
        )
    )

    cfg = load_config_from_dict(
        {
            "plugins": [
                "storage.artifacts_sqlite",
                "literature.crossref",
                "literature.ingestion",
            ],
            "artifacts": {"store": "sqlite", "path": str(tmp_path / "artifacts.db")},
            "literature": {"crossref": {"enabled": True, "timeout_seconds": 5}},
        }
    )
    runtime = build_runtime(cfg)
    async with runtime:
        store = runtime.services.require("artifact_store.default")
        source = runtime.services.require("literature_source.crossref")
        ingestor = runtime.services.require("literature_ingestor.default")

        from research_harness.contracts.literature import LiteratureSearchRequest

        req = LiteratureSearchRequest(query="test", limit=2)
        search_env, snapshot_envs, paper_envs = await ingestor.ingest_search(source, req)

        assert len(paper_envs) == 2
        assert len(snapshot_envs) == 2
        # Check paper titles
        titles = {p.parse_payload(PaperRecord).title for p in paper_envs}
        assert "Crossref Paper One" in titles
        assert "Crossref Paper Two" in titles
        # Check provenance
        for paper_env, snap_env in zip(paper_envs, snapshot_envs, strict=True):
            parents = await store.get_parents(paper_env.artifact_id)
            assert len(parents) == 1
            assert parents[0].source_artifact_id == snap_env.artifact_id
            assert parents[0].relation.value == "generated_from"
        # Check search record
        search_rec = search_env.parse_payload(LiteratureSearchRecord)
        assert search_rec.provider == "crossref"
        assert search_rec.returned_count == 2
        assert len(search_rec.paper_artifact_ids) == 2
        # Verify paper still retrievable after reopen
        await store.close()
        # Reopen store directly
        from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore

        store2 = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
        for pe in paper_envs:
            fetched = await store2.get(pe.artifact_id)
            assert fetched.parse_payload(PaperRecord).title in titles
            # Lineage finds snapshot
            ancestors = await store2.get_lineage(pe.artifact_id, direction="ancestors")
            assert len(ancestors) == 1
            assert ancestors[0].artifact_type == "provider_record_snapshot"
        await store2.close()


@pytest.mark.asyncio
@respx.mock
async def test_semantic_scholar_ingestion_e2e(tmp_path: pathlib.Path):
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "total": 1,
                "data": [
                    {
                        "paperId": "p1",
                        "corpusId": 123,
                        "externalIds": {"DOI": "10.5678/ss1"},
                        "title": "Semantic Paper One",
                        "abstract": "Abstract SS",
                        "year": 2022,
                        "venue": "SS Venue",
                        "authors": [{"name": "Alice"}],
                        "url": "https://semanticscholar.org/p1",
                        "openAccessPdf": {"url": "https://arxiv.org/pdf/123.pdf"},
                        "publicationTypes": ["JournalArticle"],
                    }
                ],
            },
        )
    )

    cfg = load_config_from_dict(
        {
            "plugins": [
                "storage.artifacts_sqlite",
                "literature.semantic_scholar",
                "literature.ingestion",
            ],
            "artifacts": {"store": "sqlite", "path": str(tmp_path / "artifacts.db")},
            "literature": {"semantic_scholar": {"enabled": True, "timeout_seconds": 5}},
        }
    )
    runtime = build_runtime(cfg)
    async with runtime:
        store = runtime.services.require("artifact_store.default")
        source = runtime.services.require("literature_source.semantic_scholar")
        ingestor = runtime.services.require("literature_ingestor.default")

        from research_harness.contracts.literature import LiteratureSearchRequest

        req = LiteratureSearchRequest(query="test", limit=1)
        search_env, snapshot_envs, paper_envs = await ingestor.ingest_search(source, req)

        assert len(paper_envs) == 1
        paper = paper_envs[0].parse_payload(PaperRecord)
        assert paper.title == "Semantic Paper One"
        assert paper.doi == "10.5678/ss1"
        assert paper.open_access_url == "https://arxiv.org/pdf/123.pdf"
        # Check snapshot raw preserved
        snap = snapshot_envs[0].parse_payload(ProviderRecordSnapshot)
        assert snap.raw_payload["paperId"] == "p1"
        # Provenance
        parents = await store.get_parents(paper_envs[0].artifact_id)
        assert parents[0].relation.value == "generated_from"
        await store.close()
