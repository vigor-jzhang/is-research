"""Literature ingestion service — separates persistence from provider networking."""

from __future__ import annotations

import logging
from typing import Any

from research_harness.contracts.literature import LiteratureSearchRequest, LiteratureSource
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.provider_snapshot import ProviderRecordSnapshot
from research_harness.research.schemas.search_record import LiteratureSearchRecord

logger = logging.getLogger(__name__)


class LiteratureIngestor:
    """Ingests normalized hits into artifact store with provenance."""

    def __init__(self, artifact_store: Any, events: Any | None = None) -> None:
        self._store = artifact_store
        self._events = events

    async def ingest_search(
        self,
        source: LiteratureSource,
        request: LiteratureSearchRequest,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        producer: str = "literature.ingestion",
        query_artifact_id: str | None = None,
    ) -> tuple[
        ArtifactEnvelope[LiteratureSearchRecord],
        list[ArtifactEnvelope[ProviderRecordSnapshot]],
        list[ArtifactEnvelope[Any]],
    ]:
        # Emit started
        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="literature.search.started",
                        source=producer,
                        payload={
                            "provider": source.provider_name,
                            "query": request.query,
                            "limit": request.limit,
                        },
                        session_id=session_id,
                        run_id=run_id,
                    )
                )
            except Exception:
                logger.exception("failed to emit literature.search.started")

        # Perform search via provider
        try:
            page = await source.search(request)
        except Exception as e:
            if self._events is not None:
                try:
                    from research_harness.kernel.events import Event

                    await self._events.publish(
                        Event.create(
                            event_type="literature.search.failed",
                            source=producer,
                            payload={
                                "provider": source.provider_name,
                                "query": request.query,
                                "error": str(e),
                            },
                            session_id=session_id,
                            run_id=run_id,
                        )
                    )
                except Exception:
                    pass
            raise

        # For each hit, create snapshot + paper artifacts
        snapshot_envelopes: list[ArtifactEnvelope[ProviderRecordSnapshot]] = []
        paper_envelopes: list[ArtifactEnvelope[Any]] = []

        for hit in page.hits:
            # Provider snapshot
            snapshot_payload = ProviderRecordSnapshot(
                provider=hit.provider,
                provider_record_id=hit.provider_record_id,
                request_kind="search",
                request_metadata={"query": request.query, "rank": hit.rank, "score": hit.score},
                raw_payload=hit.raw_payload,
            )
            snapshot_env = ArtifactEnvelope.create(
                payload=snapshot_payload,
                artifact_type="provider_record_snapshot",
                producer=producer,
                session_id=session_id,
                run_id=run_id,
            )
            await self._store.put(snapshot_env)
            snapshot_envelopes.append(snapshot_env)

            # Canonical paper
            paper_env = ArtifactEnvelope.create(
                payload=hit.paper,
                artifact_type="paper_record",
                producer=producer,
                session_id=session_id,
                run_id=run_id,
            )
            await self._store.put(paper_env)
            paper_envelopes.append(paper_env)

            # Provenance: paper generated_from snapshot
            link = ProvenanceLink(
                relation=ProvenanceRelation.generated_from,
                source_artifact_id=snapshot_env.artifact_id,
                target_artifact_id=paper_env.artifact_id,
                producer=producer,
                metadata={"provider": hit.provider},
            )
            await self._store.add_provenance(link)

        # Create search record
        search_payload = LiteratureSearchRecord(
            provider=source.provider_name,
            query=request.query,
            query_artifact_id=query_artifact_id,
            filters={"year_from": request.year_from, "year_to": request.year_to},
            requested_limit=request.limit,
            returned_count=len(page.hits),
            total_estimate=page.total_estimate,
            paper_artifact_ids=[e.artifact_id for e in paper_envelopes],
            provider_snapshot_artifact_ids=[e.artifact_id for e in snapshot_envelopes],
            pagination={"next_page_token": page.next_page_token, "page_metadata": page.metadata},
        )
        search_env = ArtifactEnvelope.create(
            payload=search_payload,
            artifact_type="literature_search_record",
            producer=producer,
            session_id=session_id,
            run_id=run_id,
        )
        await self._store.put(search_env)

        # Emit completed
        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="literature.search.completed",
                        source=producer,
                        payload={
                            "provider": source.provider_name,
                            "query": request.query,
                            "returned_count": len(page.hits),
                            "search_artifact_id": search_env.artifact_id,
                        },
                        session_id=session_id,
                        run_id=run_id,
                    )
                )
                await self._events.publish(
                    Event.create(
                        event_type="literature.ingestion.completed",
                        source=producer,
                        payload={
                            "provider": source.provider_name,
                            "paper_artifact_ids": [e.artifact_id for e in paper_envelopes],
                            "snapshot_artifact_ids": [e.artifact_id for e in snapshot_envelopes],
                            "search_artifact_id": search_env.artifact_id,
                        },
                        session_id=session_id,
                        run_id=run_id,
                    )
                )
            except Exception:
                logger.exception("failed to emit ingestion completed")

        return search_env, snapshot_envelopes, paper_envelopes

    async def ingest_get(
        self,
        source: LiteratureSource,
        identifier: str,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        producer: str = "literature.ingestion",
    ) -> tuple[ArtifactEnvelope[ProviderRecordSnapshot], ArtifactEnvelope[Any]]:
        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="literature.get.started",
                        source=producer,
                        payload={"provider": source.provider_name, "identifier": identifier},
                        session_id=session_id,
                        run_id=run_id,
                    )
                )
            except Exception:
                pass

        try:
            hit = await source.get(identifier)
        except Exception as e:
            if self._events is not None:
                try:
                    from research_harness.kernel.events import Event

                    await self._events.publish(
                        Event.create(
                            event_type="literature.get.failed",
                            source=producer,
                            payload={
                                "provider": source.provider_name,
                                "identifier": identifier,
                                "error": str(e),
                            },
                            session_id=session_id,
                            run_id=run_id,
                        )
                    )
                except Exception:
                    pass
            raise

        snapshot_payload = ProviderRecordSnapshot(
            provider=hit.provider,
            provider_record_id=hit.provider_record_id,
            request_kind="get",
            request_metadata={"identifier": identifier},
            raw_payload=hit.raw_payload,
        )
        snapshot_env = ArtifactEnvelope.create(
            payload=snapshot_payload,
            artifact_type="provider_record_snapshot",
            producer=producer,
            session_id=session_id,
            run_id=run_id,
        )
        await self._store.put(snapshot_env)

        paper_env = ArtifactEnvelope.create(
            payload=hit.paper,
            artifact_type="paper_record",
            producer=producer,
            session_id=session_id,
            run_id=run_id,
        )
        await self._store.put(paper_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.generated_from,
                source_artifact_id=snapshot_env.artifact_id,
                target_artifact_id=paper_env.artifact_id,
                producer=producer,
            )
        )

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="literature.get.completed",
                        source=producer,
                        payload={
                            "provider": source.provider_name,
                            "identifier": identifier,
                            "paper_artifact_id": paper_env.artifact_id,
                        },
                        session_id=session_id,
                        run_id=run_id,
                    )
                )
            except Exception:
                pass

        return snapshot_env, paper_env


class LiteratureIngestionPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="literature.ingestion",
            version="0.1.0",
            plugin_type="literature",
            description="Literature ingestion service",
            provides=["literature_ingestor.default"],
            requires=["artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        store = ctx.require("artifact_store.default")
        ingestor = LiteratureIngestor(artifact_store=store, events=ctx.events)
        ctx.register("literature_ingestor.default", ingestor)
