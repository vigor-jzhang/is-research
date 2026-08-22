import pathlib

import pytest

from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.identity import (
    IdentityEvidence,
    PaperIdentity,
    ResolutionMethod,
)
from research_harness.research.schemas.paper import Author, PaperRecord
from research_harness.research.schemas.provider_snapshot import ProviderRecordSnapshot


def _paper(title, doi=None, abstract=None, year=None, venue=None, provider_marker=None):
    meta = {}
    if provider_marker == "crossref":
        meta["crossref_publisher"] = "Test"
    elif provider_marker == "semantic_scholar":
        meta["semantic_scholar_paperId"] = "p123"
    return PaperRecord(
        title=title,
        abstract=abstract,
        year=year,
        venue=venue,
        doi=doi,
        authors=[Author(name="A One")],
        metadata=meta,
    )


async def _put_paper_with_snapshot(store, paper, provider, provider_record_id):
    snap = ProviderRecordSnapshot(
        provider=provider,
        provider_record_id=provider_record_id,
        retrieved_at=paper.metadata.get("retrieved_at", "2024-01-01T00:00:00Z"),
        request_kind="search",
        request_metadata={},
        raw_payload={"title": paper.title},
        metadata={},
    )
    # Ensure retrieved_at is valid datetime; ProviderRecordSnapshot expects datetime?
    # Check schema: it likely expects datetime; we pass now if needed
    from datetime import UTC, datetime

    if isinstance(snap.retrieved_at, str):
        snap = snap.model_copy(update={"retrieved_at": datetime.now(UTC)})
    snap_env = ArtifactEnvelope.create(
        payload=snap, artifact_type="provider_record_snapshot", producer="test"
    )
    await store.put(snap_env)
    paper_env = ArtifactEnvelope.create(
        payload=paper, artifact_type="paper_record", producer="test"
    )
    await store.put(paper_env)
    await store.add_provenance(
        ProvenanceLink(
            relation=ProvenanceRelation.generated_from,
            source_artifact_id=snap_env.artifact_id,
            target_artifact_id=paper_env.artifact_id,
            producer="test",
        )
    )
    return paper_env, snap_env


