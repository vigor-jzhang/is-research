"""Phase 5C unit tests — evidence enrichment for sparse novelty candidates.

Fake provider `get` + fake document pipeline (locator/fetcher/extractor), no
network, no paid models.
"""

from __future__ import annotations

import json
import pathlib
from datetime import date

import pytest

from research_harness.contracts.literature import (
    LiteratureNotFoundError,
    LiteratureRateLimitError,
    LiteratureSearchHit,
    LiteratureSearchPage,
)
from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.literature.identity_resolver.plugin import (
    PaperIdentityResolverService,
)
from research_harness.plugins.literature.ingestion.plugin import LiteratureIngestor
from research_harness.plugins.research.novelty_validator.plugin import NoveltyValidationService
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.plugins.storage.blobs_filesystem.plugin import FilesystemBlobStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.document_acquisition import (
    AcquisitionStatus,
    DocumentAcquisition,
)
from research_harness.research.schemas.document_location import DocumentLocation
from research_harness.research.schemas.evidence import EvidenceItem
from research_harness.research.schemas.full_text import FullTextDocument, TextStatus
from research_harness.research.schemas.novelty import (
    CandidateRelationship,
    EnrichmentAttemptStatus,
    EnrichmentOutcome,
    EvidenceBasis,
    EvidenceEnrichmentAttempt,
    EvidenceEnrichmentExecution,
    NoveltyCandidateAssessment,
    NoveltyClaim,
    NoveltyClaimAssessment,
    NoveltyClaimStatus,
    NoveltyClaimType,
    NoveltyReportStatus,
    NoveltyValidationReport,
    ReadinessStatus,
    SubmissionReadinessGate,
)
from research_harness.research.schemas.paper import Author, ExternalIdentifier, PaperRecord
from research_harness.research.schemas.publication import (
    FormattedManuscript,
    FormattedManuscriptStatus,
    FormattedSection,
    FrontMatter,
    SubmissionPackage,
    SubmissionPackageStatus,
)

CLAIM = "We are the first study to model demand-driven platform dynamics."


class FlipRouter:
    """Routes by marker; responses per marker are consumed in order."""

    def __init__(self, markers: dict[str, list[dict]]):
        self.markers = markers
        self.calls: dict[str, int] = {}

    async def complete(self, role, request):
        text = " ".join(m.content or "" for m in request.messages)
        for marker, queue in self.markers.items():
            if marker in text:
                idx = self.calls.get(marker, 0)
                self.calls[marker] = idx + 1
                resp = queue[min(idx, len(queue) - 1)]
                return ModelResponse(
                    message=Message(role="assistant", content=json.dumps(resp)),
                    tool_calls=[],
                    finish_reason="stop",
                    model="fake",
                )
        raise AssertionError(f"no builder for prompt: {text[:200]}")


class FakeSource:
    """Literature source with search + get; get failures configurable."""

    def __init__(
        self,
        provider_name: str,
        papers: list[PaperRecord] | None = None,
        get_hits: dict[str, PaperRecord] | None = None,
        get_fail: dict[str, Exception] | None = None,
    ):
        self.provider_name = provider_name
        self.papers = papers or []
        self.get_hits = get_hits or {}
        self.get_fail = get_fail or {}
        self.get_calls: list[str] = []

    async def search(self, request):
        return LiteratureSearchPage(
            provider=self.provider_name,
            hits=[
                LiteratureSearchHit(
                    paper=p,
                    raw_payload={"title": p.title},
                    provider=self.provider_name,
                    provider_record_id=p.doi or p.title,
                )
                for p in self.papers
            ],
            total_estimate=len(self.papers),
        )

    async def get(self, identifier: str):
        self.get_calls.append(identifier)
        if identifier in self.get_fail:
            raise self.get_fail[identifier]
        if identifier in self.get_hits:
            hit = self.get_hits[identifier]
            return LiteratureSearchHit(
                paper=hit,
                raw_payload={"title": hit.title},
                provider=self.provider_name,
                provider_record_id=hit.doi or identifier,
            )
        raise LiteratureNotFoundError(f"no record for {identifier}")


