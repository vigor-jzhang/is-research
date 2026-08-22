import json

import pytest

from research_harness.contracts.model import Message, ModelRequest, ModelResponse
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.project import ResearchQuestion


class FakeRouter:
    def __init__(self, response_content: str, should_fail: bool = False):
        self.response_content = response_content
        self.should_fail = should_fail
        self.last_request: ModelRequest | None = None
        self.last_role: str | None = None

    async def complete(self, role: str, request: ModelRequest):  # type: ignore[no-untyped-def]
        self.last_role = role
        self.last_request = request
        if self.should_fail:
            raise RuntimeError("model failure")
        return ModelResponse(
            message=Message(role="assistant", content=self.response_content),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


@pytest.mark.asyncio
async def test_planner_valid_strategy(tmp_path):
    from research_harness.plugins.literature.search_planner.plugin import (
        LiteratureSearchPlannerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    # Create ResearchQuestion
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="How does X affect Y?"), artifact_type="research_question"
    )
    await store.put(rq)

    # Fake model returns valid structured output
    proposal = {
        "objective": "Find papers on X and Y",
        "concepts": ["X", "Y"],
        "queries": [
            {
                "query": "X AND Y",
                "purpose": "test",
                "concepts": ["X"],
                "target_sources": ["crossref"],
            },
            {
                "query": "X OR Y",
                "purpose": "test2",
                "concepts": ["Y"],
                "target_sources": ["semantic_scholar", "crossref"],
            },
        ],
    }
    router = FakeRouter(response_content=json.dumps(proposal))
    service = LiteratureSearchPlannerService(
        model_router=router, artifact_store=store, model_role="fast", max_queries=8
    )

    strat_id, query_ids = await service.plan(rq.artifact_id)
    assert strat_id
    assert len(query_ids) == 2
    # Check artifacts persisted
    strat_env = await store.get(strat_id)
    from research_harness.research.schemas.strategy import LiteratureSearchStrategy

    strat = strat_env.parse_payload(LiteratureSearchStrategy)
    assert strat.objective == "Find papers on X and Y"
    assert len(strat.query_artifact_ids) == 2
    # Check queries
    from research_harness.research.schemas.query import LiteratureQuery

    q1 = (await store.get(query_ids[0])).parse_payload(LiteratureQuery)
    assert q1.query == "X AND Y"
    assert q1.target_sources == ["crossref"]
    # Check model role used
    assert router.last_role == "fast"
    assert router.last_request is not None
    assert router.last_request.response_schema is not None
    await store.close()


@pytest.mark.asyncio
async def test_planner_target_source_validation(tmp_path):
    from research_harness.plugins.literature.search_planner.plugin import (
        LiteratureSearchPlannerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)

    proposal = {
        "objective": "obj",
        "concepts": [],
        "queries": [{"query": "test", "target_sources": ["unknown_source"]}],
    }
    router = FakeRouter(response_content=json.dumps(proposal))
    service = LiteratureSearchPlannerService(model_router=router, artifact_store=store)

    with pytest.raises(Exception, match="target_sources"):
        await service.plan(rq.artifact_id)
    await store.close()


@pytest.mark.asyncio
async def test_planner_max_query_enforcement(tmp_path):
    from research_harness.plugins.literature.search_planner.plugin import (
        LiteratureSearchPlannerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)

    # Propose 10 queries but max is 2
    proposal = {
        "objective": "obj",
        "concepts": [],
        "queries": [{"query": f"q{i}", "target_sources": ["crossref"]} for i in range(10)],
    }
    router = FakeRouter(response_content=json.dumps(proposal))
    service = LiteratureSearchPlannerService(
        model_router=router, artifact_store=store, max_queries=2
    )
    strat_id, query_ids = await service.plan(rq.artifact_id)
    assert len(query_ids) == 2
    # Should have truncated
    await store.close()


@pytest.mark.asyncio
async def test_planner_year_validation(tmp_path):
    from research_harness.plugins.literature.search_planner.plugin import (
        LiteratureSearchPlannerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)

    proposal = {
        "objective": "obj",
        "concepts": [],
        "queries": [
            {"query": "test", "target_sources": ["crossref"], "year_from": 2025, "year_to": 2020}
        ],
    }
    router = FakeRouter(response_content=json.dumps(proposal))
    service = LiteratureSearchPlannerService(model_router=router, artifact_store=store)
    with pytest.raises(Exception, match="year_to"):
        await service.plan(rq.artifact_id)
    await store.close()


@pytest.mark.asyncio
async def test_planner_empty_query_rejected(tmp_path):
    from research_harness.plugins.literature.search_planner.plugin import (
        LiteratureSearchPlannerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)

    proposal = {
        "objective": "obj",
        "concepts": [],
        "queries": [{"query": "   ", "target_sources": ["crossref"]}],
    }
    router = FakeRouter(response_content=json.dumps(proposal))
    service = LiteratureSearchPlannerService(model_router=router, artifact_store=store)
    with pytest.raises(Exception, match="non-empty"):
        await service.plan(rq.artifact_id)
    await store.close()


@pytest.mark.asyncio
async def test_planner_model_failure(tmp_path):
    from research_harness.plugins.literature.search_planner.plugin import (
        LiteratureSearchPlannerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)

    router = FakeRouter(response_content="", should_fail=True)
    service = LiteratureSearchPlannerService(model_router=router, artifact_store=store)
    with pytest.raises(Exception, match="model call failed"):
        await service.plan(rq.artifact_id)
    await store.close()


@pytest.mark.asyncio
async def test_planner_malformed_output(tmp_path):
    from research_harness.plugins.literature.search_planner.plugin import (
        LiteratureSearchPlannerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)

    router = FakeRouter(response_content="not json")
    service = LiteratureSearchPlannerService(model_router=router, artifact_store=store)
    with pytest.raises(Exception, match="invalid JSON"):
        await service.plan(rq.artifact_id)
    await store.close()


@pytest.mark.asyncio
async def test_planner_model_role_configurable(tmp_path):
    from research_harness.plugins.literature.search_planner.plugin import (
        LiteratureSearchPlannerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)

    proposal = {
        "objective": "obj",
        "concepts": [],
        "queries": [{"query": "test", "target_sources": ["crossref"]}],
    }
    router = FakeRouter(response_content=json.dumps(proposal))
    service = LiteratureSearchPlannerService(
        model_router=router, artifact_store=store, model_role="reasoning"
    )
    await service.plan(rq.artifact_id)
    assert router.last_role == "reasoning"
    await store.close()
