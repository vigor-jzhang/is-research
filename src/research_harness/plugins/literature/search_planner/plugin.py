"""Literature search planner — uses model structured output."""

from __future__ import annotations

import logging
from typing import Any

from research_harness.contracts.planning import SearchStrategyProposal
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.query import LiteratureQuery
from research_harness.research.schemas.strategy import LiteratureSearchStrategy

logger = logging.getLogger(__name__)


class LiteratureSearchPlannerService:
    def __init__(
        self,
        model_router: Any,
        artifact_store: Any,
        events: Any | None = None,
        model_role: str = "fast",
        max_queries: int = 8,
        max_sources_per_query: int = 2,
    ) -> None:
        self._router = model_router
        self._store = artifact_store
        self._events = events
        self._model_role = model_role
        self._max_queries = max_queries
        self._max_sources_per_query = max_sources_per_query

    async def plan(
        self,
        research_question_id: str,
        research_plan_id: str | None = None,
    ) -> tuple[str, list[str]]:
        # Load artifacts
        try:
            rq_env = await self._store.get(research_question_id)
        except Exception as e:
            raise ValueError(f"ResearchQuestion {research_question_id!r} not found: {e}") from e
        # Try to parse payload
        try:
            rq = rq_env.parse_payload(
                __import__(
                    "research_harness.research.schemas.project", fromlist=["ResearchQuestion"]
                ).ResearchQuestion
            )  # type: ignore[attr-defined]
        except Exception:
            # Fallback: try direct model_validate if payload is dict
            from research_harness.research.schemas.project import ResearchQuestion

            if isinstance(rq_env.payload, dict):
                rq = ResearchQuestion.model_validate(rq_env.payload)
            else:
                rq = rq_env.payload  # type: ignore[assignment]

        rp = None
        if research_plan_id:
            try:
                rp_env = await self._store.get(research_plan_id)
                from research_harness.research.schemas.project import ResearchPlan

                if isinstance(rp_env.payload, dict):
                    rp = ResearchPlan.model_validate(rp_env.payload)
                else:
                    rp = rp_env.parse_payload(ResearchPlan)  # type: ignore[attr-defined]
            except Exception as e:
                raise ValueError(f"ResearchPlan {research_plan_id!r} not found: {e}") from e

        # Build model-visible context (controlled)
        context_parts = [f"Research Question: {rq.question}"]
        if rq.motivation:
            context_parts.append(f"Motivation: {rq.motivation}")
        if rq.scope:
            context_parts.append(f"Scope: {rq.scope}")
        if rq.constraints:
            context_parts.append(f"Constraints: {rq.constraints}")
        if rp:
            context_parts.append(f"Research Plan Objective: {rp.objective}")
            if rp.steps:
                context_parts.append(f"Plan Steps: {', '.join(rp.steps)}")
            if rp.search_concepts:
                context_parts.append(f"Search Concepts: {', '.join(rp.search_concepts)}")
        context = "\n".join(context_parts)

        # Define structured output schema
        # We use response_schema for model
        response_schema = {
            "type": "object",
            "properties": {
                "objective": {"type": "string"},
                "concepts": {"type": "array", "items": {"type": "string"}},
                "queries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "purpose": {"type": "string"},
                            "concepts": {"type": "array", "items": {"type": "string"}},
                            "target_sources": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "enum": ["crossref", "semantic_scholar"],
                                },
                            },
                            "year_from": {"type": ["integer", "null"]},
                            "year_to": {"type": ["integer", "null"]},
                        },
                        "required": ["query", "target_sources"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["objective", "queries"],
            "additionalProperties": False,
        }

        # Build prompt
        prompt = f"""You are a research assistant planning literature search.

Context:
{context}

Task: Propose a search strategy with 2-4 queries for finding relevant scholarly papers.
Each query should target crossref and/or semantic_scholar.
Keep queries concise (3-8 keywords, may include AND/OR), purpose short, concepts list relevant.
Year ranges are optional.
Target sources must be subset of ["crossref", "semantic_scholar"].

Limits: max {self._max_queries} queries, max {self._max_sources_per_query} sources per query.
"""

        # Prepare model request
        from research_harness.contracts.model import Message, ModelRequest

        messages = [
            Message(
                role="system",
                content="You are a helpful research planning assistant. Respond with valid JSON matching the requested schema.",
            ),
            Message(role="user", content=prompt),
        ]
        request = ModelRequest(
            messages=messages,
            response_schema=response_schema,
            temperature=0.2,
            metadata={},
        )

        # Call model via router
        try:
            response = await self._router.complete(self._model_role, request)
        except Exception as e:
            raise RuntimeError(f"planner model call failed (role {self._model_role!r}): {e}") from e

        content = response.message.content or ""
        # Parse structured output
        import json

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"planner model returned invalid JSON: {content[:500]!r}: {e}") from e

        # Validate via Pydantic
        try:
            proposal = SearchStrategyProposal.model_validate(data)
        except Exception as e:
            raise ValueError(f"planner output failed validation: {e}, data: {data}") from e

        # Enforce bounds even if model ignores
        if len(proposal.queries) > self._max_queries:
            logger.warning(
                "planner proposed %d queries, truncating to %d",
                len(proposal.queries),
                self._max_queries,
            )
            proposal = proposal.model_copy(
                update={"queries": proposal.queries[: self._max_queries]}
            )
        if not proposal.queries:
            raise ValueError("planner must propose at least 1 query")
        for q in proposal.queries:
            if not q.query.strip():
                raise ValueError("planner query must be non-empty")
            if len(q.query) > 500:
                raise ValueError("planner query too long")
            if not q.target_sources:
                raise ValueError("planner query must have at least one target source")
            if len(q.target_sources) > self._max_sources_per_query:
                raise ValueError(
                    f"too many sources {q.target_sources!r}, max {self._max_sources_per_query}"
                )
            if q.year_from is not None and q.year_to is not None and q.year_to < q.year_from:
                raise ValueError("year_to must be >= year_from")

        # Persist LiteratureQuery artifacts
        query_ids: list[str] = []
        for qp in proposal.queries:
            query_payload = LiteratureQuery(
                query=qp.query,
                purpose=qp.purpose,
                concepts=qp.concepts,
                target_sources=qp.target_sources,
                year_from=qp.year_from,
                year_to=qp.year_to,
                generated_by=f"model:{self._model_role}",
            )
            q_env = ArtifactEnvelope.create(
                payload=query_payload,
                artifact_type="literature_query",
                producer=f"literature.search_planner:{self._model_role}",
            )
            await self._store.put(q_env)
            # Provenance: query derived_from ResearchQuestion (and optionally ResearchPlan)
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=research_question_id,
                    target_artifact_id=q_env.artifact_id,
                    producer="literature.search_planner",
                )
            )
            if research_plan_id:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=research_plan_id,
                        target_artifact_id=q_env.artifact_id,
                        producer="literature.search_planner",
                    )
                )
            query_ids.append(q_env.artifact_id)

        # Persist SearchStrategy
        strategy = LiteratureSearchStrategy(
            research_question_id=research_question_id,
            research_plan_id=research_plan_id,
            objective=proposal.objective,
            concepts=proposal.concepts,
            query_artifact_ids=query_ids,
            source_names=list({s for q in proposal.queries for s in q.target_sources}),
            year_constraints={},
            max_results_per_query=None,
            max_total_results=None,
        )
        strat_env = ArtifactEnvelope.create(
            payload=strategy,
            artifact_type="literature_search_strategy",
            producer=f"literature.search_planner:{self._model_role}",
        )
        await self._store.put(strat_env)
        # Provenance: strategy derived_from ResearchQuestion
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=research_question_id,
                target_artifact_id=strat_env.artifact_id,
                producer="literature.search_planner",
            )
        )
        if research_plan_id:
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=research_plan_id,
                    target_artifact_id=strat_env.artifact_id,
                    producer="literature.search_planner",
                )
            )
        # Also link strategy derived_from queries
        for qid in query_ids:
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=qid,
                    target_artifact_id=strat_env.artifact_id,
                    producer="literature.search_planner",
                )
            )

        # Emit event
        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="literature.planning.completed",
                        source="literature.search_planner",
                        payload={
                            "strategy_artifact_id": strat_env.artifact_id,
                            "query_artifact_ids": query_ids,
                            "objective": proposal.objective,
                        },
                    )
                )
            except Exception:
                logger.exception("failed to emit planning completed")

        return strat_env.artifact_id, query_ids