class FakeLocator:
    def __init__(self, store: SQLiteArtifactStore, locations: list[DocumentLocation] | None = None):
        self.store = store
        self.locations = locations or []

    async def resolve(self, paper_identity_id: str) -> list[str]:
        ids: list[str] = []
        for loc in self.locations:
            if loc.paper_identity_id == paper_identity_id:
                env = ArtifactEnvelope.create(
                    payload=loc, artifact_type="document_location", producer="test"
                )
                await self.store.put(env)
                ids.append(env.artifact_id)
        return ids


class FakeFetcher:
    def __init__(self, store: SQLiteArtifactStore, blobs: FilesystemBlobStore):
        self.store = store
        self.blobs = blobs
        self.calls = 0

    async def fetch(self, document_location_id: str) -> str:
        self.calls += 1
        loc = (await self.store.get(document_location_id)).parse_payload(DocumentLocation)
        blob = await self.blobs.put_bytes(b"%PDF-fake", media_type="application/pdf")
        acq = DocumentAcquisition(
            paper_identity_id=loc.paper_identity_id,
            document_location_id=document_location_id,
            status=AcquisitionStatus.downloaded,
            blob=blob,
            sha256=blob.digest,
            size_bytes=8,
            media_type="application/pdf",
            source_type="http",
        )
        env = ArtifactEnvelope.create(
            payload=acq, artifact_type="document_acquisition", producer="test"
        )
        await self.store.put(env)
        return env.artifact_id


class FakeExtractor:
    def __init__(self, store: SQLiteArtifactStore, blobs: FilesystemBlobStore, pages: list[str]):
        self.store = store
        self.blobs = blobs
        self.pages = pages
        self.calls = 0

    async def extract(self, acquisition_id: str) -> str:
        self.calls += 1
        acq = (await self.store.get(acquisition_id)).parse_payload(DocumentAcquisition)
        text_blob = await self.blobs.put_bytes(
            json.dumps(
                {"pages": [{"page": i + 1, "text": t} for i, t in enumerate(self.pages)]}
            ).encode(),
            media_type="application/json",
        )
        doc = FullTextDocument(
            paper_identity_id=acq.paper_identity_id,
            document_acquisition_id=acquisition_id,
            source_blob=acq.blob,
            text_blob=text_blob,
            extractor="fake",
            extractor_version="1",
            page_count=len(self.pages),
            pages_with_text=len(self.pages),
            character_count=sum(len(t) for t in self.pages),
            text_status=TextStatus.extracted,
            metadata={},
        )
        env = ArtifactEnvelope.create(
            payload=doc, artifact_type="full_text_document", producer="test"
        )
        await self.store.put(env)
        return env.artifact_id


def _sparse_paper(title: str, doi: str, external_doi: bool = False) -> PaperRecord:
    if external_doi:
        return PaperRecord(
            title=title,
            authors=[Author(name="Smith, Jane")],
            year=None,
            venue=None,
            doi=None,
            external_identifiers=[ExternalIdentifier(scheme="doi", value=doi)],
        )
    return PaperRecord(
        title=title,
        authors=[Author(name="Smith, Jane")],
        year=2019,
        venue="Journal of Platform Studies",
        doi=doi,
        abstract=None,
    )


def _paper_with_abstract(title: str, doi: str, abstract: str) -> PaperRecord:
    return PaperRecord(
        title=title,
        authors=[Author(name="Smith, Jane")],
        year=2019,
        venue="Journal of Platform Studies",
        doi=doi,
        abstract=abstract,
    )


async def _manuscript_env(store: SQLiteArtifactStore) -> tuple[str, str]:
    fm = FormattedManuscript(
        draft_id="d1",
        results_package_id="pkg1",
        profile_id="prof1",
        profile_name="Generic",
        citation_style="author_year",
        front_matter=FrontMatter(title="T", generated_by="deterministic"),
        sections=[
            FormattedSection(
                section_id="introduction", title="Introduction", body=CLAIM, word_count=5
            )
        ],
        anonymous_review=False,
        total_word_count=5,
        validation_status=FormattedManuscriptStatus.validated,
        model_role="test",
    )
    m_env = ArtifactEnvelope.create(
        payload=fm, artifact_type="formatted_manuscript", producer="test"
    )
    await store.put(m_env)
    pkg = SubmissionPackage(
        formatted_manuscript_id=m_env.artifact_id,
        draft_id="d1",
        profile_id="prof1",
        status=SubmissionPackageStatus.ready,
        summary="ready",
        model_role="test",
    )
    p_env = ArtifactEnvelope.create(
        payload=pkg, artifact_type="submission_package", producer="test"
    )
    await store.put(p_env)
    return m_env.artifact_id, p_env.artifact_id


