"""Phase 5D offline integration — bounded evidence pre-acquisition:

search results -> normalize/deduplicate -> several sparse candidates
-> bounded pre-acquisition (fake abstract provider)
-> candidate assessment on the pre-acquired evidence
-> novelty report/gate
-> fewer Phase 5C fallback enrichment calls than sparse candidates
-> reopen -> provenance verified.

No network, no paid models.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.literature import (
    LiteratureNotFoundError,
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
from research_harness.research.schemas.novelty import (
    EvidenceBasis,
    EvidenceEnrichmentExecution,
    NoveltyCandidateAssessment,
    NoveltyClaimAssessment,
    NoveltyReportStatus,
    NoveltyValidationReport,
    PreAcquisitionExecution,
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

CLAIM = "We are the first study to model demand-driven platform dynamics."


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
    def __init__(self, papers: list[PaperRecord], get_hits: dict[str, PaperRecord]):
        self.provider_name = "semantic_scholar"
        self.papers = papers
        self.get_hits = get_hits
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


@pytest.mark.asyncio
async def test_phase5d_preacquisition_chain(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")

    # ---- three sparse candidates (plus a duplicate of one via same DOI) ----
    sparse_papers = [
        _sparse("Demand Dynamics in Platform Markets", "10.1000/d1"),
        _sparse("Competition under Demand Uncertainty", "10.1000/d2"),
        _sparse("Strategic Interaction on Platforms", "10.1000/d3"),
    ]
    source = FakeSource(
        sparse_papers,
        get_hits={
            "10.1000/d1": _with_abstract(
                "Demand Dynamics in Platform Markets",
                "10.1000/d1",
                "We model demand-driven platform dynamics.",
            ),
            "10.1000/d2": _with_abstract(
                "Competition under Demand Uncertainty",
                "10.1000/d2",
                "We study quantity competition under demand uncertainty.",
            ),
            "10.1000/d3": _with_abstract(
                "Strategic Interaction on Platforms",
                "10.1000/d3",
                "We analyze strategic interaction on two-sided platforms.",
            ),
        },
    )
    router = FlipRouter(
        {
            "extract novelty claims": [{"claims": []}],
            "Demand Dynamics in Platform Markets": [
                {
                    "dimensions": [{"dimension": "mechanism", "value": "match"}],
                    "relationship": "strong_overlap",
                    "assessment": "same mechanism.",
                }
            ],
            "Competition under Demand Uncertainty": [
                {"dimensions": [], "relationship": "adjacent", "assessment": "adjacent."}
            ],
            "Strategic Interaction on Platforms": [
                {"dimensions": [], "relationship": "distinct", "assessment": "distinct."}
            ],
            "independently verify": [{"verdict": "concurs", "reasoning": "yes"}],
        }
    )
    svc = NoveltyValidationService(
        model_router=router,
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        service_lookup=lambda name: source if name.split(".")[-1] == "semantic_scholar" else source,
        blob_store=blobs,
        providers=["semantic_scholar"],
        acquire_full_text=False,
        preacquisition_enabled=True,
        preacquisition_risk_levels=["critical", "high"],
        preacquisition_max_per_claim=10,
        preacquisition_max_total=30,
    )

    # ---- manuscript + package --------------------------------------------
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

    # ---- full validation with pre-acquisition -----------------------------
    report_id = await svc.create_report(p_env.artifact_id, as_of="2026-08-23", offline=False)
    report = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
    assert report.overall_status == NoveltyReportStatus.blocked  # strong overlap
    assert len(report.critical_threats) == 1
    claim_assessment_id = report.claim_assessment_ids[0]
    ca = (await store.get(claim_assessment_id)).parse_payload(NoveltyClaimAssessment)
    assert ca.status.value == "threatened"
    assert ca.coverage.candidates_with_evidence_count == 3

    # every candidate assessed on the PRE-ACQUIRED abstract
    candidate_assessments = [
        (await store.get(aid)).parse_payload(NoveltyCandidateAssessment)
        for aid in ca.candidate_assessment_ids
    ]
    assert len(candidate_assessments) == 3
    assert all(a.evidence_basis == EvidenceBasis.abstract for a in candidate_assessments)

    # ---- pre-acquisition metrics ------------------------------------------
    preacq_envs = await store.list(artifact_type="evidence_preacquisition_execution")
    assert len(preacq_envs) == 1
    preacq = preacq_envs[0].parse_payload(PreAcquisitionExecution)
    assert preacq.metrics["candidates_considered"] == 3
    assert preacq.metrics["candidates_selected"] == 3
    assert preacq.metrics["abstracts_acquired"] == 3
    assert preacq.metrics["candidates_upgraded"] == 3
    assert preacq.metrics["external_attempts"] == 3
    assert preacq.metrics["cache_hits"] == 0
    assert preacq.metrics["failures"] == 0
    assert len(preacq.enrichment_execution_ids) == 3
    assert source.get_calls == ["10.1000/d1", "10.1000/d2", "10.1000/d3"]

    # ---- Phase 5C fallback NOT invoked for pre-acquired candidates --------
    all_enrichments = [
        env.parse_payload(EvidenceEnrichmentExecution)
        for env in await store.list(artifact_type="evidence_enrichment_execution")
    ]
    # exactly the three pre-acquisition enrichments, zero inline fallbacks
    assert len(all_enrichments) == 3
    assert len(source.get_calls) == 3  # no repeated acquisition

    gate_id = await svc.create_gate(p_env.artifact_id, report_id)
    gate = (await store.get(gate_id)).parse_payload(SubmissionReadinessGate)
    assert gate.status == ReadinessStatus.blocked

    # ---- reopen: provenance intact ----------------------------------------
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")

    report2 = (await store2.get(report_id)).parse_payload(NoveltyValidationReport)
    assert report2.overall_status == NoveltyReportStatus.blocked
    preacq2 = (await store2.get(preacq_envs[0].artifact_id)).parse_payload(PreAcquisitionExecution)
    assert preacq2.metrics["candidates_upgraded"] == 3
    # preacquisition -> claim + candidate set + enrichment executions
    preacq2_parents = await store2.get_parents(preacq_envs[0].artifact_id)
    assert any(p.source_artifact_id == report2.claim_ids[0] for p in preacq2_parents)
    assert any(p.source_artifact_id == ca.candidate_set_id for p in preacq2_parents)
    for eid in preacq2.enrichment_execution_ids:
        assert any(p.source_artifact_id == eid for p in preacq2_parents)
    # enrichment execution -> plan -> evidence items -> candidate assessment
    exec2 = (await store2.get(preacq2.enrichment_execution_ids[0])).parse_payload(
        EvidenceEnrichmentExecution
    )
    assert exec2.outcome.value == "enriched"
    plan2 = await store2.get(exec2.plan_id)
    plan2_parents = await store2.get_parents(plan2.artifact_id)
    assert any(p.source_artifact_id == report2.claim_ids[0] for p in plan2_parents)
    # every assessed candidate's evidence came from pre-acquisition
    for aid in ca.candidate_assessment_ids:
        a = (await store2.get(aid)).parse_payload(NoveltyCandidateAssessment)
        assert a.evidence_basis == EvidenceBasis.abstract
        assert a.evidence_artifact_ids
    gate2 = (await store2.get(gate_id)).parse_payload(SubmissionReadinessGate)
    assert gate2.status == ReadinessStatus.blocked

    await store2.close()
