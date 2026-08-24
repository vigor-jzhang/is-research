"""Phase 5D unit tests — bounded evidence pre-acquisition.

Deterministic selection, budgeting, cache hits, dedup across claims,
failure non-fatality, Phase 5C fallback interaction. No network.
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
from research_harness.research.schemas.novelty import (
    CandidateRelationship,
    EvidenceBasis,
    EvidenceEnrichmentExecution,
    NoveltyCandidateAssessment,
    NoveltyClaim,
    NoveltyClaimAssessment,
    NoveltyClaimStatus,
    NoveltyClaimType,
    PreAcquisitionExecution,
)
from research_harness.research.schemas.paper import Author, PaperRecord

CLAIM_A = "We are the first study to model demand-driven platform dynamics."
CLAIM_B = "To our knowledge, no prior research has examined this mechanism."


class FlipRouter:
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


def _sparse(title: str, doi: str) -> PaperRecord:
    return PaperRecord(
        title=title,
        authors=[Author(name="Smith, Jane")],
        year=2019,
        venue="Journal of Platform Studies",
        doi=doi,
        abstract=None,
    )


def _with_abstract(title: str, doi: str, abstract: str) -> PaperRecord:
    return PaperRecord(
        title=title,
        authors=[Author(name="Smith, Jane")],
        year=2019,
        venue="Journal of Platform Studies",
        doi=doi,
        abstract=abstract,
    )


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


async def _setup(
    store: SQLiteArtifactStore,
    blobs: FilesystemBlobStore,
    router: FlipRouter,
    source: FakeSource,
    *,
    preacq_enabled: bool = True,
    preacq_risk_levels: list[str] | None = None,
    preacq_max_per_claim: int = 10,
    preacq_max_total: int = 30,
    risk: str = "critical",
) -> NoveltyValidationService:
    return NoveltyValidationService(
        model_router=router,
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        service_lookup=_lookup({"semantic_scholar": source}),
        blob_store=blobs,
        providers=["semantic_scholar"],
        acquire_full_text=False,
        preacquisition_enabled=preacq_enabled,
        preacquisition_risk_levels=preacq_risk_levels,
        preacquisition_max_per_claim=preacq_max_per_claim,
        preacquisition_max_total=preacq_max_total,
    )


async def _run_pipeline(
    svc: NoveltyValidationService,
    store: SQLiteArtifactStore,
    claim: NoveltyClaim | None = None,
) -> tuple[str, str, str | None]:
    """plan -> execute -> cset -> (preacquisition) -> assessments.
    Returns (claim_id, assessment_id, preacquisition_execution_id | None)."""
    claim = claim or NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text=CLAIM_A,
        claim_type=NoveltyClaimType.absolute_priority,
        risk="critical",
        extraction_method="deterministic",
        source_quote="We are the first study",
    )
    claim_id = await svc._put(claim, "novelty_claim")
    plan_id = await svc.plan_searches(claim_id, as_of=date(2026, 8, 23), offline=True)
    exec_id = await svc.execute_searches(plan_id)
    cset_id = await svc.build_candidate_set(claim_id, plan_id, exec_id)
    preacq_id = None
    if svc._preacquisition_enabled:
        preacq_id = await svc.preacquire_evidence(claim_id, cset_id, offline=False)
    assessment_ids = await svc.assess_candidates(claim_id, cset_id, offline=False)
    return claim_id, assessment_ids[0], preacq_id


async def _preacq(store: SQLiteArtifactStore, preacq_id: str) -> PreAcquisitionExecution:
    return (await store.get(preacq_id)).parse_payload(PreAcquisitionExecution)


# ---------------------------------------------------------------------------
# 1. critical sparse candidate -> pre-acquired before assessment
# ---------------------------------------------------------------------------


async def test_critical_sparse_preacquired(store: SQLiteArtifactStore, blobs):
    paper = _sparse("Sparse Critical", "10.1000/a")
    source = FakeSource(
        "semantic_scholar",
        papers=[paper],
        get_hits={"10.1000/a": _with_abstract("Sparse Critical", "10.1000/a", "Abstract text.")},
    )
    svc = await _setup(
        store,
        blobs,
        FlipRouter(
            {
                "candidate prior-art paper": [
                    {"dimensions": [], "relationship": "distinct", "assessment": "d."}
                ]
            }
        ),
        source,
    )
    _claim_id, assessment_id, preacq_id = await _run_pipeline(svc, store)
    preacq = await _preacq(store, preacq_id)
    assert preacq.metrics["candidates_selected"] == 1
    assert preacq.metrics["abstracts_acquired"] == 1
    assert preacq.metrics["candidates_upgraded"] == 1
    assert preacq.metrics["cache_hits"] == 0
    assert (
        "selected: sparse candidate" in preacq.selection_reasons[preacq.selected_candidate_ids[0]]
    )
    # the assessment ran on the pre-acquired abstract
    a = (await store.get(assessment_id)).parse_payload(NoveltyCandidateAssessment)
    assert a.evidence_basis == EvidenceBasis.abstract


# ---------------------------------------------------------------------------
# 2. low-risk candidate -> skipped under default policy
# ---------------------------------------------------------------------------


async def test_low_risk_skipped(store: SQLiteArtifactStore, blobs):
    paper = _sparse("Sparse Low Risk", "10.1000/l")
    source = FakeSource(
        "semantic_scholar",
        papers=[paper],
        get_hits={"10.1000/l": _with_abstract("Sparse Low Risk", "10.1000/l", "Abs.")},
    )
    svc = await _setup(
        store,
        blobs,
        FlipRouter({}),
        source,
    )
    low_claim = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text="Our contribution differs from prior work.",
        claim_type=NoveltyClaimType.contribution_difference,
        risk="medium",
        extraction_method="deterministic",
        source_quote="Our contribution",
    )
    _claim_id, _assessment_id, preacq_id = await _run_pipeline(svc, store, low_claim)
    # medium risk not in [critical, high] -> no pre-acquisition artifact at all
    assert preacq_id is None
    assert await store.list(artifact_type="evidence_preacquisition_execution") == []
    assert source.get_calls == []


# ---------------------------------------------------------------------------
# 3. existing abstract -> cache hit, zero provider calls
# ---------------------------------------------------------------------------


async def test_existing_abstract_cache_hit(store: SQLiteArtifactStore, blobs):
    paper = _with_abstract("Has Abstract", "10.1000/c", "Already have it.")
    source = FakeSource("semantic_scholar", papers=[paper])
    svc = await _setup(
        store,
        blobs,
        FlipRouter(
            {
                "candidate prior-art paper": [
                    {"dimensions": [], "relationship": "distinct", "assessment": "d."}
                ]
            }
        ),
        source,
    )
    _claim_id, _assessment_id, preacq_id = await _run_pipeline(svc, store)
    preacq = await _preacq(store, preacq_id)
    assert preacq.metrics["candidates_considered"] == 0
    assert preacq.metrics["cache_hits"] == 1
    assert preacq.metrics["external_attempts"] == 0
    assert preacq.selected_candidate_ids == []
    assert source.get_calls == []


# ---------------------------------------------------------------------------
# 4. same paper from multiple queries -> acquired once
# ---------------------------------------------------------------------------


async def test_multi_query_acquired_once(store: SQLiteArtifactStore, blobs):
    paper = _sparse("Multi Query Paper", "10.1000/mq")
    source = FakeSource(
        "semantic_scholar",
        papers=[paper],
        get_hits={"10.1000/mq": _with_abstract("Multi Query Paper", "10.1000/mq", "Abs.")},
    )
    svc = await _setup(
        store,
        blobs,
        FlipRouter(
            {
                "candidate prior-art paper": [
                    {"dimensions": [], "relationship": "distinct", "assessment": "d."}
                ]
            }
        ),
        source,
    )
    claim = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text=CLAIM_A,
        claim_type=NoveltyClaimType.absolute_priority,
        risk="critical",
        extraction_method="deterministic",
        source_quote="We are the first study",
    )
    claim_id = await svc._put(claim, "novelty_claim")
    plan_id = await svc.plan_searches(claim_id, as_of=date(2026, 8, 23), offline=True)
    exec_id = await svc.execute_searches(plan_id)
    cset_id = await svc.build_candidate_set(claim_id, plan_id, exec_id)
    # the paper surfaces under BOTH deterministic queries -> one identity
    from research_harness.research.schemas.novelty import NoveltyCandidateSet

    cset = (await store.get(cset_id)).parse_payload(NoveltyCandidateSet)
    assert len(cset.candidates) == 1
    assert len(cset.candidates[0].found_by_query_ids) == 2  # two queries found it
    preacq_id = await svc.preacquire_evidence(claim_id, cset_id, offline=False)
    preacq = await _preacq(store, preacq_id)
    assert preacq.metrics["candidates_selected"] == 1
    assert preacq.metrics["abstracts_acquired"] == 1
    assert len(source.get_calls) == 1  # one acquisition, not two
    assert len(preacq.enrichment_execution_ids) == 1


# ---------------------------------------------------------------------------
# 5. same paper across multiple claims -> acquired once
# ---------------------------------------------------------------------------


async def test_across_claims_acquired_once(store: SQLiteArtifactStore, blobs):
    paper = _sparse("Shared Paper", "10.1000/sh")
    source = FakeSource(
        "semantic_scholar",
        papers=[paper],
        get_hits={"10.1000/sh": _with_abstract("Shared Paper", "10.1000/sh", "Abs.")},
    )
    svc = await _setup(
        store,
        blobs,
        FlipRouter(
            {
                "candidate prior-art paper": [
                    {"dimensions": [], "relationship": "distinct", "assessment": "d."}
                ]
            }
        ),
        source,
    )
    claim_a = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text=CLAIM_A,
        claim_type=NoveltyClaimType.absolute_priority,
        risk="critical",
        extraction_method="deterministic",
        source_quote="We are the first study",
    )
    claim_b = NoveltyClaim(
        manuscript_id="m1",
        section_id="conclusion",
        claim_text=CLAIM_B,
        claim_type=NoveltyClaimType.scoped_priority,
        risk="high",
        extraction_method="deterministic",
        source_quote="To our knowledge",
    )
    _ca, _aa, preacq_a = await _run_pipeline(svc, store, claim_a)
    _cb, _ab, preacq_b = await _run_pipeline(svc, store, claim_b)
    assert source.get_calls == ["10.1000/sh"]  # acquired exactly once
    pa = await _preacq(store, preacq_a)
    pb = await _preacq(store, preacq_b)
    assert pa.metrics["abstracts_acquired"] == 1
    # second claim: evidence already present -> cache hit, zero external calls
    assert pb.metrics["external_attempts"] == 0
    assert pb.metrics["cache_hits"] == 1
    assert pb.selected_candidate_ids == []


# ---------------------------------------------------------------------------
# 6. budget exceeded -> deterministic bounded selection
# ---------------------------------------------------------------------------


async def test_budget_bounded_selection(store: SQLiteArtifactStore, blobs):
    papers = [_sparse(f"Paper {i}", f"10.1000/b{i}") for i in range(4)]
    source = FakeSource(
        "semantic_scholar",
        papers=papers,
        get_hits={
            f"10.1000/b{i}": _with_abstract(f"Paper {i}", f"10.1000/b{i}", "Abs.") for i in range(4)
        },
    )
    svc = await _setup(
        store,
        blobs,
        FlipRouter(
            {
                "candidate prior-art paper": [
                    {"dimensions": [], "relationship": "distinct", "assessment": "d."}
                ]
            }
        ),
        source,
        preacq_max_per_claim=2,
    )
    claim = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text=CLAIM_A,
        claim_type=NoveltyClaimType.absolute_priority,
        risk="critical",
        extraction_method="deterministic",
        source_quote="We are the first study",
    )
    claim_id = await svc._put(claim, "novelty_claim")
    plan_id = await svc.plan_searches(claim_id, as_of=date(2026, 8, 23), offline=True)
    exec_id = await svc.execute_searches(plan_id)
    cset_id = await svc.build_candidate_set(claim_id, plan_id, exec_id)
    preacq_id = await svc.preacquire_evidence(claim_id, cset_id, offline=False)
    preacq = await _preacq(store, preacq_id)
    assert preacq.metrics["candidates_considered"] == 4
    assert preacq.metrics["candidates_selected"] == 2
    assert len(preacq.selected_candidate_ids) == 2
    assert preacq.metrics["abstracts_acquired"] == 2
    skipped = list(preacq.skipped_candidate_ids)
    assert len(skipped) == 2
    assert all("per-claim budget" in preacq.selection_reasons[s] for s in skipped)
    # deterministic selection reasons recorded for every considered candidate
    assert all(iid in preacq.selection_reasons for iid in preacq.considered_candidate_ids)


async def test_global_budget_bounded(store: SQLiteArtifactStore, blobs):
    papers = [_sparse(f"Paper {i}", f"10.1000/g{i}") for i in range(3)]
    source = FakeSource(
        "semantic_scholar",
        papers=papers,
        get_hits={
            f"10.1000/g{i}": _with_abstract(f"Paper {i}", f"10.1000/g{i}", "Abs.") for i in range(3)
        },
    )
    svc = await _setup(
        store,
        blobs,
        FlipRouter(
            {
                "candidate prior-art paper": [
                    {"dimensions": [], "relationship": "distinct", "assessment": "d."}
                ]
            }
        ),
        source,
        preacq_max_total=2,
    )
    # two claims, each with sparse candidates; global cap 2
    claim_a = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text=CLAIM_A,
        claim_type=NoveltyClaimType.absolute_priority,
        risk="critical",
        extraction_method="deterministic",
        source_quote="We are the first study",
    )
    claim_b = NoveltyClaim(
        manuscript_id="m1",
        section_id="conclusion",
        claim_text=CLAIM_B,
        claim_type=NoveltyClaimType.scoped_priority,
        risk="high",
        extraction_method="deterministic",
        source_quote="To our knowledge",
    )
    _ca, _aa, preacq_a = await _run_pipeline(svc, store, claim_a)
    _cb, _ab, preacq_b = await _run_pipeline(svc, store, claim_b)
    pa = await _preacq(store, preacq_a)
    pb = await _preacq(store, preacq_b)
    assert pa.metrics["candidates_selected"] == 2  # claim A used the whole global cap
    assert pb.metrics["candidates_selected"] == 0  # global budget exhausted
    assert pb.selected_candidate_ids == []
    assert all("global budget" in pb.selection_reasons[iid] for iid in pb.considered_candidate_ids)
    # claim A pre-acquired g0/g1; claim B's uncovered candidate g2 was left
    # to the Phase 5C inline fallback during assessment
    assert source.get_calls == ["10.1000/g0", "10.1000/g1", "10.1000/g2"]


# ---------------------------------------------------------------------------
# 7. rate limit -> recorded, assessment continues
# ---------------------------------------------------------------------------


async def test_rate_limit_recorded_assessment_continues(store: SQLiteArtifactStore, blobs):
    paper = _sparse("Rate Limited", "10.1000/rl")
    source = FakeSource(
        "semantic_scholar",
        papers=[paper],
        get_fail={"10.1000/rl": LiteratureRateLimitError("429")},
    )
    svc = await _setup(
        store,
        blobs,
        FlipRouter(
            {
                "candidate prior-art paper": [
                    {
                        "dimensions": [],
                        "relationship": "direct_prior_art",
                        "assessment": "x",
                    }
                ]
            }
        ),
        source,
    )
    _claim_id, assessment_id, preacq_id = await _run_pipeline(svc, store)
    preacq = await _preacq(store, preacq_id)
    assert preacq.metrics["failures"] >= 1
    assert preacq.metrics["candidates_upgraded"] == 0
    # assessment continues: metadata-only evidence cannot support the claim
    a = (await store.get(assessment_id)).parse_payload(NoveltyCandidateAssessment)
    assert a.relationship == CandidateRelationship.insufficient_evidence
    # never a clear novelty result
    ca_id = await svc.assess_claim(_claim_id)
    ca = (await store.get(ca_id)).parse_payload(NoveltyClaimAssessment)
    assert ca.status == NoveltyClaimStatus.unverified


# ---------------------------------------------------------------------------
# 8. successful pre-acquisition -> Phase 5C fallback not invoked
# ---------------------------------------------------------------------------


async def test_successful_preacq_no_fallback(store: SQLiteArtifactStore, blobs):
    paper = _sparse("Fallback Free", "10.1000/ff")
    source = FakeSource(
        "semantic_scholar",
        papers=[paper],
        get_hits={"10.1000/ff": _with_abstract("Fallback Free", "10.1000/ff", "Abs.")},
    )
    svc = await _setup(
        store,
        blobs,
        FlipRouter(
            {
                "candidate prior-art paper": [
                    {"dimensions": [], "relationship": "distinct", "assessment": "d."}
                ]
            }
        ),
        source,
    )
    _claim_id, _assessment_id, preacq_id = await _run_pipeline(svc, store)
    preacq = await _preacq(store, preacq_id)
    # the only enrichment execution is the pre-acquisition's (no 5C fallback)
    assert len(preacq.enrichment_execution_ids) == 1
    executions = [
        env.parse_payload(EvidenceEnrichmentExecution)
        for env in await store.list(artifact_type="evidence_enrichment_execution")
    ]
    assert len(executions) == 1
    # candidate assessed directly on the abstract
    assert source.get_calls == ["10.1000/ff"]


# ---------------------------------------------------------------------------
# 9. insufficient pre-acquisition -> Phase 5C fallback still works
# ---------------------------------------------------------------------------


async def test_insufficient_preacq_fallback_works(store: SQLiteArtifactStore, blobs):
    # two sparse candidates; pre-acquisition budget 1 -> the second candidate
    # is not covered, so the Phase 5C inline fallback must enrich it
    papers = [_sparse("Covered", "10.1000/c1"), _sparse("Uncovered", "10.1000/c2")]
    source = FakeSource(
        "semantic_scholar",
        papers=papers,
        get_hits={
            "10.1000/c1": _with_abstract("Covered", "10.1000/c1", "Abs one."),
            "10.1000/c2": _with_abstract("Uncovered", "10.1000/c2", "Abs two."),
        },
    )
    svc = await _setup(
        store,
        blobs,
        FlipRouter(
            {
                "candidate prior-art paper": [
                    {"dimensions": [], "relationship": "distinct", "assessment": "d."}
                ]
            }
        ),
        source,
        preacq_max_per_claim=1,
    )
    _claim_id, _assessment_id, preacq_id = await _run_pipeline(svc, store)
    preacq = await _preacq(store, preacq_id)
    assert preacq.metrics["candidates_selected"] == 1
    # Phase 5C fallback enriched the budget-skipped candidate during assessment
    executions = [
        env.parse_payload(EvidenceEnrichmentExecution)
        for env in await store.list(artifact_type="evidence_enrichment_execution")
    ]
    assert len(executions) == 2  # preacquisition + one fallback
    assert source.get_calls == ["10.1000/c1", "10.1000/c2"]


# ---------------------------------------------------------------------------
# 10. disabled config -> existing Phase 5C behavior unchanged
# ---------------------------------------------------------------------------


async def test_disabled_preacq_behavior_unchanged(store: SQLiteArtifactStore, blobs):
    paper = _sparse("Disabled Mode", "10.1000/dm")
    source = FakeSource(
        "semantic_scholar",
        papers=[paper],
        get_hits={"10.1000/dm": _with_abstract("Disabled Mode", "10.1000/dm", "Abs.")},
    )
    svc = await _setup(
        store,
        blobs,
        FlipRouter(
            {
                "candidate prior-art paper": [
                    {"dimensions": [], "relationship": "distinct", "assessment": "d."}
                ]
            }
        ),
        source,
        preacq_enabled=False,
    )
    _claim_id, assessment_id, preacq_id = await _run_pipeline(svc, store)
    assert preacq_id is None
    assert await store.list(artifact_type="evidence_preacquisition_execution") == []
    # Phase 5C inline enrichment still runs (the sparse candidate is enriched
    # during assessment), exactly as before 5D
    a = (await store.get(assessment_id)).parse_payload(NoveltyCandidateAssessment)
    assert a.evidence_basis == EvidenceBasis.abstract
    executions = [
        env.parse_payload(EvidenceEnrichmentExecution)
        for env in await store.list(artifact_type="evidence_enrichment_execution")
    ]
    assert len(executions) == 1
    assert source.get_calls == ["10.1000/dm"]


# ---------------------------------------------------------------------------
# 11. failed pre-acquisition prevents repeated fallback strategies
# ---------------------------------------------------------------------------


async def test_failed_preacq_prevents_repeat(store: SQLiteArtifactStore, blobs):
    paper = _sparse("Fails Everywhere", "10.1000/f")
    source = FakeSource(
        "semantic_scholar",
        papers=[paper],
        get_fail={"10.1000/f": RuntimeError("provider outage")},
    )
    svc = await _setup(
        store,
        blobs,
        FlipRouter(
            {
                "candidate prior-art paper": [
                    {"dimensions": [], "relationship": "distinct", "assessment": "d."}
                ]
            }
        ),
        source,
    )
    _claim_id, _assessment_id, preacq_id = await _run_pipeline(svc, store)
    preacq = await _preacq(store, preacq_id)
    assert preacq.metrics["failures"] >= 1
    # the inline fallback must NOT repeat the failed strategy in the same run
    executions = [
        env.parse_payload(EvidenceEnrichmentExecution)
        for env in await store.list(artifact_type="evidence_enrichment_execution")
    ]
    assert len(executions) == 1  # only the pre-acquisition attempt
    assert len(source.get_calls) == 1