@pytest.fixture()
async def store(tmp_path: pathlib.Path):
    s = SQLiteArtifactStore(path=tmp_path / "art.db")
    yield s
    await s.close()


@pytest.fixture()
def blobs(tmp_path: pathlib.Path) -> FilesystemBlobStore:
    return FilesystemBlobStore(root=tmp_path / "blobs")


def _lookup(sources: dict[str, object]):
    def f(name: str):
        if name in sources:
            return sources[name]
        return sources[name.split(".")[-1]]

    return f


async def _setup_service(
    store: SQLiteArtifactStore,
    blobs: FilesystemBlobStore,
    router: FlipRouter,
    sources: dict[str, object],
    *,
    enrichment_enabled: bool = True,
    acquire_full_text: bool = True,
    providers: list[str] | None = None,
) -> NoveltyValidationService:
    return NoveltyValidationService(
        model_router=router,
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        service_lookup=_lookup(sources),
        blob_store=blobs,
        providers=providers or ["semantic_scholar"],
        enrichment_enabled=enrichment_enabled,
        acquire_full_text=acquire_full_text,
        max_enrichment_attempts=3,
    )


async def _run_candidate_pipeline(
    svc: NoveltyValidationService,
    store: SQLiteArtifactStore,
    claim: NoveltyClaim | None = None,
) -> tuple[str, str]:
    """Full per-claim pipeline: plan -> execute -> cset -> assessments.
    Returns (claim_id, assessment_id)."""
    claim = claim or NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text=CLAIM,
        claim_type=NoveltyClaimType.absolute_priority,
        risk="critical",
        extraction_method="deterministic",
        source_quote="We are the first study",
    )
    claim_id = await svc._put(claim, "novelty_claim")
    plan_id = await svc.plan_searches(claim_id, as_of=date(2026, 8, 23), offline=True)
    exec_id = await svc.execute_searches(plan_id)
    cset_id = await svc.build_candidate_set(claim_id, plan_id, exec_id)
    assessment_ids = await svc.assess_candidates(claim_id, cset_id, offline=False)
    return claim_id, assessment_ids[0]


async def _enrichment_execution(
    store: SQLiteArtifactStore, assessment_id: str
) -> EvidenceEnrichmentExecution:
    envs = await store.list(artifact_type="evidence_enrichment_execution")
    for env in envs:
        exec_ = env.parse_payload(EvidenceEnrichmentExecution)
        children = await store.get_children(env.artifact_id)
        if any(c.target_artifact_id == assessment_id for c in children):
            return exec_
    raise AssertionError("no enrichment execution linked to assessment")


# ---------------------------------------------------------------------------
# 1. sparse (title-only) candidate -> abstract acquired -> reassessed
# ---------------------------------------------------------------------------


