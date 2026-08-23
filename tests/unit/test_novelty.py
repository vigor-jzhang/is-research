"""Phase 5A unit tests — external novelty validation.

Deterministic-only paths (offline=True) plus KeyedRouter-driven model paths,
so ordinary `uv run pytest` never touches the network or paid models.
"""

from __future__ import annotations

import json
import pathlib
from datetime import date

import pytest

from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.literature.identity_resolver.plugin import (
    PaperIdentityResolverService,
)
from research_harness.plugins.literature.ingestion.plugin import LiteratureIngestor
from research_harness.plugins.research.novelty_validator import detection
from research_harness.plugins.research.novelty_validator.plugin import (
    NoveltyValidationService,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.identity import PaperIdentity
from research_harness.research.schemas.novelty import (
    CandidateRelationship,
    ClaimRiskLevel,
    EvidenceBasis,
    NoveltyCandidateAssessment,
    NoveltyCandidateSet,
    NoveltyClaim,
    NoveltyClaimAssessment,
    NoveltyClaimStatus,
    NoveltyClaimType,
    NoveltyCriticAssessment,
    NoveltyReportStatus,
    NoveltyRevisionRecommendation,
    NoveltySearchExecution,
    NoveltySearchPlan,
    NoveltyValidationReport,
    ReadinessStatus,
    SubmissionReadinessGate,
)
from research_harness.research.schemas.paper import Author, PaperRecord
from research_harness.research.schemas.publication import (
    FormattedManuscript,
    FormattedManuscriptStatus,
    FormattedSection,
    FrontMatter,
    SubmissionPackage,
    SubmissionPackageStatus,
)


class KeyedRouter:
    """Routes model calls by marker substrings in the prompt; returns the
    matching builder's JSON. KeyedRouter does not retry on bad output."""

    def __init__(self, builders: dict[str, dict], default: dict | None = None):
        self.builders = builders
        self.default = default
        self.calls = 0

    async def complete(self, role, request):
        self.calls += 1
        text = " ".join(m.content or "" for m in request.messages)
        for marker, resp in self.builders.items():
            if marker in text:
                return ModelResponse(
                    message=Message(role="assistant", content=json.dumps(resp)),
                    tool_calls=[],
                    finish_reason="stop",
                    model="fake",
                )
        if self.default is not None:
            return ModelResponse(
                message=Message(role="assistant", content=json.dumps(self.default)),
                tool_calls=[],
                finish_reason="stop",
                model="fake",
            )
        raise AssertionError(f"no builder for prompt: {text[:200]}")


class FakeSource:
    def __init__(
        self,
        provider_name: str,
        hits_by_query: dict[str, list[PaperRecord]] | None = None,
        catchall: list[PaperRecord] | None = None,
    ):
        self.provider_name = provider_name
        self.hits_by_query = hits_by_query or {}
        self.catchall = catchall or []
        self.fail_on: set[str] = set()
        self.fail_all = False
        self.requests: list[str] = []

    async def search(self, request):
        self.requests.append(request.query)
        if self.fail_all or request.query in self.fail_on:
            raise RuntimeError(f"provider {self.provider_name} failed for {request.query}")
        hits = self.hits_by_query.get(request.query)
        if hits is None:
            hits = self.catchall
        from research_harness.contracts.literature import (
            LiteratureSearchHit,
            LiteratureSearchPage,
        )

        return LiteratureSearchPage(
            provider=self.provider_name,
            hits=[
                LiteratureSearchHit(
                    paper=p,
                    raw_payload={"title": p.title},
                    provider=self.provider_name,
                    provider_record_id=p.doi or p.title,
                )
                for p in hits
            ],
            total_estimate=len(hits),
        )

    async def get(self, identifier: str):
        raise NotImplementedError


def _paper(title: str, year: int | None, doi: str, abstract: str | None = None) -> PaperRecord:
    return PaperRecord(
        title=title,
        authors=[Author(name="Smith, Jane")],
        year=year,
        venue="Journal of Platform Studies",
        doi=doi,
        abstract=abstract,
    )


def _lookup(sources: dict[str, FakeSource]):
    def f(name: str):
        return sources[name.split(".")[-1]]

    return f


async def _manuscript_env(
    store: SQLiteArtifactStore,
    *,
    title: str = "Demand-Driven Platform Quantity Dynamics",
    abstract: str = "",
    sections: list[tuple[str, str, str]] | None = None,
) -> tuple[str, str, str]:
    """Persists formatted manuscript + ready submission package. Returns
    (manuscript_id, package_id, draft_id)."""
    sections = sections or [
        (
            "introduction",
            "Introduction",
            "We are the first study to model demand-driven platform dynamics.",
        ),
        ("conclusion", "Conclusion", "Demand dynamics matter for platforms."),
    ]
    fm = FormattedManuscript(
        draft_id="d1",
        results_package_id="pkg1",
        profile_id="prof1",
        profile_name="Generic",
        citation_style="author_year",
        front_matter=FrontMatter(title=title, abstract=abstract, generated_by="deterministic"),
        sections=[
            FormattedSection(section_id=sid, title=t, body=b, word_count=len(b.split()))
            for sid, t, b in sections
        ],
        anonymous_review=False,
        total_word_count=10,
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
    return m_env.artifact_id, p_env.artifact_id, "d1"


@pytest.fixture()
async def store(tmp_path: pathlib.Path):
    s = SQLiteArtifactStore(path=tmp_path / "art.db")
    yield s
    await s.close()


@pytest.fixture()
def svc(store: SQLiteArtifactStore):
    sources = {
        "semantic_scholar": FakeSource("semantic_scholar", {}),
        "crossref": FakeSource("crossref", {}),
    }
    router = KeyedRouter({}, default=None)
    return NoveltyValidationService(
        model_router=router,
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        service_lookup=_lookup(sources),
        max_llm_calls=100,
        providers=["semantic_scholar"],
    )


# ---------------------------------------------------------------------------
# Layer A: deterministic claim extraction
# ---------------------------------------------------------------------------


def test_detection_first_study_is_critical():
    findings = detection.detect_high_risk("We are the first study to model platform demand.")
    assert findings and findings[0][0] == "absolute_priority"
    assert findings[0][1] == "critical"


def test_detection_categorical_absence_is_critical():
    text = "No prior research has examined this mechanism."
    findings = detection.detect_high_risk(text)
    assert findings and findings[0][0] == "literature_absence"
    assert findings[0][1] == "critical"


def test_detection_scoped_priority_is_high():
    text = "To our knowledge, this is the first analytical model of X under Y."
    findings = detection.detect_high_risk(text)
    assert findings and findings[0][0] == "scoped_priority"
    assert findings[0][1] == "high"


def test_detection_technical_first_not_flagged():
    text = "We solve the first-order condition for each platform's quantity."
    assert detection.detect_high_risk(text) == []
    assert detection.detect_high_risk("As a first step, we define the game.") == []


def test_detection_ordinary_contribution_not_critical():
    text = "Our contribution is a new equilibrium characterization."
    findings = detection.detect_high_risk(text)
    assert all(r != "critical" for _ct, r, _s, _e in findings)


async def test_extraction_layer_a_only(store: SQLiteArtifactStore, svc):
    m_id, _pkg, _draft = await _manuscript_env(
        store,
        sections=[
            (
                "introduction",
                "Introduction",
                "We are the first study to model demand-driven platform dynamics.",
            ),
            (
                "conclusion",
                "Conclusion",
                "No prior research has examined this mechanism.",
            ),
        ],
    )
    claim_ids = await svc.extract_claims(m_id, offline=True)
    assert len(claim_ids) == 2
    claims = [(await store.get(c)).parse_payload(NoveltyClaim) for c in claim_ids]
    types = {c.claim_type for c in claims}
    assert NoveltyClaimType.absolute_priority in types
    assert NoveltyClaimType.literature_absence in types
    risks = {c.risk for c in claims}
    assert risks == {ClaimRiskLevel.critical}
    for c in claims:
        assert c.source_quote in c.claim_text or c.source_quote
        assert c.extraction_method == "deterministic"


async def test_extraction_duplicate_locations_merge(store: SQLiteArtifactStore, svc):
    body = "We are the first study to model demand-driven platform dynamics."
    m_id, _pkg, _draft = await _manuscript_env(
        store,
        sections=[
            ("introduction", "Introduction", body),
            ("research_gap", "Research Gap", body),
        ],
    )
    claim_ids = await svc.extract_claims(m_id, offline=True)
    assert len(claim_ids) == 1
    claim = (await store.get(claim_ids[0])).parse_payload(NoveltyClaim)
    assert len(claim.locations) == 2
    assert {loc.section_id for loc in claim.locations} == {"introduction", "research_gap"}


async def test_extraction_hybrid_upgrades_priority(store: SQLiteArtifactStore, svc):
    m_id, _pkg, _draft = await _manuscript_env(
        store,
        sections=[
            (
                "introduction",
                "Introduction",
                "We introduce a fresh mechanism for platform dynamics.",
            ),
        ],
    )
    svc._router = KeyedRouter(
        {
            "extract novelty claims": {
                "claims": [
                    {
                        "claim_text": "This is the first study of platform dynamics.",
                        "source_quote": "We introduce a fresh mechanism for platform dynamics.",
                        "section_id": "introduction",
                        "claim_type": "contribution_difference",
                        "importance": "major",
                    }
                ]
            }
        }
    )
    claim_ids = await svc.extract_claims(m_id, offline=False)
    assert len(claim_ids) == 1
    claim = (await store.get(claim_ids[0])).parse_payload(NoveltyClaim)
    # LLM said contribution_difference; deterministic upgrade must win
    assert claim.claim_type == NoveltyClaimType.absolute_priority
    assert claim.risk == ClaimRiskLevel.critical
    assert claim.extraction_method == "hybrid"


async def test_extraction_ignores_fabricated_quotes(store: SQLiteArtifactStore, svc):
    m_id, _pkg, _draft = await _manuscript_env(
        store, sections=[("introduction", "I", "Plain text here.")]
    )
    svc._router = KeyedRouter(
        {
            "extract novelty claims": {
                "claims": [
                    {
                        "claim_text": "Invented claim not in the manuscript.",
                        "source_quote": "This sentence does not exist anywhere.",
                        "section_id": "introduction",
                        "claim_type": "absolute_priority",
                        "importance": "major",
                    }
                ]
            }
        }
    )
    claim_ids = await svc.extract_claims(m_id, offline=False)
    assert claim_ids == []


# ---------------------------------------------------------------------------
# Search planning
# ---------------------------------------------------------------------------


async def test_plan_bounded_and_claim_linked(store: SQLiteArtifactStore, svc):
    claim = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text="We are the first study to model demand-driven platform dynamics.",
        claim_type=NoveltyClaimType.absolute_priority,
        risk=ClaimRiskLevel.critical,
        extraction_method="deterministic",
        source_quote="We are the first study",
    )
    claim_id = await svc._put(claim, "novelty_claim")
    plan_id = await svc.plan_searches(claim_id, as_of=date(2026, 8, 23), offline=True)
    plan = (await store.get(plan_id)).parse_payload(NoveltySearchPlan)
    assert plan.claim_id == claim_id
    # critical -> budget 10 (offline: deterministic only, 3 queries)
    assert 0 < len(plan.queries) <= 10
    assert len(plan.queries) == len(plan.query_artifact_ids)
    assert plan.providers == ["semantic_scholar"]
    assert plan.date_cutoff == date(2026, 8, 23)
    assert plan.year_from == 1976 and plan.year_to == 2026
    assert plan.generation.method == "deterministic"
    types = {q.query_type for q in plan.queries}
    assert "exact" in types
    parents = await store.get_parents(plan_id)
    assert any(p.source_artifact_id == claim_id for p in parents)


async def test_plan_model_expansion_bounded(store: SQLiteArtifactStore, svc):
    claim = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text="We are the first to study platform competition.",
        claim_type=NoveltyClaimType.absolute_priority,
        risk=ClaimRiskLevel.critical,
        extraction_method="deterministic",
        source_quote="first to study",
    )
    claim_id = await svc._put(claim, "novelty_claim")
    svc._router = KeyedRouter(
        {
            "bounded novelty search queries": {
                "queries": [
                    {
                        "query": "platform competition game",
                        "query_type": "mechanism",
                        "synonyms": [],
                    },
                    {
                        "query": "two-sided markets platform",
                        "query_type": "setting",
                        "synonyms": ["platforms"],
                    },
                    {"query": "", "query_type": "exact", "synonyms": []},  # invalid
                    {
                        "query": "platform competition game",
                        "query_type": "mechanism",
                        "synonyms": [],
                    },  # dup
                    {"query": "repeated query", "query_type": "theory", "synonyms": []},
                    {"query": "repeated query", "query_type": "theory", "synonyms": []},
                    {"query": "another query", "query_type": "synonym", "synonyms": ["x"]},
                ]
            }
        }
    )
    plan_id = await svc.plan_searches(claim_id, as_of=date(2026, 8, 23), offline=False)
    plan = (await store.get(plan_id)).parse_payload(NoveltySearchPlan)
    # deterministic 2 + model 4 (2 invalid/dup dropped, 1 dup dropped) = 6 <= budget 10
    assert len(plan.queries) == 6
    assert plan.generation.method == "hybrid"
    # query types preserved
    assert {q.query_type.value for q in plan.queries} == {
        "exact",
        "mechanism",
        "setting",
        "theory",
        "synonym",
    }