@pytest.mark.asyncio
async def test_view_builder_single_member_deterministic(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_view_builder.plugin import (
        ScreeningViewBuilderService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    paper = _paper(
        "Single Title",
        doi="10.123/a",
        abstract="Abstract here",
        year=2020,
        venue="J1",
        provider_marker="crossref",
    )
    p_env, _ = await _put_paper_with_snapshot(store, paper, "crossref", "10.123/a")
    identity = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[
            IdentityEvidence(
                identifier_scheme="doi",
                normalized_value="10.123/a",
                member_artifact_ids=[p_env.artifact_id],
            )
        ],
    )
    ident_env = ArtifactEnvelope.create(
        payload=identity, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)

    svc = ScreeningViewBuilderService(artifact_store=store)
    view_id = await svc.build(ident_env.artifact_id)
    assert view_id
    from research_harness.research.schemas.screening_view import PaperScreeningView

    view = (await store.get(view_id)).parse_payload(PaperScreeningView)
    assert view.title == "Single Title"
    assert view.abstract == "Abstract here"
    assert view.year == 2020
    assert view.venue == "J1"
    assert view.field_sources["title"].provider == "crossref"
    assert view.field_sources["abstract"].provider == "crossref"
    assert not view.metadata.get("missing_abstract")
    await store.close()


@pytest.mark.asyncio
async def test_view_builder_title_prefers_crossref_over_semantic(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_view_builder.plugin import (
        ScreeningViewBuilderService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    p1 = _paper("Crossref Title Distinct", doi="10.123/a", provider_marker="crossref")
    p2 = _paper("Semantic Title Distinct", doi="10.123/a", provider_marker="semantic_scholar")
    e1, _ = await _put_paper_with_snapshot(store, p1, "crossref", "10.123/a")
    e2, _ = await _put_paper_with_snapshot(store, p2, "semantic_scholar", "10.123/a")
    identity = PaperIdentity(
        member_paper_artifact_ids=[e1.artifact_id, e2.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[
            IdentityEvidence(
                identifier_scheme="doi",
                normalized_value="10.123/a",
                member_artifact_ids=[e1.artifact_id, e2.artifact_id],
            )
        ],
    )
    ident_env = ArtifactEnvelope.create(
        payload=identity, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)
    svc = ScreeningViewBuilderService(artifact_store=store)
    view_id = await svc.build(ident_env.artifact_id)
    from research_harness.research.schemas.screening_view import PaperScreeningView

    view = (await store.get(view_id)).parse_payload(PaperScreeningView)
    # Deterministic: crossref preferred
    assert view.title == "Crossref Title Distinct"
    assert view.field_sources["title"].provider == "crossref"
    assert "title_conflicts" in view.metadata
    assert set(view.metadata["title_conflicts"]) == {
        "Crossref Title Distinct",
        "Semantic Title Distinct",
    }
    await store.close()


@pytest.mark.asyncio
async def test_view_builder_abstract_prefers_longest(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_view_builder.plugin import (
        ScreeningViewBuilderService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    p1 = _paper("T", doi="10.123/a", abstract="short", provider_marker="crossref")
    p2 = _paper(
        "T",
        doi="10.123/a",
        abstract="this is a much longer abstract with more richness",
        provider_marker="semantic_scholar",
    )
    e1, _ = await _put_paper_with_snapshot(store, p1, "crossref", "10.123/a")
    e2, _ = await _put_paper_with_snapshot(store, p2, "semantic_scholar", "10.123/a")
    identity = PaperIdentity(
        member_paper_artifact_ids=[e1.artifact_id, e2.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[
            IdentityEvidence(
                identifier_scheme="doi",
                normalized_value="10.123/a",
                member_artifact_ids=[e1.artifact_id, e2.artifact_id],
            )
        ],
    )
    ident_env = ArtifactEnvelope.create(
        payload=identity, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)
    svc = ScreeningViewBuilderService(artifact_store=store)
    view_id = await svc.build(ident_env.artifact_id)
    from research_harness.research.schemas.screening_view import PaperScreeningView

    view = (await store.get(view_id)).parse_payload(PaperScreeningView)
    assert view.abstract == "this is a much longer abstract with more richness"
    assert view.field_sources["abstract"].provider == "semantic_scholar"
    await store.close()


@pytest.mark.asyncio
async def test_view_builder_missing_abstract_flag(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_view_builder.plugin import (
        ScreeningViewBuilderService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    p1 = _paper("T", doi="10.123/a", abstract=None, provider_marker="crossref")
    e1, _ = await _put_paper_with_snapshot(store, p1, "crossref", "10.123/a")
    identity = PaperIdentity(
        member_paper_artifact_ids=[e1.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=identity, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)
    svc = ScreeningViewBuilderService(artifact_store=store)
    view_id = await svc.build(ident_env.artifact_id)
    from research_harness.research.schemas.screening_view import PaperScreeningView

    view = (await store.get(view_id)).parse_payload(PaperScreeningView)
    assert view.abstract is None
    assert view.metadata.get("missing_abstract") is True
    await store.close()


@pytest.mark.asyncio
async def test_view_builder_year_most_common(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_view_builder.plugin import (
        ScreeningViewBuilderService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    p1 = _paper("T", doi="10.123/a", year=2020)
    p2 = _paper("T", doi="10.123/a", year=2020)
    p3 = _paper("T", doi="10.123/a", year=2021)
    e1, _ = await _put_paper_with_snapshot(store, p1, "crossref", "1")
    e2, _ = await _put_paper_with_snapshot(store, p2, "crossref", "2")
    e3, _ = await _put_paper_with_snapshot(store, p3, "crossref", "3")
    identity = PaperIdentity(
        member_paper_artifact_ids=[e1.artifact_id, e2.artifact_id, e3.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=identity, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)
    svc = ScreeningViewBuilderService(artifact_store=store)
    view_id = await svc.build(ident_env.artifact_id)
    from research_harness.research.schemas.screening_view import PaperScreeningView

    view = (await store.get(view_id)).parse_payload(PaperScreeningView)
    assert view.year == 2020
    assert "year_conflicts" in view.metadata
    await store.close()


@pytest.mark.asyncio
async def test_view_builder_idempotent_same_members(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_view_builder.plugin import (
        ScreeningViewBuilderService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    p1 = _paper("T", doi="10.123/a", abstract="abs")
    e1, _ = await _put_paper_with_snapshot(store, p1, "crossref", "10.123/a")
    identity = PaperIdentity(
        member_paper_artifact_ids=[e1.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=identity, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)
    svc = ScreeningViewBuilderService(artifact_store=store)
    v1 = await svc.build(ident_env.artifact_id)
    v2 = await svc.build(ident_env.artifact_id)
    assert v1 == v2
    # Ensure only one view persisted
    views = await store.list(artifact_type="paper_screening_view")
    assert len(views) == 1
    await store.close()


@pytest.mark.asyncio
async def test_view_builder_provenance(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_view_builder.plugin import (
        ScreeningViewBuilderService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    p1 = _paper("T", doi="10.123/a")
    p2 = _paper("T", doi="10.123/a")
    e1, _ = await _put_paper_with_snapshot(store, p1, "crossref", "1")
    e2, _ = await _put_paper_with_snapshot(store, p2, "semantic_scholar", "2")
    identity = PaperIdentity(
        member_paper_artifact_ids=[e1.artifact_id, e2.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=identity, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)
    svc = ScreeningViewBuilderService(artifact_store=store)
    view_id = await svc.build(ident_env.artifact_id)
    parents = await store.get_parents(view_id)
    source_ids = {p.source_artifact_id for p in parents}
    assert ident_env.artifact_id in source_ids
    assert e1.artifact_id in source_ids
    assert e2.artifact_id in source_ids
    for p in parents:
        assert p.relation == ProvenanceRelation.derived_from
    await store.close()


@pytest.mark.asyncio
async def test_view_builder_fallback_provider_when_no_snapshot(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_view_builder.plugin import (
        ScreeningViewBuilderService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    # No snapshot provenance, rely on metadata fallback
    p1 = _paper("T", doi="10.123/a", provider_marker="crossref")
    p_env = ArtifactEnvelope.create(payload=p1, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    identity = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    ident_env = ArtifactEnvelope.create(
        payload=identity, artifact_type="paper_identity", producer="test"
    )
    await store.put(ident_env)
    svc = ScreeningViewBuilderService(artifact_store=store)
    view_id = await svc.build(ident_env.artifact_id)
    from research_harness.research.schemas.screening_view import PaperScreeningView

    view = (await store.get(view_id)).parse_payload(PaperScreeningView)
    assert view.field_sources["title"].provider == "crossref"
    await store.close()