async def test_title_only_abstract_acquired(store: SQLiteArtifactStore, blobs):
    paper = _sparse_paper("Mysterious Prior Work", "10.1000/xyz", external_doi=True)
    abstract_paper = _paper_with_abstract(
        "Mysterious Prior Work", "10.1000/xyz", "We model demand-driven platform dynamics."
    )
    source = FakeSource(
        "semantic_scholar", papers=[paper], get_hits={"10.1000/xyz": abstract_paper}
    )
    router = FlipRouter(
        {
            "candidate prior-art paper": [
                {
                    "dimensions": [{"dimension": "setting", "value": "match"}],
                    "relationship": "strong_overlap",
                    "assessment": "overlaps with the claim.",
                }
            ]
        }
    )
    svc = await _setup_service(
        store, blobs, router, {"semantic_scholar": source}, acquire_full_text=False
    )
    _claim_id, assessment_id = await _run_candidate_pipeline(svc, store)
    a = (await store.get(assessment_id)).parse_payload(NoveltyCandidateAssessment)
    assert a.evidence_basis == EvidenceBasis.abstract
    assert a.relationship == CandidateRelationship.strong_overlap

    execution = await _enrichment_execution(store, assessment_id)
    assert execution.before_evidence_basis == EvidenceBasis.title_only
    assert execution.after_evidence_basis == EvidenceBasis.abstract
    assert execution.outcome == EnrichmentOutcome.enriched
    assert execution.resulting_evidence_ids
    assert len(execution.attempt_ids) == 1
    attempt = (await store.get(execution.attempt_ids[0])).parse_payload(EvidenceEnrichmentAttempt)
    assert attempt.status == EnrichmentAttemptStatus.success
    assert attempt.retrieved_artifact_ids
    # abstract evidence item provenance
    item_id = attempt.retrieved_artifact_ids[1]
    item_parents = await store.get_parents(item_id)
    assert any(p.source_artifact_id == a.paper_identity_id for p in item_parents)


# ---------------------------------------------------------------------------
# 2. metadata-only candidate -> full text acquired -> stronger reassessment
# ---------------------------------------------------------------------------


async def test_metadata_only_full_text_acquired(store: SQLiteArtifactStore, blobs):
    paper = _sparse_paper("Metadata Only Paper", "10.1000/ft")  # year+venue+doi, no abstract
    identity_paper = _sparse_paper("Metadata Only Paper", "10.1000/ft")
    source = FakeSource("semantic_scholar", papers=[paper])
    locator = FakeLocator(
        store,
        [DocumentLocation(paper_identity_id="__id__", resolver="fake", url="https://x/paper.pdf")],
    )
    # FakeLocator matches by paper_identity_id which we cannot know upfront;
    # give it the identity after resolution via a mutable holder instead.
    holder: dict[str, str] = {}

    class DynamicLocator(FakeLocator):
        async def resolve(self, paper_identity_id: str) -> list[str]:
            holder["identity_id"] = paper_identity_id
            loc = DocumentLocation(
                paper_identity_id=paper_identity_id, resolver="fake", url="https://x/paper.pdf"
            )
            env = ArtifactEnvelope.create(
                payload=loc, artifact_type="document_location", producer="test"
            )
            await store.put(env)
            return [env.artifact_id]

    fetcher = FakeFetcher(store, blobs)
    extractor = FakeExtractor(
        store, blobs, ["Demand driven platform dynamics are modeled here. Platforms compete."]
    )
    router = FlipRouter(
        {
            "candidate prior-art paper": [
                {
                    "dimensions": [{"dimension": "mechanism", "value": "match"}],
                    "relationship": "strong_overlap",
                    "assessment": "same mechanism in full text.",
                }
            ]
        }
    )
    svc = await _setup_service(
        store,
        blobs,
        router,
        {
            "semantic_scholar": source,
            "document_locator.metadata": DynamicLocator(store),
            "document_fetcher.default": fetcher,
            "document_extractor.pypdf": extractor,
        },
    )
    _claim_id, assessment_id = await _run_candidate_pipeline(svc, store)
    a = (await store.get(assessment_id)).parse_payload(NoveltyCandidateAssessment)
    assert a.evidence_basis == EvidenceBasis.full_text
    assert a.relationship == CandidateRelationship.strong_overlap  # not downgraded

    execution = await _enrichment_execution(store, assessment_id)
    assert execution.before_evidence_basis == EvidenceBasis.indexed_metadata
    assert execution.after_evidence_basis == EvidenceBasis.full_text
    assert execution.outcome == EnrichmentOutcome.enriched
    assert fetcher.calls == 1 and extractor.calls == 1
    # deterministic relevance extraction produced EvidenceItems
    items = [
        env.parse_payload(EvidenceItem)
        for env in await store.list(artifact_type="evidence_item")
        if env.payload.get("metadata", {}).get("novelty_enrichment")
    ]
    assert items
    assert items[0].extraction_method == "deterministic"
    # candidate assessment -> full text document -> identity
    doc_ids = [
        eid
        for eid in a.evidence_artifact_ids
        if (await store.get(eid)).artifact_type == "full_text_document"
    ]
    assert doc_ids
    doc = (await store.get(doc_ids[0])).parse_payload(FullTextDocument)
    assert doc.paper_identity_id == a.paper_identity_id


