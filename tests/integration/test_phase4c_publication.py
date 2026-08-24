"""Phase 4C offline integration — full chain with fake model, no network:

ManuscriptDraft -> formatting (citation resolution + bibliography)
-> validation -> all four exporters (BlobStore) -> SubmissionPackage,
with provenance checks after reopen.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.blob import BlobReference
from research_harness.contracts.model import Message, ModelResponse
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
from research_harness.research.schemas.paper import Author, PaperRecord
from research_harness.research.schemas.proposition import (
    Proposition,
    PropositionClaimType,
    PropositionVerification,
    PropositionVerificationStatus,
)
from research_harness.research.schemas.publication import (
    ExportRecord,
    FormattedManuscript,
    FormattedManuscriptStatus,
    SubmissionPackage,
)


class FakeRouter:
    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.calls = 0

    async def complete(self, role, request):
        self.calls += 1
        idx = min(self.calls - 1, len(self.responses) - 1)
        return ModelResponse(
            message=Message(role="assistant", content=json.dumps(self.responses[idx])),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


@pytest.mark.asyncio
async def test_phase4c_full_chain(tmp_path: pathlib.Path):
    from research_harness.plugins.research.publication_formatter.plugin import (
        PublicationFormatterService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")

    # ---- paper records + identities --------------------------------------
    rec = PaperRecord(
        title="Platform Competition and Demand",
        authors=[Author(name="Smith, Jane"), Author(name="Doe, John")],
        year=2021,
        venue="Journal of Platform Studies",
        doi="10.1000/abc",
    )
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

    # ---- verified proposition for the conditions check --------------------
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

    # ---- draft with two sections + citations ------------------------------
    s1 = ManuscriptSection(
        outline_id="o1",
        section_id=ManuscriptSectionId.introduction,
        title="Introduction",
        body="Demand growth matters. [CITE:c1] also established this.",
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
        section_id=ManuscriptSectionId.propositions,
        title="Propositions",
        body="Equilibrium quantity rises with demand [CITE:c1].",
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

    # ---- full publication pipeline ------------------------------------------
    router = FakeRouter(
        [
            {
                "abstract": "We study demand-driven platform quantity dynamics.",
                "keywords": ["platforms", "demand"],
            },
            {
                "opening": "Dear Editor,",
                "contribution_summary": [
                    "We characterize demand-driven platform quantity dynamics."
                ],
                "journal_fit": "The manuscript fits the journal's platform economics scope.",
                "closing": "Sincerely,",
            },
        ]
    )

    svc = PublicationFormatterService(model_router=router, artifact_store=store, blob_store=blobs)
    profile_id = await svc.create_profile(
        name="Generic IS Journal",
        required_sections=["introduction", "propositions"],
        abstract_max_words=100,
    )

    # format
    m_id = await svc.format(d_env.artifact_id, profile_id)
    fm = (await store.get(m_id)).parse_payload(FormattedManuscript)
    assert len(fm.sections) == 2
    assert len(fm.bibliography.entries) == 1  # c1 used twice -> dedup
    assert "[CITE:" not in "\n".join(s.body for s in fm.sections)
    assert fm.front_matter.generated_by == "llm"
    assert fm.front_matter.abstract

    # validate
    leaf, passed = await svc.validate(m_id)
    assert passed is True
    fmv = (await store.get(leaf)).parse_payload(FormattedManuscript)
    assert fmv.validation_status == FormattedManuscriptStatus.validated

    # export all formats -> BlobStore
    export_ids = {fmt: await svc.export(leaf, fmt) for fmt in ("markdown", "latex", "docx", "pdf")}
    for _fmt, eid in export_ids.items():
        er = (await store.get(eid)).parse_payload(ExportRecord)
        assert er.source_draft_id == d_env.artifact_id
        assert er.profile_id == profile_id
        data = await blobs.get_bytes(BlobReference(**er.blob_ref))
        assert len(data) == er.size_bytes

    # package
    pkg_id = await svc.package(leaf, with_cover_letter=True)
    pkg = (await store.get(pkg_id)).parse_payload(SubmissionPackage)
    assert pkg.status.value == "ready"
    assert len(pkg.export_records) == 4
    assert pkg.cover_letter_id

    # ---- provenance after reopen ---------------------------------------------
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")
    pkg2 = (await store2.get(pkg_id)).parse_payload(SubmissionPackage)
    assert pkg2.formatted_manuscript_id == leaf
    # package -> manuscript
    assert any(p.source_artifact_id == leaf for p in await store2.get_parents(pkg_id))
    # manuscript (validated leaf) -> draft + profile + bibliography
    m_parents = await store2.get_parents(leaf)
    assert any(p.source_artifact_id == d_env.artifact_id for p in m_parents)
    assert any(p.source_artifact_id == profile_id for p in m_parents)
    # supersedes chain V1 -> validated leaf
    supers = [c for c in await store2.get_children(m_id) if c.relation.value == "supersedes"]
    assert supers and supers[0].target_artifact_id == leaf
    # bibliography -> paper identity
    bib_id = fmv.bibliography_id
    bib_parents = await store2.get_parents(bib_id)
    assert any(p.source_artifact_id == id_env.artifact_id for p in bib_parents)
    # export records keep provenance ids
    er0 = pkg2.export_records[0]
    assert er0.blob_ref["storage_key"]
    assert er0.content_hash
    # citation_map preserved for provenance after rendering
    assert fmv.citation_map["c1"] == id_env.artifact_id
    await store2.close()