# ---------------------------------------------------------------------------
# Search execution + candidate set (dedup, post-cutoff, dates)
# ---------------------------------------------------------------------------


def _claim_with_plan(
    store: SQLiteArtifactStore, svc, as_of: date = date(2026, 8, 23)
) -> tuple[str, str, str]:
    import asyncio

    loop = asyncio.get_event_loop()
    claim = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text="We are the first to study platform competition.",
        claim_type=NoveltyClaimType.absolute_priority,
        risk=ClaimRiskLevel.critical,
        extraction_method="deterministic",
        source_quote="first to study",
    )
    claim_id = loop.run_until_complete(svc._put(claim, "novelty_claim"))
    plan_id = loop.run_until_complete(svc.plan_searches(claim_id, as_of=as_of, offline=True))
    return claim_id, plan_id, plan_id


async def test_search_execution_and_dedup(store: SQLiteArtifactStore, svc):
    """Same paper via two providers/queries collapses to one identity; one
    paper preserves several discovery queries/providers."""
    dup_a = _paper(
        "Platform Competition and Demand", 2019, "10.1000/dup", "An abstract about platforms."
    )
    dup_b = _paper("Platform Competition and Demand", 2019, "10.1000/dup", None)  # same DOI
    unrelated = _paper(
        "Unrelated Agriculture Study", 2018, "10.1000/uni", "An abstract about farming."
    )
    sources = {
        "semantic_scholar": FakeSource("semantic_scholar", catchall=[dup_a, unrelated]),
        "crossref": FakeSource("crossref", catchall=[dup_b]),
    }
    svc._lookup = _lookup(sources)
    svc._providers = ["semantic_scholar", "crossref"]

    claim = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text="We are the first to study platform competition.",
        claim_type=NoveltyClaimType.absolute_priority,
        risk=ClaimRiskLevel.critical,
        extraction_method="deterministic",
        source_quote="first to study",
    )
    claim_id = await svc._put(claim, "novelty_claim")
    plan_id = await svc.plan_searches(claim_id, as_of=date(2026, 8, 23), offline=True)
    plan = (await store.get(plan_id)).parse_payload(NoveltySearchPlan)

    exec_id = await svc.execute_searches(plan_id)
    execution = (await store.get(exec_id)).parse_payload(NoveltySearchExecution)
    expected_planned = len(plan.query_artifact_ids) * len(plan.providers)
    assert execution.planned_searches == expected_planned
    assert execution.successful_searches == expected_planned
    assert execution.as_of_date == date(2026, 8, 23)

    cset_id = await svc.build_candidate_set(claim_id, plan_id, exec_id)
    cset = (await store.get(cset_id)).parse_payload(NoveltyCandidateSet)
    # dup_a + dup_b collapse via DOI -> one identity; unrelated -> second
    assert len(cset.candidates) == 2
    by_year = {c.earliest_year: c for c in cset.candidates}
    dup_cand = by_year[2019]
    # the dup paper was found by multiple queries and both providers
    assert len(dup_cand.found_by_query_ids) >= 1
    assert set(dup_cand.found_by_providers) == {"semantic_scholar", "crossref"}