# ---------------------------------------------------------------------------
# 3. existing adequate evidence -> zero acquisition calls
# ---------------------------------------------------------------------------


async def test_existing_evidence_no_acquisition(store: SQLiteArtifactStore, blobs):
    paper = _paper_with_abstract(
        "Prior Work with Abstract", "10.1000/ok", "An abstract is already present."
    )
    source = FakeSource("semantic_scholar", papers=[paper])
    router = FlipRouter(
        {
            "candidate prior-art paper": [
                {
                    "dimensions": [],
                    "relationship": "distinct",
                    "assessment": "distinct work.",
                }
            ]
        }
    )
    svc = await _setup_service(store, blobs, router, {"semantic_scholar": source})
    _claim_id, assessment_id = await _run_candidate_pipeline(svc, store)
    a = (await store.get(assessment_id)).parse_payload(NoveltyCandidateAssessment)
    assert a.evidence_basis == EvidenceBasis.abstract
    assert await store.list(artifact_type="evidence_enrichment_plan") == []
    assert source.get_calls == []
    assert await store.list(artifact_type="evidence_enrichment_attempt") == []


# ---------------------------------------------------------------------------
# 4. same paper used by two claims -> acquired once, evidence reused
# ---------------------------------------------------------------------------


async def test_same_paper_acquired_once(store: SQLiteArtifactStore, blobs):
    paper = _sparse_paper("Shared Sparse Paper", "10.1000/shared")
    abstract_paper = _paper_with_abstract(
        "Shared Sparse Paper", "10.1000/shared", "We model demand-driven platform dynamics."
    )
    source = FakeSource(
        "semantic_scholar", papers=[paper], get_hits={"10.1000/shared": abstract_paper}
    )
    router = FlipRouter(
        {
            "candidate prior-art paper": [
                {
                    "dimensions": [],
                    "relationship": "distinct",
                    "assessment": "distinct work.",
                }
            ]
        }
    )
    svc = await _setup_service(store, blobs, router, {"semantic_scholar": source})

    claim_a = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text=CLAIM,
        claim_type=NoveltyClaimType.absolute_priority,
        risk="critical",
        extraction_method="deterministic",
        source_quote="We are the first study",
    )
    claim_b = NoveltyClaim(
        manuscript_id="m1",
        section_id="conclusion",
        claim_text="To our knowledge, no prior research has examined this mechanism.",
        claim_type=NoveltyClaimType.scoped_priority,
        risk="high",
        extraction_method="deterministic",
        source_quote="To our knowledge",
    )
    a1 = await _run_candidate_pipeline(svc, store, claim_a)
    b1 = await _run_candidate_pipeline(svc, store, claim_b)
    a = (await store.get(a1[1])).parse_payload(NoveltyCandidateAssessment)
    b = (await store.get(b1[1])).parse_payload(NoveltyCandidateAssessment)
    assert a.evidence_basis == EvidenceBasis.abstract
    assert b.evidence_basis == EvidenceBasis.abstract  # reused, not re-acquired
    assert source.get_calls == ["10.1000/shared"]  # acquired exactly once


# ---------------------------------------------------------------------------
# 5. DOI acquisition failure -> fallback strategy attempted
# ---------------------------------------------------------------------------


