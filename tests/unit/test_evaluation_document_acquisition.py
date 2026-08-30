"""Phase 7A unit tests — document-acquisition evaluator.

Covers: acquisition status classification, text-extraction status, corpus
availability (available/unavailable/restricted/failed), fallback usage, and
duplicate-blob reuse.
"""

from __future__ import annotations

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_document_acquisition.plugin import (
    DocumentAcquisitionEvaluator,
)
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus


def _identity_env(iid: str) -> ArtifactEnvelope:
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod

    return ArtifactEnvelope.create(
        payload=PaperIdentity(
            member_paper_artifact_ids=[f"{iid}-paper"],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.manual,
            resolution_evidence=[],
        ),
        artifact_type="paper_identity",
        producer="test",
        artifact_id=iid,
    )


def _acq_env(
    aid: str,
    paper_id: str,
    status: str,
    *,
    location_id: str | None = None,
    sha: str | None = None,
) -> ArtifactEnvelope:
    from research_harness.research.schemas.document_acquisition import (
        AcquisitionStatus,
        DocumentAcquisition,
    )

    return ArtifactEnvelope.create(
        payload=DocumentAcquisition(
            paper_identity_id=paper_id,
            document_location_id=location_id,
            status=AcquisitionStatus(status),
            sha256=sha,
            source_type="http",
        ),
        artifact_type="document_acquisition",
        producer="test",
        artifact_id=aid,
    )


def _doc_env(doc_id: str, acq_id: str, paper_id: str, text_status: str) -> ArtifactEnvelope:
    from research_harness.research.schemas.full_text import FullTextDocument, TextStatus

    return ArtifactEnvelope.create(
        payload=FullTextDocument(
            paper_identity_id=paper_id,
            document_acquisition_id=acq_id,
            source_blob={
                "digest": "x",
                "size_bytes": 1,
                "storage_key": "x",
                "media_type": "application/pdf",
            },
            text_blob=None,
            extractor="documents.extractor.pypdf",
            page_count=1,
            pages_with_text=1,
            character_count=10,
            text_status=TextStatus(text_status),
        ),
        artifact_type="full_text_document",
        producer="test",
        artifact_id=doc_id,
    )


def _corpus_env(
    *,
    available: list[str],
    unavailable: list[str] | None = None,
    restricted: list[str] | None = None,
    failed: list[str] | None = None,
) -> ArtifactEnvelope:
    from research_harness.research.schemas.full_text import FullTextCorpus

    return ArtifactEnvelope.create(
        payload=FullTextCorpus(
            document_acquisition_execution_id="exec",
            screened_literature_set_id="set",
            available_document_ids=available,
            unavailable_identity_ids=list(unavailable or []),
            restricted_identity_ids=list(restricted or []),
            failed_identity_ids=list(failed or []),
        ),
        artifact_type="full_text_corpus",
        producer="test",
    )


def _loc_env(loc_id: str, paper_id: str, url: str) -> ArtifactEnvelope:
    from research_harness.research.schemas.document_location import DocumentLocation

    return ArtifactEnvelope.create(
        payload=DocumentLocation(
            paper_identity_id=paper_id,
            resolver="documents.locator.metadata",
            url=url,
        ),
        artifact_type="document_location",
        producer="test",
        artifact_id=loc_id,
    )


def _case(reference: dict) -> BenchmarkCase:
    return BenchmarkCase(
        id="c1",
        benchmark_id="b",
        version=1,
        name="case",
        input={},
        reference=reference,
        evaluation_dimensions=["acquisition"],
        tags=[],
    )


def _ctx(case: BenchmarkCase, produced: list) -> EvaluatorContext:
    return EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=produced,
        config={},
    )


async def test_valid_download_with_extraction_passes():
    ref = {
        "expected_statuses": {"acq-paper-0": "downloaded"},
        "expected_text_status": {"acq-paper-0": "extracted"},
        "expected_corpus_available": ["acq-paper-0"],
        "expected_corpus_unavailable": [],
        "expected_corpus_restricted": [],
    }
    iid = "acq-identity-0"
    produced = [
        _identity_env(iid),
        _acq_env("acq-1", iid, "downloaded", location_id="loc-1", sha="abc"),
        _doc_env("doc-1", "acq-1", iid, "extracted"),
        _corpus_env(available=["doc-1"]),
    ]
    result = await DocumentAcquisitionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.passed
    m = result.value["metrics"]
    assert m["acquisition_success_rate"]["value"] == 1.0
    assert m["extraction_success_rate"]["value"] == 1.0
    assert m["failure_classification_accuracy"]["value"] == 1.0
    assert m["corpus_availability_accuracy"]["value"] == 1.0


async def test_invalid_content_classification_passes():
    ref = {
        "expected_statuses": {"acq-paper-0": "invalid_content"},
        "expected_corpus_available": [],
        "expected_corpus_unavailable": [],
        "expected_corpus_restricted": [],
        "expected_corpus_failed": ["acq-paper-0"],
    }
    iid = "acq-identity-0"
    produced = [
        _identity_env(iid),
        _acq_env("acq-1", iid, "invalid_content", location_id="loc-1"),
        _corpus_env(available=[], failed=[iid]),
    ]
    result = await DocumentAcquisitionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["failure_classification_accuracy"]["value"] == 1.0