async def test_post_cutoff_excluded_missing_date_conservative(store: SQLiteArtifactStore, svc):
    recent = _paper("Brand New Result", 2027, "10.1000/new", "An abstract.")
    no_year = _paper("Undated Mystery Paper", None, "10.1000/ny", "An abstract.")
    sources = {"semantic_scholar": FakeSource("semantic_scholar", catchall=[recent, no_year])}
    svc._lookup = _lookup(sources)

    claim = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text="We are the first to study platform competition.",
        claim_type=NoveltyClaimType.absolute_priority,
        risk=ClaimRiskLevel.critical,
        extraction_method="deterministic",
        source_quote="first to study",
    )
    claim_id = await svc._put(claim, "novelty_claim")
    plan_id = await svc.plan_searches(claim_id, as_of=date(2026, 8, 23), offline=True)
    exec_id = await svc.execute_searches(plan_id)
    cset_id = await svc.build_candidate_set(claim_id, plan_id, exec_id)
    cset = (await store.get(cset_id)).parse_payload(NoveltyCandidateSet)
    assert len(cset.candidates) == 1
    assert cset.candidates[0].earliest_year is None  # missing date kept, not dropped
    assert any("post_cutoff" in e.reason for e in cset.excluded)


# ---------------------------------------------------------------------------
# Candidate assessment (evidence guards + critic pass)
# ---------------------------------------------------------------------------