async def test_doi_failure_fallback(store: SQLiteArtifactStore, blobs):
    paper = PaperRecord(
        title="Fallback Paper",
        authors=[Author(name="Smith, Jane")],
        year=2020,
        venue="J",
        doi="10.1000/fallback",
        external_identifiers=[ExternalIdentifier(scheme="semantic_scholar", value="SS:paper1")],
        abstract=None,
    )
    abstract_paper = _paper_with_abstract(
        "Fallback Paper", "10.1000/fallback", "We model platform dynamics."
    )
    source = FakeSource(
        "semantic_scholar",
        papers=[paper],
        get_hits={"SS:paper1": abstract_paper},
        get_fail={"10.1000/fallback": LiteratureNotFoundError("doi missing")},
    )
    router = FlipRouter(
        {
            "candidate prior-art paper": [
                {
                    "dimensions": [],
                    "relationship": "adjacent",
                    "assessment": "adjacent.",
                }
            ]
        }
    )
    svc = await _setup_service(store, blobs, router, {"semantic_scholar": source})
    _claim_id, assessment_id = await _run_candidate_pipeline(svc, store)
    a = (await store.get(assessment_id)).parse_payload(NoveltyCandidateAssessment)
    assert a.evidence_basis == EvidenceBasis.abstract

    execution = await _enrichment_execution(store, assessment_id)
    attempts = [
        (await store.get(aid)).parse_payload(EvidenceEnrichmentAttempt)
        for aid in execution.attempt_ids
    ]
    statuses = [t.status for t in attempts]
    assert EnrichmentAttemptStatus.not_found in statuses  # doi failed
    assert EnrichmentAttemptStatus.success in statuses  # paper id fallback worked
    assert source.get_calls == ["10.1000/fallback", "SS:paper1"]
    assert execution.outcome == EnrichmentOutcome.enriched


# ---------------------------------------------------------------------------
# 6. all acquisition strategies fail -> insufficient_evidence preserved
# ---------------------------------------------------------------------------


async def test_all_strategies_fail_insufficient(store: SQLiteArtifactStore, blobs):
    paper = _sparse_paper("Unreachable Paper", "10.1000/none")
    source = FakeSource(
        "semantic_scholar",
        papers=[paper],
        get_fail={"10.1000/none": RuntimeError("provider outage")},
    )
    router = FlipRouter(
        {
            "candidate prior-art paper": [
                {
                    "dimensions": [],
                    "relationship": "direct_prior_art",
                    "assessment": "would threaten if evidence existed.",
                }
            ]
        }
    )
    svc = await _setup_service(
        store, blobs, router, {"semantic_scholar": source}, acquire_full_text=False
    )
    _claim_id, assessment_id = await _run_candidate_pipeline(svc, store)
    a = (await store.get(assessment_id)).parse_payload(NoveltyCandidateAssessment)
    # acquisition failed -> the model's strong claim cannot stand
    assert a.evidence_basis == EvidenceBasis.indexed_metadata
    assert a.relationship == CandidateRelationship.insufficient_evidence
    execution = await _enrichment_execution(store, assessment_id)
    assert execution.outcome == EnrichmentOutcome.failed
    assert execution.after_evidence_basis == execution.before_evidence_basis
    # claim -> unverified (never a false clear)
    ca_id = await svc.assess_claim(_claim_id)
    ca = (await store.get(ca_id)).parse_payload(NoveltyClaimAssessment)
    assert ca.status == NoveltyClaimStatus.unverified


# ---------------------------------------------------------------------------
# 7. rate limit -> explicit failure, never clear novelty
# ---------------------------------------------------------------------------


async def test_rate_limit_explicit_failure(store: SQLiteArtifactStore, blobs):
    paper = _sparse_paper("Rate Limited Paper", "10.1000/rl")
    source = FakeSource(
        "semantic_scholar",
        papers=[paper],
        get_fail={"10.1000/rl": LiteratureRateLimitError("429 too many requests")},
    )
    router = FlipRouter(
        {
            "candidate prior-art paper": [
                {
                    "dimensions": [],
                    "relationship": "distinct",
                    "assessment": "distinct.",
                }
            ]
        }
    )
    svc = await _setup_service(
        store, blobs, router, {"semantic_scholar": source}, acquire_full_text=False
    )
    _claim_id, assessment_id = await _run_candidate_pipeline(svc, store)
    execution = await _enrichment_execution(store, assessment_id)
    attempt = (await store.get(execution.attempt_ids[0])).parse_payload(EvidenceEnrichmentAttempt)
    assert attempt.status == EnrichmentAttemptStatus.rate_limited
    assert execution.outcome == EnrichmentOutcome.failed
    ca_id = await svc.assess_claim(_claim_id)
    ca = (await store.get(ca_id)).parse_payload(NoveltyClaimAssessment)
    assert ca.status == NoveltyClaimStatus.unverified


