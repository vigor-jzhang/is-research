"""Phase 5B unit tests — incremental novelty revalidation.

Deterministic/offline-friendly: fake router + fake providers, no network.
Covers change detection, reuse policy, mandatory revalidation, staleness,
and --force-all.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.literature.identity_resolver.plugin import (
    PaperIdentityResolverService,
)
from research_harness.plugins.literature.ingestion.plugin import LiteratureIngestor
from research_harness.plugins.research.novelty_validator.plugin import NoveltyValidationService
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.novelty import (
    ManuscriptChangeSet,
    NoveltyClaim,
    NoveltyClaimAssessment,
    NoveltyClaimType,
    NoveltyReportStatus,
    NoveltyRevalidationExecution,
    NoveltyRevalidationPlan,
    NoveltyValidationReport,
    ReadinessStatus,
    StalenessStatus,
    SubmissionReadinessGate,
)
from research_harness.research.schemas.publication import (
    FormattedManuscript,
    FormattedManuscriptStatus,
    FormattedSection,
    FrontMatter,
    SubmissionPackage,
    SubmissionPackageStatus,
)

CLAIM_A = "We are the first study to model demand-driven platform dynamics."
CLAIM_B = "To our knowledge, no prior research has examined this mechanism."


class KeyedRouter:
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
    def __init__(self, provider_name: str, papers: list = None, hits_by_query: dict = None):
        self.provider_name = provider_name
        self.papers = papers or []
        self.hits_by_query = hits_by_query or {}

    async def search(self, request):
        from research_harness.contracts.literature import (
            LiteratureSearchHit,
            LiteratureSearchPage,
        )

        hits = self.hits_by_query.get(request.query)
        if hits is None:
            hits = self.papers
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


def _paper(title: str, year: int, doi: str, abstract: str):
    from research_harness.research.schemas.paper import Author, PaperRecord

    return PaperRecord(
        title=title,
        authors=[Author(name="Smith, Jane")],
        year=year,
        venue="Journal of Platform Studies",
        doi=doi,
        abstract=abstract,
    )


async def _manuscript_env(
    store: SQLiteArtifactStore, sections: list[tuple[str, str, str]] | None = None
) -> tuple[str, str]:
    """Formatted manuscript (draft 'd1') + ready package. Returns
    (manuscript_id, package_id)."""
    sections = sections or [
        ("introduction", "Introduction", CLAIM_A),
        ("conclusion", "Conclusion", CLAIM_B),
    ]
    fm = FormattedManuscript(
        draft_id="d1",
        results_package_id="pkg1",
        profile_id="prof1",
        profile_name="Generic",
        citation_style="author_year",
        front_matter=FrontMatter(title="T", generated_by="deterministic"),
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
    return m_env.artifact_id, p_env.artifact_id


@pytest.fixture()
async def store(tmp_path: pathlib.Path):
    s = SQLiteArtifactStore(path=tmp_path / "art.db")
    yield s
    await s.close()


@pytest.fixture()
def svc(store: SQLiteArtifactStore):
    sources = {"semantic_scholar": FakeSource("semantic_scholar")}
    router = KeyedRouter({"extract novelty claims": {"claims": []}})
    return NoveltyValidationService(
        model_router=router,
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        service_lookup=lambda name: sources[name.split(".")[-1]],
        providers=["semantic_scholar"],
    )


async def _run_report(
    store: SQLiteArtifactStore,
    svc: NoveltyValidationService,
    sections: list[tuple[str, str, str]],
    as_of: str = "2026-08-23",
) -> tuple[str, str, str]:
    m_id, pkg_id = await _manuscript_env(store, sections=sections)
    report_id = await svc.create_report(pkg_id, as_of=as_of, offline=False)
    gate_id = await svc.create_gate(pkg_id, report_id)
    return m_id, pkg_id, report_id, gate_id


async def _load_claims(store: SQLiteArtifactStore, report_id: str) -> dict[str, NoveltyClaim]:
    report = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
    return {cid: (await store.get(cid)).parse_payload(NoveltyClaim) for cid in report.claim_ids}


async def _load_execution(
    store: SQLiteArtifactStore, report_id: str
) -> NoveltyRevalidationExecution:
    for env in await store.list(artifact_type="novelty_revalidation_execution"):
        ex = env.parse_payload(NoveltyRevalidationExecution)
        if ex.resulting_report_id == report_id:
            return ex
    raise AssertionError("no revalidation execution found")


# ---------------------------------------------------------------------------
# 1. no manuscript change -> all eligible assessments reused
# ---------------------------------------------------------------------------


async def test_no_change_all_reused(store: SQLiteArtifactStore, svc):
    m1, p1, report1, _gate1 = await _run_report(store, svc, None)
    report1_loaded = (await store.get(report1)).parse_payload(NoveltyValidationReport)
    old_assessment_ids = set(report1_loaded.claim_assessment_ids)
    assert len(old_assessment_ids) == 2

    m2, p2 = await _manuscript_env(store)  # identical content
    report2_id = await svc.revalidate(report1, p2, as_of="2026-08-23")
    report2 = (await store.get(report2_id)).parse_payload(NoveltyValidationReport)

    assert set(report2.claim_assessment_ids) == old_assessment_ids  # all reused
    assert report2.coverage_summary["claims_reused"] == 2
    assert report2.coverage_summary["claims_revalidated"] == 0
    assert report2.coverage_summary["searches_executed"] == 0
    assert report2.supersedes == report1
    assert report2.manuscript_content_hash

    ex = await _load_execution(store, report2_id)
    assert len(ex.reused_assessment_ids) == 2
    assert ex.newly_assessment_ids == []
    assert ex.resulting_gate_id

    plan = (await store.get(ex.plan_id)).parse_payload(NoveltyRevalidationPlan)
    assert set(plan.reusable_claim_assessment_ids) == old_assessment_ids
    assert plan.affected_claim_ids == []
    assert all("unchanged claim" in reason for reason in plan.reuse_reasons.values())

    change_set = (await store.get(plan.manuscript_change_set_id)).parse_payload(ManuscriptChangeSet)
    assert change_set.old_manuscript_id == m1
    assert change_set.new_manuscript_id == m2
    assert change_set.old_content_hash and change_set.new_content_hash
    assert change_set.removed_claim_ids == []
    assert len(change_set.unchanged_claim_ids) == 2
    # stable identity preserved (payload ids)
    old_claims = await _load_claims(store, report1)
    new_claims = await _load_claims(store, report2_id)
    assert {c.equivalent_claim_id for c in new_claims.values()} == {
        c.id for c in old_claims.values()
    }

    # new gate reflects the new report
    gate2 = (await store.get(ex.resulting_gate_id)).parse_payload(SubmissionReadinessGate)
    assert gate2.novelty_report_id == report2_id
    assert gate2.status == ReadinessStatus.ready


# ---------------------------------------------------------------------------
# 2. non-novelty section change -> novelty assessments reused
# ---------------------------------------------------------------------------


async def test_non_novelty_section_change_reused(store: SQLiteArtifactStore, svc):
    report1, _p1, _m1, _g1 = (None,) * 4
    m1, p1, report1, _gate1 = await _run_report(store, svc, None)
    old_assessments = set(
        (await store.get(report1)).parse_payload(NoveltyValidationReport).claim_assessment_ids
    )
    # conclusion gains non-claim prose; both claim sentences intact
    m2, p2 = await _manuscript_env(
        store,
        sections=[
            ("introduction", "Introduction", CLAIM_A),
            ("conclusion", "Conclusion", "Demand dynamics matter for platforms. " + CLAIM_B),
        ],
    )
    report2_id = await svc.revalidate(report1, p2, as_of="2026-08-23")
    report2 = (await store.get(report2_id)).parse_payload(NoveltyValidationReport)
    assert set(report2.claim_assessment_ids) == old_assessments
    assert report2.coverage_summary["claims_reused"] == 2
    assert report2.coverage_summary["searches_executed"] == 0

    ex = await _load_execution(store, report2_id)
    plan = (await store.get(ex.plan_id)).parse_payload(NoveltyRevalidationPlan)
    change_set = (await store.get(plan.manuscript_change_set_id)).parse_payload(ManuscriptChangeSet)
    # conclusion detected as changed, claims unaffected
    conclusion = next(c for c in change_set.changed_sections if c.section_id == "conclusion")
    assert conclusion.change_type.value == "changed"
    assert len(change_set.unchanged_claim_ids) == 2


# ---------------------------------------------------------------------------
# 3. one novelty claim wording change -> only that claim revalidated
# ---------------------------------------------------------------------------


async def test_one_claim_wording_change_revalidated(store: SQLiteArtifactStore, svc):
    m1, p1, report1, _g1 = await _run_report(store, svc, None)
    old_claims = await _load_claims(store, report1)
    old_assessments = set(
        (await store.get(report1)).parse_payload(NoveltyValidationReport).claim_assessment_ids
    )

    # ambiguous similarity band -> model decides materially changed
    svc._router = KeyedRouter(
        {
            "extract novelty claims": {"claims": []},
            "compare two novelty claim statements": {"materially_changed": True},
        }
    )
    m2, p2 = await _manuscript_env(
        store,
        sections=[
            ("introduction", "Introduction", CLAIM_A.replace("model", "analyze")),
            ("conclusion", "Conclusion", CLAIM_B),
        ],
    )
    report2_id = await svc.revalidate(report1, p2, as_of="2026-08-23")
    report2 = (await store.get(report2_id)).parse_payload(NoveltyValidationReport)
    new_claims = await _load_claims(store, report2_id)
    new_assessments = set(report2.claim_assessment_ids)

    # only claim A revalidated; claim B assessment reused
    assert report2.coverage_summary["claims_revalidated"] == 1
    assert report2.coverage_summary["claims_reused"] == 1
    assert len(new_assessments - old_assessments) == 1
    assert len(new_assessments & old_assessments) == 1

    ex = await _load_execution(store, report2_id)
    assert len(ex.newly_validated_claim_ids) == 1
    assert len(ex.reused_assessment_ids) == 1
    plan = (await store.get(ex.plan_id)).parse_payload(NoveltyRevalidationPlan)
    affected = plan.affected_claim_ids[0]
    assert plan.revalidation_reasons[affected].startswith("modified claim")
    assert "unchanged claim" in plan.reuse_reasons[ex.reused_assessment_ids[0]]
    # the unchanged claim B kept its stable identity
    unchanged_new = next(c for c in new_claims.values() if c.claim_text == CLAIM_B)
    assert unchanged_new.equivalent_claim_id is not None


# ---------------------------------------------------------------------------
# 4. new claim -> new validation
# ---------------------------------------------------------------------------


async def test_new_claim_validated(store: SQLiteArtifactStore, svc):
    m1, p1, report1, _g1 = await _run_report(store, svc, None)
    m2, p2 = await _manuscript_env(
        store,
        sections=[
            ("introduction", "Introduction", CLAIM_A),
            ("conclusion", "Conclusion", CLAIM_B),
            (
                "discussion",
                "Discussion",
                "This mechanism has not been studied in platform markets.",
            ),
        ],
    )
    report2_id = await svc.revalidate(report1, p2, as_of="2026-08-23")
    report2 = (await store.get(report2_id)).parse_payload(NoveltyValidationReport)
    assert report2.coverage_summary["claims_reused"] == 2
    assert report2.coverage_summary["claims_revalidated"] == 1
    assert len(report2.claim_ids) == 3

    ex = await _load_execution(store, report2_id)
    plan = (await store.get(ex.plan_id)).parse_payload(NoveltyRevalidationPlan)
    change_set = (await store.get(plan.manuscript_change_set_id)).parse_payload(ManuscriptChangeSet)
    assert len(change_set.added_claim_ids) == 1
    added_claim = (await store.get(change_set.added_claim_ids[0])).parse_payload(NoveltyClaim)
    assert added_claim.claim_type == NoveltyClaimType.literature_absence


# ---------------------------------------------------------------------------
# 5. removed claim -> absent from new report
# ---------------------------------------------------------------------------


async def test_removed_claim_absent(store: SQLiteArtifactStore, svc):
    m1, p1, report1, _g1 = await _run_report(store, svc, None)
    m2, p2 = await _manuscript_env(
        store,
        sections=[("introduction", "Introduction", CLAIM_A)],  # claim B removed
    )
    report2_id = await svc.revalidate(report1, p2, as_of="2026-08-23")
    report2 = (await store.get(report2_id)).parse_payload(NoveltyValidationReport)
    new_claims = await _load_claims(store, report2_id)
    assert len(new_claims) == 1
    assert all(c.claim_text != CLAIM_B for c in new_claims.values())

    ex = await _load_execution(store, report2_id)
    plan = (await store.get(ex.plan_id)).parse_payload(NoveltyRevalidationPlan)
    change_set = (await store.get(plan.manuscript_change_set_id)).parse_payload(ManuscriptChangeSet)
    assert len(change_set.removed_claim_ids) == 1
    # removed claims require no new search and no assessment
    assert plan.affected_claim_ids == []
    assert ex.newly_assessment_ids == []


# ---------------------------------------------------------------------------
# 6. scoped -> absolute: risk increases -> mandatory revalidation
# ---------------------------------------------------------------------------


async def test_risk_increase_mandatory_revalidation(store: SQLiteArtifactStore, svc):
    m1, p1, report1, _g1 = await _run_report(
        store,
        svc,
        [
            (
                "introduction",
                "Introduction",
                "We are the first study to examine demand-driven platform dynamics.",
            ),
            ("conclusion", "Conclusion", CLAIM_B),
        ],
    )
    old_claims = await _load_claims(store, report1)
    old_assessments = set(
        (await store.get(report1)).parse_payload(NoveltyValidationReport).claim_assessment_ids
    )

    svc._router = KeyedRouter(
        {
            "extract novelty claims": {"claims": []},
            "compare two novelty claim statements": {"materially_changed": True},
        }
    )
    m2, p2 = await _manuscript_env(
        store,
        sections=[
            ("introduction", "Introduction", CLAIM_A),  # now absolute first study
            ("conclusion", "Conclusion", CLAIM_B),
        ],
    )
    report2_id = await svc.revalidate(report1, p2, as_of="2026-08-23")
    report2 = (await store.get(report2_id)).parse_payload(NoveltyValidationReport)
    new_claims = await _load_claims(store, report2_id)

    intro = next(c for c in new_claims.values() if c.section_id == "introduction")
    # the claim became absolute_priority / critical
    assert intro.claim_type == NoveltyClaimType.absolute_priority
    assert intro.risk.value == "critical"
    # it was revalidated, its old assessment NOT reused
    assert report2.coverage_summary["claims_revalidated"] == 1
    ex = await _load_execution(store, report2_id)
    plan = (await store.get(ex.plan_id)).parse_payload(NoveltyRevalidationPlan)
    affected = plan.affected_claim_ids
    assert len(affected) == 1
    assert plan.revalidation_reasons[affected[0]].startswith("modified claim")
    reused_old = set(ex.reused_assessment_ids) & old_assessments
    assert reused_old  # claim B reused
    # claim A old assessment is not referenced anywhere in the new report
    intro_old_cid = next(cid for cid, c in old_claims.items() if c.section_id == "introduction")
    a_old_id = [
        aid
        for aid in old_assessments
        if (await store.get(aid)).parse_payload(NoveltyClaimAssessment).claim_id == intro_old_cid
    ][0]
    assert a_old_id not in set(report2.claim_assessment_ids)


# ---------------------------------------------------------------------------
# 7. unchanged claim + changed search policy -> no reuse
# ---------------------------------------------------------------------------


async def test_policy_change_invalidates_reuse(store: SQLiteArtifactStore, svc):
    m1, p1, report1, _g1 = await _run_report(store, svc, None, as_of="2026-08-23")
    m2, p2 = await _manuscript_env(store)  # identical content
    report2_id = await svc.revalidate(report1, p2, as_of="2026-09-01")  # different cutoff
    report2 = (await store.get(report2_id)).parse_payload(NoveltyValidationReport)
    assert report2.coverage_summary["claims_reused"] == 0
    assert report2.coverage_summary["claims_revalidated"] == 2

    ex = await _load_execution(store, report2_id)
    plan = (await store.get(ex.plan_id)).parse_payload(NoveltyRevalidationPlan)
    assert plan.reusable_claim_assessment_ids == []
    assert all("search policy changed" in r for r in plan.revalidation_reasons.values())


# ---------------------------------------------------------------------------
# 8. staleness
# ---------------------------------------------------------------------------


async def test_staleness_current_then_stale(store: SQLiteArtifactStore, svc):
    m1, p1, report1, gate1 = await _run_report(store, svc, None)
    assert await svc.staleness(report1) == StalenessStatus.current
    assert await svc.staleness(gate1) == StalenessStatus.current

    # a new, different manuscript supersedes the assessed one
    await _manuscript_env(
        store,
        sections=[("introduction", "Introduction", CLAIM_A.replace("model", "analyze"))],
    )
    assert await svc.staleness(report1) == StalenessStatus.stale
    assert await svc.staleness(gate1) == StalenessStatus.stale


async def test_stale_report_cannot_create_gate(store: SQLiteArtifactStore, svc):
    m1, p1, report1, gate1 = await _run_report(store, svc, None)
    gate_before = (await store.get(gate1)).parse_payload(SubmissionReadinessGate)
    assert gate_before.status == ReadinessStatus.ready

    # manuscript changes -> the old ready gate is stale, never current
    await _manuscript_env(
        store,
        sections=[("introduction", "Introduction", CLAIM_A.replace("model", "analyze"))],
    )
    assert await svc.staleness(gate1) == StalenessStatus.stale
    # a new gate cannot be built on the stale report
    with pytest.raises(ValueError, match="stale"):
        await svc.create_gate(p1, report1)
    # the old gate artifact is untouched and still historically ready
    gate_after = (await store.get(gate1)).parse_payload(SubmissionReadinessGate)
    assert gate_after.status == ReadinessStatus.ready
    assert gate_after.model_dump() == gate_before.model_dump()


# ---------------------------------------------------------------------------
# 9. --force-all
# ---------------------------------------------------------------------------


async def test_force_all_no_reuse(store: SQLiteArtifactStore, svc):
    m1, p1, report1, _g1 = await _run_report(store, svc, None)
    m2, p2 = await _manuscript_env(store)
    report2_id = await svc.revalidate(report1, p2, as_of="2026-08-23", force_all=True)
    report2 = (await store.get(report2_id)).parse_payload(NoveltyValidationReport)
    assert report2.coverage_summary["claims_reused"] == 0
    assert report2.coverage_summary["claims_revalidated"] == 2
    old = set(
        (await store.get(report1)).parse_payload(NoveltyValidationReport).claim_assessment_ids
    )
    assert not (set(report2.claim_assessment_ids) & old)

    ex = await _load_execution(store, report2_id)
    plan = (await store.get(ex.plan_id)).parse_payload(NoveltyRevalidationPlan)
    assert plan.reusable_claim_assessment_ids == []
    assert all(r == "force_all" for r in plan.revalidation_reasons.values())


# ---------------------------------------------------------------------------
# 10. report aggregation still deterministic across reuse + new assessments
# ---------------------------------------------------------------------------


async def test_revalidated_report_aggregation(store: SQLiteArtifactStore, svc):
    """A reused not-threatened assessment + a newly threatened one aggregate
    deterministically to blocked."""
    threatening = _paper(
        "Platform Competition under Demand Uncertainty",
        2019,
        "10.1000/rv",
        "We model competition between platforms choosing quantities.",
    )
    # the threatening paper surfaces ONLY for the modified claim's query
    modified_a = CLAIM_A.replace("model", "analyze")
    sources = {
        "semantic_scholar": FakeSource(
            "semantic_scholar",
            hits_by_query={f'"{modified_a}"': [threatening]},
        )
    }
    svc._lookup = lambda name: sources[name.split(".")[-1]]
    svc._router = KeyedRouter(
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
    m1, p1, report1, _g1 = await _run_report(store, svc, None)
    assert (await store.get(report1)).parse_payload(NoveltyValidationReport).overall_status == (
        NoveltyReportStatus.clear
    )

    # modify claim A -> only A is revalidated, now threatened -> blocked
    m2, p2 = await _manuscript_env(
        store,
        sections=[
            ("introduction", "Introduction", CLAIM_A.replace("model", "analyze")),
            ("conclusion", "Conclusion", CLAIM_B),
        ],
    )
    report2_id = await svc.revalidate(report1, p2, as_of="2026-08-23")
    report2 = (await store.get(report2_id)).parse_payload(NoveltyValidationReport)
    assert report2.coverage_summary["claims_reused"] == 1
    assert report2.coverage_summary["claims_revalidated"] == 1
    assert report2.overall_status == NoveltyReportStatus.blocked
    assert len(report2.critical_threats) == 1

    gate2 = await svc.create_gate(p2, report2_id)
    gate = (await store.get(gate2)).parse_payload(SubmissionReadinessGate)
    assert gate.status == ReadinessStatus.blocked
