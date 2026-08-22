import pytest
from pydantic import BaseModel

from research_harness.research.envelope import ArtifactEnvelope, compute_content_hash
from research_harness.research.schemas.paper import PaperRecord


class DummyPayload(BaseModel):
    x: int
    y: str

    model_config = {"extra": "forbid"}


def test_id_generation():
    p = DummyPayload(x=1, y="a")
    e1 = ArtifactEnvelope.create(payload=p, artifact_type="dummy", producer="test")
    e2 = ArtifactEnvelope.create(payload=p, artifact_type="dummy", producer="test")
    assert e1.artifact_id != e2.artifact_id
    assert e1.content_hash == e2.content_hash  # same payload => same hash


def test_timestamps():
    p = DummyPayload(x=1, y="a")
    e = ArtifactEnvelope.create(payload=p, artifact_type="dummy")
    assert e.created_at is not None
    # Should be UTC
    assert e.created_at.tzinfo is not None


def test_schema_version():
    p = DummyPayload(x=1, y="a")
    e = ArtifactEnvelope.create(payload=p, artifact_type="dummy", schema_version=1)
    assert e.schema_version == 1
    # Payload's own schema_version is separate
    pr = PaperRecord(title="T")
    e2 = ArtifactEnvelope.create(payload=pr, artifact_type="paper_record", schema_version=2)
    assert e2.schema_version == 2
    assert e2.payload.schema_version == 1


def test_content_hashing_deterministic():
    p1 = PaperRecord(title="Test", authors=[], year=2020)
    p2 = PaperRecord(title="Test", authors=[], year=2020)
    # Same payload should produce same hash, even if envelope metadata differs
    e1 = ArtifactEnvelope.create(
        payload=p1, artifact_type="paper_record", session_id="s1", producer="p1"
    )
    e2 = ArtifactEnvelope.create(
        payload=p2, artifact_type="paper_record", session_id="s2", producer="p2"
    )
    assert e1.content_hash == e2.content_hash
    assert e1.artifact_id != e2.artifact_id
    assert e1.session_id != e2.session_id
    # Hash should be SHA-256 hex length 64
    assert len(e1.content_hash) == 64
    # Different payload => different hash
    p3 = PaperRecord(title="Different", authors=[], year=2020)
    e3 = ArtifactEnvelope.create(payload=p3, artifact_type="paper_record")
    assert e1.content_hash != e3.content_hash


def test_hash_not_include_envelope_metadata():
    p = DummyPayload(x=1, y="a")
    # Create envelope, then create another with same payload but different metadata
    e1 = ArtifactEnvelope.create(payload=p, artifact_type="dummy", metadata={"a": 1})
    e2 = ArtifactEnvelope.create(payload=p, artifact_type="dummy", metadata={"a": 2})
    # Metadata should not affect hash
    assert e1.content_hash == e2.content_hash
    # Also artifact_type not in payload hash (envelope type), but payload is same
    # Changing artifact_type should not affect computed hash from payload alone, but envelope's stored hash is based on payload only
    # So both still same hash even though type differs
    e3 = ArtifactEnvelope.create(payload=p, artifact_type="other_type")
    assert e1.content_hash == e3.content_hash


def test_compute_content_hash_canonical():
    # Dict and model with same data should produce same hash when payload dict is canonical
    payload_dict = {"x": 1, "y": "a"}
    p = DummyPayload(x=1, y="a")
    # Compute via dict vs model should be same because model_dump produces same dict
    h1 = compute_content_hash(payload_dict)
    h2 = compute_content_hash(p)
    assert h1 == h2
    # Order shouldn't matter for dict with sorted keys
    h3 = compute_content_hash({"y": "a", "x": 1})
    assert h1 == h3


def test_serialization_round_trip():
    p = PaperRecord(title="T", year=2021, doi="10.123/abc")
    e = ArtifactEnvelope.create(
        payload=p, artifact_type="paper_record", producer="test", session_id="s1"
    )
    data = e.model_dump(mode="json")
    # Should contain expected fields
    assert data["artifact_id"] == e.artifact_id
    assert data["artifact_type"] == e.artifact_type
    assert data["content_hash"] == e.content_hash
    assert data["payload"]["title"] == "T"
    # Re-validate via model_validate
    # Need to specify generic type
    e2 = ArtifactEnvelope[PaperRecord].model_validate(data)
    assert e2.artifact_id == e.artifact_id
    assert e2.payload.title == "T"


def test_immutability():
    p = DummyPayload(x=1, y="a")
    e = ArtifactEnvelope.create(payload=p, artifact_type="dummy")
    with pytest.raises(Exception):
        e.artifact_id = "new"  # type: ignore[misc]
    with pytest.raises(Exception):
        e.payload = DummyPayload(x=2, y="b")  # type: ignore[misc]


def test_artifact_type_required():
    p = DummyPayload(x=1, y="a")
    with pytest.raises(Exception):
        ArtifactEnvelope.create(payload=p, artifact_type="   ")