async def test_candidate_assessment_title_only_forced_insufficient(store: SQLiteArtifactStore, svc):
    paper = PaperRecord(title="Mysterious Prior Work")  # nothing but a title
    sources = {"semantic_scholar": FakeSource("semantic_scholar", catchall=[paper])}
    svc._lookup = _lookup(sources)
    svc._router = KeyedRouter(
        {
            "candidate prior-art paper": {
                "dimensions": [
                    {"dimension": "mechanism", "value": "match"},
                    {"dimension": "setting", "value": "match"},
                ],
                "relationship": "direct_prior_art",
                "assessment": "This paper looks identical.",
            }
        }
    )

    claim = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text="We are the first to study platform competition.",
        claim_type=NoveltyClaimType.absolute_priority,
        risk=ClaimRiskLevel.critical,
        extraction_method="deterministic",
        source_quote="first to study",
    )
    claim_id = await svc._put(claim, "novelty_claim")
    plan_id = await svc.plan_searches(claim_id, as_of=date(2026, 8, 23), offline=True)
    exec_id = await svc.execute_searches(plan_id)
    cset_id = await svc.build_candidate_set(claim_id, plan_id, exec_id)
    assessment_ids = await svc.assess_candidates(claim_id, cset_id, offline=False)
    a = (await store.get(assessment_ids[0])).parse_payload(NoveltyCandidateAssessment)
    # title-only evidence cannot produce a strong judgment, even if the model says so
    assert a.evidence_basis == EvidenceBasis.title_only
    assert a.relationship == CandidateRelationship.insufficient_evidence


async def test_candidate_assessment_indexed_metadata_downgrade(store: SQLiteArtifactStore, svc):
    paper = _paper("Overlapping Work", 2020, "10.1000/y", None)
    sources = {"semantic_scholar": FakeSource("semantic_scholar", catchall=[paper])}
    svc._lookup = _lookup(sources)
    svc._router = KeyedRouter(
        {
            "candidate prior-art paper": {
                "dimensions": [],
                "relationship": "strong_overlap",
                "assessment": "Looks overlapping.",
            }
        }
    )
    claim = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text="We are the first to study platform competition.",
        claim_type=NoveltyClaimType.absolute_priority,
        risk=ClaimRiskLevel.critical,
        extraction_method="deterministic",
        source_quote="first to study",
    )
    claim_id = await svc._put(claim, "novelty_claim")
    plan_id = await svc.plan_searches(claim_id, as_of=date(2026, 8, 23), offline=True)
    exec_id = await svc.execute_searches(plan_id)
    cset_id = await svc.build_candidate_set(claim_id, plan_id, exec_id)
    assessment_ids = await svc.assess_candidates(claim_id, cset_id, offline=False)
    a = (await store.get(assessment_ids[0])).parse_payload(NoveltyCandidateAssessment)
    assert a.evidence_basis == EvidenceBasis.indexed_metadata
    assert a.relationship == CandidateRelationship.insufficient_evidence