# ---------------------------------------------------------------------------
# 8. enrich_candidate: new evidence reveals strong overlap -> report/gate
# ---------------------------------------------------------------------------


async def test_enrich_reveals_threat_recomputes_chain(store: SQLiteArtifactStore, blobs):
    paper = _sparse_paper("Sparse Threat Paper", "10.1000/th")
    abstract_paper = _paper_with_abstract(
        "Sparse Threat Paper",
        "10.1000/th",
        "We model demand-driven platform dynamics and characterize equilibrium quantities.",
    )
    source = FakeSource(
        "semantic_scholar",
        papers=[paper],
        get_hits={"10.1000/th": abstract_paper},
        get_fail={"10.1000/th": LiteratureRateLimitError("429 try later")},
    )
    router = FlipRouter(
        {
            "extract novelty claims": [{"claims": []}],
            "Sparse Threat Paper": [
                {"dimensions": [], "relationship": "distinct", "assessment": "distinct."},
                {"dimensions": [], "relationship": "direct_prior_art", "assessment": "threatens."},
            ],
            "independently verify": [{"verdict": "concurs", "reasoning": "yes"}],
        }
    )
    svc = await _setup_service(store, blobs, router, {"semantic_scholar": source})
    m_id, pkg_id = await _manuscript_env(store)
    report_id = await svc.create_report(pkg_id, as_of="2026-08-23", offline=False)
    report = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
    # rate-limited enrichment -> sparse candidate -> coverage insufficient
    assert report.overall_status == NoveltyReportStatus.unverified
    gate_id = await svc.create_gate(pkg_id, report_id)
    gate = (await store.get(gate_id)).parse_payload(SubmissionReadinessGate)
    assert gate.status == ReadinessStatus.unverified

    old_assessment_id = (
        (await store.get(report.claim_assessment_ids[0]))
        .parse_payload(NoveltyClaimAssessment)
        .candidate_assessment_ids[0]
    )
    old = (await store.get(old_assessment_id)).parse_payload(NoveltyCandidateAssessment)
    # inline enrichment was rate-limited -> sparse metadata evidence only
    assert old.evidence_basis == EvidenceBasis.indexed_metadata
    # provider recovers: retry enrichment explicitly
    source.get_fail.pop("10.1000/th")
    new_id = await svc.enrich_candidate(old_assessment_id)
    assert new_id != old_assessment_id
    new = (await store.get(new_id)).parse_payload(NoveltyCandidateAssessment)
    assert new.evidence_basis == EvidenceBasis.abstract
    assert new.relationship == CandidateRelationship.direct_prior_art
    supers = [
        c for c in await store.get_children(old_assessment_id) if c.relation.value == "supersedes"
    ]
    assert supers and supers[0].target_artifact_id == new_id

    # claim assessment superseded -> threatened
    new_claim_assessments = [
        env.parse_payload(NoveltyClaimAssessment)
        for env in await store.list(artifact_type="novelty_claim_assessment")
    ]
    claim_superseded = [a for a in new_claim_assessments if new_id in a.candidate_assessment_ids]
    assert claim_superseded and claim_superseded[0].status == NoveltyClaimStatus.threatened

    # report superseded -> blocked; gate recomputed -> blocked
    report_envs = await store.list(artifact_type="novelty_validation_report")
    enriched_envs = [
        env
        for env in report_envs
        if env.parse_payload(NoveltyValidationReport).metadata.get("recomputed_after_enrichment")
    ]
    assert len(enriched_envs) == 1
    enriched = enriched_envs[0].parse_payload(NoveltyValidationReport)
    assert enriched.overall_status == NoveltyReportStatus.blocked
    assert enriched.supersedes == report_id
    gates = [
        env.parse_payload(SubmissionReadinessGate)
        for env in await store.list(artifact_type="submission_readiness_gate")
    ]
    new_gates = [g for g in gates if g.novelty_report_id == enriched_envs[0].artifact_id]
    assert new_gates and new_gates[0].status == ReadinessStatus.blocked
    # old report/gate untouched and historically unverified
    assert (await store.get(report_id)).parse_payload(
        NoveltyValidationReport
    ).overall_status == NoveltyReportStatus.unverified
    assert (await store.get(gate_id)).parse_payload(
        SubmissionReadinessGate
    ).status == ReadinessStatus.unverified


