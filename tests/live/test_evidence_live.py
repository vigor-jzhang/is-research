"""Live evidence smoke test — opt-in (live_evidence).

Two modes:
1. EVIDENCE_LIVE_CORPUS_ID set -> use that FullTextCorpus.
2. Not set -> acquire ONE small OA paper (Unpaywall + fetch + extract),
   build a tiny FullTextCorpus, run evidence extraction on 1-2 chunks.

Requires OPENROUTER_API_KEY and UNPAYWALL_EMAIL. Skips cleanly otherwise.
Structural assertions only; no research-accuracy claims.
"""

import os
import pathlib

import pytest

pytestmark = pytest.mark.live_evidence


@pytest.mark.asyncio
async def test_live_evidence_smoke(tmp_path: pathlib.Path):
    if not os.getenv("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")

    cfg_path = pathlib.Path("configs/example.yaml")
    if not cfg_path.exists():
        pytest.skip("configs/example.yaml not present")

    from research_harness.app.bootstrap import build_runtime
    from research_harness.config.loader import load_config

    cfg = load_config(str(cfg_path))
    runtime = build_runtime(cfg)
    async with runtime:
        store = runtime.services.require("artifact_store.default")

        corpus_id = os.getenv("EVIDENCE_LIVE_CORPUS_ID")
        acquired_doc_ids: list[str] = []
        if not corpus_id:
            corpus_id, acquired_doc_ids = await _acquire_small_corpus(runtime, store, tmp_path)

        # Resolve the corpus documents (for scoped assertions)
        from research_harness.research.schemas.full_text import FullTextCorpus

        corpus_env = await store.get(corpus_id)
        corpus = corpus_env.parse_payload(FullTextCorpus)
        doc_ids = list(corpus.available_document_ids) or acquired_doc_ids

        # Reuse existing execution if model role matches (idempotent)
        from research_harness.research.schemas.evidence_extraction import (
            EvidenceExtractionExecution,
        )

        existing = await store.list(artifact_type="evidence_extraction_execution")
        role = "reasoning"
        for env in existing:
            rec = env.parse_payload(EvidenceExtractionExecution)
            if rec.full_text_corpus_id == corpus_id and rec.model_role == role:
                if rec.evidence_items_created > 0:
                    await _assert_structural(store, doc_ids)
                    pytest.skip("already extracted; structural assertions passed")

        # Bounded: 1 document, 1-2 chunks
        from research_harness.plugins.literature.evidence_extractor.plugin import (
            EvidenceExtractorService,
        )
        from research_harness.plugins.literature.evidence_orchestrator.plugin import (
            EvidenceOrchestratorService,
        )

        blobs = runtime.services.require("blob_store.default")
        router = runtime.services.require("model_router.default")
        extractor = EvidenceExtractorService(
            model_router=router, artifact_store=store, blob_store=blobs, model_role="reasoning"
        )
        orch = EvidenceOrchestratorService(
            artifact_store=store,
            blob_store=blobs,
            extractor=extractor,
            model_role="reasoning",
            pages_per_chunk=4,
            max_chunks_per_document=2,
            max_model_calls=4,
        )
        exec_id = await orch.run(corpus_id)
        rec = (await store.get(exec_id)).parse_payload(EvidenceExtractionExecution)
        assert rec.chunks_processed >= 1

        await _assert_structural(store, doc_ids)
        print(
            f"live evidence: docs completed {rec.documents_completed} "
            f"evidence {rec.evidence_items_created} profiles {rec.profiles_created} "
            f"chunks {rec.chunks_processed}"
        )


