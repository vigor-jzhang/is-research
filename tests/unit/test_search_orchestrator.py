import pathlib

import pytest

from research_harness.contracts.literature import (
    LiteratureSearchHit,
    LiteratureSearchPage,
    LiteratureSearchRequest,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.paper import PaperRecord
from research_harness.research.schemas.project import ResearchQuestion
from research_harness.research.schemas.query import LiteratureQuery
from research_harness.research.schemas.strategy import LiteratureSearchStrategy


class FakeSource:
    def __init__(self, name, hits=None, should_fail=False):
        self.provider_name = name
        self._hits = hits or []
        self.should_fail = should_fail
        self.calls: list[LiteratureSearchRequest] = []

    async def search(self, request):  # type: ignore[no-untyped-def]
        self.calls.append(request)
        if self.should_fail:
            raise Exception(f"{self.provider_name} failed")
        return LiteratureSearchPage(
            provider=self.provider_name,
            hits=self._hits,
            total_estimate=len(self._hits),
            next_page_token=None,
            metadata={},
        )

    async def get(self, identifier: str):  # type: ignore[no-untyped-def]
        raise NotImplementedError


def _make_hit(title, doi="10.123/test", provider="crossref"):
    paper = PaperRecord(title=title, doi=doi)
    return LiteratureSearchHit(
        paper=paper,
        raw_payload={"title": title, "DOI": doi},
        provider=provider,
        provider_record_id=doi,
    )


@pytest.mark.asyncio
async def test_orchestrator_one_query_two_sources(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.ingestion.plugin import LiteratureIngestor
    from research_harness.plugins.literature.search_orchestrator.plugin import (
        LiteratureSearchOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    ingestor = LiteratureIngestor(artifact_store=store)

    # Create strategy with one query targeting both sources
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)
    q = ArtifactEnvelope.create(
        payload=LiteratureQuery(
            query="test query", target_sources=["crossref", "semantic_scholar"]
        ),
        artifact_type="literature_query",
    )
    await store.put(q)
    strat = ArtifactEnvelope.create(
        payload=LiteratureSearchStrategy(
            research_question_id=rq.artifact_id,
            objective="obj",
            concepts=[],
            query_artifact_ids=[q.artifact_id],
            source_names=["crossref", "semantic_scholar"],
        ),
        artifact_type="literature_search_strategy",
    )
    await store.put(strat)

    # Fake sources with hits
    crossref_hits = [_make_hit("Crossref Paper", doi="10.123/a", provider="crossref")]
    ss_hits = [_make_hit("SS Paper", doi="10.123/b", provider="semantic_scholar")]
    crossref_src = FakeSource("crossref", hits=crossref_hits)
    ss_src = FakeSource("semantic_scholar", hits=ss_hits)

    def lookup(name):  # type: ignore[no-untyped-def]
        if name == "literature_source.crossref":
            return crossref_src
        if name == "literature_source.semantic_scholar":
            return ss_src
        if name == "paper_identity_resolver.default":
            # Mock resolver that does no dedup (returns no identities)
            class FakeResolver:
                async def resolve(self, ids):  # type: ignore[no-untyped-def]
                    from research_harness.contracts.identity import IdentityResolutionResult

                    return IdentityResolutionResult(
                        identities_created=[], identities_reused=[], matches=[]
                    )

            return FakeResolver()
        raise Exception(f"unknown {name}")

    orchestrator = LiteratureSearchOrchestratorService(
        artifact_store=store,
        ingestor=ingestor,
        service_lookup=lookup,
        max_queries=8,
        max_results_per_query_per_source=10,
        max_total_provider_requests=10,
        max_total_papers=10,
    )
    exec_id = await orchestrator.execute(strat.artifact_id)
    exec_env = await store.get(exec_id)
    from research_harness.research.schemas.execution import LiteratureSearchExecution

    exec_rec = exec_env.parse_payload(LiteratureSearchExecution)
    assert exec_rec.counts["queries_executed"] == 1
    assert exec_rec.counts["provider_searches_attempted"] == 2
    assert exec_rec.counts["provider_searches_succeeded"] == 2
    assert len(exec_rec.search_record_artifact_ids) == 2
    assert len(exec_rec.paper_artifact_ids) == 2
    await store.close()


@pytest.mark.asyncio
async def test_orchestrator_partial_failure(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.ingestion.plugin import LiteratureIngestor
    from research_harness.plugins.literature.search_orchestrator.plugin import (
        LiteratureSearchOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    ingestor = LiteratureIngestor(artifact_store=store)

    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)
    q = ArtifactEnvelope.create(
        payload=LiteratureQuery(query="test", target_sources=["crossref", "semantic_scholar"]),
        artifact_type="literature_query",
    )
    await store.put(q)
    strat = ArtifactEnvelope.create(
        payload=LiteratureSearchStrategy(
            research_question_id=rq.artifact_id,
            objective="obj",
            query_artifact_ids=[q.artifact_id],
            source_names=["crossref", "semantic_scholar"],
        ),
        artifact_type="literature_search_strategy",
    )
    await store.put(strat)

    crossref_hits = [_make_hit("P1", doi="10.123/a", provider="crossref")]
    crossref_src = FakeSource("crossref", hits=crossref_hits)
    ss_src = FakeSource("semantic_scholar", should_fail=True)

    def lookup(name):  # type: ignore[no-untyped-def]
        if name == "literature_source.crossref":
            return crossref_src
        if name == "literature_source.semantic_scholar":
            return ss_src
        if name == "paper_identity_resolver.default":

            class FakeResolver:
                async def resolve(self, ids):  # type: ignore[no-untyped-def]
                    from research_harness.contracts.identity import IdentityResolutionResult

                    return IdentityResolutionResult()

            return FakeResolver()
        raise Exception(name)

    orchestrator = LiteratureSearchOrchestratorService(
        artifact_store=store, ingestor=ingestor, service_lookup=lookup
    )
    exec_id = await orchestrator.execute(strat.artifact_id)
    exec_env = await store.get(exec_id)
    from research_harness.research.schemas.execution import LiteratureSearchExecution

    rec = exec_env.parse_payload(LiteratureSearchExecution)
    assert rec.counts["provider_searches_succeeded"] == 1
    assert rec.counts["provider_searches_failed"] == 1
    assert len(rec.provider_failures) == 1
    assert len(rec.paper_artifact_ids) == 1  # only crossref succeeded
    await store.close()


@pytest.mark.asyncio
async def test_orchestrator_budget_enforcement(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.ingestion.plugin import LiteratureIngestor
    from research_harness.plugins.literature.search_orchestrator.plugin import (
        LiteratureSearchOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    ingestor = LiteratureIngestor(artifact_store=store)

    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)
    # Create 5 queries but budget max_queries=2
    q_ids = []
    for i in range(5):
        q = ArtifactEnvelope.create(
            payload=LiteratureQuery(query=f"q{i}", target_sources=["crossref"]),
            artifact_type="literature_query",
        )
        await store.put(q)
        q_ids.append(q.artifact_id)
    strat = ArtifactEnvelope.create(
        payload=LiteratureSearchStrategy(
            research_question_id=rq.artifact_id,
            objective="obj",
            query_artifact_ids=q_ids,
            source_names=["crossref"],
        ),
        artifact_type="literature_search_strategy",
    )
    await store.put(strat)

    src = FakeSource("crossref", hits=[_make_hit("P")])

    def lookup(name):  # type: ignore[no-untyped-def]
        if name.startswith("literature_source"):
            return src
        if name == "paper_identity_resolver.default":

            class FakeResolver:
                async def resolve(self, ids):  # type: ignore[no-untyped-def]
                    from research_harness.contracts.identity import IdentityResolutionResult

                    return IdentityResolutionResult()

            return FakeResolver()
        raise Exception(name)

    orchestrator = LiteratureSearchOrchestratorService(
        artifact_store=store,
        ingestor=ingestor,
        service_lookup=lookup,
        max_queries=2,
        max_results_per_query_per_source=10,
        max_total_provider_requests=10,
        max_total_papers=10,
    )
    exec_id = await orchestrator.execute(strat.artifact_id)
    exec_env = await store.get(exec_id)
    from research_harness.research.schemas.execution import LiteratureSearchExecution

    rec = exec_env.parse_payload(LiteratureSearchExecution)
    assert rec.counts["queries_executed"] == 2  # truncated
    await store.close()


@pytest.mark.asyncio
async def test_orchestrator_query_traceability(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.ingestion.plugin import LiteratureIngestor
    from research_harness.plugins.literature.search_orchestrator.plugin import (
        LiteratureSearchOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    ingestor = LiteratureIngestor(artifact_store=store)

    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)
    q = ArtifactEnvelope.create(
        payload=LiteratureQuery(query="test", target_sources=["crossref"]),
        artifact_type="literature_query",
    )
    await store.put(q)
    strat = ArtifactEnvelope.create(
        payload=LiteratureSearchStrategy(
            research_question_id=rq.artifact_id,
            objective="obj",
            query_artifact_ids=[q.artifact_id],
            source_names=["crossref"],
        ),
        artifact_type="literature_search_strategy",
    )
    await store.put(strat)

    src = FakeSource("crossref", hits=[_make_hit("P")])

    def lookup(name):  # type: ignore[no-untyped-def]
        if name == "literature_source.crossref":
            return src
        if name == "paper_identity_resolver.default":

            class FakeResolver:
                async def resolve(self, ids):  # type: ignore[no-untyped-def]
                    from research_harness.contracts.identity import IdentityResolutionResult

                    return IdentityResolutionResult()

            return FakeResolver()
        raise Exception(name)

    orchestrator = LiteratureSearchOrchestratorService(
        artifact_store=store, ingestor=ingestor, service_lookup=lookup
    )
    exec_id = await orchestrator.execute(strat.artifact_id)
    # Check that each search record is traceable to query via provenance
    exec_env = await store.get(exec_id)
    from research_harness.research.schemas.execution import LiteratureSearchExecution

    exec_rec = exec_env.parse_payload(LiteratureSearchExecution)
    assert len(exec_rec.search_record_artifact_ids) == 1
    search_id = exec_rec.search_record_artifact_ids[0]
    # Provenance from query to search record
    parents = await store.get_parents(search_id)
    assert any(p.source_artifact_id == q.artifact_id for p in parents)
    await store.close()