# ---------------------------------------------------------------------------
# 9. enrich_candidate: new evidence shows distinct -> conservative update
# ---------------------------------------------------------------------------


async def test_enrich_reveals_distinct_conservative(store: SQLiteArtifactStore, blobs):
    paper = _sparse_paper("Sparse Distinct Paper", "10.1000/d")
    abstract_paper = _paper_with_abstract(
        "Sparse Distinct Paper", "10.1000/d", "A study of crop yields in arid agriculture."
    )
    source = FakeSource(
        "semantic_scholar",
        papers=[paper],
        get_hits={"10.1000/d": abstract_paper},
        get_fail={"10.1000/d": LiteratureRateLimitError("429 try later")},
    )
    router = FlipRouter(
        {
            "extract novelty claims": [{"claims": []}],
            "Sparse Distinct Paper": [
                {"dimensions": [], "relationship": "direct_prior_art", "assessment": "x."},
                {"dimensions": [], "relationship": "distinct", "assessment": "unrelated."},
            ],
        }
    )
    svc = await _setup_service(store, blobs, router, {"semantic_scholar": source})
    m_id, pkg_id = await _manuscript_env(store)
    report_id = await svc.create_report(pkg_id, as_of="2026-08-23", offline=False)
    report = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
    # metadata-only evidence cannot support direct prior art -> unverified
    assert report.overall_status == NoveltyReportStatus.unverified
    old_assessment_id = (
        (await store.get(report.claim_assessment_ids[0]))
        .parse_payload(NoveltyClaimAssessment)
        .candidate_assessment_ids[0]
    )
    old = (await store.get(old_assessment_id)).parse_payload(NoveltyCandidateAssessment)
    assert old.relationship == CandidateRelationship.insufficient_evidence

    source.get_fail.pop("10.1000/d")
    new_id = await svc.enrich_candidate(old_assessment_id)
    new = (await store.get(new_id)).parse_payload(NoveltyCandidateAssessment)
    assert new.evidence_basis == EvidenceBasis.abstract
    assert new.relationship == CandidateRelationship.distinct
    reports = [
        env.parse_payload(NoveltyValidationReport)
        for env in await store.list(artifact_type="novelty_validation_report")
    ]
    enriched_envs = [
        env
        for env in await store.list(artifact_type="novelty_validation_report")
        if env.parse_payload(NoveltyValidationReport).metadata.get("recomputed_after_enrichment")
    ]
    assert len(enriched_envs) == 1
    enriched = enriched_envs[0].parse_payload(NoveltyValidationReport)
    # distinct candidate -> claim not threatened; report moves to clear
    assert enriched.overall_status == NoveltyReportStatus.clear
    assert enriched.safe_within_scope_claims
    gates = [
        env.parse_payload(SubmissionReadinessGate)
        for env in await store.list(artifact_type="submission_readiness_gate")
    ]
    assert any(
        g.novelty_report_id == enriched_envs[0].artifact_id and g.status == ReadinessStatus.ready
        for g in gates
    )


# ---------------------------------------------------------------------------
# 10. enrich_candidate no-op when evidence already sufficient
# ---------------------------------------------------------------------------


async def test_enrich_noop_when_sufficient(store: SQLiteArtifactStore, blobs):
    paper = _paper_with_abstract("Has Abstract", "10.1000/s", "Abstract present.")
    source = FakeSource("semantic_scholar", papers=[paper])
    router = FlipRouter(
        {
            "candidate prior-art paper": [
                {"dimensions": [], "relationship": "distinct", "assessment": "d."}
            ]
        }
    )
    svc = await _setup_service(store, blobs, router, {"semantic_scholar": source})
    _claim_id, assessment_id = await _run_candidate_pipeline(svc, store)
    returned = await svc.enrich_candidate(assessment_id)
    assert returned == assessment_id
    assert await store.list(artifact_type="evidence_enrichment_plan") == []