async def _assert_structural(store, doc_ids: list[str]):
    from research_harness.research.schemas.evidence import EvidenceItem
    from research_harness.research.schemas.research_profile import PaperResearchProfile

    items = list(await store.list(artifact_type="evidence_item"))
    scoped = [env for env in items if env.parse_payload(EvidenceItem).source_artifact_id in doc_ids]
    assert len(scoped) >= 1, "no evidence created for the acquired document"
    for env in scoped:
        ev = env.parse_payload(EvidenceItem)
        assert ev.locator is not None and ev.locator.pages
        assert all(p >= 1 for p in ev.locator.pages)
        assert ev.source_artifact_id in doc_ids
        parents = await store.get_parents(env.artifact_id)
        assert any(p.relation.value == "derived_from" for p in parents)

    profiles = await store.list(artifact_type="paper_research_profile")
    assert len(profiles) >= 1
    prof = profiles[0].parse_payload(PaperResearchProfile)
    assert prof.evidence_item_ids
    assert prof.full_text_document_id

    corpora = await store.list(artifact_type="evidence_corpus")
    assert len(corpora) >= 1


async def _acquire_small_corpus(runtime, store, tmp_path):
    """Acquire one small OA paper and build a FullTextCorpus in the main store."""

    from research_harness.research.envelope import ArtifactEnvelope
    from research_harness.research.schemas.evidence_extraction import (
        EvidenceExtractionExecution,
    )
    from research_harness.research.schemas.full_text import (
        FullTextCorpus,
    )

    doi = os.getenv("FULLTEXT_SMOKE_DOI", "10.1371/journal.pone.0151203")
    fallback_dois = ["10.1038/nature12373", "10.1371/journal.pone.0264300"]

    doc_id = None
    for candidate_doi in [doi, *fallback_dois]:
        doc_id = await _acquire_one(runtime, store, candidate_doi)
        if doc_id:
            break
    if not doc_id:
        pytest.skip("no OA paper could be acquired live")

    # 5. Build FullTextCorpus
    seed = EvidenceExtractionExecution(full_text_corpus_id="seed-live")
    s_env = ArtifactEnvelope.create(
        payload=seed, artifact_type="evidence_extraction_execution", producer="live_evidence"
    )
    await store.put(s_env)
    corpus = FullTextCorpus(
        document_acquisition_execution_id=s_env.artifact_id,
        screened_literature_set_id="set-live",
        available_document_ids=[doc_id],
        unavailable_identity_ids=[],
        restricted_identity_ids=[],
        failed_identity_ids=[],
    )
    c_env = ArtifactEnvelope.create(
        payload=corpus, artifact_type="full_text_corpus", producer="live_evidence"
    )
    await store.put(c_env)
    return c_env.artifact_id, [doc_id]


async def _acquire_one(runtime, store, doi: str):
    """Acquire one paper end-to-end; return FullTextDocument id or None."""
    from research_harness.research.envelope import ArtifactEnvelope
    from research_harness.research.schemas.common import ExternalIdentifier
    from research_harness.research.schemas.document_acquisition import DocumentAcquisition
    from research_harness.research.schemas.full_text import FullTextDocument
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.paper import PaperRecord

    paper = PaperRecord(title="Live evidence paper", doi=doi)
    p_env = ArtifactEnvelope.create(
        payload=paper, artifact_type="paper_record", producer="live_evidence"
    )
    await store.put(p_env)
    ident = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[ExternalIdentifier(scheme="doi", value=doi)],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    i_env = ArtifactEnvelope.create(
        payload=ident, artifact_type="paper_identity", producer="live_evidence"
    )
    await store.put(i_env)

    locator = runtime.services.get("document_locator.unpaywall")
    if locator is None:
        return None
    loc_ids = await locator.resolve(i_env.artifact_id)
    if not loc_ids:
        return None

    fetcher = runtime.services.get("document_fetcher.default")
    if fetcher is None:
        return None
    acq_id = await fetcher.fetch(loc_ids[0])
    acq = (await store.get(acq_id)).parse_payload(DocumentAcquisition)
    if acq.status.value != "downloaded":
        return None

    extractor = runtime.services.get("document_extractor.pypdf")
    if extractor is None:
        return None
    doc_id = await extractor.extract(acq_id)
    doc = (await store.get(doc_id)).parse_payload(FullTextDocument)
    if doc.text_status.value != "extracted":
        return None
    return doc_id