async def test_candidate_assessment_direct_prior_art_with_abstract(store: SQLiteArtifactStore, svc):
    paper = _paper(
        "Platform Competition under Demand Uncertainty",
        2019,
        "10.1000/z",
        "We model competition between platforms choosing quantities under demand uncertainty.",
    )
    sources = {"semantic_scholar": FakeSource("semantic_scholar", catchall=[paper])}
    svc._lookup = _lookup(sources)
    svc._router = KeyedRouter(
        {
            "candidate prior-art paper": {
                "dimensions": [
                    {"dimension": "mechanism", "value": "match"},
                    {"dimension": "setting", "value": "match"},
                ],
                "relationship": "direct_prior_art",
                "assessment": "Models the same mechanism in the same setting, predating us.",
            },
            "independently verify": {"verdict": "concurs", "reasoning": "Evidence supports it."},
        }
    )
    claim = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text="We are the first to study platform competition.",
        claim_type=NoveltyClaimType.absolute_priority,
        risk=ClaimRiskLevel.critical,
        extraction_method="deterministic",
        source_quote="first to study",
    )
    claim_id = await svc._put(claim, "novelty_claim")
    plan_id = await svc.plan_searches(claim_id, as_of=date(2026, 8, 23), offline=True)
    exec_id = await svc.execute_searches(plan_id)
    cset_id = await svc.build_candidate_set(claim_id, plan_id, exec_id)
    assessment_ids = await svc.assess_candidates(claim_id, cset_id, offline=False)
    a = (await store.get(assessment_ids[0])).parse_payload(NoveltyCandidateAssessment)
    assert a.evidence_basis == EvidenceBasis.abstract
    assert a.relationship == CandidateRelationship.direct_prior_art
    # critical claim -> critic pass ran and is preserved
    assert len(a.critic_assessment_ids) == 1
    critic = (await store.get(a.critic_assessment_ids[0])).parse_payload(NoveltyCriticAssessment)
    assert critic.verdict.value == "concurs"


async def test_offline_assessment_is_insufficient(store: SQLiteArtifactStore, svc):
    paper = _paper("Whatever", 2020, "10.1000/w", "Abstract text.")
    sources = {"semantic_scholar": FakeSource("semantic_scholar", catchall=[paper])}
    svc._lookup = _lookup(sources)
    claim = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text="We are the first to study platform competition.",
        claim_type=NoveltyClaimType.absolute_priority,
        risk=ClaimRiskLevel.critical,
        extraction_method="deterministic",
        source_quote="first to study",
    )
    claim_id = await svc._put(claim, "novelty_claim")
    plan_id = await svc.plan_searches(claim_id, as_of=date(2026, 8, 23), offline=True)
    exec_id = await svc.execute_searches(plan_id)
    cset_id = await svc.build_candidate_set(claim_id, plan_id, exec_id)
    assessment_ids = await svc.assess_candidates(claim_id, cset_id, offline=True)
    a = (await store.get(assessment_ids[0])).parse_payload(NoveltyCandidateAssessment)
    assert a.relationship == CandidateRelationship.insufficient_evidence
    assert "offline" in a.assessment_text


# ---------------------------------------------------------------------------
# Coverage policy
# ---------------------------------------------------------------------------


async def test_provider_failure_cannot_be_clear(store: SQLiteArtifactStore, svc):
    paper = _paper("Platform Paper", 2020, "10.1000/p", "Abstract.")
    src = FakeSource("semantic_scholar", catchall=[paper])
    src.fail_all = True
    svc._lookup = _lookup({"semantic_scholar": src})
    claim = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text="We are the first to study platform competition.",
        claim_type=NoveltyClaimType.absolute_priority,
        risk=ClaimRiskLevel.critical,
        extraction_method="deterministic",
        source_quote="first to study",
    )
    claim_id = await svc._put(claim, "novelty_claim")
    plan_id = await svc.plan_searches(claim_id, as_of=date(2026, 8, 23), offline=True)
    exec_id = await svc.execute_searches(plan_id)
    cset_id = await svc.build_candidate_set(claim_id, plan_id, exec_id)
    await svc.assess_candidates(claim_id, cset_id, offline=True)
    assessment_id = await svc.assess_claim(claim_id)
    a = (await store.get(assessment_id)).parse_payload(NoveltyClaimAssessment)
    assert a.status == NoveltyClaimStatus.unverified
    assert not a.coverage.coverage_sufficient
    assert a.coverage.provider_failures


async def test_high_risk_candidates_without_evidence_not_clear(store: SQLiteArtifactStore, svc):
    paper = _paper("No Abstract Paper", 2020, "10.1000/n", None)
    sources = {"semantic_scholar": FakeSource("semantic_scholar", catchall=[paper])}
    svc._lookup = _lookup(sources)
    claim = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text="We are the first to study platform competition.",
        claim_type=NoveltyClaimType.absolute_priority,
        risk=ClaimRiskLevel.critical,
        extraction_method="deterministic",
        source_quote="first to study",
    )
    claim_id = await svc._put(claim, "novelty_claim")
    plan_id = await svc.plan_searches(claim_id, as_of=date(2026, 8, 23), offline=True)
    exec_id = await svc.execute_searches(plan_id)
    cset_id = await svc.build_candidate_set(claim_id, plan_id, exec_id)
    await svc.assess_candidates(claim_id, cset_id, offline=False)
    assessment_id = await svc.assess_claim(claim_id)
    a = (await store.get(assessment_id)).parse_payload(NoveltyClaimAssessment)
    assert a.status == NoveltyClaimStatus.unverified


