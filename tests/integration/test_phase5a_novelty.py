"""Phase 5A offline integration — full novelty-validation chain with fake
model + fake literature providers, no network:

existing SubmissionPackage (built by the real Phase 4C formatter)
-> extract novelty claims -> search plans -> fake providers
-> PaperIdentity deduplication -> candidate prior-art assessments
-> claim assessments -> NoveltyValidationReport -> SubmissionReadinessGate
-> persist -> close/reopen -> verify the provenance graph.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.literature import LiteratureSearchHit, LiteratureSearchPage
from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.literature.identity_resolver.plugin import (
    PaperIdentityResolverService,
)
from research_harness.plugins.literature.ingestion.plugin import LiteratureIngestor
from research_harness.plugins.research.novelty_validator.plugin import NoveltyValidationService
from research_harness.plugins.research.publication_formatter.plugin import (
    PublicationFormatterService,
)
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.plugins.storage.blobs_filesystem.plugin import FilesystemBlobStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
from research_harness.research.schemas.manuscript import (
    CitationReference,
    ManuscriptClaim,
    ManuscriptDraft,
    ManuscriptSection,
    ManuscriptSectionId,
    SectionArtifactType,
)
from research_harness.research.schemas.novelty import (
    CandidateRelationship,
    NoveltyCandidateAssessment,
    NoveltyCandidateSet,
    NoveltyClaim,
    NoveltyClaimAssessment,
    NoveltyClaimStatus,
    NoveltyClaimType,
    NoveltySearchExecution,
    NoveltySearchPlan,
    NoveltyValidationReport,
    ReadinessStatus,
    SubmissionReadinessGate,
)
from research_harness.research.schemas.paper import Author, PaperRecord
from research_harness.research.schemas.proposition import (
    Proposition,
    PropositionClaimType,
    PropositionVerification,
    PropositionVerificationStatus,
)
from research_harness.research.schemas.publication import (
    FormattedManuscript,
    FormattedManuscriptStatus,
    SubmissionPackage,
)


class FakeRouter:
    """Routes by marker in the joined prompt text."""

    def __init__(self, builders: dict[str, dict]):
        self.builders = builders
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
        raise AssertionError(f"no builder for prompt: {text[:200]}")


class FakeSource:
    def __init__(self, provider_name: str, papers: list[PaperRecord]):
        self.provider_name = provider_name
        self.papers = papers

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
        raise NotImplementedError


def _paper(title: str, year: int, doi: str, abstract: str) -> PaperRecord:
    return PaperRecord(
        title=title,
        authors=[Author(name="Smith, Jane")],
        year=year,
        venue="Journal of Platform Studies",
        doi=doi,
        abstract=abstract,
    )


@pytest.mark.asyncio
async def test_phase5a_full_chain(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")

    # ------------------------------------------------------------------
    # fixture: a real Phase 4C SubmissionPackage (ready)
    # ------------------------------------------------------------------
    rec = _paper("Platform Competition and Demand", 2021, "10.1000/abc", "An abstract.")
    rec_env = ArtifactEnvelope.create(payload=rec, artifact_type="paper_record", producer="test")
    await store.put(rec_env)
    identity = PaperIdentity(
        member_paper_artifact_ids=[rec_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.manual,
        resolution_evidence=[],
    )
    id_env = ArtifactEnvelope.create(
        payload=identity, artifact_type="paper_identity", producer="test"
    )
    await store.put(id_env)

    prop = Proposition(
        model_id="model1",
        equilibrium_candidate_id="cand1",
        comparative_statics_analysis_id="cs-a",
        statement="Increasing demand raises equilibrium quantity.",
        claim_type=PropositionClaimType.monotonicity,
        outcome_variable="q1",
        parameter="a",
        expected_sign="positive",
        conditions=["a > c"],
        supporting_static_ids=[],
        status="candidate",
    )
    prop_env = ArtifactEnvelope.create(payload=prop, artifact_type="proposition", producer="test")
    await store.put(prop_env)
    await store.put(
        ArtifactEnvelope.create(
            payload=PropositionVerification(
                proposition_id=prop_env.artifact_id,
                model_id="model1",
                status=PropositionVerificationStatus.verified,
                checks=[],
            ),
            artifact_type="proposition_verification",
            producer="test",
        )
    )

    s1 = ManuscriptSection(
        outline_id="o1",
        section_id=ManuscriptSectionId.introduction,
        title="Introduction",
        body=(
            "We are the first study to model demand-driven platform dynamics. "
            "[CITE:c1] also established related results."
        ),
        claims=[
            ManuscriptClaim(
                text="Demand growth raises equilibrium quantities (a > c).",
                grounding_type=SectionArtifactType.verified_proposition,
                grounding_artifact_id=prop_env.artifact_id,
                citation_id=None,
                conditions=["a > c"],
            )
        ],
        citations=[
            CitationReference(
                citation_id="c1",
                paper_identity_id=id_env.artifact_id,
                evidence_item_id="ev1",
                page_locator="p. 12",
            )
        ],
    )
    e1 = ArtifactEnvelope.create(payload=s1, artifact_type="manuscript_section", producer="test")
    await store.put(e1)
    s2 = ManuscriptSection(
        outline_id="o1",
        section_id=ManuscriptSectionId.conclusion,
        title="Conclusion",
        body="Demand dynamics matter for platforms. [CITE:c1]",
        claims=[],
        citations=[
            CitationReference(
                citation_id="c1",
                paper_identity_id=id_env.artifact_id,
                evidence_item_id="ev1",
                page_locator=None,
            )
        ],
    )
    e2 = ArtifactEnvelope.create(payload=s2, artifact_type="manuscript_section", producer="test")
    await store.put(e2)
    draft = ManuscriptDraft(
        outline_id="o1",
        results_package_id="pkg1",
        title="Demand-Driven Platform Quantity Dynamics",
        version=1,
        section_ids=[e1.artifact_id, e2.artifact_id],
        status="drafted",
        summary="2 sections",
        model_role="reasoning",
    )
    d_env = ArtifactEnvelope.create(
        payload=draft, artifact_type="manuscript_draft", producer="test"
    )
    await store.put(d_env)

    p4c_router = FakeRouter(
        {
            "Write the front matter": {
                "abstract": "We study demand-driven platform quantity dynamics.",
                "keywords": ["platforms", "demand"],
            },
            "Write a cover letter": {
                "opening": "Dear Editor,",
                "contribution_summary": ["We characterize demand-driven platform dynamics."],
                "journal_fit": "Fits the journal scope.",
                "closing": "Sincerely,",
            },
        }
    )

    formatter = PublicationFormatterService(
        model_router=p4c_router, artifact_store=store, blob_store=blobs
    )
    profile_id = await formatter.create_profile(
        name="Generic IS Journal",
        required_sections=["introduction", "conclusion"],
        abstract_max_words=100,
    )
    m_id = await formatter.format(d_env.artifact_id, profile_id)
    leaf, passed = await formatter.validate(m_id)
    assert passed is True
    pkg_id = await formatter.package(leaf)
    pkg = (await store.get(pkg_id)).parse_payload(SubmissionPackage)
    assert pkg.status.value == "ready"

    # ------------------------------------------------------------------
    # Phase 5A: fake prior-art corpus: one threatening, one partial
    # overlap, one unrelated, plus a duplicate of the threatening paper
    # (same DOI, found through another query) to exercise deduplication
    # ------------------------------------------------------------------
    threatening = _paper(
        "Platform Competition under Demand Uncertainty",
        2019,
        "10.1000/threat",
        "We model quantity competition between platforms under demand uncertainty "
        "and characterize the resulting equilibrium.",
    )
    threatening_dup = _paper(
        "Platform Competition under Demand Uncertainty",
        2019,
        "10.1000/threat",  # same DOI -> must collapse via PaperIdentity
        None,
    )
    partial = _paper(
        "Demand Shocks in Platform Markets",
        2020,
        "10.1000/partial",
        "We study how demand shocks affect platform pricing strategies.",
    )
    unrelated = _paper(
        "Crop Yields in Arid Agriculture",
        2018,
        "10.1000/unrelated",
        "An empirical study of farming yields.",
    )
    sources = {
        "semantic_scholar": FakeSource("semantic_scholar", [threatening, partial, unrelated]),
        "crossref": FakeSource("crossref", [threatening_dup, partial]),
    }

    novelty_router = FakeRouter(
        {
            "extract novelty claims": {"claims": []},
            "bounded novelty search queries": {
                "queries": [
                    {
                        "query": "platform quantity competition demand uncertainty",
                        "query_type": "mechanism",
                        "synonyms": [],
                    }
                ]
            },
            threatening.title: {
                "dimensions": [
                    {"dimension": "mechanism", "value": "match"},
                    {"dimension": "setting", "value": "match"},
                    {"dimension": "theoretical_result", "value": "match"},
                ],
                "relationship": "direct_prior_art",
                "assessment": "Models the same mechanism and setting, predating us.",
            },
            partial.title: {
                "dimensions": [
                    {"dimension": "mechanism", "value": "partial_match"},
                    {"dimension": "setting", "value": "match"},
                ],
                "relationship": "partial_overlap",
                "assessment": "Related setting but different strategic decision.",
            },
            unrelated.title: {
                "dimensions": [{"dimension": "focal_phenomenon", "value": "different"}],
                "relationship": "distinct",
                "assessment": "Unrelated field.",
            },
            "independently verify": {"verdict": "concurs", "reasoning": "Evidence agrees."},
            "conservative rewording": {
                "suggested_scope_change": "limit to demand-driven quantity dynamics",
                "suggested_wording": "To our knowledge, this study is among the first "
                "to model demand-driven platform quantity dynamics.",
            },
        }
    )
    svc = NoveltyValidationService(
        model_router=novelty_router,
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        service_lookup=lambda name: sources[name.split(".")[-1]],
        providers=["semantic_scholar", "crossref"],
        max_results_per_query=10,
    )

    report_id = await svc.create_report(pkg_id, as_of="2026-08-23", offline=False)
    gate_id = await svc.create_gate(pkg_id, report_id)

    # ------------------------------------------------------------------
    # deterministic expectations
    # ------------------------------------------------------------------
    report = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
    assert report.submission_package_id == pkg_id
    assert report.manuscript_id == leaf
    assert report.manuscript_content_hash
    assert report.as_of_date.isoformat() == "2026-08-23"
    assert report.claim_ids
    claims = [(await store.get(cid)).parse_payload(NoveltyClaim) for cid in report.claim_ids]
    assert any(c.claim_type == NoveltyClaimType.absolute_priority for c in claims)
    # critical threat -> blocked
    assert report.overall_status.value == "blocked"
    assert report.critical_threats
    assert report.revision_recommendation_ids
    # one claim assessment, one candidate set, one search execution
    assert len(report.claim_assessment_ids) == 1
    assert len(report.search_execution_ids) == 1
    assert len(report.candidate_set_ids) == 1

    claim_ass = (await store.get(report.claim_assessment_ids[0])).parse_payload(
        NoveltyClaimAssessment
    )
    assert claim_ass.status == NoveltyClaimStatus.threatened
    # 4 raw paper records (with dup) -> 3 identities
    cset = (await store.get(claim_ass.candidate_set_id)).parse_payload(NoveltyCandidateSet)
    assert len(cset.candidates) == 3
    # the deduplicated threatening identity was found via both providers
    by_year = {c.earliest_year: c for c in cset.candidates}
    threat_cand = by_year[2019]
    assert set(threat_cand.found_by_providers) == {"semantic_scholar", "crossref"}

    cand_asses = [
        (await store.get(aid)).parse_payload(NoveltyCandidateAssessment)
        for aid in claim_ass.candidate_assessment_ids
    ]
    rels = {a.relationship for a in cand_asses}
    assert CandidateRelationship.direct_prior_art in rels
    assert CandidateRelationship.partial_overlap in rels
    assert CandidateRelationship.distinct in rels
    # the direct-prior-art assessment has a critic pass (critical claim)
    strong = next(a for a in cand_asses if a.relationship == CandidateRelationship.direct_prior_art)
    assert len(strong.critic_assessment_ids) == 1
    assert report.critic_assessment_ids

    # coverage: all planned searches succeeded
    execution = (await store.get(claim_ass.search_execution_id)).parse_payload(
        NoveltySearchExecution
    )
    assert execution.successful_searches == execution.planned_searches
    assert execution.as_of_date.isoformat() == "2026-08-23"
    plan = (await store.get(claim_ass.search_plan_id)).parse_payload(NoveltySearchPlan)
    assert plan.date_cutoff.isoformat() == "2026-08-23"

    gate = (await store.get(gate_id)).parse_payload(SubmissionReadinessGate)
    assert gate.status == ReadinessStatus.blocked
    assert gate.novelty_report_id == report_id
    assert gate.submission_package_id == pkg_id
    assert gate.package_status == "ready"

    # ------------------------------------------------------------------
    # provenance graph after reopen
    # ------------------------------------------------------------------
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")

    # gate -> package + report
    assert any(p.source_artifact_id == pkg_id for p in await store2.get_parents(gate_id))
    assert any(p.source_artifact_id == report_id for p in await store2.get_parents(gate_id))

    # report -> claim + claim assessment (+ manuscript + package)
    report2 = (await store2.get(report_id)).parse_payload(NoveltyValidationReport)
    report_parents = await store2.get_parents(report_id)
    assert any(p.source_artifact_id == pkg_id for p in report_parents)
    assert any(p.source_artifact_id == leaf for p in report_parents)
    claim_id = report2.claim_ids[0]
    assert any(p.source_artifact_id == claim_id for p in report_parents)
    assert any(p.source_artifact_id == report2.claim_assessment_ids[0] for p in report_parents)

    # claim -> manuscript
    assert any(p.source_artifact_id == leaf for p in await store2.get_parents(claim_id))

    # claim assessment -> plan, execution, candidate set, candidate assessments
    ca2 = (await store2.get(report2.claim_assessment_ids[0])).parse_payload(NoveltyClaimAssessment)
    ca_parents = await store2.get_parents(report2.claim_assessment_ids[0])
    for upstream in (
        claim_id,
        ca2.search_plan_id,
        ca2.search_execution_id,
        ca2.candidate_set_id,
    ):
        assert any(p.source_artifact_id == upstream for p in ca_parents), upstream

    # plan -> claim; execution -> plan + search records; search record -> query
    assert any(
        p.source_artifact_id == claim_id for p in await store2.get_parents(ca2.search_plan_id)
    )
    exec_parents = await store2.get_parents(ca2.search_execution_id)
    assert any(p.source_artifact_id == ca2.search_plan_id for p in exec_parents)
    search_rec = await store2.get(execution.search_record_artifact_ids[0])
    from research_harness.research.schemas.search_record import LiteratureSearchRecord

    srec = search_rec.parse_payload(LiteratureSearchRecord)
    assert any(
        p.source_artifact_id == srec.query_artifact_id
        for p in await store2.get_parents(search_rec.artifact_id)
    )
    assert any(
        p.source_artifact_id == claim_id for p in await store2.get_parents(srec.query_artifact_id)
    )

    # candidate assessment -> candidate set + paper identity (+ evidence)
    cand_payloads = [
        (await store2.get(aid)).parse_payload(NoveltyCandidateAssessment)
        for aid in ca2.candidate_assessment_ids
    ]
    strong2 = next(
        a for a in cand_payloads if a.relationship == CandidateRelationship.direct_prior_art
    )
    cand_parents = await store2.get_parents(strong2.id)
    assert any(p.source_artifact_id == ca2.candidate_set_id for p in cand_parents)
    assert any(p.source_artifact_id == strong2.paper_identity_id for p in cand_parents)
    assert strong2.evidence_artifact_ids  # abstracts used as evidence

    # critic assessment -> candidate assessment + identity
    critic_id = strong2.critic_assessment_ids[0]
    critic_parents = await store2.get_parents(critic_id)
    assert any(p.source_artifact_id == strong2.id for p in critic_parents)
    assert any(p.source_artifact_id == strong2.paper_identity_id for p in critic_parents)

    # paper identity -> paper records (all dup copies collapsed under one DOI)
    identity_env = await store2.get(strong2.paper_identity_id)
    identity = identity_env.parse_payload(PaperIdentity)
    member_recs = [
        (await store2.get(pid)).parse_payload(PaperRecord)
        for pid in identity.member_paper_artifact_ids
    ]
    assert len(member_recs) >= 2  # threatening paper ingested via several queries/providers
    assert {m.doi for m in member_recs} == {"10.1000/threat"}
    assert {m.title for m in member_recs} == {threatening.title}

    # revision recommendation -> claim + supporting candidates
    rec_id = report2.revision_recommendation_ids[0]
    rec_parents = await store2.get_parents(rec_id)
    assert any(p.source_artifact_id == claim_id for p in rec_parents)
    assert any(p.source_artifact_id == strong2.id for p in rec_parents)

    # formatted manuscript remains validated (package semantics unchanged)
    fm2 = (await store2.get(leaf)).parse_payload(FormattedManuscript)
    assert fm2.validation_status == FormattedManuscriptStatus.validated
    pkg2 = (await store2.get(pkg_id)).parse_payload(SubmissionPackage)
    assert pkg2.status.value == "ready"

    await store2.close()