async def test_status_mismatch_fails():
    ref = {"expected_statuses": {"acq-paper-0": "downloaded"}}
    iid = "acq-identity-0"
    produced = [_identity_env(iid), _acq_env("acq-1", iid, "not_available")]
    result = await DocumentAcquisitionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.failed
    assert "ACQUISITION STATUS" in result.explanation


async def test_corpus_mismatch_fails():
    ref = {
        "expected_statuses": {"acq-paper-0": "downloaded"},
        "expected_corpus_available": ["acq-paper-0"],
        "expected_corpus_unavailable": [],
        "expected_corpus_restricted": [],
    }
    iid = "acq-identity-0"
    produced = [
        _identity_env(iid),
        _acq_env("acq-1", iid, "downloaded", location_id="loc-1"),
        _doc_env("doc-1", "acq-1", iid, "extracted"),
        _corpus_env(available=[], unavailable=[iid]),
    ]
    result = await DocumentAcquisitionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.failed
    assert "CORPUS AVAILABILITY MISMATCH" in result.explanation


async def test_fallback_used_passes():
    ref = {
        "expected_statuses": {"acq-paper-0": "downloaded"},
        "expected_corpus_available": ["acq-paper-0"],
        "expected_corpus_unavailable": [],
        "expected_corpus_restricted": [],
        "expected_fallback_used": True,
    }
    iid = "acq-identity-0"
    produced = [
        _identity_env(iid),
        _loc_env("loc-1", iid, "https://a.example.com/broken.pdf"),
        _loc_env("loc-2", iid, "https://b.example.com/fallback.pdf"),
        _acq_env("acq-bad", iid, "invalid_content", location_id="loc-1"),
        _acq_env("acq-ok", iid, "downloaded", location_id="loc-2", sha="abc"),
        _doc_env("doc-1", "acq-ok", iid, "extracted"),
        _corpus_env(available=["doc-1"]),
    ]
    result = await DocumentAcquisitionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["fallback_usage_accuracy"]["value"] == 1.0


async def test_duplicate_blob_detected_fails():
    ref = {
        "expected_statuses": {"acq-paper-0": "downloaded"},
        "expected_corpus_available": ["acq-paper-0"],
        "expected_corpus_unavailable": [],
        "expected_corpus_restricted": [],
        "expected_duplicate_reuse": True,
    }
    iid = "acq-identity-0"
    produced = [
        _identity_env(iid),
        _acq_env("acq-1", iid, "downloaded", location_id="loc-1", sha="abc"),
        _acq_env("acq-2", iid, "downloaded", location_id="loc-1", sha="abc"),
        _doc_env("doc-1", "acq-1", iid, "extracted"),
        _corpus_env(available=["doc-1"]),
    ]
    result = await DocumentAcquisitionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.failed
    assert "DUPLICATE BLOB" in result.explanation


async def test_no_acquisition_artifacts_fails():
    result = await DocumentAcquisitionEvaluator().evaluate(_ctx(_case({}), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None


# --- regression: declared corpus expectations must actually be checked -----
#
# The corpus block was guarded by ``if corpora:``, so when no FullTextCorpus
# was produced at all every ``expected_corpus_*`` expectation was skipped,
# ``corpus_ok``/``corpus_total`` stayed 0 and the case could still pass -- a
# run whose corpus assembly failed looked like a verified corpus.


async def test_missing_corpus_with_corpus_expectations_fails():
    """Corpus expectations declared, no corpus produced -> cannot be a pass."""
    from research_harness.plugins.research.evaluator_document_acquisition.plugin import (
        DocumentAcquisitionEvaluator,
    )

    ref = {
        "expected_status": {"p0": "failed"},
        "expected_corpus_failed": ["p0"],
    }
    # An acquisition was produced (so the evaluator does not short-circuit) but
    # no FullTextCorpus followed: the corpus expectation is then unchecked, and
    # that must not be reported as a pass.
    produced = [
        _identity_env("p0"),
        _acq_env("a0", "p0", "failed"),
    ]
    result = await DocumentAcquisitionEvaluator().evaluate(_ctx(_case(ref), produced))

    assert result.status == EvaluatorStatus.failed
    assert "CORPUS MISSING" in (result.explanation or "")


async def test_missing_corpus_is_fine_when_no_corpus_expectations():
    """No corpus expectations declared -> absence of a corpus is not a failure."""
    from research_harness.plugins.research.evaluator_document_acquisition.plugin import (
        DocumentAcquisitionEvaluator,
    )

    ref = {
        "expected_status": {},
        "expected_corpus_available": [],
        "expected_corpus_unavailable": [],
        "expected_corpus_restricted": [],
        "expected_corpus_failed": [],
    }
    result = await DocumentAcquisitionEvaluator().evaluate(_ctx(_case(ref), []))
    assert "CORPUS MISSING" not in (result.explanation or "")
