"""Phase 5B offline integration — incremental novelty revalidation:

Phase 5A report -> supersede manuscript (draft V2, one novelty claim modified)
-> incremental revalidation -> reuse unaffected claim -> revalidate changed
claim -> new report -> new gate -> reopen -> verify provenance.

No network, no paid models (fake router + fake providers).
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
    ManuscriptDraft,
    ManuscriptSection,
    ManuscriptSectionId,
)
from research_harness.research.schemas.novelty import (
    ManuscriptChangeSet,
    NoveltyClaim,
    NoveltyClaimAssessment,
    NoveltyClaimStatus,
    NoveltyClaimType,
    NoveltyRevalidationExecution,
    NoveltyRevalidationPlan,
    NoveltyValidationReport,
    ReadinessStatus,
    StalenessStatus,
    SubmissionReadinessGate,
)
from research_harness.research.schemas.paper import Author, PaperRecord
from research_harness.research.schemas.publication import (
    SubmissionPackage,
    SubmissionPackageStatus,
)

CLAIM_A = "We are the first study to model demand-driven platform dynamics."
CLAIM_B = "To our knowledge, no prior research has examined this mechanism."


class FakeRouter:
    def __init__(self, builders: dict[str, dict]):
        self.builders = builders

    async def complete(self, role, request):
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
    def __init__(self, provider_name: str, hits_by_query: dict[str, list[PaperRecord]]):
        self.provider_name = provider_name
        self.hits_by_query = hits_by_query

    async def search(self, request):
        hits = self.hits_by_query.get(request.query, [])
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


def _paper(title: str, year: int, doi: str, abstract: str) -> PaperRecord:
    return PaperRecord(
        title=title,
        authors=[Author(name="Smith, Jane")],
        year=year,
        venue="Journal of Platform Studies",
        doi=doi,
        abstract=abstract,
    )


async def _build_phase4_package(
    store: SQLiteArtifactStore,
    blobs: FilesystemBlobStore,
    router: FakeRouter,
    *,
    draft: ManuscriptDraft | None = None,
    intro_body: str = CLAIM_A,
) -> tuple[str, str, str]:
    """Manuscript sections -> draft -> Phase 4C package. Returns
    (draft_id, manuscript_id, package_id)."""
    s1 = ManuscriptSection(
        outline_id="o1",
        section_id=ManuscriptSectionId.introduction,
        title="Introduction",
        body=intro_body,
        claims=[],
        citations=[],
    )
    e1 = ArtifactEnvelope.create(payload=s1, artifact_type="manuscript_section", producer="test")
    await store.put(e1)
    s2 = ManuscriptSection(
        outline_id="o1",
        section_id=ManuscriptSectionId.conclusion,
        title="Conclusion",
        body=CLAIM_B,
        claims=[],
        citations=[],
    )
    e2 = ArtifactEnvelope.create(payload=s2, artifact_type="manuscript_section", producer="test")
    await store.put(e2)
    if draft is None:
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
    else:
        draft = draft.model_copy(update={"section_ids": [e1.artifact_id, e2.artifact_id]})
    d_env = ArtifactEnvelope.create(
        payload=draft, artifact_type="manuscript_draft", producer="test"
    )
    await store.put(d_env)

    formatter = PublicationFormatterService(
        model_router=router, artifact_store=store, blob_store=blobs
    )
    profile_id = await formatter.create_profile(
        name="Generic IS Journal",
        required_sections=["introduction", "conclusion"],
        abstract_max_words=100,
        abstract_required=False,
    )
    m_id = await formatter.format(d_env.artifact_id, profile_id)
    leaf, passed = await formatter.validate(m_id)
    assert passed is True
    pkg_id = await formatter.package(leaf)
    return d_env.artifact_id, leaf, pkg_id


@pytest.mark.asyncio
async def test_phase5b_incremental_revalidation(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")

    # a paper identity so 5A candidate resolution works
    rec = _paper("Platform Competition and Demand", 2021, "10.1000/abc", "An abstract.")
    rec_env = ArtifactEnvelope.create(payload=rec, artifact_type="paper_record", producer="test")
    await store.put(rec_env)
    id_env = ArtifactEnvelope.create(
        payload=PaperIdentity(
            member_paper_artifact_ids=[rec_env.artifact_id],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.manual,
            resolution_evidence=[],
        ),
        artifact_type="paper_identity",
        producer="test",
    )
    await store.put(id_env)

    # ---- Phase 5A report on V1 ------------------------------------------
    threatening = _paper(
        "Platform Competition under Demand Uncertainty",
        2019,
        "10.1000/threat",
        "We model quantity competition between platforms under demand uncertainty.",
    )
    modified_a = CLAIM_A.replace("model", "analyze")
    p4c_router = FakeRouter(
        {
            "Write the front matter": {"abstract": "", "keywords": []},
            "Write a cover letter": {
                "opening": "Dear Editor,",
                "contribution_summary": ["x"],
                "journal_fit": "y",
                "closing": "z",
            },
        }
    )
    draft1, manuscript1, package1 = await _build_phase4_package(store, blobs, p4c_router)

    novelty_router = FakeRouter(
        {
            "extract novelty claims": {"claims": []},
            threatening.title: {
                "dimensions": [],
                "relationship": "direct_prior_art",
                "assessment": "predates us",
            },
            "independently verify": {"verdict": "concurs", "reasoning": "yes"},
        }
    )
    sources = {
        "semantic_scholar": FakeSource("semantic_scholar", {f'"{modified_a}"': [threatening]}),
    }
    svc = NoveltyValidationService(
        model_router=novelty_router,
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        service_lookup=lambda name: sources[name.split(".")[-1]],
        providers=["semantic_scholar"],
    )
    report1 = await svc.create_report(package1, as_of="2026-08-23", offline=False)
    gate1 = await svc.create_gate(package1, report1)
    r1 = (await store.get(report1)).parse_payload(NoveltyValidationReport)
    # no threat surfaces for V1 -> clear
    assert r1.overall_status.value == "clear"
    assert await svc.staleness(report1) == StalenessStatus.current
    assert len(r1.claim_ids) == 2
    r1_assessments = set(r1.claim_assessment_ids)

    # ---- V2: supersede draft, modify claim A ------------------------------
    draft2 = ManuscriptDraft(
        outline_id="o1",
        results_package_id="pkg1",
        title="Demand-Driven Platform Quantity Dynamics (v2)",
        version=2,
        section_ids=[],  # filled by the builder
        status="drafted",
        summary="2 sections",
        model_role="reasoning",
        supersedes=draft1,
    )
    _d2, manuscript2, package2 = await _build_phase4_package(
        store, blobs, p4c_router, draft=draft2, intro_body=modified_a
    )
    # the old report is now stale
    assert await svc.staleness(report1) == StalenessStatus.stale
    assert await svc.staleness(gate1) == StalenessStatus.stale

    # ---- incremental revalidation ----------------------------------------
    report2 = await svc.revalidate(report1, package2, as_of="2026-08-23")
    r2 = (await store.get(report2)).parse_payload(NoveltyValidationReport)
    assert r2.supersedes == report1
    assert r2.submission_package_id == package2
    assert r2.manuscript_id == manuscript2
    assert r2.manuscript_content_hash
    assert r2.as_of_date.isoformat() == "2026-08-23"

    # claim A modified -> revalidated (threat found now); claim B reused
    assert r2.coverage_summary["claims_reused"] == 1
    assert r2.coverage_summary["claims_revalidated"] == 1
    reused = set(r2.claim_assessment_ids) & r1_assessments
    assert len(reused) == 1
    assert r2.overall_status.value == "blocked"
    assert len(r2.critical_threats) == 1

    new_claims = {cid: (await store.get(cid)).parse_payload(NoveltyClaim) for cid in r2.claim_ids}
    intro_claim = next(c for c in new_claims.values() if c.section_id == "introduction")
    assert intro_claim.claim_type == NoveltyClaimType.absolute_priority
    assert intro_claim.risk.value == "critical"

    gate2 = await svc.create_gate(package2, report2)
    g2 = (await store.get(gate2)).parse_payload(SubmissionReadinessGate)
    assert g2.status == ReadinessStatus.blocked
    assert g2.novelty_report_id == report2
    assert g2.submission_package_id == package2
    # revalidate() already produced its own gate for the new report
    exec_envs = [
        env
        for env in await store.list(artifact_type="novelty_revalidation_execution")
        if env.payload.get("resulting_report_id") == report2
    ]
    internal_gate = exec_envs[0].parse_payload(NoveltyRevalidationExecution).resulting_gate_id
    assert (
        internal_gate
        and (await store.get(internal_gate)).parse_payload(SubmissionReadinessGate).status
        == ReadinessStatus.blocked
    )

    # ---- reopen: full incremental provenance ------------------------------
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")

    # gate -> package + new report
    assert any(p.source_artifact_id == package2 for p in await store2.get_parents(gate2))
    assert any(p.source_artifact_id == report2 for p in await store2.get_parents(gate2))

    # new report -> reused old assessment + new assessment + claims + package
    r2b = (await store2.get(report2)).parse_payload(NoveltyValidationReport)
    r2_parents = await store2.get_parents(report2)
    assert any(p.source_artifact_id == package2 for p in r2_parents)
    assert any(p.source_artifact_id == manuscript2 for p in r2_parents)
    for aid in r2b.claim_assessment_ids:
        assert any(p.source_artifact_id == aid for p in r2_parents)
    assert any(p.source_artifact_id == r2b.claim_ids[0] for p in r2_parents)
    # supersedes edge old report -> new report
    supers = [c for c in await store2.get_children(report1) if c.relation.value == "supersedes"]
    assert supers and supers[0].target_artifact_id == report2

    # execution -> plan -> change set -> old + new manuscripts
    executions = [
        env.parse_payload(NoveltyRevalidationExecution)
        for env in await store2.list(artifact_type="novelty_revalidation_execution")
        if env.payload.get("resulting_report_id") == report2
    ]
    assert len(executions) == 1
    ex = executions[0]
    assert ex.resulting_gate_id == internal_gate
    assert len(ex.reused_assessment_ids) == 1
    assert len(ex.newly_assessment_ids) == 1
    exec_parents = await store2.get_parents(
        next(
            env.artifact_id
            for env in await store2.list(artifact_type="novelty_revalidation_execution")
            if env.payload.get("resulting_report_id") == report2
        )
    )
    assert any(p.source_artifact_id == report2 for p in exec_parents)

    plan_env = await store2.get(ex.plan_id)
    plan = plan_env.parse_payload(NoveltyRevalidationPlan)
    assert plan.previous_report_id == report1
    assert plan.reuse_reasons  # explicit reasons persisted
    assert plan.revalidation_reasons  # one modified claim
    plan_parents = await store2.get_parents(ex.plan_id)
    assert any(p.source_artifact_id == report1 for p in plan_parents)

    change_set = (await store2.get(plan.manuscript_change_set_id)).parse_payload(
        ManuscriptChangeSet
    )
    assert change_set.old_manuscript_id == manuscript1
    assert change_set.new_manuscript_id == manuscript2
    assert len(change_set.unchanged_claim_ids) == 1
    assert len(change_set.modified_claim_ids) == 1
    change_parents = await store2.get_parents(plan.manuscript_change_set_id)
    assert any(p.source_artifact_id == manuscript1 for p in change_parents)
    assert any(p.source_artifact_id == manuscript2 for p in change_parents)

    # the reused assessment still carries its old claim linkage
    reused_id = ex.reused_assessment_ids[0]
    reused_assessment = (await store2.get(reused_id)).parse_payload(NoveltyClaimAssessment)
    assert reused_assessment.status == NoveltyClaimStatus.not_threatened_within_search_scope

    # old artifacts are untouched and stale
    r1b = (await store2.get(report1)).parse_payload(NoveltyValidationReport)
    assert r1b.manuscript_id == manuscript1
    assert r1b.overall_status.value == "clear"
    pkg1b = (await store2.get(package1)).parse_payload(SubmissionPackage)
    assert pkg1b.status == SubmissionPackageStatus.ready

    await store2.close()
