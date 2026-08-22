import pathlib

import pytest

from research_harness.app.bootstrap import build_runtime
from research_harness.config.loader import load_config_from_dict
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.claim import ClaimType, ResearchClaim
from research_harness.research.schemas.evidence import EvidenceItem, Locator
from research_harness.research.schemas.paper import PaperRecord
from research_harness.research.schemas.project import ResearchQuestion


@pytest.mark.asyncio
async def test_paper_evidence_claim_lineage(tmp_path: pathlib.Path):
    # Build runtime with artifact store
    cfg = load_config_from_dict(
        {
            "plugins": ["session.jsonl", "storage.artifacts_sqlite"],
            "session": {"root": str(tmp_path / "sessions")},
            "artifacts": {"store": "sqlite", "path": str(tmp_path / "artifacts.db")},
        }
    )
    runtime = build_runtime(cfg)
    async with runtime:
        store = runtime.services.require("artifact_store.default")
        # Create ResearchQuestion
        rq = ArtifactEnvelope.create(
            payload=ResearchQuestion(question="What is X?"),
            artifact_type="research_question",
            producer="test",
        )
        await store.put(rq)
        # Create PaperRecord
        paper = ArtifactEnvelope.create(
            payload=PaperRecord(title="Paper P1", year=2020, doi="10.123/abc"),
            artifact_type="paper_record",
            producer="test",
        )
        await store.put(paper)
        # Create EvidenceItem extracted_from PaperRecord
        evidence = ArtifactEnvelope.create(
            payload=EvidenceItem(
                statement="Finding X",
                source_artifact_id=paper.artifact_id,
                locator=Locator(section="abstract"),
                extraction_method="human",
            ),
            artifact_type="evidence_item",
            producer="test",
        )
        await store.put(evidence)
        # Link evidence -> paper
        await store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.extracted_from,
                source_artifact_id=paper.artifact_id,
                target_artifact_id=evidence.artifact_id,
                producer="test",
            )
        )
        # Create ResearchClaim derived_from Evidence
        claim = ArtifactEnvelope.create(
            payload=ResearchClaim(
                statement="Claim C1",
                claim_type=ClaimType.fact,
                evidence_refs=[evidence.artifact_id],
            ),
            artifact_type="research_claim",
            producer="test",
        )
        await store.put(claim)
        await store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=evidence.artifact_id,
                target_artifact_id=claim.artifact_id,
                producer="test",
            )
        )

        # Retrieve claim and walk lineage backward
        fetched_claim = await store.get(claim.artifact_id)
        assert fetched_claim.parse_payload(ResearchClaim).statement == "Claim C1"

        # Walk ancestors of claim: should be evidence and paper
        ancestors = await store.get_lineage(claim.artifact_id, direction="ancestors")
        ancestor_ids = {e.artifact_id for e in ancestors}
        assert evidence.artifact_id in ancestor_ids
        assert paper.artifact_id in ancestor_ids

        # Walk descendants of paper: should be evidence and claim
        descendants = await store.get_lineage(paper.artifact_id, direction="descendants")
        desc_ids = {e.artifact_id for e in descendants}
        assert evidence.artifact_id in desc_ids
        assert claim.artifact_id in desc_ids

        # Ensure session trajectory and artifact store are separate:
        # Session store should have session events, artifact store has research objects
        # Check that artifact store does not contain session events and vice versa
        # Session events are via EventBus, artifact events are via store
        # Verify artifact store list
        all_artifacts = await store.list()
        assert len(all_artifacts) == 4
        # Check types
        types = {e.artifact_type for e in all_artifacts}
        assert "paper_record" in types
        assert "evidence_item" in types
        assert "research_claim" in types


@pytest.mark.asyncio
async def test_artifact_events_not_authoritative_for_content(tmp_path: pathlib.Path):
    # Ensure artifact.created event does not become authoritative storage
    from research_harness.kernel.events import EventBus

    bus = EventBus()
    cfg = load_config_from_dict(
        {
            "plugins": ["storage.artifacts_sqlite"],
            "artifacts": {"store": "sqlite", "path": str(tmp_path / "artifacts.db")},
        }
    )
    # Build runtime with our bus
    from research_harness.kernel.services import ServiceRegistry
    from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore

    services = ServiceRegistry()
    events = bus

    # Manually create store with bus
    store = SQLiteArtifactStore(path=tmp_path / "artifacts.db", events=bus)
    # Put artifact
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)
    # Check that bus received artifact.created but store is authoritative
    assert any(e.event_type == "artifact.created" for e in bus.history())
    # Retrieve from store is authoritative
    fetched = await store.get(rq.artifact_id)
    assert fetched.parse_payload(ResearchQuestion).question == "Q"
    # Bus history alone is not sufficient for replay — need store
    # Simulate clearing bus history: store still has data
    bus.clear_history()
    still = await store.get(rq.artifact_id)
    assert still is not None
    await store.close()
