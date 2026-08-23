"""Phase 4C unit tests — publication formatting, citation resolution,
bibliography, exports, anonymous mode, validation (offline).

Covers: citation resolution; bibliography deduplication; missing metadata
without fabrication; unresolved citation rejection; no leftover placeholders;
anonymous mode; condition preservation; Markdown/LaTeX/DOCX/PDF export;
BlobStore persistence; deterministic rerender; provenance after reopen;
profile section ordering; word-count limits; novelty normalization.
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


async def build_chain(
    store: SQLiteArtifactStore,
) -> dict[str, str]:
    """Persist paper records, identities, and a 3-section draft with citations."""
    ids: dict[str, str] = {}

    # paper A: full metadata
    rec_a = PaperRecord(
        title="Platform Competition and Demand",
        authors=[Author(name="Smith, Jane"), Author(name="Doe, John")],
        year=2021,
        venue="Journal of Platform Studies",
        doi="10.1000/abc",
    )
    a_env = ArtifactEnvelope.create(payload=rec_a, artifact_type="paper_record", producer="test")
    await store.put(a_env)
    ids["paper_a"] = a_env.artifact_id
    id_a = PaperIdentity(
        member_paper_artifact_ids=[ids["paper_a"]],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.manual,
        resolution_evidence=[],
    )
    ia_env = ArtifactEnvelope.create(payload=id_a, artifact_type="paper_identity", producer="test")
    await store.put(ia_env)
    ids["identity_a"] = ia_env.artifact_id

    # paper B: sparse metadata (no authors, no year) -> never fabricated
    rec_b = PaperRecord(title="Working Paper on Quantity Games")
    b_env = ArtifactEnvelope.create(payload=rec_b, artifact_type="paper_record", producer="test")
    await store.put(b_env)
    ids["paper_b"] = b_env.artifact_id
    id_b = PaperIdentity(
        member_paper_artifact_ids=[ids["paper_b"]],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.manual,
        resolution_evidence=[],
    )
    ib_env = ArtifactEnvelope.create(payload=id_b, artifact_type="paper_identity", producer="test")
    await store.put(ib_env)
    ids["identity_b"] = ib_env.artifact_id

    # sections
    from research_harness.research.schemas.proposition import (
        Proposition,
        PropositionClaimType,
        PropositionVerification,
        PropositionVerificationStatus,
    )

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
    p_env = ArtifactEnvelope.create(payload=prop, artifact_type="proposition", producer="test")
    await store.put(p_env)
    ids["prop"] = p_env.artifact_id
    await store.put(
        ArtifactEnvelope.create(
            payload=PropositionVerification(
                proposition_id=ids["prop"],
                model_id="model1",
                status=PropositionVerificationStatus.verified,
                checks=[],
            ),
            artifact_type="proposition_verification",
            producer="test",
        )
    )

    s_intro = ManuscriptSection(
        outline_id="outline1",
        section_id=ManuscriptSectionId.introduction,
        title="Introduction",
        body="Demand growth matters. [CITE:c1] also established this.",
        claims=[
            ManuscriptClaim(
                text="Demand growth raises equilibrium quantities (a > 0).",
                grounding_type=SectionArtifactType.research_finding,
                grounding_artifact_id="f1",
                citation_id=None,
                conditions=["a > 0"],
            )
        ],
        citations=[
            CitationReference(
                citation_id="c1",
                paper_identity_id=ids["identity_a"],
                evidence_item_id="ev1",
                page_locator="p. 12",
            )
        ],
    )
    e1 = ArtifactEnvelope.create(
        payload=s_intro, artifact_type="manuscript_section", producer="test"
    )
    await store.put(e1)
    ids["section_intro"] = e1.artifact_id

    s_props = ManuscriptSection(
        outline_id="outline1",
        section_id=ManuscriptSectionId.propositions,
        title="Propositions",
        body="Equilibrium quantity rises with demand [CITE:c2] and cost falls it [CITE:c3].",
        claims=[
            ManuscriptClaim(
                text="Equilibrium quantity rises with demand (a > c).",
                grounding_type=SectionArtifactType.verified_proposition,
                grounding_artifact_id=ids["prop"],
                citation_id=None,
                conditions=["a > c"],
            )
        ],
        citations=[
            CitationReference(
                citation_id="c2",
                paper_identity_id=ids["identity_a"],  # same paper as c1 -> dedup
                evidence_item_id="ev2",
                page_locator=None,
            ),
            CitationReference(
                citation_id="c3",
                paper_identity_id=ids["identity_b"],  # sparse metadata paper
                evidence_item_id="ev3",
                page_locator=None,
            ),
        ],
    )
    e2 = ArtifactEnvelope.create(
        payload=s_props, artifact_type="manuscript_section", producer="test"
    )
    await store.put(e2)
    ids["section_props"] = e2.artifact_id

    s_lim = ManuscriptSection(
        outline_id="outline1",
        section_id=ManuscriptSectionId.limitations,
        title="Limitations",
        body="Single-period setting.",
        claims=[],
        citations=[],
    )
    e3 = ArtifactEnvelope.create(payload=s_lim, artifact_type="manuscript_section", producer="test")
    await store.put(e3)
    ids["section_lim"] = e3.artifact_id

    draft = ManuscriptDraft(
        outline_id="outline1",
        results_package_id="pkg1",
        title="Demand-Driven Platform Quantity Dynamics",
        version=1,
        section_ids=[ids["section_intro"], ids["section_props"], ids["section_lim"]],
        status="drafted",
        summary="3 sections",
        model_role="reasoning",
    )
    d_env = ArtifactEnvelope.create(
        payload=draft, artifact_type="manuscript_draft", producer="test"
    )
    await store.put(d_env)
    ids["draft"] = d_env.artifact_id
    return ids


@pytest.mark.asyncio
async def test_citation_resolution_and_dedup(tmp_path: pathlib.Path):
    from research_harness.plugins.research.publication_formatter.plugin import (
        PublicationFormatterService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    svc = PublicationFormatterService(
        model_router=FakeRouter([]), artifact_store=store, blob_store=None
    )
    profile_id = await svc.create_profile(
        name="Generic IS Journal", required_sections=[], abstract_required=False
    )
    m_id = await svc.format(ids["draft"], profile_id)
    fm = (await store.get(m_id)).parse_payload(FormattedManuscript)
    # c1 + c2 -> identity_a, c3 -> identity_b: 2 bibliography entries
    assert len(fm.bibliography.entries) == 2
    assert fm.bibliography.entries[0].paper_identity_id == ids["identity_a"]
    assert fm.bibliography.entries[0].citation_ids == ["c1", "c2"]
    # inline citations rendered: (Smith, Jane and Doe, John, 2021, p. 12)
    intro = next(s for s in fm.sections if s.section_id == "introduction")
    assert "[CITE:" not in intro.body
    assert "(Smith, Jane and Doe, John, 2021, p. 12)" in intro.body
    # provenance kept
    assert fm.citation_map["c1"] == ids["identity_a"]


@pytest.mark.asyncio
async def test_missing_metadata_no_fabrication(tmp_path: pathlib.Path):
    from research_harness.plugins.research.publication_formatter.plugin import (
        PublicationFormatterService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    svc = PublicationFormatterService(
        model_router=FakeRouter([]), artifact_store=store, blob_store=None
    )
    profile_id = await svc.create_profile(
        name="Generic IS Journal", required_sections=[], abstract_required=False
    )
    m_id = await svc.format(ids["draft"], profile_id)
    fm = (await store.get(m_id)).parse_payload(FormattedManuscript)
    entry_b = next(e for e in fm.bibliography.entries if e.paper_identity_id == ids["identity_b"])
    # no authors, no year invented
    assert entry_b.authors == []
    assert entry_b.year is None
    assert "2021" not in entry_b.rendered
    # inline citation without authors/year uses the title
    props = next(s for s in fm.sections if s.section_id == "propositions")
    assert "[CITE:c3]" not in props.body
    assert '"Working Paper on Quantity Games"' in props.body


@pytest.mark.asyncio
async def test_unresolved_citation_rejection(tmp_path: pathlib.Path):
    from research_harness.plugins.research.publication_formatter.plugin import (
        PublicationFormatterService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    # add a bogus placeholder to the intro body (no matching citation)
    intro = (await store.get(ids["section_intro"])).parse_payload(ManuscriptSection)
    intro = intro.model_copy(update={"body": intro.body + " [CITE:bogus1]"})
    await store.put(
        ArtifactEnvelope.create(
            payload=intro,
            artifact_type="manuscript_section",
            producer="test",
            artifact_id=ids["section_intro"] + "-v2",
        )
    )
    draft = (await store.get(ids["draft"])).parse_payload(ManuscriptDraft)
    draft = draft.model_copy(
        update={
            "section_ids": [ids["section_intro"] + "-v2", ids["section_props"], ids["section_lim"]]
        }
    )
    d_env = ArtifactEnvelope.create(
        payload=draft,
        artifact_type="manuscript_draft",
        producer="test",
        artifact_id=ids["draft"] + "-v2",
    )
    await store.put(d_env)
    svc = PublicationFormatterService(
        model_router=FakeRouter([]), artifact_store=store, blob_store=None
    )
    profile_id = await svc.create_profile(
        name="Generic IS Journal", required_sections=[], abstract_required=False
    )
    m_id = await svc.format(d_env.artifact_id, profile_id)
    leaf, passed = await svc.validate(m_id)
    fm = (await store.get(leaf)).parse_payload(FormattedManuscript)
    assert passed is False
    assert fm.validation_status == FormattedManuscriptStatus.failed
    assert any(i.check == "unresolved_citations" for i in fm.validation_issues)
    assert any(i.check == "leftover_placeholders" for i in fm.validation_issues)


@pytest.mark.asyncio
async def test_anonymous_mode(tmp_path: pathlib.Path):
    from research_harness.plugins.research.publication_formatter.plugin import (
        PublicationFormatterService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    svc = PublicationFormatterService(
        model_router=FakeRouter([]), artifact_store=store, blob_store=None
    )
    profile_id = await svc.create_profile(
        name="Anonymous Journal",
        required_sections=[],
        abstract_required=False,
        anonymous_review=True,
    )
    m_id = await svc.format(ids["draft"], profile_id)
    fm = (await store.get(m_id)).parse_payload(FormattedManuscript)
    assert fm.anonymous_review is True
    assert fm.front_matter.authors == []
    assert fm.front_matter.affiliations == []
    # citations unaffected by anonymization
    assert (
        "(Smith, Jane and Doe, John, 2021, p. 12)"
        in next(s for s in fm.sections if s.section_id == "introduction").body
    )
    leaf, passed = await svc.validate(m_id)
    assert passed is True


@pytest.mark.asyncio
async def test_condition_preservation(tmp_path: pathlib.Path):
    from research_harness.plugins.research.publication_formatter.plugin import (
        PublicationFormatterService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    # claim drops its condition
    props = (await store.get(ids["section_props"])).parse_payload(ManuscriptSection)
    claims = [
        c.model_copy(update={"conditions": []}) if c.grounding_artifact_id == ids["prop"] else c
        for c in props.claims
    ]
    props = props.model_copy(update={"claims": claims})
    await store.put(
        ArtifactEnvelope.create(
            payload=props,
            artifact_type="manuscript_section",
            producer="test",
            artifact_id=ids["section_props"] + "-v2",
        )
    )
    draft = (await store.get(ids["draft"])).parse_payload(ManuscriptDraft)
    draft = draft.model_copy(
        update={
            "section_ids": [ids["section_intro"], ids["section_props"] + "-v2", ids["section_lim"]]
        }
    )
    d_env = ArtifactEnvelope.create(
        payload=draft,
        artifact_type="manuscript_draft",
        producer="test",
        artifact_id=ids["draft"] + "-v2",
    )
    await store.put(d_env)
    svc = PublicationFormatterService(
        model_router=FakeRouter([]), artifact_store=store, blob_store=None
    )
    profile_id = await svc.create_profile(
        name="Generic IS Journal", required_sections=[], abstract_required=False
    )
    m_id = await svc.format(d_env.artifact_id, profile_id)
    fm = (await store.get(m_id)).parse_payload(FormattedManuscript)
    props_fm = next(s for s in fm.sections if s.section_id == "propositions")
    assert props_fm.conditions_preserved is False
    leaf, passed = await svc.validate(m_id)
    assert passed is False
    issues = (await store.get(leaf)).parse_payload(FormattedManuscript).validation_issues
    assert any(i.check == "conditions_changed" for i in issues)


@pytest.mark.asyncio
async def test_exports_and_blob_persistence(tmp_path: pathlib.Path):
    from research_harness.plugins.research.publication_formatter.plugin import (
        PublicationFormatterService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    ids = await build_chain(store)
    svc = PublicationFormatterService(
        model_router=FakeRouter([]), artifact_store=store, blob_store=blobs
    )
    profile_id = await svc.create_profile(
        name="Generic IS Journal", required_sections=[], abstract_required=False
    )
    m_id = await svc.format(ids["draft"], profile_id)
    leaf, passed = await svc.validate(m_id)
    assert passed is True

    export_ids = {fmt: await svc.export(leaf, fmt) for fmt in ("markdown", "latex", "docx", "pdf")}
    records = {
        fmt: (await store.get(eid)).parse_payload(ExportRecord) for fmt, eid in export_ids.items()
    }
    # blob content round-trips and hash matches
    for fmt, rec in records.items():
        data = await blobs.get_bytes(BlobReference(**rec.blob_ref))
        import hashlib

        assert hashlib.sha256(data).hexdigest() == rec.content_hash
        assert rec.size_bytes == len(data)
    md = records["markdown"]
    assert b"# Demand-Driven Platform Quantity Dynamics" in await blobs.get_bytes(
        BlobReference(**md.blob_ref)
    )
    assert b"## References" in await blobs.get_bytes(BlobReference(**md.blob_ref))
    latex = await blobs.get_bytes(BlobReference(**records["latex"].blob_ref))
    assert b"\\documentclass" in latex and b"\\section{" in latex and b"thebibliography" in latex
    docx = await blobs.get_bytes(BlobReference(**records["docx"].blob_ref))
    assert docx[:2] == b"PK"  # zip container
    pdf = await blobs.get_bytes(BlobReference(**records["pdf"].blob_ref))
    assert pdf[:5] == b"%PDF-"


@pytest.mark.asyncio
async def test_deterministic_rerender(tmp_path: pathlib.Path):
    from research_harness.plugins.research.publication_formatter.plugin import (
        PublicationFormatterService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    ids = await build_chain(store)
    svc = PublicationFormatterService(
        model_router=FakeRouter([]), artifact_store=store, blob_store=blobs
    )
    profile_id = await svc.create_profile(
        name="Generic IS Journal", required_sections=[], abstract_required=False
    )
    m_id = await svc.format(ids["draft"], profile_id)
    leaf, _ = await svc.validate(m_id)
    e1 = await svc.export(leaf, "latex")
    e2 = await svc.export(leaf, "latex")  # idempotent: same record
    assert e1 == e2
    r1 = (await store.get(e1)).parse_payload(ExportRecord)
    # deterministic: fresh store, same inputs -> same bytes
    store2 = SQLiteArtifactStore(path=tmp_path / "art2.db")
    ids2 = await build_chain(store2)
    svc2 = PublicationFormatterService(
        model_router=FakeRouter([]), artifact_store=store2, blob_store=blobs
    )
    profile2 = await svc2.create_profile(
        name="Generic IS Journal", required_sections=[], abstract_required=False
    )
    m2 = await svc2.format(ids2["draft"], profile2)
    leaf2, _ = await svc2.validate(m2)
    e3 = await svc2.export(leaf2, "latex")
    r3 = (await store2.get(e3)).parse_payload(ExportRecord)
    # deterministic modulo run-specific artifact ids (UUIDs)
    import re

    def norm(content: bytes) -> bytes:
        return re.sub(
            rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", b"<id>", content
        )

    d1 = await blobs.get_bytes(BlobReference(**r1.blob_ref))
    d3 = await blobs.get_bytes(BlobReference(**r3.blob_ref))
    assert norm(d1) == norm(d3)
    assert r1.size_bytes == r3.size_bytes


@pytest.mark.asyncio
async def test_section_ordering_and_word_limits(tmp_path: pathlib.Path):
    from research_harness.plugins.research.publication_formatter.plugin import (
        PublicationFormatterService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    svc = PublicationFormatterService(
        model_router=FakeRouter([]), artifact_store=store, blob_store=None
    )
    profile_id = await svc.create_profile(
        name="Reversed Journal",
        required_sections=["propositions", "introduction"],
        section_order=["propositions", "introduction", "limitations"],
        word_limits={"introduction": 5},
        total_word_limit=10,
        abstract_required=False,
    )
    m_id = await svc.format(ids["draft"], profile_id)
    fm = (await store.get(m_id)).parse_payload(FormattedManuscript)
    assert [s.section_id for s in fm.sections] == ["propositions", "introduction", "limitations"]
    leaf, passed = await svc.validate(m_id)
    assert passed is False
    issues = (await store.get(leaf)).parse_payload(FormattedManuscript).validation_issues
    checks = {i.check for i in issues}
    assert "word_count" in checks


@pytest.mark.asyncio
async def test_front_matter_and_novelty_normalization(tmp_path: pathlib.Path):
    from research_harness.plugins.research.publication_formatter.plugin import (
        PublicationFormatterService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    router = FakeRouter(
        [
            {
                "abstract": "This is the first study of demand-driven platform quantity effects. Demand raises quantity.",
                "keywords": ["platforms", "demand", "quantity"],
            }
        ]
    )
    svc = PublicationFormatterService(model_router=router, artifact_store=store, blob_store=None)
    profile_id = await svc.create_profile(
        name="Generic IS Journal", required_sections=[], abstract_required=True
    )
    m_id = await svc.format(ids["draft"], profile_id)
    fm = (await store.get(m_id)).parse_payload(FormattedManuscript)
    assert fm.front_matter.generated_by == "llm"
    assert "first study" not in fm.front_matter.abstract.lower()
    assert fm.front_matter.keywords == ["platforms", "demand", "quantity"]
    leaf, passed = await svc.validate(m_id)
    assert passed is True


@pytest.mark.asyncio
async def test_package_and_provenance_after_reopen(tmp_path: pathlib.Path):
    from research_harness.plugins.research.publication_formatter.plugin import (
        PublicationFormatterService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    ids = await build_chain(store)
    svc = PublicationFormatterService(
        model_router=FakeRouter([]), artifact_store=store, blob_store=blobs
    )
    profile_id = await svc.create_profile(
        name="Generic IS Journal", required_sections=[], abstract_required=False
    )
    m_id = await svc.format(ids["draft"], profile_id)
    leaf, passed = await svc.validate(m_id)
    assert passed is True
    pkg_id = await svc.package(leaf)
    pkg = (await store.get(pkg_id)).parse_payload(SubmissionPackage)
    assert pkg.status.value == "ready"
    assert len(pkg.export_records) == 4

    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")
    pkg2 = (await store2.get(pkg_id)).parse_payload(SubmissionPackage)
    # package -> manuscript -> draft
    assert pkg2.formatted_manuscript_id == leaf
    fm = (await store2.get(pkg2.formatted_manuscript_id)).parse_payload(FormattedManuscript)
    assert fm.draft_id == ids["draft"]
    # export record fields
    er = pkg2.export_records[0]
    assert er.source_draft_id == ids["draft"]
    assert er.profile_id == profile_id
    assert er.blob_ref["storage_key"]
    # bibliography -> identity provenance
    bib_id = fm.bibliography_id
    bib_parents = await store2.get_parents(bib_id)
    assert any(p.source_artifact_id == ids["identity_a"] for p in bib_parents)
    assert any(p.source_artifact_id == ids["identity_b"] for p in bib_parents)
    # manuscript -> draft + profile
    m_parents = await store2.get_parents(pkg2.formatted_manuscript_id)
    assert any(p.source_artifact_id == ids["draft"] for p in m_parents)
    assert any(p.source_artifact_id == profile_id for p in m_parents)
    # package -> manuscript
    pkg_parents = await store2.get_parents(pkg_id)
    assert any(p.source_artifact_id == leaf for p in pkg_parents)
    await store2.close()


@pytest.mark.asyncio
async def test_package_failed_when_unvalidated(tmp_path: pathlib.Path):
    from research_harness.plugins.research.publication_formatter.plugin import (
        PublicationFormatterService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    ids = await build_chain(store)
    svc = PublicationFormatterService(
        model_router=FakeRouter([]), artifact_store=store, blob_store=blobs
    )
    profile_id = await svc.create_profile(
        name="Generic IS Journal",
        required_sections=["missing_section_xyz"],
        abstract_required=False,
    )
    m_id = await svc.format(ids["draft"], profile_id)
    leaf, passed = await svc.validate(m_id)
    assert passed is False
    pkg_id = await svc.package(leaf)
    pkg = (await store.get(pkg_id)).parse_payload(SubmissionPackage)
    assert pkg.status.value == "failed"  # not publication-ready