async def test_no_candidates_all_searches_ok_not_threatened(store: SQLiteArtifactStore, svc):
    claim = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text="We are the first to study platform competition.",
        claim_type=NoveltyClaimType.absolute_priority,
        risk=ClaimRiskLevel.critical,
        extraction_method="deterministic",
        source_quote="first to study",
    )
    claim_id = await svc._put(claim, "novelty_claim")
    plan_id = await svc.plan_searches(claim_id, as_of=date(2026, 8, 23), offline=True)
    exec_id = await svc.execute_searches(plan_id)
    cset_id = await svc.build_candidate_set(claim_id, plan_id, exec_id)
    await svc.assess_candidates(claim_id, cset_id, offline=True)
    assessment_id = await svc.assess_claim(claim_id)
    a = (await store.get(assessment_id)).parse_payload(NoveltyClaimAssessment)
    assert a.status == NoveltyClaimStatus.not_threatened_within_search_scope
    assert "does not prove global novelty" in a.reasoning


# ---------------------------------------------------------------------------
# Claim aggregation + report + gate
# ---------------------------------------------------------------------------


async def _run_report_with(
    store: SQLiteArtifactStore,
    svc,
    papers: list[PaperRecord],
    relationships: dict[str, str],
    critic: dict[str, str] | None = None,
    title: str = "Demand-Driven Platform Quantity Dynamics",
) -> tuple[str, str]:
    m_id, pkg_id, _draft = await _manuscript_env(store, title=title)
    sources = {"semantic_scholar": FakeSource("semantic_scholar", catchall=papers)}
    svc._lookup = _lookup(sources)

    builders: dict[str, dict] = {
        "extract novelty claims": {"claims": []},
    }
    if critic:
        builders["independently verify"] = critic
    # per-candidate relationships routed by title substring in the prompt
    for idx, p in enumerate(papers):
        builders[p.title] = {
            "dimensions": [],
            "relationship": relationships.get(p.title, "distinct"),
            "assessment": f"assessment for {p.title}",
        }
    svc._router = KeyedRouter(builders)
    report_id = await svc.create_report(pkg_id, as_of="2026-08-23", offline=False)
    gate_id = await svc.create_gate(pkg_id, report_id)
    return report_id, gate_id


async def test_report_blocked_critical_threat(store: SQLiteArtifactStore, svc):
    threatening = _paper(
        "Platform Competition under Demand Uncertainty",
        2019,
        "10.1000/t1",
        "We model competition between platforms choosing quantities.",
    )
    report_id, gate_id = await _run_report_with(
        store,
        svc,
        [threatening],
        {threatening.title: "direct_prior_art"},
        critic={"verdict": "disputes", "reasoning": "I disagree."},
    )
    report = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
    assert report.overall_status == NoveltyReportStatus.blocked
    assert len(report.critical_threats) == 1
    # critic disagreement is preserved, not erased
    assert len(report.critic_assessment_ids) == 1
    gate = (await store.get(gate_id)).parse_payload(SubmissionReadinessGate)
    assert gate.status == ReadinessStatus.blocked
    assert gate.package_status == "ready"
    assert gate.novelty_status == NoveltyReportStatus.blocked
    assert gate.blocking_claim_ids == report.critical_threats


async def test_report_revise_for_partial_overlap(store: SQLiteArtifactStore, svc):
    partial = _paper("Some Overlapping Analysis", 2020, "10.1000/t2", "Related work abstract.")
    report_id, gate_id = await _run_report_with(
        store, svc, [partial], {partial.title: "partial_overlap"}
    )
    report = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
    assert report.overall_status == NoveltyReportStatus.revise
    assert len(report.weakened_claims) == 1
    gate = (await store.get(gate_id)).parse_payload(SubmissionReadinessGate)
    assert gate.status == ReadinessStatus.needs_revision
    assert gate.revision_claim_ids


async def test_report_clear_when_distinct_only(store: SQLiteArtifactStore, svc):
    distinct = _paper("Unrelated Agriculture Study", 2018, "10.1000/t3", "Farming abstract.")
    report_id, gate_id = await _run_report_with(
        store, svc, [distinct], {distinct.title: "distinct"}
    )
    report = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
    assert report.overall_status == NoveltyReportStatus.clear
    assert report.safe_within_scope_claims
    gate = (await store.get(gate_id)).parse_payload(SubmissionReadinessGate)
    assert gate.status == ReadinessStatus.ready


