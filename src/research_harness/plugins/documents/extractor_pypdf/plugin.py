"""pypdf extractor — page-level text extraction."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.blob import BlobReference
from research_harness.research.schemas.full_text import FullTextDocument, TextStatus

logger = logging.getLogger(__name__)

# Threshold for insufficient_text — documented deterministic
INSUFFICIENT_CHAR_THRESHOLD = 200
INSUFFICIENT_PAGE_RATIO = 0.5


def _pages_to_blob(pages: list[dict[str, Any]]) -> tuple[bytes, str]:
    # Deterministic JSON: schema_version 1, pages sorted by page, sort_keys True
    data = {"schema_version": 1, "pages": sorted(pages, key=lambda p: p["page"])}
    json_bytes = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return json_bytes, hashlib.sha256(json_bytes).hexdigest()


class PypdfExtractorService:
    def __init__(self, artifact_store: Any, blob_store: Any, events: Any | None = None) -> None:
        self._store = artifact_store
        self._blobs = blob_store
        self._events = events
        self._version = "0.1.0"

    @property
    def extractor_id(self) -> str:
        return "documents.extractor.pypdf"

    @property
    def extractor_version(self) -> str:
        return self._version

    async def extract(self, acquisition_id: str) -> str:
        # Load acquisition
        acq_env = await self._store.get(acquisition_id)
        from research_harness.research.schemas.document_acquisition import DocumentAcquisition

        if isinstance(acq_env.payload, dict):
            acq = DocumentAcquisition.model_validate(acq_env.payload)
        else:
            acq = acq_env.parse_payload(DocumentAcquisition)  # type: ignore[attr-defined]

        if acq.blob is None or acq.status not in ("downloaded", "imported"):
            # No blob to extract
            # Create failed FullTextDocument with extraction_failed?
            # For acquisition that is not downloaded, we should not extract; but if called, create failure doc
            # However orchestrator only calls extract when status is downloaded/imported
            raise ValueError(
                f"acquisition {acquisition_id!r} has no blob to extract (status {acq.status.value})"
            )

        # Idempotency: same blob + same extractor version -> reuse
        existing = await self._store.list(artifact_type="full_text_document")
        for env in existing:
            try:
                if isinstance(env.payload, dict):
                    doc = FullTextDocument.model_validate(env.payload)
                else:
                    doc = env.parse_payload(FullTextDocument)  # type: ignore[attr-defined]
                if (
                    doc.document_acquisition_id == acquisition_id
                    and doc.extractor == self.extractor_id
                    and doc.extractor_version == self._version
                    and doc.source_blob.digest == acq.blob.digest
                ):
                    # Reuse if blob exists
                    if doc.text_blob and await self._blobs.exists(doc.text_blob):
                        return env.artifact_id
                    if doc.text_status in (TextStatus.encrypted, TextStatus.extraction_failed):
                        # For encrypted/failed, also reuse as they are deterministic per blob
                        return env.artifact_id
            except Exception:
                continue

        # Emit started
        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="document.extraction.started",
                        source="documents.extractor.pypdf",
                        payload={
                            "acquisition_id": acquisition_id,
                            "paper_identity_id": acq.paper_identity_id,
                        },
                    )
                )
            except Exception:
                pass

        # Load PDF bytes via blob store
        try:
            pdf_bytes = await self._blobs.get_bytes(acq.blob)
        except Exception as e:
            # Failed to get blob
            return await self._create_failed_document(
                acq, acquisition_id, TextStatus.extraction_failed, f"blob get failed: {e}"
            )

        # Try extraction via pypdf
        try:
            from io import BytesIO

            import pypdf

            reader = pypdf.PdfReader(BytesIO(pdf_bytes))
        except Exception as e:
            # Malformed PDF?
            if "encrypted" in str(e).lower():
                return await self._create_document_for_encrypted(acq, acquisition_id)
            return await self._create_failed_document(
                acq, acquisition_id, TextStatus.extraction_failed, f"pdf reader failed: {e}"
            )

        # Check encrypted
        try:
            if reader.is_encrypted:
                # Try to decrypt with empty password? Some PDFs are encrypted but empty password works
                try:
                    reader.decrypt("")
                except Exception:
                    pass
                if reader.is_encrypted:
                    return await self._create_document_for_encrypted(acq, acquisition_id)
        except Exception:
            # If we cannot check is_encrypted, treat as failed
            pass

        pages: list[dict[str, Any]] = []
        page_count = len(reader.pages)
        pages_with_text = 0
        char_count = 0

        for idx, page in enumerate(reader.pages):
            page_num = idx + 1  # 1-based human-facing
            try:
                text = page.extract_text() or ""
            except Exception as e:
                logger.warning("pypdf extract_text failed for page %s: %s", page_num, e)
                text = ""
            # Normalize text: keep as is, strip trailing whitespace? Preserve exactly but trim?
            # Use text as extracted
            if text.strip():
                pages_with_text += 1
            char_count += len(text)
            pages.append({"page": page_num, "text": text})

        # Quality metrics
        pages_without_text = page_count - pages_with_text
        avg_chars = char_count / page_count if page_count else 0
        empty_ratio = pages_without_text / page_count if page_count else 1.0

        quality_metrics = {
            "page_count": page_count,
            "pages_with_text": pages_with_text,
            "pages_without_text": pages_without_text,
            "character_count": char_count,
            "average_characters_per_page": avg_chars,
            "empty_page_ratio": empty_ratio,
        }

        # Determine text_status
        if page_count == 0:
            text_status = TextStatus.extraction_failed
        elif char_count < INSUFFICIENT_CHAR_THRESHOLD or empty_ratio > INSUFFICIENT_PAGE_RATIO:
            # If we have some text but very little, mark insufficient
            # Check if at least one page has substantial text? Use threshold
            text_status = TextStatus.insufficient_text
        else:
            text_status = TextStatus.extracted

        # For encrypted already handled, for extraction_failed handled above
        # If insufficient_text, we still store text blob (with whatever we have)

        # Serialize pages to blob
        json_bytes, _ = _pages_to_blob(pages)
        # Determine if we need to store blob: if insufficient_text but all pages empty, still store empty pages?
        # Store blob for extracted and insufficient_text
        text_blob_ref: BlobReference | None = None
        if text_status in (TextStatus.extracted, TextStatus.insufficient_text):
            # Store via blob store
            # Check if blob with same digest already exists (deduplication)
            text_blob_ref = await self._blobs.put_bytes(json_bytes, media_type="application/json")
        else:
            text_blob_ref = None

        doc = FullTextDocument(
            paper_identity_id=acq.paper_identity_id,
            document_acquisition_id=acquisition_id,
            source_blob=acq.blob,
            text_blob=text_blob_ref,
            extractor=self.extractor_id,
            extractor_version=self._version,
            page_count=page_count,
            pages_with_text=pages_with_text,
            character_count=char_count,
            text_status=text_status,
            language=None,
            quality_metrics=quality_metrics,
            metadata={},
        )
        env = ArtifactEnvelope.create(
            payload=doc, artifact_type="full_text_document", producer="documents.extractor.pypdf"
        )
        await self._store.put(env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=acquisition_id,
                target_artifact_id=env.artifact_id,
                producer="documents.extractor.pypdf",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=acq.paper_identity_id,
                target_artifact_id=env.artifact_id,
                producer="documents.extractor.pypdf",
            )
        )

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="document.extraction.completed",
                        source="documents.extractor.pypdf",
                        payload={
                            "document_id": env.artifact_id,
                            "acquisition_id": acquisition_id,
                            "text_status": text_status.value,
                            "page_count": page_count,
                        },
                    )
                )
            except Exception:
                pass

        return env.artifact_id

    async def _create_document_for_encrypted(self, acq: Any, acquisition_id: str) -> str:
        doc = FullTextDocument(
            paper_identity_id=acq.paper_identity_id,
            document_acquisition_id=acquisition_id,
            source_blob=acq.blob,  # type: ignore[arg-type]
            text_blob=None,
            extractor=self.extractor_id,
            extractor_version=self._version,
            page_count=0,
            pages_with_text=0,
            character_count=0,
            text_status=TextStatus.encrypted,
            quality_metrics={
                "page_count": 0,
                "pages_with_text": 0,
                "pages_without_text": 0,
                "character_count": 0,
            },
            metadata={"failure": "encrypted"},
        )
        env = ArtifactEnvelope.create(
            payload=doc, artifact_type="full_text_document", producer="documents.extractor.pypdf"
        )
        await self._store.put(env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=acquisition_id,
                target_artifact_id=env.artifact_id,
                producer="documents.extractor.pypdf",
            )
        )
        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="document.extraction.failed",
                        source="documents.extractor.pypdf",
                        payload={"acquisition_id": acquisition_id, "reason": "encrypted"},
                    )
                )
            except Exception:
                pass
        return env.artifact_id

    async def _create_failed_document(
        self, acq: Any, acquisition_id: str, status: TextStatus, reason: str
    ) -> str:
        doc = FullTextDocument(
            paper_identity_id=acq.paper_identity_id,
            document_acquisition_id=acquisition_id,
            source_blob=acq.blob,  # type: ignore[arg-type]
            text_blob=None,
            extractor=self.extractor_id,
            extractor_version=self._version,
            page_count=0,
            pages_with_text=0,
            character_count=0,
            text_status=status,
            quality_metrics={"error": reason},
            metadata={"failure": reason},
        )
        env = ArtifactEnvelope.create(
            payload=doc, artifact_type="full_text_document", producer="documents.extractor.pypdf"
        )
        await self._store.put(env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=acquisition_id,
                target_artifact_id=env.artifact_id,
                producer="documents.extractor.pypdf",
            )
        )
        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="document.extraction.failed",
                        source="documents.extractor.pypdf",
                        payload={"acquisition_id": acquisition_id, "reason": reason},
                    )
                )
            except Exception:
                pass
        return env.artifact_id


class PypdfExtractorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="documents.extractor.pypdf",
            version="0.1.0",
            plugin_type="document_extractor",
            description="pypdf page-level text extractor",
            provides=["document_extractor.pypdf"],
            requires=["artifact_store.default", "blob_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        store = ctx.require("artifact_store.default")
        blobs = ctx.require("blob_store.default")
        svc = PypdfExtractorService(artifact_store=store, blob_store=blobs, events=ctx.events)
        ctx.register("document_extractor.pypdf", svc)
