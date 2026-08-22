import pathlib

import pytest

from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.paper import PaperRecord


@pytest.mark.asyncio
async def test_same_doi_merged(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.identity_resolver.plugin import (
        PaperIdentityResolverService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    resolver = PaperIdentityResolverService(artifact_store=store)

    p1 = ArtifactEnvelope.create(
        payload=PaperRecord(title="Paper A", doi="10.123/a"), artifact_type="paper_record"
    )
    p2 = ArtifactEnvelope.create(
        payload=PaperRecord(title="Paper B same DOI", doi="10.123/a"), artifact_type="paper_record"
    )
    await store.put(p1)
    await store.put(p2)

    result = await resolver.resolve([p1.artifact_id, p2.artifact_id])
    assert len(result.identities_created) == 1
    assert len(result.matches) == 1
    assert result.matches[0].method == "exact_identifier"
    # Both members in same identity
    assert set(result.matches[0].member_paper_artifact_ids) == {p1.artifact_id, p2.artifact_id}
    # Check identity artifact
    identity_id = result.identities_created[0]
    identity_env = await store.get(identity_id)
    from research_harness.research.schemas.identity import PaperIdentity

    identity = identity_env.parse_payload(PaperIdentity)
    assert set(identity.member_paper_artifact_ids) == {p1.artifact_id, p2.artifact_id}
    assert identity.resolution_method.value == "exact_identifier"
    await store.close()


@pytest.mark.asyncio
async def test_different_doi_separate(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.identity_resolver.plugin import (
        PaperIdentityResolverService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    resolver = PaperIdentityResolverService(artifact_store=store)

    p1 = ArtifactEnvelope.create(
        payload=PaperRecord(title="A", doi="10.123/a"), artifact_type="paper_record"
    )
    p2 = ArtifactEnvelope.create(
        payload=PaperRecord(title="B", doi="10.123/b"), artifact_type="paper_record"
    )
    await store.put(p1)
    await store.put(p2)

    result = await resolver.resolve([p1.artifact_id, p2.artifact_id])
    assert len(result.identities_created) == 2
    # Each identity should have single member
    for m in result.matches:
        assert len(m.member_paper_artifact_ids) == 1
    await store.close()


@pytest.mark.asyncio
async def test_same_arxiv_merged(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.identity_resolver.plugin import (
        PaperIdentityResolverService,
    )
    from research_harness.research.schemas.common import ExternalIdentifier

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    resolver = PaperIdentityResolverService(artifact_store=store)

    p1 = ArtifactEnvelope.create(
        payload=PaperRecord(
            title="P1",
            external_identifiers=[ExternalIdentifier(scheme="arxiv", value="2101.00001")],
        ),
        artifact_type="paper_record",
    )
    p2 = ArtifactEnvelope.create(
        payload=PaperRecord(
            title="P2",
            external_identifiers=[ExternalIdentifier(scheme="arxiv", value="2101.00001")],
        ),
        artifact_type="paper_record",
    )
    await store.put(p1)
    await store.put(p2)

    result = await resolver.resolve([p1.artifact_id, p2.artifact_id])
    assert len(result.identities_created) == 1
    await store.close()


@pytest.mark.asyncio
async def test_exact_content_duplicate(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.identity_resolver.plugin import (
        PaperIdentityResolverService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    resolver = PaperIdentityResolverService(artifact_store=store)

    # Same payload exactly
    p1 = ArtifactEnvelope.create(
        payload=PaperRecord(title="Same", year=2020, doi="10.123/a"), artifact_type="paper_record"
    )
    p2 = ArtifactEnvelope.create(
        payload=PaperRecord(title="Same", year=2020, doi="10.123/a"), artifact_type="paper_record"
    )
    # They will have same content_hash but different artifact_ids
    assert p1.content_hash == p2.content_hash
    await store.put(p1)
    await store.put(p2)

    result = await resolver.resolve([p1.artifact_id, p2.artifact_id])
    # Should be merged via exact_content or DOI
    assert len(result.identities_created) == 1
    await store.close()


@pytest.mark.asyncio
async def test_similar_title_only_not_merged(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.identity_resolver.plugin import (
        PaperIdentityResolverService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    resolver = PaperIdentityResolverService(artifact_store=store)

    p1 = ArtifactEnvelope.create(
        payload=PaperRecord(title="Algorithmic Pricing in Platforms", year=2020),
        artifact_type="paper_record",
    )
    p2 = ArtifactEnvelope.create(
        payload=PaperRecord(title="Algorithmic Pricing on Platforms", year=2020),
        artifact_type="paper_record",
    )
    await store.put(p1)
    await store.put(p2)

    result = await resolver.resolve([p1.artifact_id, p2.artifact_id])
    assert len(result.identities_created) == 2
    # Each should be separate
    await store.close()


@pytest.mark.asyncio
async def test_missing_identifiers_separate(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.identity_resolver.plugin import (
        PaperIdentityResolverService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    resolver = PaperIdentityResolverService(artifact_store=store)

    p1 = ArtifactEnvelope.create(payload=PaperRecord(title="P1"), artifact_type="paper_record")
    p2 = ArtifactEnvelope.create(payload=PaperRecord(title="P2"), artifact_type="paper_record")
    await store.put(p1)
    await store.put(p2)

    result = await resolver.resolve([p1.artifact_id, p2.artifact_id])
    assert len(result.identities_created) == 2
    await store.close()


@pytest.mark.asyncio
async def test_identity_revision_supersedes(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.identity_resolver.plugin import (
        PaperIdentityResolverService,
    )
    from research_harness.research.provenance.relations import ProvenanceRelation

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    resolver = PaperIdentityResolverService(artifact_store=store)

    p1 = ArtifactEnvelope.create(
        payload=PaperRecord(title="P1", doi="10.123/a"), artifact_type="paper_record"
    )
    p2 = ArtifactEnvelope.create(
        payload=PaperRecord(title="P2", doi="10.123/a"), artifact_type="paper_record"
    )
    await store.put(p1)
    await store.put(p2)

    result1 = await resolver.resolve([p1.artifact_id, p2.artifact_id])
    assert len(result1.identities_created) == 1
    pi1 = result1.identities_created[0]

    # Later discover P3 with same DOI
    p3 = ArtifactEnvelope.create(
        payload=PaperRecord(title="P3", doi="10.123/a"), artifact_type="paper_record"
    )
    await store.put(p3)

    result2 = await resolver.resolve([p1.artifact_id, p2.artifact_id, p3.artifact_id])
    assert len(result2.identities_created) == 1
    pi2 = result2.identities_created[0]
    assert pi2 != pi1
    # PI2 should supersede PI1
    assert pi1 in result2.identities_superseded
    # Check provenance
    parents = await store.get_parents(pi2)
    # Should have supersedes link? Actually supersedes is from old to new, so pi1 -> pi2 via supersedes
    # Our resolver creates supersedes edge where source=old, target=new
    # So pi2's parents should contain pi1 with supersedes?
    # Check get_parents for pi2
    assert any(
        p.relation == ProvenanceRelation.supersedes and p.source_artifact_id == pi1 for p in parents
    )
    # Old should still be retrievable
    old_env = await store.get(pi1)
    assert old_env is not None
    await store.close()


@pytest.mark.asyncio
async def test_idempotency_reuse(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.identity_resolver.plugin import (
        PaperIdentityResolverService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    resolver = PaperIdentityResolverService(artifact_store=store)

    p1 = ArtifactEnvelope.create(
        payload=PaperRecord(title="P1", doi="10.123/a"), artifact_type="paper_record"
    )
    p2 = ArtifactEnvelope.create(
        payload=PaperRecord(title="P2", doi="10.123/a"), artifact_type="paper_record"
    )
    await store.put(p1)
    await store.put(p2)

    result1 = await resolver.resolve([p1.artifact_id, p2.artifact_id])
    pi1 = result1.identities_created[0]
    # Re-run with same set
    result2 = await resolver.resolve([p1.artifact_id, p2.artifact_id])
    assert len(result2.identities_created) == 0
    assert len(result2.identities_reused) == 1
    assert result2.identities_reused[0] == pi1
    assert len(result2.identities_superseded) == 0
    await store.close()


@pytest.mark.asyncio
async def test_paper_records_not_supersede(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.identity_resolver.plugin import (
        PaperIdentityResolverService,
    )
    from research_harness.research.provenance.relations import ProvenanceRelation

    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db")
    resolver = PaperIdentityResolverService(artifact_store=store)

    p1 = ArtifactEnvelope.create(
        payload=PaperRecord(title="P1", doi="10.123/a"), artifact_type="paper_record"
    )
    p2 = ArtifactEnvelope.create(
        payload=PaperRecord(title="P2", doi="10.123/a"), artifact_type="paper_record"
    )
    await store.put(p1)
    await store.put(p2)

    await resolver.resolve([p1.artifact_id, p2.artifact_id])
    # Check that PaperRecords themselves do not have supersedes edges
    parents_p1 = await store.get_parents(p1.artifact_id)
    children_p1 = await store.get_children(p1.artifact_id)
    # P1 should have no supersedes relation
    for link in parents_p1 + children_p1:
        assert (
            link.relation != ProvenanceRelation.supersedes
            or link.source_artifact_id != p1.artifact_id
            or link.target_artifact_id != p2.artifact_id
        )
    # Only identities supersede
    await store.close()
