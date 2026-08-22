import json
import pathlib

import pytest

from research_harness.app.bootstrap import build_runtime
from research_harness.config.loader import load_config_from_dict
from research_harness.contracts.model import Message, ModelResponse
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.paper import PaperRecord
from research_harness.research.schemas.project import ResearchQuestion


@pytest.mark.asyncio
async def test_phase2c_end_to_end_offline(tmp_path: pathlib.Path):
    # Fake model that returns a valid SearchStrategyProposal
    proposal = {
        "objective": "Find papers on algorithmic pricing",
        "concepts": ["algorithmic pricing", "platform competition"],
        "queries": [
            {
                "query": "algorithmic pricing AND platform",
                "purpose": "find analytical models",
                "concepts": ["algorithmic pricing"],
                "target_sources": ["crossref", "semantic_scholar"],
            },
            {
                "query": "platform competition AND pricing",
                "purpose": "find competition",
                "concepts": ["platform competition"],
                "target_sources": ["crossref"],
            },
        ],
    }

    class FakeModel:
        async def complete(self, request):  # type: ignore[no-untyped-def]
            # Verify structured output requested
            assert request.response_schema is not None
            return ModelResponse(
                message=Message(role="assistant", content=json.dumps(proposal)),
                tool_calls=[],
                finish_reason="stop",
                model="fake",
            )

    class FakeModelRouter:
        def __init__(self, model):
            self._model = model

        async def complete(self, role, request):  # type: ignore[no-untyped-def]
            assert role == "fast"
            return await self._model.complete(request)

        def resolve(self, role):  # type: ignore[no-untyped-def]
            return {"provider": "fake", "model": "fake"}

    fake_model = FakeModel()
    fake_router = FakeModelRouter(fake_model)

    # Fake LiteratureSources: both return hits, with one overlapping DOI
    from research_harness.contracts.literature import LiteratureSearchHit, LiteratureSearchPage

    def make_paper(title, doi, provider):
        paper = PaperRecord(title=title, doi=doi, year=2021)
        return LiteratureSearchHit(
            paper=paper,
            raw_payload={"title": title, "DOI": doi},
            provider=provider,
            provider_record_id=doi,
            rank=0,
        )

    # Crossref will return P1 (doi A) and P2 (doi B)
    # Semantic Scholar will return P1 duplicate (same DOI A) and P3 (doi C)
    crossref_hits = [
        make_paper("Paper A", "10.123/a", "crossref"),
        make_paper("Paper B", "10.123/b", "crossref"),
    ]
    ss_hits = [
        make_paper("Paper A duplicate", "10.123/a", "semantic_scholar"),
        make_paper("Paper C", "10.123/c", "semantic_scholar"),
    ]

    class FakeCrossref:
        provider_name = "crossref"

        async def search(self, req):  # type: ignore[no-untyped-def]
            # Return distinct hits per query to test deduplication
            if req.query == "algorithmic pricing AND platform":
                return LiteratureSearchPage(
                    provider="crossref",
                    hits=crossref_hits[:1],
                    total_estimate=1,
                    next_page_token=None,
                    metadata={},
                )
            elif req.query == "platform competition AND pricing":
                return LiteratureSearchPage(
                    provider="crossref",
                    hits=crossref_hits[1:],
                    total_estimate=1,
                    next_page_token=None,
                    metadata={},
                )
            return LiteratureSearchPage(
                provider="crossref",
                hits=crossref_hits[:1],
                total_estimate=1,
                next_page_token=None,
                metadata={},
            )

        async def get(self, identifier):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    class FakeSS:
        provider_name = "semantic_scholar"

        async def search(self, req):  # type: ignore[no-untyped-def]
            # Only first query targets semantic_scholar, so return duplicate of A
            return LiteratureSearchPage(
                provider="semantic_scholar",
                hits=ss_hits[:1],
                total_estimate=1,
                next_page_token=None,
                metadata={},
            )

        async def get(self, identifier):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    # Build runtime with artifact store and literature plugins, but inject fake router and sources via extra_plugins
    # We need to create a custom plugin for fake router
    from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata

    class FakeRouterPlugin(Plugin):
        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(
                id="model.fake_router",
                version="0.1.0",
                plugin_type="model_router",
                description="fake",
                provides=["model_router.default"],
                requires=[],
            )

        async def setup(self, ctx: PluginContext) -> None:
            ctx.register("model_router.default", fake_router)

    class FakeSourcePlugin(Plugin):
        def __init__(self, pid, source):
            self._pid = pid
            self._source = source

        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(
                id=self._pid,
                version="0.1.0",
                plugin_type="literature_source",
                description="fake",
                provides=[f"literature_source.{self._pid.split('.')[1]}"],
                requires=[],
            )

        async def setup(self, ctx: PluginContext) -> None:
            ctx.register(f"literature_source.{self._pid.split('.')[1]}", self._source)

    cfg = load_config_from_dict(
        {
            "plugins": [
                "storage.artifacts_sqlite",
                "literature.ingestion",
                "literature.identity_resolver",
                "literature.search_planner",
                "literature.search_orchestrator",
            ],
            "artifacts": {"store": "sqlite", "path": str(tmp_path / "artifacts.db")},
            "literature": {
                "planning": {"model_role": "fast", "max_queries": 8, "max_sources_per_query": 2},
                "orchestration": {
                    "max_queries": 8,
                    "max_results_per_query_per_source": 10,
                    "max_total_provider_requests": 10,
                    "max_total_papers": 10,
                },
            },
        }
    )

    # Inject fakes
    extra = [
        FakeRouterPlugin(),
        FakeSourcePlugin("literature.crossref", FakeCrossref()),
        FakeSourcePlugin("literature.semantic_scholar", FakeSS()),
    ]

    runtime = build_runtime(cfg, extra_plugins=extra)
    async with runtime:
        store = runtime.services.require("artifact_store.default")
        # Create ResearchQuestion
        rq = ArtifactEnvelope.create(
            payload=ResearchQuestion(
                question="How does algorithmic pricing affect competition in digital platforms?"
            ),
            artifact_type="research_question",
        )
        await store.put(rq)

        # Plan via planner
        planner = runtime.services.require("literature_search_planner.default")
        strat_id, query_ids = await planner.plan(rq.artifact_id)
        assert strat_id
        assert len(query_ids) == 2

        # Execute via orchestrator
        orchestrator = runtime.services.require("literature_search_orchestrator.default")
        exec_id = await orchestrator.execute(strat_id)
        exec_env = await store.get(exec_id)
        from research_harness.research.schemas.execution import LiteratureSearchExecution

        exec_rec = exec_env.parse_payload(LiteratureSearchExecution)
        # Verify counts
        assert exec_rec.counts["queries_planned"] == 2
        assert exec_rec.counts["queries_executed"] == 2
        # At least 2 provider searches (one per query per source, but we have 2 queries: first has 2 sources, second has 1 source? Actually our fake sources: first query crossref+ss, second crossref only => total 3 provider searches? Let's check our fake: first query "algorithmic pricing AND platform" targets both, second "platform competition AND pricing" targets crossref only => 3
        # But our FakeCrossref returns 1 hit per query, FakeSS returns 1 hit per query, so total raw papers = 3? Actually first query: crossref 1 + ss 1 =2, second query: crossref 1 =1 => total 3
        # But we have duplicate DOI "10.123/a" appears twice (crossref P1 and ss P1 duplicate), so unique identities should be 2 (A and B and C? Wait A appears twice, so unique should be 3? Let's see: crossref hits: A and B (2), ss hits: A duplicate and C? Actually our FakeSS always returns 1 hit (ss P1 duplicate of A) for each query, so for first query: crossref A, ss A duplicate => 2 hits but same DOI A, second query: crossref B => 1 hit with B, so total raw =3, but A duplicate means unique should be 2? Wait we have A, B, and A duplicate, so unique are A, B =2? But we also have C? In our fake, ss_hits has A duplicate and C? We defined ss_hits as [A duplicate, C]? Actually we defined crossref_hits = [A, B], ss_hits = [A duplicate, C] but our FakeSS search returns only first hit (ss_hits[:1] = A duplicate) for both queries, so we never get C. So total raw = 3 (A from crossref first query, A duplicate from ss first query, B from crossref second query) => unique should be 2 (A and B)
        assert exec_rec.counts["raw_paper_records"] == 3
        assert exec_rec.counts["unique_paper_identities"] == 2
        assert exec_rec.counts["duplicate_records_collapsed"] == 1

        # Verify provenance chain: RQ -> Strategy -> Queries -> Execution -> SearchRecords -> Papers -> Identities
        # Check that PaperRecords are retrievable and have identities
        assert len(exec_rec.paper_artifact_ids) == 3
        assert len(exec_rec.paper_identity_artifact_ids) == 2

        # Verify that each paper has an identity and that duplicate DOI shares same identity
        # Find identity for A
        from research_harness.research.schemas.identity import PaperIdentity

        identities = []
        for iid in exec_rec.paper_identity_artifact_ids:
            env = await store.get(iid)
            identities.append(env.parse_payload(PaperIdentity))
        # One identity should have 2 members (the duplicate A)
        found_duplicate_identity = False
        for ident in identities:
            if len(ident.member_paper_artifact_ids) == 2:
                found_duplicate_identity = True
                # Check evidence
                assert any(
                    e.identifier_scheme == "doi" and e.normalized_value == "10.123/a"
                    for e in ident.resolution_evidence
                )
        assert found_duplicate_identity

        # Verify that raw PaperRecords remain immutable and separate (no supersedes among them)
        for pid in exec_rec.paper_artifact_ids:
            parents = await store.get_parents(pid)
            # PaperRecords should not have supersedes
            for p in parents:
                assert p.relation.value != "supersedes"

        # Verify that search records are traceable to queries via provenance
        for sid in exec_rec.search_record_artifact_ids:
            parents = await store.get_parents(sid)
            # Should have at least one parent which is a query
            assert any(p.source_artifact_id in query_ids for p in parents)

        # Verify DB reopen persistence
        await store.close()
        from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore

        store2 = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
        for pid in exec_rec.paper_artifact_ids:
            assert await store2.exists(pid)
        await store2.close()