class LiteratureSearchPlannerPlugin(Plugin):
    def __init__(
        self,
        model_role: str | None = None,
        max_queries: int | None = None,
        max_sources_per_query: int | None = None,
    ) -> None:
        self._model_role_override = model_role
        self._max_queries_override = max_queries
        self._max_sources_override = max_sources_per_query
        self._service: LiteratureSearchPlannerService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="literature.search_planner",
            version="0.1.0",
            plugin_type="literature",
            description="Literature search planner (model-assisted)",
            provides=["literature_search_planner.default"],
            requires=["model_router.default", "artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        # cfg may be {"literature": {"planning": {...}}} or flat
        planning_cfg: dict[str, Any] = {}
        if "literature" in cfg and isinstance(cfg["literature"], dict):
            planning_cfg = (
                cfg["literature"].get("planning", {})
                if isinstance(cfg["literature"].get("planning"), dict)
                else {}
            )
        elif "planning" in cfg and isinstance(cfg["planning"], dict):
            planning_cfg = cfg["planning"]

        model_role = self._model_role_override or planning_cfg.get("model_role") or "fast"
        max_queries = self._max_queries_override or planning_cfg.get("max_queries") or 8
        max_sources = self._max_sources_override or planning_cfg.get("max_sources_per_query") or 2

        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        service = LiteratureSearchPlannerService(
            model_router=router,
            artifact_store=store,
            events=ctx.events,
            model_role=str(model_role),
            max_queries=int(max_queries),
            max_sources_per_query=int(max_sources),
        )
        self._service = service
        ctx.register("literature_search_planner.default", service)
