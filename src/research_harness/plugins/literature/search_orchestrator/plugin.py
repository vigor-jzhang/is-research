"""Literature search orchestrator — multi-source, budgets, provenance."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from research_harness.contracts.literature import LiteratureSearchRequest
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.execution import LiteratureSearchExecution
from research_harness.research.schemas.query import LiteratureQuery
from research_harness.research.schemas.strategy import LiteratureSearchStrategy

logger = logging.getLogger(__name__)


class LiteratureSearchOrchestratorService:
    def __init__(
        self,
        artifact_store: Any,
        ingestor: Any,
        service_lookup: Any,
        events: Any | None = None,
        max_queries: int = 8,
        max_results_per_query_per_source: int = 50,
        max_total_provider_requests: int = 50,
        max_total_papers: int = 500,
    ) -> None:
        self._store = artifact_store
        self._ingestor = ingestor
        self._lookup = service_lookup
        self._events = events
        self._max_queries = max_queries
        self._max_results_per_query = max_results_per_query_per_source
        self._max_requests = max_total_provider_requests
        self._max_papers = max_total_papers

    async def execute(self, strategy_artifact_id: str) -> str:
        # Load strategy
        strat_env = await self._store.get(strategy_artifact_id)
        # Parse payload
        if isinstance(strat_env.payload, dict):
            strategy = LiteratureSearchStrategy.model_validate(strat_env.payload)
        else:
            strategy = strat_env.parse_payload(LiteratureSearchStrategy)  # type: ignore[attr-defined]

        # Enforce max_queries budget (strategy may have more than allowed)
        query_ids = strategy.query_artifact_ids[: self._max_queries]
        if len(strategy.query_artifact_ids) > self._max_queries:
            logger.warning(
                "strategy has %d queries, truncating to %d",
                len(strategy.query_artifact_ids),
                self._max_queries,
            )

        started_at = datetime.now(UTC)
        search_record_ids: list[str] = []
        paper_ids: list[str] = []
        paper_identity_ids: list[str] = []
        provider_failures: list[dict[str, Any]] = []
        provider_searches_attempted = 0
        provider_searches_succeeded = 0
        provider_searches_failed = 0
        raw_paper_count = 0

        # Sequential execution
        total_requests = 0
        total_papers = 0

        for qid in query_ids:
            # Load query
            try:
                q_env = await self._store.get(qid)
                if isinstance(q_env.payload, dict):
                    query = LiteratureQuery.model_validate(q_env.payload)
                else:
                    query = q_env.parse_payload(LiteratureQuery)  # type: ignore[attr-defined]
            except Exception as e:
                logger.warning("Failed to load query %s: %s", qid, e)
                provider_failures.append({"query_id": qid, "error": str(e), "provider": "unknown"})
                continue

            # Enforce per-query source limit
            sources = query.target_sources[:2]  # bounded
            for src_name in sources:
                if total_requests >= self._max_requests:
                    logger.warning(
                        "max_total_provider_requests %d reached, stopping", self._max_requests
                    )
                    provider_failures.append(
                        {
                            "query_id": qid,
                            "provider": src_name,
                            "error": "budget max_total_provider_requests reached",
                        }
                    )
                    break
                if total_papers >= self._max_papers:
                    logger.warning("max_total_papers %d reached, stopping", self._max_papers)
                    break

                provider_searches_attempted += 1
                total_requests += 1

                # Resolve source service
                try:
                    source = self._lookup(f"literature_source.{src_name}")
                except Exception as e:
                    provider_searches_failed += 1
                    provider_failures.append(
                        {
                            "query_id": qid,
                            "provider": src_name,
                            "error": f"source not available: {e}",
                        }
                    )
                    continue

                # Build search request for this query+source
                req = LiteratureSearchRequest(
                    query=query.query,
                    year_from=query.year_from,
                    year_to=query.year_to,
                    limit=self._max_results_per_query,
                    page_token=None,
                )

                try:
                    search_env, _snapshot_envs, paper_envs = await self._ingestor.ingest_search(
                        source, req, query_artifact_id=qid
                    )
                    # Provenance for traceability: search record derived_from query
                    await self._store.add_provenance(
                        ProvenanceLink(
                            relation=ProvenanceRelation.derived_from,
                            source_artifact_id=qid,
                            target_artifact_id=search_env.artifact_id,
                            producer="literature.search_orchestrator",
                        )
                    )

                    search_record_ids.append(search_env.artifact_id)
                    for pe in paper_envs:
                        if pe.artifact_id not in paper_ids:
                            paper_ids.append(pe.artifact_id)
                            raw_paper_count += 1
                            total_papers += 1
                            if total_papers >= self._max_papers:
                                break
                    provider_searches_succeeded += 1

                    # Add provenance from execution perspective: we will link later

                except Exception as e:
                    provider_searches_failed += 1
                    provider_failures.append(
                        {
                            "query_id": qid,
                            "provider": src_name,
                            "error": str(e),
                            "query": query.query,
                        }
                    )
                    logger.warning(
                        "Provider search failed for query %s source %s: %s", qid, src_name, e
                    )
                    continue

                # Check budgets after each
                if total_papers >= self._max_papers:
                    break
            if total_papers >= self._max_papers or total_requests >= self._max_requests:
                break

        # After all searches, invoke identity resolver if available
        try:
            resolver = self._lookup("paper_identity_resolver.default")
            # Only resolve if we have papers
            if paper_ids:
                result = await resolver.resolve(paper_ids)
                paper_identity_ids = result.identities_created + result.identities_reused
                # Note: superseded are not counted as current
            else:
                paper_identity_ids = []
        except Exception as e:
            logger.warning("Identity resolver not available or failed: %s", e)
            paper_identity_ids = []

        completed_at = datetime.now(UTC)

        # Build execution artifact
        execution = LiteratureSearchExecution(
            strategy_artifact_id=strategy_artifact_id,
            query_artifact_ids=query_ids,
            search_record_artifact_ids=search_record_ids,
            paper_artifact_ids=paper_ids,
            paper_identity_artifact_ids=paper_identity_ids,
            started_at=started_at,
            completed_at=completed_at,
            provider_failures=provider_failures,
            counts={
                "queries_planned": len(strategy.query_artifact_ids),
                "queries_executed": len(query_ids),
                "provider_searches_attempted": provider_searches_attempted,
                "provider_searches_succeeded": provider_searches_succeeded,
                "provider_searches_failed": provider_searches_failed,
                "raw_paper_records": raw_paper_count,
                "unique_paper_identities": len(paper_identity_ids),
                "duplicate_records_collapsed": max(0, raw_paper_count - len(paper_identity_ids)),
                "unresolved_records": 0,
            },
            metadata={
                "budgets": {
                    "max_queries": self._max_queries,
                    "max_results_per_query": self._max_results_per_query,
                    "max_requests": self._max_requests,
                    "max_papers": self._max_papers,
                }
            },
        )
        exec_env = ArtifactEnvelope.create(
            payload=execution,
            artifact_type="literature_search_execution",
            producer="literature.search_orchestrator",
        )
        await self._store.put(exec_env)
        # Provenance: execution derived_from strategy and queries
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=strategy_artifact_id,
                target_artifact_id=exec_env.artifact_id,
                producer="literature.search_orchestrator",
            )
        )
        for qid in query_ids:
            try:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=qid,
                        target_artifact_id=exec_env.artifact_id,
                        producer="literature.search_orchestrator",
                    )
                )
            except Exception:
                pass
        for sid in search_record_ids:
            try:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=sid,
                        target_artifact_id=exec_env.artifact_id,
                        producer="literature.search_orchestrator",
                    )
                )
            except Exception:
                pass

        # Emit event
        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                status = (
                    "complete_success"
                    if provider_searches_failed == 0
                    else (
                        "partial_success" if provider_searches_succeeded > 0 else "complete_failure"
                    )
                )
                await self._events.publish(
                    Event.create(
                        event_type="literature.orchestration.completed",
                        source="literature.search_orchestrator",
                        payload={
                            "execution_artifact_id": exec_env.artifact_id,
                            "strategy_artifact_id": strategy_artifact_id,
                            "status": status,
                            "counts": execution.counts,
                            "failures": provider_failures,
                        },
                    )
                )
            except Exception:
                logger.exception("failed to emit orchestration completed")

        return exec_env.artifact_id


class LiteratureSearchOrchestratorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="literature.search_orchestrator",
            version="0.1.0",
            plugin_type="literature",
            description="Multi-source search orchestrator",
            provides=["literature_search_orchestrator.default"],
            requires=["artifact_store.default", "literature_ingestor.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        orch_cfg: dict[str, Any] = {}
        if "literature" in cfg and isinstance(cfg["literature"], dict):
            orch_cfg = (
                cfg["literature"].get("orchestration", {})
                if isinstance(cfg["literature"].get("orchestration"), dict)
                else {}
            )
        elif "orchestration" in cfg and isinstance(cfg["orchestration"], dict):
            orch_cfg = cfg["orchestration"]

        store = ctx.require("artifact_store.default")
        ingestor = ctx.require("literature_ingestor.default")

        def lookup(name: str):  # type: ignore[no-untyped-def]
            return ctx.require(name)

        # Try to get providers if available, but not required at setup
        service = LiteratureSearchOrchestratorService(
            artifact_store=store,
            ingestor=ingestor,
            service_lookup=lookup,
            events=ctx.events,
            max_queries=int(orch_cfg.get("max_queries", 8)),
            max_results_per_query_per_source=int(
                orch_cfg.get("max_results_per_query_per_source", 50)
            ),
            max_total_provider_requests=int(orch_cfg.get("max_total_provider_requests", 50)),
            max_total_papers=int(orch_cfg.get("max_total_papers", 500)),
        )
        ctx.register("literature_search_orchestrator.default", service)
