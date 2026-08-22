"""Phase 2F evidence orchestrator — FullTextCorpus → EvidenceItems → PaperResearchProfiles → EvidenceCorpus.

Incremental persistence: one failed chunk never discards completed evidence.
Budgets enforced: max_chunks_per_document, max_model_calls.
Conservative dedup: normalized statement collapse within a paper (no embeddings).
Idempotent rerun: reuses existing EvidenceExtractionExecution for the same corpus.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.evidence import EvidenceCategory, EvidenceItem, Locator
from research_harness.research.schemas.evidence_extraction import (
    EvidenceCorpus,
    EvidenceExtractionExecution,
)
from research_harness.research.schemas.full_text import FullTextDocument, TextStatus
from research_harness.research.schemas.research_profile import PaperResearchProfile, ProfileClaim

logger = logging.getLogger(__name__)

_PROFILE_SECTION_BY_CATEGORY: dict[str, str] = {
    "research_question": "research_question",
    "theory": "theories",
    "construct": "constructs",
    "mechanism": "mechanisms",
    "assumption": "assumptions",
    "method": "methodology",
    "data": "data",
    "variable": "variables",
    "finding": "main_findings",
    "result": "results",
    "boundary_condition": "boundary_conditions",
    "limitation": "limitations",
    "future_research": "future_research",
}


def _normalize(statement: str) -> str:
    s = " ".join(statement.strip().lower().split())
    return s.rstrip(".,;:!?")


class EvidenceOrchestratorService:
    def __init__(
        self,
        artifact_store: Any,
        blob_store: Any,
        extractor: Any,
        model_role: str = "reasoning",
        pages_per_chunk: int = 4,
        max_chunks_per_document: int = 50,
        max_model_calls: int = 500,
        events: Any | None = None,
    ) -> None:
        self._store = artifact_store
        self._blobs = blob_store
        self._extractor = extractor
        self._model_role = model_role
        self._pages_per_chunk = pages_per_chunk
        self._max_chunks_per_document = max_chunks_per_document
        self._max_model_calls = max_model_calls
        self._events = events

    async def run(self, full_text_corpus_id: str) -> str:
        """Process a FullTextCorpus into evidence. Returns EvidenceExtractionExecution id.

        Idempotency: an existing execution for the same corpus is reused only when
        model_role and pages_per_chunk match (materially-changed model config must
        not silently reuse incompatible prior results).
        """
        existing = await self._store.list(artifact_type="evidence_extraction_execution")
        for env in existing:
            try:
                ex = EvidenceExtractionExecution.model_validate(env.payload)
                if (
                    ex.full_text_corpus_id == full_text_corpus_id
                    and ex.model_role == self._model_role
                    and ex.counts.get("pages_per_chunk") == self._pages_per_chunk
                ):
                    return env.artifact_id
            except Exception:
                continue

        corp_env = await self._store.get(full_text_corpus_id)
        from research_harness.research.schemas.full_text import FullTextCorpus

        corpus = corp_env.parse_payload(FullTextCorpus)
        started = datetime.now(UTC)
        exec_record = EvidenceExtractionExecution(
            full_text_corpus_id=full_text_corpus_id,
            documents_attempted=0,
            documents_completed=0,
            chunks_processed=0,
            chunks_failed=0,
            evidence_items_created=0,
            profiles_created=0,
            model_role=self._model_role,
            failures=[],
            counts={
                "documents_without_evidence": 0,
                "model_calls": 0,
                "pages_per_chunk": self._pages_per_chunk,
            },
            started_at=started,
        )

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="evidence.extraction.started",
                        source="literature.evidence_orchestrator",
                        payload={
                            "corpus_id": full_text_corpus_id,
                            "documents": len(corpus.available_document_ids),
                        },
                    )
                )
            except Exception:
                pass

        evidence_item_ids: list[str] = []
        profile_ids: list[str] = []
        without_evidence: list[str] = []
        failed_docs: list[str] = []
        total_model_calls = 0

        # Existing evidence for dedup (all evidence items in store, keyed by source doc)
        existing_evidence_by_doc: dict[str, dict[str, str]] = {}
        all_ev = await self._store.list(artifact_type="evidence_item")
        for ev_env in all_ev:
            try:
                ev = EvidenceItem.model_validate(ev_env.payload)
                existing_evidence_by_doc.setdefault(ev.source_artifact_id, {})[
                    _normalize(ev.statement)
                ] = ev_env.artifact_id
            except Exception:
                continue

        for doc_id in corpus.available_document_ids:
            exec_record.documents_attempted += 1
            try:
                doc_env = await self._store.get(doc_id)
                doc = doc_env.parse_payload(FullTextDocument)
            except Exception as e:
                exec_record.failures.append({"document_id": doc_id, "error": str(e)})
                failed_docs.append(doc_id)
                continue

            if doc.text_status != TextStatus.extracted or doc.text_blob is None:
                # Do not fabricate evidence from unavailable text
                exec_record.counts["documents_without_evidence"] += 1
                without_evidence.append(doc_id)
                continue

            try:
                blob_bytes = await self._blobs.get_bytes(doc.text_blob)
            except Exception as e:
                exec_record.failures.append({"document_id": doc_id, "error": f"blob read: {e}"})
                failed_docs.append(doc_id)
                continue

            import json

            try:
                j = json.loads(blob_bytes.decode("utf-8"))
                pages = j.get("pages", [])
            except Exception as e:
                exec_record.failures.append({"document_id": doc_id, "error": f"text parse: {e}"})
                failed_docs.append(doc_id)
                continue

            # Page-bounded chunking
            from research_harness.plugins.literature.evidence_extractor.plugin import chunk_pages

            chunks = chunk_pages(doc_id, pages, self._pages_per_chunk)
            # Enforce budget
            chunks = chunks[: self._max_chunks_per_document]

            doc_evidence_ids: list[str] = []
            for chunk in chunks:
                if total_model_calls >= self._max_model_calls:
                    exec_record.failures.append(
                        {"document_id": doc_id, "error": "max_model_calls budget reached"}
                    )
                    break
                try:
                    candidates = await self._extractor.extract_chunk(chunk)
                    total_model_calls += 1
                    exec_record.chunks_processed += 1
                except Exception as e:
                    logger.warning("chunk %s failed: %s", chunk.chunk_index, e)
                    total_model_calls += 1
                    exec_record.chunks_failed += 1
                    exec_record.failures.append(
                        {"document_id": doc_id, "chunk_index": chunk.chunk_index, "error": str(e)}
                    )
                    continue

                # Persist evidence incrementally
                for cand in candidates:
                    norm = _normalize(cand.statement)
                    existing_map = existing_evidence_by_doc.get(doc_id, {})
                    if norm in existing_map:
                        eid = existing_map[norm]
                    else:
                        eid = await self._persist_evidence(doc, doc_id, cand, chunk)
                        existing_evidence_by_doc.setdefault(doc_id, {})[norm] = eid
                    if eid not in doc_evidence_ids:
                        doc_evidence_ids.append(eid)

            if doc_evidence_ids:
                exec_record.evidence_items_created += len(doc_evidence_ids)
                evidence_item_ids.extend(doc_evidence_ids)
                # Build profile deterministically from this document's evidence
                profile_id = await self._build_profile(doc, doc_id, doc_evidence_ids)
                profile_ids.append(profile_id)
                exec_record.profiles_created += 1
                exec_record.documents_completed += 1
            else:
                without_evidence.append(doc_id)
                exec_record.counts["documents_without_evidence"] += 1

        exec_record.counts["model_calls"] = total_model_calls
        exec_record.completed_at = datetime.now(UTC)

        exec_env = ArtifactEnvelope.create(
            payload=exec_record,
            artifact_type="evidence_extraction_execution",
            producer="literature.evidence_orchestrator",
        )
        await self._store.put(exec_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=full_text_corpus_id,
                target_artifact_id=exec_env.artifact_id,
                producer="literature.evidence_orchestrator",
            )
        )

        # EvidenceCorpus
        evidence_corpus = EvidenceCorpus(
            evidence_extraction_execution_id=exec_env.artifact_id,
            full_text_corpus_id=full_text_corpus_id,
            paper_profile_ids=profile_ids,
            evidence_item_ids=evidence_item_ids,
            documents_without_evidence=without_evidence,
            failed_document_ids=failed_docs,
            metadata={"counts": exec_record.counts},
        )
        corp_env_out = ArtifactEnvelope.create(
            payload=evidence_corpus,
            artifact_type="evidence_corpus",
            producer="literature.evidence_orchestrator",
        )
        await self._store.put(corp_env_out)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=exec_env.artifact_id,
                target_artifact_id=corp_env_out.artifact_id,
                producer="literature.evidence_orchestrator",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=full_text_corpus_id,
                target_artifact_id=corp_env_out.artifact_id,
                producer="literature.evidence_orchestrator",
            )
        )

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="evidence.corpus.completed",
                        source="literature.evidence_orchestrator",
                        payload={
                            "corpus_id": corp_env_out.artifact_id,
                            "execution_id": exec_env.artifact_id,
                            "evidence_items": len(evidence_item_ids),
                            "profiles": len(profile_ids),
                        },
                    )
                )
            except Exception:
                pass

        return exec_env.artifact_id

    async def _persist_evidence(
        self, doc: FullTextDocument, doc_id: str, cand: Any, chunk: Any
    ) -> str:
        locator = Locator(page=cand.page_numbers[0], pages=cand.page_numbers)
        item = EvidenceItem(
            statement=cand.statement,
            source_artifact_id=doc_id,
            category=EvidenceCategory(cand.category),
            locator=locator,
            extraction_method="model-assisted",
            confidence=cand.confidence,
            metadata={
                "excerpt": cand.excerpt,
                "chunk_index": chunk.chunk_index,
                "start_page": chunk.start_page,
                "end_page": chunk.end_page,
                "extractor": "literature.evidence_extractor",
                "model_role": self._model_role,
            },
        )
        env = ArtifactEnvelope.create(
            payload=item,
            artifact_type="evidence_item",
            producer=f"literature.evidence_extractor:{self._model_role}",
        )
        await self._store.put(env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=doc_id,
                target_artifact_id=env.artifact_id,
                producer="literature.evidence_orchestrator",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=doc.paper_identity_id,
                target_artifact_id=env.artifact_id,
                producer="literature.evidence_orchestrator",
            )
        )
        return env.artifact_id

    async def _build_profile(
        self, doc: FullTextDocument, doc_id: str, doc_evidence_ids: list[str]
    ) -> str:
        # Deterministic aggregation: group evidence by category into profile sections
        sections: dict[str, list[ProfileClaim]] = {
            v: [] for v in _PROFILE_SECTION_BY_CATEGORY.values()
        }
        # 'sample' has no direct category; keep section available and empty
        sections["sample"] = []
        sections["research_context"] = []
        for eid in doc_evidence_ids:
            ev_env = await self._store.get(eid)
            ev = EvidenceItem.model_validate(ev_env.payload)
            section = _PROFILE_SECTION_BY_CATEGORY.get(
                ev.category.value if ev.category else "", "research_context"
            )
            sections.setdefault(section, []).append(
                ProfileClaim(
                    text=ev.statement,
                    evidence_item_ids=[eid],
                    inference=False,
                    category=ev.category.value if ev.category else None,
                )
            )

        profile = PaperResearchProfile(
            paper_identity_id=doc.paper_identity_id,
            full_text_document_id=doc_id,
            research_question=sections["research_question"],
            research_context=sections["research_context"],
            theories=sections["theories"],
            constructs=sections["constructs"],
            mechanisms=sections["mechanisms"],
            assumptions=sections["assumptions"],
            methodology=sections["methodology"],
            data=sections["data"],
            sample=sections["sample"],
            variables=sections["variables"],
            main_findings=sections["main_findings"],
            results=sections["results"],
            boundary_conditions=sections["boundary_conditions"],
            limitations=sections["limitations"],
            future_research=sections["future_research"],
            evidence_item_ids=list(doc_evidence_ids),
            model_role=self._model_role,
            extraction_method="model-assisted",
            metadata={},
        )
        env = ArtifactEnvelope.create(
            payload=profile,
            artifact_type="paper_research_profile",
            producer="literature.evidence_orchestrator",
        )
        await self._store.put(env)
        for eid in doc_evidence_ids:
            try:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=eid,
                        target_artifact_id=env.artifact_id,
                        producer="literature.evidence_orchestrator",
                    )
                )
            except Exception:
                pass
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=doc_id,
                target_artifact_id=env.artifact_id,
                producer="literature.evidence_orchestrator",
            )
        )
        return env.artifact_id


class EvidenceOrchestratorPlugin(Plugin):
    def __init__(self, model_role: str | None = None) -> None:
        self._model_role_override = model_role
        self._service: EvidenceOrchestratorService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="literature.evidence_orchestrator",
            version="0.1.0",
            plugin_type="literature",
            description="Evidence extraction orchestrator (FullTextCorpus -> EvidenceCorpus)",
            provides=["evidence_orchestrator.default"],
            requires=[
                "evidence_extractor.default",
                "artifact_store.default",
                "blob_store.default",
            ],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        lit_cfg: dict[str, Any] = {}
        if "literature" in cfg and isinstance(cfg["literature"], dict):
            lit_cfg = (
                cfg["literature"].get("evidence", {})
                if isinstance(cfg["literature"].get("evidence"), dict)
                else {}
            )
        model_role = self._model_role_override or lit_cfg.get("model_role") or "reasoning"
        pages_per_chunk = int(lit_cfg.get("pages_per_chunk", 4))
        max_chunks_per_document = int(lit_cfg.get("max_chunks_per_document", 50))
        max_model_calls = int(lit_cfg.get("max_model_calls", 500))
        store = ctx.require("artifact_store.default")
        blobs = ctx.require("blob_store.default")
        extractor = ctx.require("evidence_extractor.default")
        self._service = EvidenceOrchestratorService(
            artifact_store=store,
            blob_store=blobs,
            extractor=extractor,
            model_role=str(model_role),
            pages_per_chunk=pages_per_chunk,
            max_chunks_per_document=max_chunks_per_document,
            max_model_calls=max_model_calls,
            events=ctx.events,
        )
        ctx.register("evidence_orchestrator.default", self._service)
