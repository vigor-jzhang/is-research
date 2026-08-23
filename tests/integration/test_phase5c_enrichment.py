"""Phase 5C offline integration — evidence enrichment for a sparse novelty
candidate:

Phase 5A novelty candidate (title-only, DOI via external identifier)
-> inline enrichment acquires abstract via fake provider get
-> candidate reassessed with abstract evidence
-> claim/report/gate reflect the stronger evidence
-> reopen -> full enrichment provenance verified.

No network, no paid models.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.literature import (
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
from research_harness.research.schemas.evidence import EvidenceItem
from research_harness.research.schemas.novelty import (
    CandidateRelationship,
    EnrichmentAttemptStatus,
    EnrichmentOutcome,
    EvidenceBasis,
    EvidenceEnrichmentAttempt,
    EvidenceEnrichmentExecution,
    EvidenceEnrichmentPlan,
    NoveltyCandidateAssessment,
    NoveltyClaimAssessment,
    NoveltyClaimStatus,
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
    def __init__(self, provider_name: str, papers: list[PaperRecord], get_hits: dict):
        self.provider_name = provider_name
        self.papers = papers
        self.get_hits = get_hits

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
        if identifier in self.get_hits:
            hit = self.get_hits[identifier]
            return LiteratureSearchHit(
                paper=hit,
                raw_payload={"title": hit.title},
                provider=self.provider_name,
                provider_record_id=hit.doi or identifier,
            )
        from research_harness.contracts.literature import LiteratureNotFoundError

        raise LiteratureNotFoundError(f"no record for {identifier}")


def _lookup(sources: dict[str, object]):
    def f(name: str):
        if name in sources:
            return sources[name]
        return sources[name.split(".")[-1]]

    return f


@pytest.mark.asyncio
async def test_phase5c_enrichment_chain(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")

    # ---- sparse candidate: title only, DOI via external identifier --------
    sparse = PaperRecord(
        title="Demand Dynamics in Platform Markets",
        authors=[Author(name="Smith, Jane")],
        year=None,
        venue=None,
        doi=None,
        external_identifiers=[ExternalIdentifier(scheme="doi", value="10.1000/5c")],
    )
    with_abstract = PaperRecord(
        title="Demand Dynamics in Platform Markets",
        authors=[Author(name="Smith, Jane")],
        year=2019,
        venue="Journal of Platform Studies",
        doi="10.1000/5c",
        abstract=(
            "We model demand-driven platform dynamics and characterize the "
            "equilibrium quantities under demand uncertainty."
        ),
    )
    source = FakeSource(
        "semantic_scholar",
        papers=[sparse],
        get_hits={"10.1000/5c": with_abstract},
    )
    router = FakeRouter(
        {
            "extract novelty claims": {"claims": []},
            "Demand Dynamics in Platform Markets": {
                "dimensions": [
                    {"dimension": "mechanism", "value": "match"},
                    {"dimension": "setting", "value": "match"},
                    {"dimension": "theoretical_result", "value": "match"},
                ],
                "relationship": "direct_prior_art",
                "assessment": "Models the same mechanism and setting, predating us.",
            },
            "independently verify": {"verdict": "concurs", "reasoning": "evidence agrees."},
        }
    )
    svc = NoveltyValidationService(
        model_router=router,
        artifact_store=store,
        ingestor=LiteratureIngestor(artifact_store=store),
        identity_resolver=PaperIdentityResolverService(artifact_store=store),
        service_lookup=_lookup({"semantic_scholar": source}),
        blob_store=blobs,
        providers=["semantic_scholar"],
        acquire_full_text=False,
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

    # ---- full 5A validation with automatic enrichment ---------------------
    report_id = await svc.create_report(p_env.artifact_id, as_of="2026-08-23", offline=False)
    report = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
    assert report.overall_status == NoveltyReportStatus.blocked  # direct prior art
    assert report.critical_threats
    claim_assessment_id = report.claim_assessment_ids[0]
    ca = (await store.get(claim_assessment_id)).parse_payload(NoveltyClaimAssessment)
    assert ca.status == NoveltyClaimStatus.threatened
    assert ca.coverage.candidates_with_evidence_count == 1

    candidate_id = ca.candidate_assessment_ids[0]
    candidate = (await store.get(candidate_id)).parse_payload(NoveltyCandidateAssessment)
    # the sparse candidate was enriched to abstract evidence before assessment
    assert candidate.evidence_basis == EvidenceBasis.abstract
    assert candidate.relationship == CandidateRelationship.direct_prior_art
    assert candidate.evidence_artifact_ids  # abstract paper record + item

    # enrichment provenance: assessment <- execution <- plan/attempt <- items
    enrichment_exec_id = None
    for parent in await store.get_parents(candidate_id):
        if (await store.exists(parent.source_artifact_id)) and (
            await store.get(parent.source_artifact_id)
        ).artifact_type == "evidence_enrichment_execution":
            enrichment_exec_id = parent.source_artifact_id
            break
    assert enrichment_exec_id
    execution = (await store.get(enrichment_exec_id)).parse_payload(EvidenceEnrichmentExecution)
    assert execution.before_evidence_basis == EvidenceBasis.title_only
    assert execution.after_evidence_basis == EvidenceBasis.abstract
    assert execution.outcome == EnrichmentOutcome.enriched
    assert len(execution.attempt_ids) == 1

    attempt = (await store.get(execution.attempt_ids[0])).parse_payload(EvidenceEnrichmentAttempt)
    assert attempt.status == EnrichmentAttemptStatus.success
    assert attempt.strategy == "provider_get_abstract"
    plan = (await store.get(execution.plan_id)).parse_payload(EvidenceEnrichmentPlan)
    assert plan.paper_identity_id == candidate.paper_identity_id
    assert "abstract" in plan.requested_evidence_types
    assert "title_only" in plan.reason

    # the acquired abstract became an EvidenceItem linked to the identity
    item_id = attempt.retrieved_artifact_ids[1]
    item = (await store.get(item_id)).parse_payload(EvidenceItem)
    assert item.extraction_method == "provider_import"
    assert item.metadata.get("novelty_enrichment") is True
    item_parents = await store.get_parents(item_id)
    assert any(p.source_artifact_id == candidate.paper_identity_id for p in item_parents)

    gate_id = await svc.create_gate(p_env.artifact_id, report_id)
    gate = (await store.get(gate_id)).parse_payload(SubmissionReadinessGate)
    assert gate.status == ReadinessStatus.blocked

    # ---- reopen: provenance intact ----------------------------------------
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")

    report2 = (await store2.get(report_id)).parse_payload(NoveltyValidationReport)
    assert report2.overall_status == NoveltyReportStatus.blocked
    ca2 = (await store2.get(claim_assessment_id)).parse_payload(NoveltyClaimAssessment)
    candidate2 = (await store2.get(candidate_id)).parse_payload(NoveltyCandidateAssessment)
    assert candidate2.evidence_basis == EvidenceBasis.abstract

    exec2_env = await store2.get(enrichment_exec_id)
    execution2 = exec2_env.parse_payload(EvidenceEnrichmentExecution)
    assert execution2.outcome == EnrichmentOutcome.enriched
    # candidate assessment -> execution (derived_from)
    assert any(
        p.source_artifact_id == enrichment_exec_id for p in await store2.get_parents(candidate_id)
    )
    # execution -> plan + attempt
    exec2_parents = await store2.get_parents(enrichment_exec_id)
    assert any(p.source_artifact_id == execution2.plan_id for p in exec2_parents)
    assert any(p.source_artifact_id == execution2.attempt_ids[0] for p in exec2_parents)
    # plan -> identity + claim; attempt -> plan
    plan2 = (await store2.get(execution2.plan_id)).parse_payload(EvidenceEnrichmentPlan)
    plan2_parents = await store2.get_parents(execution2.plan_id)
    assert any(p.source_artifact_id == candidate2.paper_identity_id for p in plan2_parents)
    assert any(p.source_artifact_id == plan2.claim_id for p in plan2_parents)
    attempt2_parents = await store2.get_parents(execution2.attempt_ids[0])
    assert any(p.source_artifact_id == execution2.plan_id for p in attempt2_parents)
    # attempt -> acquired paper record + evidence item
    attempt2 = (await store2.get(execution2.attempt_ids[0])).parse_payload(
        EvidenceEnrichmentAttempt
    )
    assert attempt2.retrieved_artifact_ids
    # evidence item -> paper identity
    item2 = (await store2.get(attempt2.retrieved_artifact_ids[1])).parse_payload(EvidenceItem)
    assert item2.metadata.get("novelty_enrichment")
    item2_parents = await store2.get_parents(attempt2.retrieved_artifact_ids[1])
    assert any(p.source_artifact_id == candidate2.paper_identity_id for p in item2_parents)
    # claim assessment unchanged by enrichment (report + gate intact)
    assert any(
        p.source_artifact_id == candidate_id for p in await store2.get_parents(claim_assessment_id)
    )
    gate2 = (await store2.get(gate_id)).parse_payload(SubmissionReadinessGate)
    assert gate2.status == ReadinessStatus.blocked

    await store2.close()
