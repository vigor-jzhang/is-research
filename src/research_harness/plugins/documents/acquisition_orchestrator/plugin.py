"""Document acquisition orchestrator — consumes ScreenedLiteratureSet, produces FullTextCorpus."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.document_acquisition import (
    AcquisitionStatus,
    DocumentAcquisition,
)
from research_harness.research.schemas.full_text import DocumentAcquisitionExecution, FullTextCorpus

logger = logging.getLogger(__name__)


class DocumentAcquisitionOrchestratorService:
    def __init__(
        self,
        artifact_store: Any,
        blob_store: Any,
        fetcher: Any | None = None,
        extractor: Any | None = None,
        metadata_locator: Any | None = None,
        unpaywall_locator: Any | None = None,
        events: Any | None = None,
        max_locations_per_paper: int = 5,
        max_candidates: int = 500,  # alias for safety
    ) -> None:
        self._store = artifact_store
        self._blobs = blob_store
        self._fetcher = fetcher
        self._extractor = extractor
        self._meta_locator = metadata_locator
        self._unpaywall_locator = unpaywall_locator
        self._events = events
        self._max_locations = max_locations_per_paper

    async def _resolve_locations(self, paper_identity_id: str) -> list[str]:
        # Deterministic priority: metadata direct PDF first, then unpaywall best, then other unpaywall, then landing
        # We call both locators and merge with priority
        meta_ids: list[str] = []
        unpaywall_ids: list[str] = []

        if self._meta_locator is not None:
            try:
                meta_ids = await self._meta_locator.resolve(paper_identity_id)
            except Exception as e:
                logger.warning("metadata locator failed for %s: %s", paper_identity_id, e)
                meta_ids = []

        if self._unpaywall_locator is not None:
            try:
                unpaywall_ids = await self._unpaywall_locator.resolve(paper_identity_id)
            except Exception as e:
                logger.warning("unpaywall locator failed for %s: %s", paper_identity_id, e)
                unpaywall_ids = []

        # Merge with priority: meta direct PDFs first, then unpaywall
        # We need to sort each list deterministically already; then combine
        # For unpaywall, the locator already returns in priority order (direct PDF first)
        # So final order is meta_ids + unpaywall_ids, but deduplicate same url
        # To deduplicate, we need to check url equality
        combined: list[str] = []
        seen_urls: set[str] = set()

        # Helper to get url for dedup
        async def _url_for_loc(loc_id: str) -> str | None:
            try:
                env = await self._store.get(loc_id)
                from research_harness.research.schemas.document_location import DocumentLocation

                if isinstance(env.payload, dict):
                    loc = DocumentLocation.model_validate(env.payload)
                else:
                    loc = env.parse_payload(DocumentLocation)  # type: ignore[attr-defined]
                return loc.url
            except Exception:
                return None

        for loc_id in meta_ids:
            url = await _url_for_loc(loc_id)
            if url and url not in seen_urls:
                seen_urls.add(url)
                combined.append(loc_id)

        for loc_id in unpaywall_ids:
            url = await _url_for_loc(loc_id)
            if url and url not in seen_urls:
                seen_urls.add(url)
                combined.append(loc_id)

        # Limit
        combined = combined[: self._max_locations]

        # Emit location events
        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="document.location.completed",
                        source="documents.acquisition_orchestrator",
                        payload={
                            "paper_identity_id": paper_identity_id,
                            "location_count": len(combined),
                            "location_ids": combined[:3],
                        },
                    )
                )
            except Exception:
                pass

        return combined

    async def run(self, screened_literature_set_id: str) -> str:
        # Load screened set
        try:
            set_env = await self._store.get(screened_literature_set_id)
        except Exception as e:
            raise ValueError(
                f"ScreenedLiteratureSet {screened_literature_set_id!r} not found: {e}"
            ) from e

        from research_harness.research.schemas.screening_execution import ScreenedLiteratureSet

        if isinstance(set_env.payload, dict):
            screened_set = ScreenedLiteratureSet.model_validate(set_env.payload)
        else:
            screened_set = set_env.parse_payload(ScreenedLiteratureSet)  # type: ignore[attr-defined]

        # Only included by default (exact screened corpus)
        candidate_ids = list(screened_set.included_identity_ids)
        # Do NOT substitute newer PaperIdentity versions; use exact ids as supplied

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="document.acquisition.started",
                        source="documents.acquisition_orchestrator",
                        payload={
                            "screened_set_id": screened_literature_set_id,
                            "total_included": len(candidate_ids),
                        },
                    )
                )
            except Exception:
                pass

        started = datetime.now(UTC)
        location_ids: list[str] = []
        acquisition_ids: list[str] = []
        fulltext_ids: list[str] = []
        failures: list[dict[str, Any]] = []
        counts = {
            "total_included": len(candidate_ids),
            "locations_found": 0,
            "downloaded": 0,
            "imported": 0,
            "no_location": 0,
            "access_restricted": 0,
            "invalid_content": 0,
            "too_large": 0,
            "failed": 0,
            "text_extracted": 0,
            "insufficient_text": 0,
            "encrypted": 0,
        }

        # Idempotency: check if execution already exists for this screened set
        existing_executions = await self._store.list(artifact_type="document_acquisition_execution")
        for env in existing_executions:
            try:
                if isinstance(env.payload, dict):
                    ex = DocumentAcquisitionExecution.model_validate(env.payload)
                else:
                    ex = env.parse_payload(DocumentAcquisitionExecution)  # type: ignore[attr-defined]
                if ex.screened_literature_set_id == screened_literature_set_id:
                    # Reuse if already completed and counts match (simple idempotency)
                    # For now, we will not reuse automatically; we create new execution each run
                    # But we can check if all acquisitions already exist and return existing
                    pass
            except Exception:
                continue

        for pi_id in candidate_ids:
            # Resolve locations
            loc_ids = await self._resolve_locations(pi_id)
            location_ids.extend(loc_ids)
            if not loc_ids:
                counts["no_location"] += 1
                # Create a not_available acquisition? The spec says record unavailable
                # We could create a DocumentAcquisition with status not_available for traceability
                # Let's create one
                acq = DocumentAcquisition(
                    paper_identity_id=pi_id,
                    document_location_id=None,
                    status=AcquisitionStatus.not_available,
                    attempted_at=datetime.now(UTC),
                    completed_at=datetime.now(UTC),
                    source_type="none",
                    failure_code="no_location",
                    failure_message="No legitimate OA location found",
                )
                acq_env = ArtifactEnvelope.create(
                    payload=acq,
                    artifact_type="document_acquisition",
                    producer="documents.acquisition_orchestrator",
                )
                await self._store.put(acq_env)
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=pi_id,
                        target_artifact_id=acq_env.artifact_id,
                        producer="documents.acquisition_orchestrator",
                    )
                )
                # Also derived from screened set
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=screened_literature_set_id,
                        target_artifact_id=acq_env.artifact_id,
                        producer="documents.acquisition_orchestrator",
                    )
                )
                acquisition_ids.append(acq_env.artifact_id)
                counts["locations_found"] += 0
                continue

            counts["locations_found"] += len(loc_ids)
            # Attempt fetches in priority order, bounded
            acquired = False
            last_acq_id: str | None = None
            for loc_id in loc_ids:
                if self._fetcher is None:
                    # No fetcher, record failure
                    failures.append({"paper_identity_id": pi_id, "error": "no fetcher"})
                    continue
                try:
                    # Check if acquisition already exists for this location and is successful with same blob
                    # We delegate to fetcher's idempotency; just call fetch
                    acq_id = await self._fetcher.fetch(loc_id)
                    last_acq_id = acq_id
                    # Load acquisition to check status
                    acq_env = await self._store.get(acq_id)
                    if isinstance(acq_env.payload, dict):
                        acq = DocumentAcquisition.model_validate(acq_env.payload)
                    else:
                        acq = acq_env.parse_payload(DocumentAcquisition)  # type: ignore[attr-defined]
                    acquisition_ids.append(acq_id)
                    # Update counts based on status
                    if acq.status == AcquisitionStatus.downloaded:
                        counts["downloaded"] += 1
                        acquired = True
                    elif acq.status == AcquisitionStatus.imported:
                        counts["imported"] += 1
                        acquired = True
                    elif acq.status == AcquisitionStatus.not_available:
                        counts["no_location"] += 1
                    elif acq.status == AcquisitionStatus.access_restricted:
                        counts["access_restricted"] += 1
                    elif acq.status == AcquisitionStatus.invalid_content:
                        counts["invalid_content"] += 1
                    elif acq.status == AcquisitionStatus.too_large:
                        counts["too_large"] += 1
                    else:
                        counts["failed"] += 1

                    if acq.status in (AcquisitionStatus.downloaded, AcquisitionStatus.imported):
                        # Try extraction if fetcher succeeded and extractor available
                        if self._extractor is not None:
                            try:
                                doc_id = await self._extractor.extract(acq_id)
                                fulltext_ids.append(doc_id)
                                # Check text_status
                                doc_env = await self._store.get(doc_id)
                                from research_harness.research.schemas.full_text import (
                                    FullTextDocument,
                                )

                                if isinstance(doc_env.payload, dict):
                                    doc = FullTextDocument.model_validate(doc_env.payload)
                                else:
                                    doc = doc_env.parse_payload(FullTextDocument)  # type: ignore[attr-defined]
                                if doc.text_status.value == "extracted":
                                    counts["text_extracted"] += 1
                                elif doc.text_status.value == "insufficient_text":
                                    counts["insufficient_text"] += 1
                                elif doc.text_status.value == "encrypted":
                                    counts["encrypted"] += 1
                            except Exception as e:
                                logger.warning("extraction failed for %s: %s", acq_id, e)
                                failures.append(
                                    {"paper_identity_id": pi_id, "error": f"extraction failed: {e}"}
                                )
                        # Success, no need to try next location
                        break
                    else:
                        # For invalid_content etc., try next location if available
                        # But if we got invalid_content, maybe next location could succeed (e.g., 404 then Unpaywall works)
                        # So continue to next loc if not last
                        if loc_id != loc_ids[-1]:
                            continue
                        else:
                            # Last location failed, record
                            acquired = acq.status == AcquisitionStatus.downloaded
                            break
                except Exception as e:
                    logger.exception("fetch failed for %s loc %s", pi_id, loc_id)
                    failures.append(
                        {"paper_identity_id": pi_id, "location_id": loc_id, "error": str(e)}
                    )
                    counts["failed"] += 1
                    continue

            if not acquired and last_acq_id is None:
                # No acquisition created for some reason (should not happen)
                failures.append({"paper_identity_id": pi_id, "error": "no acquisition created"})
            elif not acquired:
                # We tried all locations and none succeeded via downloaded/imported; the last acquisition already counted
                pass

        completed = datetime.now(UTC)

        # Create execution
        execution = DocumentAcquisitionExecution(
            screened_literature_set_id=screened_literature_set_id,
            paper_identity_ids=candidate_ids,
            location_artifact_ids=location_ids,
            acquisition_artifact_ids=acquisition_ids,
            full_text_document_ids=fulltext_ids,
            started_at=started,
            completed_at=completed,
            counts=counts,
            failures=failures,
            metadata={},
        )
        exec_env = ArtifactEnvelope.create(
            payload=execution,
            artifact_type="document_acquisition_execution",
            producer="documents.acquisition_orchestrator",
        )
        await self._store.put(exec_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=screened_literature_set_id,
                target_artifact_id=exec_env.artifact_id,
                producer="documents.acquisition_orchestrator",
            )
        )
        # Also derived from each acquisition? Add provenance for first few
        for acq_id in acquisition_ids[:5]:
            try:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=acq_id,
                        target_artifact_id=exec_env.artifact_id,
                        producer="documents.acquisition_orchestrator",
                    )
                )
            except Exception:
                pass

        # Create FullTextCorpus
        available: list[str] = []
        unavailable: list[str] = []
        restricted: list[str] = []
        failed_ids: list[str] = []
        # Map pi_id -> status via acquisitions
        # For simplicity, iterate candidate_ids and check acquisition status
        for pi_id in candidate_ids:
            # Find acquisitions for this pi
            acqs_for_pi = []
            for acq_id in acquisition_ids:
                try:
                    acq_env = await self._store.get(acq_id)
                    if isinstance(acq_env.payload, dict):
                        acq = DocumentAcquisition.model_validate(acq_env.payload)
                    else:
                        acq = acq_env.parse_payload(DocumentAcquisition)  # type: ignore[attr-defined]
                    if acq.paper_identity_id == pi_id:
                        acqs_for_pi.append((acq_id, acq))
                except Exception:
                    continue
            if not acqs_for_pi:
                unavailable.append(pi_id)
                continue
            # Check if any acquisition is downloaded/imported and has FullTextDocument with extracted text
            has_extracted = False
            has_restricted = False
            has_failed = False
            for acq_id, acq in acqs_for_pi:
                if (
                    acq.status == AcquisitionStatus.downloaded
                    or acq.status == AcquisitionStatus.imported
                ):
                    # Check if there's a FullTextDocument for this acquisition with extracted status
                    for doc_id in fulltext_ids:
                        try:
                            doc_env = await self._store.get(doc_id)
                            from research_harness.research.schemas.full_text import (
                                FullTextDocument as FTD,
                            )

                            if isinstance(doc_env.payload, dict):
                                doc = FTD.model_validate(doc_env.payload)
                            else:
                                doc = doc_env.parse_payload(FTD)  # type: ignore[attr-defined]
                            if doc.document_acquisition_id == acq_id and doc.text_status.value in (
                                "extracted",
                                "insufficient_text",
                            ):
                                # Even insufficient counts as available? Spec says available_document_ids are FullTextDocuments with extracted text
                                # We'll count extracted as available, insufficient as not available? But spec corpus says available_document_ids are FullTextDocument ids
                                # Let's count extracted as available, insufficient as not
                                if doc.text_status.value == "extracted":
                                    has_extracted = True
                                # Also treat insufficient as available? For now, only extracted is available
                                break
                        except Exception:
                            continue
                    if has_extracted:
                        break
                elif acq.status == AcquisitionStatus.access_restricted:
                    has_restricted = True
                elif acq.status in (
                    AcquisitionStatus.failed,
                    AcquisitionStatus.invalid_content,
                    AcquisitionStatus.too_large,
                    AcquisitionStatus.not_available,
                ):
                    has_failed = True

            if has_extracted:
                # Find doc id
                for doc_id in fulltext_ids:
                    doc_env = await self._store.get(doc_id)
                    from research_harness.research.schemas.full_text import FullTextDocument as FTD

                    if isinstance(doc_env.payload, dict):
                        doc = FTD.model_validate(doc_env.payload)
                    else:
                        doc = doc_env.parse_payload(FTD)  # type: ignore[attr-defined]
                    # Match pi via acquisition
                    acq_env = await self._store.get(doc.document_acquisition_id)
                    if isinstance(acq_env.payload, dict):
                        acq = DocumentAcquisition.model_validate(acq_env.payload)
                    else:
                        acq = acq_env.parse_payload(DocumentAcquisition)  # type: ignore[attr-defined]
                    if acq.paper_identity_id == pi_id and doc.text_status.value == "extracted":
                        available.append(doc_id)
                        break
                else:
                    # No extracted doc but has acquisition, treat as failed
                    failed_ids.append(pi_id)
            elif has_restricted:
                restricted.append(pi_id)
            elif has_failed:
                # Check if unavailable vs failed
                # If any acquisition is not_available, treat as unavailable
                if any(acq.status == AcquisitionStatus.not_available for _, acq in acqs_for_pi):
                    unavailable.append(pi_id)
                else:
                    failed_ids.append(pi_id)
            else:
                unavailable.append(pi_id)

        corpus = FullTextCorpus(
            document_acquisition_execution_id=exec_env.artifact_id,
            screened_literature_set_id=screened_literature_set_id,
            available_document_ids=available,
            unavailable_identity_ids=unavailable,
            restricted_identity_ids=restricted,
            failed_identity_ids=failed_ids,
            metadata={"counts": counts},
        )
        corpus_env = ArtifactEnvelope.create(
            payload=corpus,
            artifact_type="full_text_corpus",
            producer="documents.acquisition_orchestrator",
        )
        await self._store.put(corpus_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=exec_env.artifact_id,
                target_artifact_id=corpus_env.artifact_id,
                producer="documents.acquisition_orchestrator",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=screened_literature_set_id,
                target_artifact_id=corpus_env.artifact_id,
                producer="documents.acquisition_orchestrator",
            )
        )

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="document.corpus.completed",
                        source="documents.acquisition_orchestrator",
                        payload={
                            "corpus_id": corpus_env.artifact_id,
                            "execution_id": exec_env.artifact_id,
                            "available": len(available),
                            "counts": counts,
                        },
                    )
                )
            except Exception:
                pass
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="document.acquisition.completed",
                        source="documents.acquisition_orchestrator",
                        payload={
                            "execution_id": exec_env.artifact_id,
                            "corpus_id": corpus_env.artifact_id,
                        },
                    )
                )
            except Exception:
                pass

        return exec_env.artifact_id

    async def import_local(self, paper_identity_id: str, file_path: str) -> str:
        # Validate file
        p = Path(file_path)
        if not p.exists() or not p.is_file():
            raise ValueError(f"file not found: {file_path!r}")
        # Validate PDF? Check size and signature
        data = p.read_bytes()
        if len(data) > 52428800:  # default max
            raise ValueError(f"file too large: {len(data)} > max")
        # Check PDF signature? Allow only PDF for now
        if not data.lstrip(b"\x00\x20\x09\x0a\x0d\xef\xbb\xbf").startswith(b"%PDF-"):
            raise ValueError("file is not a PDF (missing %PDF- signature)")

        # Store blob
        sha = hashlib.sha256(data).hexdigest()
        # Check existing acquisition for same paper + same sha (idempotency)
        existing = await self._store.list(artifact_type="document_acquisition")
        for env in existing:
            try:
                if isinstance(env.payload, dict):
                    acq = DocumentAcquisition.model_validate(env.payload)
                else:
                    acq = env.parse_payload(DocumentAcquisition)  # type: ignore[attr-defined]
                if (
                    acq.paper_identity_id == paper_identity_id
                    and acq.sha256 == sha
                    and acq.status == AcquisitionStatus.imported
                ):
                    if acq.blob and await self._blobs.exists(acq.blob):
                        return env.artifact_id
            except Exception:
                continue

        blob_ref = await self._blobs.put_bytes(data, media_type="application/pdf")
        acq = DocumentAcquisition(
            paper_identity_id=paper_identity_id,
            document_location_id=None,
            status=AcquisitionStatus.imported,
            attempted_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            blob=blob_ref,
            sha256=sha,
            size_bytes=len(data),
            media_type="application/pdf",
            source_type="user_provided",
            metadata={"imported_file": p.name},
        )
        env = ArtifactEnvelope.create(
            payload=acq,
            artifact_type="document_acquisition",
            producer="documents.acquisition_orchestrator.import",
        )
        await self._store.put(env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=paper_identity_id,
                target_artifact_id=env.artifact_id,
                producer="documents.acquisition_orchestrator",
            )
        )
        # Optionally run extraction immediately?
        # For now, also extract via extractor if available
        if self._extractor is not None:
            try:
                await self._extractor.extract(env.artifact_id)
            except Exception as e:
                logger.warning("extraction after import failed for %s: %s", env.artifact_id, e)

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="document.acquisition.completed",
                        source="documents.acquisition_orchestrator.import",
                        payload={
                            "acquisition_id": env.artifact_id,
                            "paper_identity_id": paper_identity_id,
                            "imported": True,
                        },
                    )
                )
            except Exception:
                pass

        return env.artifact_id


class DocumentAcquisitionOrchestratorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="documents.acquisition_orchestrator",
            version="0.1.0",
            plugin_type="document_acquisition",
            description="Document acquisition orchestrator (ScreenedLiteratureSet -> Corpus)",
            provides=["document_acquisition_orchestrator.default"],
            requires=["artifact_store.default", "blob_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        docs_cfg: dict[str, Any] = {}
        if "documents" in cfg and isinstance(cfg["documents"], dict):
            docs_cfg = cfg["documents"]  # type: ignore[assignment]
        acq_cfg = (
            docs_cfg.get("acquisition", {}) if isinstance(docs_cfg.get("acquisition"), dict) else {}
        )
        max_locs = int(acq_cfg.get("max_locations_per_paper", 5))

        store = ctx.require("artifact_store.default")
        blobs = ctx.require("blob_store.default")
        # Optional locators and fetcher/extractor (try_get)
        fetcher = ctx.try_get("document_fetcher.default")
        extractor = ctx.try_get("document_extractor.pypdf")
        # Try both metadata and unpaywall
        meta_loc = ctx.try_get("document_locator.metadata")
        unpaywall_loc = ctx.try_get("document_locator.unpaywall")
        # If not available via service registry, they may be missing in minimal config (tests)
        # For tests, we allow None and create dummy

        svc = DocumentAcquisitionOrchestratorService(
            artifact_store=store,
            blob_store=blobs,
            fetcher=fetcher,
            extractor=extractor,
            metadata_locator=meta_loc,
            unpaywall_locator=unpaywall_loc,
            events=ctx.events,
            max_locations_per_paper=max_locs,
        )
        ctx.register("document_acquisition_orchestrator.default", svc)