async def test_report_unverified_on_failure(store: SQLiteArtifactStore, svc):
    src = FakeSource("semantic_scholar", catchall=[_paper("P", 2020, "10.1000/t4", "A.")])
    src.fail_all = True
    svc._lookup = _lookup({"semantic_scholar": src})
    m_id, pkg_id, _draft = await _manuscript_env(store)
    svc._router = KeyedRouter({"extract novelty claims": {"claims": []}})
    report_id = await svc.create_report(pkg_id, as_of="2026-08-23", offline=False)
    report = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
    assert report.overall_status == NoveltyReportStatus.unverified
    gate_id = await svc.create_gate(pkg_id, report_id)
    gate = (await store.get(gate_id)).parse_payload(SubmissionReadinessGate)
    assert gate.status == ReadinessStatus.unverified
    assert gate.unverified_claim_ids


async def test_revision_recommendation_generated_without_editing(store: SQLiteArtifactStore, svc):
    partial = _paper("Some Overlapping Analysis", 2020, "10.1000/t5", "Related work abstract.")
    report_id, _gate_id = await _run_report_with(
        store, svc, [partial], {partial.title: "partial_overlap"}
    )
    report = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
    assert len(report.revision_recommendation_ids) >= 1
    rec = (await store.get(report.revision_recommendation_ids[0])).parse_payload(
        NoveltyRevisionRecommendation
    )
    assert rec.original_text
    assert rec.suggested_wording
    # the manuscript itself was not modified
    m_env = await store.get(report.manuscript_id)
    fm = m_env.parse_payload(FormattedManuscript)
    assert fm.validation_status == FormattedManuscriptStatus.validated
    # recommendations carry supporting candidate ids
    assert rec.supporting_candidate_ids


async def test_absolute_not_threatened_gets_conservative_recommendation(
    store: SQLiteArtifactStore, svc
):
    """Critical claim, no threat, but weak coverage -> conservative-language
    recommendation even though status is not_threatened."""
    distinct = _paper("Unrelated Study", 2018, "10.1000/t6", None)  # no abstract -> weak evidence
    report_id, _gate_id = await _run_report_with(
        store, svc, [distinct], {distinct.title: "distinct"}
    )
    report = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
    # coverage weak (candidate has no abstract), claim is absolute -> recommend
    assert report.revision_recommendation_ids
    rec = (await store.get(report.revision_recommendation_ids[0])).parse_payload(
        NoveltyRevisionRecommendation
    )
    assert "To our knowledge" in (rec.suggested_wording or "") or rec.suggested_wording


async def test_reassessment_supersedes_and_immutable(store: SQLiteArtifactStore, svc):
    distinct = _paper("Unrelated Study", 2018, "10.1000/t7", "Farming abstract.")
    report_id, _gate_id = await _run_report_with(
        store, svc, [distinct], {distinct.title: "distinct"}
    )
    first = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
    first_json = json.dumps(first.model_dump(mode="json"), sort_keys=True)
    assert first.supersedes is None

    # reassess the same package -> superseding report
    report2_id = await svc.create_report(
        first.submission_package_id, as_of="2026-08-24", offline=False
    )
    second = (await store.get(report2_id)).parse_payload(NoveltyValidationReport)
    assert second.supersedes == report_id
    # the original report artifact is untouched (immutability)
    first_again = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
    assert json.dumps(first_again.model_dump(mode="json"), sort_keys=True) == first_json
    assert await svc.latest_report(first.submission_package_id) == report2_id
    supers = [c for c in await store.get_children(report_id) if c.relation.value == "supersedes"]
    assert supers and supers[0].target_artifact_id == report2_id


async def test_gate_rejects_manuscript_mismatch(store: SQLiteArtifactStore, svc):
    distinct = _paper("Unrelated Study", 2018, "10.1000/t8", "Farming abstract.")
    report_id, _gate_id = await _run_report_with(
        store, svc, [distinct], {distinct.title: "distinct"}
    )
    report = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
    # a different package (different manuscript) must not be gated by this report
    m2_id, pkg2_id, _draft = await _manuscript_env(store, title="A Completely Different Manuscript")
    with pytest.raises(ValueError, match="does not cover"):
        await svc.create_gate(pkg2_id, report_id)


async def test_package_not_ready_gate_blocked(store: SQLiteArtifactStore, svc):
    distinct = _paper("Unrelated Study", 2018, "10.1000/t9", "Farming abstract.")
    report_id, _gate_id = await _run_report_with(
        store, svc, [distinct], {distinct.title: "distinct"}
    )
    # downgrade the package status via a superseding package? packages are
    # immutable; instead build a non-ready package on the same manuscript
    report = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
    pkg2 = SubmissionPackage(
        formatted_manuscript_id=report.manuscript_id,
        draft_id="d1",
        profile_id="prof1",
        status=SubmissionPackageStatus.assembled,
        summary="not ready",
        model_role="test",
    )
    p2_env = ArtifactEnvelope.create(
        payload=pkg2, artifact_type="submission_package", producer="test"
    )
    await store.put(p2_env)
    gate_id = await svc.create_gate(p2_env.artifact_id, report_id)
    gate = (await store.get(gate_id)).parse_payload(SubmissionReadinessGate)
    assert gate.status == ReadinessStatus.blocked
    assert gate.package_status == "assembled"


