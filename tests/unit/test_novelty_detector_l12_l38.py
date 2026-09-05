"""Regression tests for the novelty detector (round 21).

Batch 3 of the §9 triage: L12, L13, L14, L38.

L12 and L13 have pure, directly testable pieces (the detector and the
dispute-downgrade helper), so they are covered here. L14 and the end-to-end
critic behaviour need the full report pipeline and live in
`tests/unit/test_novelty.py`, which already has the fixtures.
"""

from __future__ import annotations

import pathlib

import pytest

from research_harness.plugins.research.novelty_validator import detection
from research_harness.plugins.research.novelty_validator.plugin import (
    NoveltyValidationService,
)
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.novelty import (
    CandidateRelationship,
    CriticVerdict,
    EvidenceBasis,
    NoveltyCandidateAssessment,
)

# ---------------------------------------------------------------------------
# L12 — the canonical priority phrase must be detectable
# ---------------------------------------------------------------------------


def test_for_the_first_time_is_detected():
    """L12: the phrase was blacklisted, so the pattern could never fire.

    `_BLACKLIST` contained `first[- ]time`, and every match is searched inside
    its own blacklist window — so the two patterns that exist solely to catch
    "for the first time" were unreachable and the highest-risk novelty signal
    was invisible.
    """
    findings = detection.detect_high_risk("We apply the method for the first time.")
    assert findings, "the canonical absolute-priority phrase was not detected"
    assert (findings[0][0], findings[0][1]) == ("absolute_priority", "critical")


def test_overlapping_spans_keep_the_higher_risk():
    """L12: the merge kept the earlier span's risk, contradicting its comment.

    "shows for the first time" matches both patterns; the result_novelty (high)
    span starts first, so the critical absolute_priority span used to be
    discarded in favour of the weaker one.
    """
    findings = detection.detect_high_risk(
        "This study shows for the first time that prices adjust."
    )
    assert findings, "neither pattern fired"
    risk = findings[0][1]
    assert risk == "critical", f"expected the higher risk, got {risk!r}"
    assert findings[0][0] == "absolute_priority"


def test_technical_first_order_is_still_blacklisted():
    """The blacklist must still suppress genuinely technical uses."""
    assert detection.detect_high_risk("The first-order condition determines the optimum.") == []


def test_hyphenated_first_time_compound_is_not_a_claim():
    """Guards the L12 fix: removing `time` must not create false positives.

    The technical uses of "first time" are hyphenated compounds, and they
    cannot match any pattern — all of which require the full "for the first
    time" phrasing.
    """
    assert detection.detect_high_risk("We study first-time buyers in this market.") == []


# ---------------------------------------------------------------------------
# L13 — a critic dispute must have a consumer
# ---------------------------------------------------------------------------


def _assessment(relationship: CandidateRelationship) -> NoveltyCandidateAssessment:
    return NoveltyCandidateAssessment(
        claim_id="c1",
        candidate_set_id="cs1",
        paper_identity_id="p1",
        relationship=relationship,
        evidence_basis=EvidenceBasis.abstract,
    )


def _svc() -> NoveltyValidationService:
    return NoveltyValidationService.__new__(NoveltyValidationService)


def test_dispute_downgrades_a_threat():
    """L13: the critic's verdict used to change nothing."""
    out = _svc()._apply_critic_dispute(
        _assessment(CandidateRelationship.direct_prior_art), CriticVerdict.disputes
    )
    assert out.relationship == CandidateRelationship.insufficient_evidence
    assert out.metadata.get("critic_disputed") is True
    assert "disputed" in out.assessment_text


def test_dispute_downgrades_partial_overlap_too():
    out = _svc()._apply_critic_dispute(
        _assessment(CandidateRelationship.partial_overlap), CriticVerdict.disputes
    )
    assert out.relationship == CandidateRelationship.insufficient_evidence


def test_dispute_does_not_invent_a_threat():
    """A dispute is not evidence, so a non-threat is never promoted.

    Downgrading is deliberately one-directional: a critic saying "this does not
    threaten" was wrong is not grounds to assert a threat.
    """
    out = _svc()._apply_critic_dispute(
        _assessment(CandidateRelationship.distinct), CriticVerdict.disputes
    )
    assert out.relationship == CandidateRelationship.distinct


def test_concurs_and_uncertain_leave_the_assessment_alone():
    for verdict in (CriticVerdict.concurs, CriticVerdict.uncertain):
        out = _svc()._apply_critic_dispute(
            _assessment(CandidateRelationship.direct_prior_art), verdict
        )
        assert out.relationship == CandidateRelationship.direct_prior_art


# ---------------------------------------------------------------------------
# L38 — evidence items resolved without a store round-trip per item
# ---------------------------------------------------------------------------


@pytest.fixture()
async def store(tmp_path: pathlib.Path):
    from research_harness.plugins.storage.artifacts_sqlite.plugin import (
        SQLiteArtifactStore,
    )

    s = SQLiteArtifactStore(path=tmp_path / "art.db")
    yield s
    await s.close()


async def test_identity_full_text_doc_ids(store):
    """L38: the identity's documents come from one listing, not per-item gets."""

    async def _put(payload, artifact_type: str, artifact_id: str) -> None:
        await store.put(
            ArtifactEnvelope.create(
                payload=payload,
                artifact_type=artifact_type,
                producer="test",
                artifact_id=artifact_id,
            )
        )

    from research_harness.contracts.blob import BlobReference
    from research_harness.research.schemas.full_text import (
        FullTextDocument,
        TextStatus,
    )

    def _doc(identity_id: str) -> FullTextDocument:
        blob = BlobReference(digest="ab" * 32, size_bytes=10, storage_key="ab/abcd")
        return FullTextDocument(
            paper_identity_id=identity_id,
            document_acquisition_id=f"acq-{identity_id}",
            source_blob=blob,
            extractor="documents.extractor.pypdf",
            page_count=1,
            pages_with_text=1,
            character_count=10,
            text_status=TextStatus.extracted,
        )

    await _put(_doc("ident-1"), "full_text_document", "doc-1")
    await _put(_doc("ident-2"), "full_text_document", "doc-2")

    got = await _svc_with(store)._identity_full_text_doc_ids("ident-1")
    assert got == {"doc-1"}, f"expected only this identity's documents, got {got}"


def _svc_with(store) -> NoveltyValidationService:
    svc = NoveltyValidationService.__new__(NoveltyValidationService)
    svc._store = store
    return svc
