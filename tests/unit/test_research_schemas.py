import pytest

from research_harness.research.schemas.claim import ClaimType, ResearchClaim
from research_harness.research.schemas.common import ExternalIdentifier, normalize_doi
from research_harness.research.schemas.evidence import EvidenceItem, Locator
from research_harness.research.schemas.paper import Author, PaperRecord
from research_harness.research.schemas.project import ResearchPlan, ResearchQuestion
from research_harness.research.schemas.source import SourceRecord, SourceType


def test_research_question_validation():
    rq = ResearchQuestion(question="What is X?")
    assert rq.question == "What is X?"
    assert rq.status.value == "open"
    with pytest.raises(Exception):
        ResearchQuestion(question="   ")
    # Round-trip
    data = rq.model_dump(mode="json")
    rq2 = ResearchQuestion.model_validate(data)
    assert rq2.question == rq.question


def test_research_plan_validation():
    rp = ResearchPlan(objective="Find gap", steps=["a", "b"], search_concepts=["c"])
    assert rp.objective == "Find gap"
    with pytest.raises(Exception):
        ResearchPlan(objective="   ")
    # With question id
    rp2 = ResearchPlan(objective="obj", research_question_id="some-id")
    assert rp2.research_question_id == "some-id"


def test_paper_record_partial_metadata():
    p = PaperRecord(title="Test Paper")
    assert p.title == "Test Paper"
    assert p.authors == []
    assert p.year is None
    assert p.doi is None
    # With doi
    p2 = PaperRecord(title="T", doi="10.1234/abc")
    assert p2.doi == "10.1234/abc"
    # Serialization round-trip
    data = p2.model_dump(mode="json")
    p3 = PaperRecord.model_validate(data)
    assert p3.doi == p2.doi


def test_author_serialization():
    a = Author(
        name="John Doe", external_ids=[ExternalIdentifier(scheme="orcid", value="0000-0001")]
    )
    p = PaperRecord(title="T", authors=[a])
    data = p.model_dump(mode="json")
    assert data["authors"][0]["name"] == "John Doe"
    p2 = PaperRecord.model_validate(data)
    assert p2.authors[0].name == "John Doe"


def test_external_identifier_normalization():
    eid = ExternalIdentifier(scheme="DOI", value=" 10.1234/ABC ")
    norm = eid.normalized()
    assert norm.scheme == "doi"
    # DOI lower-cased
    assert norm.value == "10.1234/abc"


def test_doi_normalization():
    cases = [
        ("10.1234/abc", "10.1234/abc"),
        ("https://doi.org/10.1234/ABC", "10.1234/abc"),
        ("http://dx.doi.org/10.1234/abc", "10.1234/abc"),
        ("doi:10.1234/ABC", "10.1234/abc"),
        ("  https://doi.org/10.1234/abc  ", "10.1234/abc"),
    ]
    for raw, expected in cases:
        assert normalize_doi(raw) == expected, f"failed for {raw!r}"
    # PaperRecord doi field also normalized
    p = PaperRecord(title="T", doi="https://doi.org/10.1234/ABC")
    assert p.doi == "10.1234/abc"
    p2 = PaperRecord(title="T", doi="doi:10.1234/XYZ")
    assert p2.doi == "10.1234/xyz"


def test_source_record():
    s = SourceRecord(title="Source", source_type=SourceType.paper, url="https://example.com")
    assert s.title == "Source"
    assert s.source_type == SourceType.paper
    # With external identifiers
    s2 = SourceRecord(
        title="T", external_identifiers=[ExternalIdentifier(scheme="doi", value="10.123/abc")]
    )
    assert s2.external_identifiers[0].value == "10.123/abc"


def test_evidence_requires_source():
    # Valid
    e = EvidenceItem(statement="observed", source_artifact_id="abc-123")
    assert e.source_artifact_id == "abc-123"
    # Missing source should fail
    with pytest.raises(Exception):
        EvidenceItem(statement="obs", source_artifact_id="   ")
    with pytest.raises(Exception):
        EvidenceItem(statement="   ", source_artifact_id="id")
    # With locator
    loc = Locator(section="abstract", page=1)
    e2 = EvidenceItem(
        statement="s",
        source_artifact_id="id",
        locator=loc,
        extraction_method="human",
        confidence=0.9,
    )
    assert e2.locator.section == "abstract"
    assert e2.confidence == 0.9


def test_locator():
    loc = Locator(page=2, section="results", paragraph=3)
    assert loc.page == 2
    data = loc.model_dump(mode="json")
    loc2 = Locator.model_validate(data)
    assert loc2.page == 2


def test_research_claim_types():
    # fact requires evidence (soft check via requires_evidence)
    c_fact = ResearchClaim(statement="fact", claim_type=ClaimType.fact, evidence_refs=["e1"])
    assert c_fact.requires_evidence() is True
    assert c_fact.claim_type == ClaimType.fact

    c_hyp = ResearchClaim(statement="hypothesis", claim_type=ClaimType.hypothesis, evidence_refs=[])
    assert c_hyp.requires_evidence() is False
    assert c_hyp.claim_type == ClaimType.hypothesis

    # Validation
    with pytest.raises(Exception):
        ResearchClaim(statement="   ", claim_type=ClaimType.fact)
    # Evidence refs with empty should fail
    with pytest.raises(Exception):
        ResearchClaim(statement="s", claim_type=ClaimType.fact, evidence_refs=["   "])


def test_claim_round_trip():
    c = ResearchClaim(
        statement="s", claim_type=ClaimType.inference, evidence_refs=["a"], confidence=0.8
    )
    data = c.model_dump(mode="json")
    c2 = ResearchClaim.model_validate(data)
    assert c2.statement == "s"
    assert c2.confidence == 0.8