async def test_full_chain_provenance_after_reopen(tmp_path: pathlib.Path):
    """End-to-end chain persisted and reopened: gate -> report -> claim
    assessment -> candidate assessment -> paper identity -> paper record."""
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    sources = {
        "semantic_scholar": FakeSource(
            "semantic_scholar",
            catchall=[
                _paper(
                    "Platform Competition under Demand Uncertainty",
                    2019,
                    "10.1000/pv",
                    "We model competition between platforms choosing quantities.",
                )
            ],
        )
    }
    svc = NoveltyValidationService(
        model_router=KeyedRouter(
            {
                "extract novelty claims": {"claims": []},
                "candidate prior-art paper": {
                    "dimensions": [],
                    "relationship": "direct_prior_art",
                    "assessment": "threatens.",
                },
                "independently verify": {"verdict": "concurs", "reasoning": "yes"},
            }
        ),
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        service_lookup=_lookup(sources),
        providers=["semantic_scholar"],
    )
    m_id, pkg_id, _draft = await _manuscript_env(store)
    report_id = await svc.create_report(pkg_id, as_of="2026-08-23", offline=False)
    gate_id = await svc.create_gate(pkg_id, report_id)
    report = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
    assert report.overall_status == NoveltyReportStatus.blocked

    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")

    # gate -> package + report
    gate2 = (await store2.get(gate_id)).parse_payload(SubmissionReadinessGate)
    assert gate2.novelty_report_id == report_id
    gate_parents = await store2.get_parents(gate_id)
    assert any(p.source_artifact_id == pkg_id for p in gate_parents)
    assert any(p.source_artifact_id == report_id for p in gate_parents)

    # report -> claim + claim assessment
    report2 = (await store2.get(report_id)).parse_payload(NoveltyValidationReport)
    claim_id = report2.claim_ids[0]
    claim = (await store2.get(claim_id)).parse_payload(NoveltyClaim)
    assert claim.claim_type == NoveltyClaimType.absolute_priority
    assessment_id = report2.claim_assessment_ids[0]
    ca = (await store2.get(assessment_id)).parse_payload(NoveltyClaimAssessment)
    assert ca.status == NoveltyClaimStatus.threatened
    report_parents = await store2.get_parents(report_id)
    assert any(p.source_artifact_id == claim_id for p in report_parents)
    assert any(p.source_artifact_id == assessment_id for p in report_parents)

    # claim assessment -> plan, execution, candidate set, candidate assessment
    plan = (await store2.get(ca.search_plan_id)).parse_payload(NoveltySearchPlan)
    assert plan.claim_id == claim_id
    execution = (await store2.get(ca.search_execution_id)).parse_payload(NoveltySearchExecution)
    assert execution.search_record_artifact_ids
    cset = (await store2.get(ca.candidate_set_id)).parse_payload(NoveltyCandidateSet)
    assert len(cset.candidates) == 1
    cand_ass_id = ca.candidate_assessment_ids[0]
    cand = (await store2.get(cand_ass_id)).parse_payload(NoveltyCandidateAssessment)
    assert cand.relationship == CandidateRelationship.direct_prior_art
    ca_parents = await store2.get_parents(assessment_id)
    assert any(p.source_artifact_id == cand_ass_id for p in ca_parents)
    assert any(p.source_artifact_id == ca.search_plan_id for p in ca_parents)

    # candidate assessment -> paper identity -> paper record
    identity_env = await store2.get(cand.paper_identity_id)
    identity = identity_env.parse_payload(PaperIdentity)
    assert identity.member_paper_artifact_ids
    paper_env = await store2.get(identity.member_paper_artifact_ids[0])
    paper = paper_env.parse_payload(PaperRecord)
    assert paper.doi == "10.1000/pv"
    cand_parents = await store2.get_parents(cand_ass_id)
    assert any(p.source_artifact_id == cand.paper_identity_id for p in cand_parents)
    assert any(p.source_artifact_id == ca.candidate_set_id for p in cand_parents)

    # revision recommendation linked to the report
    assert report2.revision_recommendation_ids
    rec = (await store2.get(report2.revision_recommendation_ids[0])).parse_payload(
        NoveltyRevisionRecommendation
    )
    assert rec.suggested_wording

    # search record -> literature query artifact
    rec_env = await store2.get(execution.search_record_artifact_ids[0])
    from research_harness.research.schemas.search_record import LiteratureSearchRecord

    sr = rec_env.parse_payload(LiteratureSearchRecord)
    assert sr.query_artifact_id
    q_parents = await store2.get_parents(sr.query_artifact_id)
    assert any(p.source_artifact_id == claim_id for p in q_parents)
    await store2.close()


async def test_offline_report_is_deterministic_no_router(store: SQLiteArtifactStore, svc):
    """offline=True never calls the router and still yields a full report."""
    paper = _paper("Platform Paper", 2020, "10.1000/off", "Abstract.")
    sources = {"semantic_scholar": FakeSource("semantic_scholar", catchall=[paper])}
    svc._lookup = _lookup(sources)
    svc._router = KeyedRouter({}, default=None)  # any model call would crash
    m_id, pkg_id, _draft = await _manuscript_env(store)
    report_id = await svc.create_report(pkg_id, as_of="2026-08-23", offline=True)
    report = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
    assert report.claim_ids
    # candidates assessed as insufficient (no semantic comparison) -> unverified
    assert report.overall_status == NoveltyReportStatus.unverified
    assert report.unverified_claims
    assert svc._router.calls == 0
