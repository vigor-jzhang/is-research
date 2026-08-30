"""Phase 6C unit tests — evidence evaluator.

Covers: precision/recall/F1, category accuracy, locator accuracy, required
evidence recall, unsupported evidence (statement grounding), duplicate
evidence, documents with required evidence missed, chunk-failure accounting,
and the insufficient-text path.
"""

from __future__ import annotations

import pathlib

import pytest

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_evidence.plugin import EvidenceEvaluator
from research_harness.plugins.storage.blobs_filesystem.plugin import FilesystemBlobStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus
from research_harness.research.schemas.evidence import EvidenceItem, Locator
from research_harness.research.schemas.evidence_extraction import (
    EvidenceCorpus,
    EvidenceExtractionExecution,
)
from research_harness.research.schemas.full_text import FullTextDocument, TextStatus

STATEMENT = "The platform maximizes consumer surplus in equilibrium."


async def _doc_env(
    blobs: FilesystemBlobStore,
    doc_id: str,
    pages: list[str],
    *,
    text_status: str = "extracted",
) -> ArtifactEnvelope:
    import json as _json

    pdf = await blobs.put_bytes(b"%PDF-1.4", media_type="application/pdf")
    text_blob = None
    if text_status == "extracted":
        text_blob = await blobs.put_bytes(
            _json.dumps(
                {
                    "schema_version": 1,
                    "pages": [{"page": i + 1, "text": t} for i, t in enumerate(pages)],
                },
                sort_keys=True,
            ).encode(),
            media_type="application/json",
        )
    return ArtifactEnvelope.create(
        payload=FullTextDocument(
            paper_identity_id="pi-1",
            document_acquisition_id="acq-1",
            source_blob=pdf,
            text_blob=text_blob,
            extractor="pypdf",
            page_count=len(pages) if text_status == "extracted" else 0,
            pages_with_text=len(pages) if text_status == "extracted" else 0,
            character_count=sum(len(p) for p in pages),
            text_status=TextStatus(text_status),
        ),
        artifact_type="full_text_document",
        producer="test",
        artifact_id=doc_id,
    )


def _item_env(
    item_id: str,
    doc_id: str,
    statement: str,
    pages: list[int],
    category: str = "result",
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=EvidenceItem(
            statement=statement,
            source_artifact_id=doc_id,
            category=category,
            locator=Locator(page=pages[0], pages=pages),
            extraction_method="model-assisted",
            confidence=0.9,
        ),
        artifact_type="evidence_item",
        producer="test",
        artifact_id=item_id,
    )


def _execution_env(chunks_failed: int = 0) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=EvidenceExtractionExecution(
            full_text_corpus_id="corpus-1",
            documents_attempted=1,
            documents_completed=1,
            chunks_processed=1,
            chunks_failed=chunks_failed,
            evidence_items_created=1,
            profiles_created=0,
            failures=[] if not chunks_failed else [{"chunk_index": 0, "error": "boom"}],
        ),
        artifact_type="evidence_extraction_execution",
        producer="test",
    )


def _corpus_env(without: list[str] | None = None) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=EvidenceCorpus(
            evidence_extraction_execution_id="exec-1",
            full_text_corpus_id="corpus-1",
            paper_profile_ids=[],
            evidence_item_ids=[],
            documents_without_evidence=list(without or []),
            failed_document_ids=[],
        ),
        artifact_type="evidence_corpus",
        producer="test",
    )


def _case(reference: dict) -> BenchmarkCase:
    return BenchmarkCase(
        id="c1",
        benchmark_id="b",
        version=1,
        name="case",
        input={},
        reference=reference,
        evaluation_dimensions=["evidence"],
        tags=[],
    )


def _ctx(case: BenchmarkCase, produced: list, blobs=None) -> EvaluatorContext:
    return EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=produced,
        config={},
        blob_store=blobs,
    )


def _expected(**overrides) -> dict:
    base = {
        "expected_statements": {
            STATEMENT: {"category": "result", "valid_pages": [3], "required": True}
        },
        "expected_unsupported": 0,
        "expected_chunk_failures": 0,
    }
    base.update(overrides)
    return base


@pytest.fixture
async def blobs(tmp_path: pathlib.Path):
    store = FilesystemBlobStore(root=tmp_path / "blobs")
    yield store


async def test_single_page_finding_passes(blobs):
    doc = await _doc_env(blobs, "d1", ["intro", "setup", STATEMENT, "conclusion"])
    item = _item_env("e1", "d1", STATEMENT, [3])
    produced = [doc, item, _execution_env(), _corpus_env()]
    result = await EvidenceEvaluator().evaluate(_ctx(_case(_expected()), produced, blobs))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["evidence_precision"]["value"] == 1.0
    assert result.value["metrics"]["evidence_recall"]["value"] == 1.0
    assert result.value["metrics"]["locator_accuracy"]["value"] == 1.0
    assert result.value["metrics"]["category_accuracy"]["value"] == 1.0
    assert result.value["unsupported_evidence_ids"] == []


async def test_multi_page_evidence_passes(blobs):
    doc = await _doc_env(
        blobs, "d1", ["intro", "Rising entry costs reduce market participation.", STATEMENT, "tail"]
    )
    item = _item_env("e1", "d1", "Rising entry costs reduce market participation.", [2, 3])
    produced = [doc, item, _execution_env(), _corpus_env()]
    case = _case(
        _expected(
            expected_statements={
                "Rising entry costs reduce market participation.": {
                    "category": "result",
                    "valid_pages": [2, 3],
                    "required": True,
                }
            }
        )
    )
    result = await EvidenceEvaluator().evaluate(_ctx(case, produced, blobs))
    assert result.status == EvaluatorStatus.passed


async def test_category_mismatch_fails(blobs):
    doc = await _doc_env(blobs, "d1", ["intro", "setup", STATEMENT, "tail"])
    item = _item_env("e1", "d1", STATEMENT, [3], category="mechanism")
    produced = [doc, item, _execution_env(), _corpus_env()]
    result = await EvidenceEvaluator().evaluate(_ctx(_case(_expected()), produced, blobs))
    assert result.status == EvaluatorStatus.failed
    assert "CATEGORY MISMATCHES" in result.explanation
    assert result.value["metrics"]["category_accuracy"]["value"] == 0.0


async def test_locator_outside_valid_pages_fails(blobs):
    doc = await _doc_env(blobs, "d1", ["intro", "setup", STATEMENT, "tail"])
    item = _item_env("e1", "d1", STATEMENT, [2])
    produced = [doc, item, _execution_env(), _corpus_env()]
    result = await EvidenceEvaluator().evaluate(_ctx(_case(_expected()), produced, blobs))
    assert result.status == EvaluatorStatus.failed
    assert "LOCATOR MISMATCHES" in result.explanation
    assert result.value["metrics"]["locator_accuracy"]["value"] == 0.0


async def test_unsupported_claim_fails(blobs):
    doc = await _doc_env(blobs, "d1", ["intro", "setup", "The platform maximizes welfare.", "tail"])
    item = _item_env("e1", "d1", "This claim is not supported by any page text.", [2])
    produced = [doc, item, _execution_env(), _corpus_env()]
    case = _case(_expected(expected_statements={}))
    result = await EvidenceEvaluator().evaluate(_ctx(case, produced, blobs))
    assert result.status == EvaluatorStatus.failed
    assert result.value["unsupported_evidence_ids"] == ["e1"]
    assert "UNSUPPORTED EVIDENCE" in result.explanation
    assert result.value["metrics"]["unsupported_evidence_rate"]["value"] == 1.0


async def test_missing_required_evidence_fails(blobs):
    doc = await _doc_env(blobs, "d1", ["intro", "setup", "unrelated text", "tail"])
    produced = [doc, _execution_env(), _corpus_env()]
    result = await EvidenceEvaluator().evaluate(_ctx(_case(_expected()), produced, blobs))
    assert result.status == EvaluatorStatus.failed
    assert "REQUIRED EVIDENCE MISSED" in result.explanation
    assert result.value["metrics"]["required_evidence_recall"]["value"] == 0.0
    assert result.value["metrics"]["documents_with_required_evidence_missed"]["value"] == 1.0


async def test_duplicate_evidence_fails(blobs):
    doc = await _doc_env(blobs, "d1", ["intro", "setup", STATEMENT, "tail"])
    item1 = _item_env("e1", "d1", STATEMENT, [3])
    item2 = _item_env("e2", "d1", STATEMENT, [3])
    produced = [doc, item1, item2, _execution_env(), _corpus_env()]
    result = await EvidenceEvaluator().evaluate(_ctx(_case(_expected()), produced, blobs))
    assert result.status == EvaluatorStatus.failed
    assert result.value["duplicate_evidence_ids"] == ["e2"]
    dup = result.value["metrics"]["duplicate_evidence_rate"]
    assert dup["value"] / dup["count"] == pytest.approx(0.5)


async def test_chunk_failure_mismatch_fails(blobs):
    doc = await _doc_env(blobs, "d1", ["intro", "setup", STATEMENT, "tail"])
    item = _item_env("e1", "d1", STATEMENT, [3])
    produced = [doc, item, _execution_env(chunks_failed=1), _corpus_env()]
    result = await EvidenceEvaluator().evaluate(_ctx(_case(_expected()), produced, blobs))
    assert result.status == EvaluatorStatus.failed
    assert "CHUNK FAILURES" in result.explanation


async def test_expected_chunk_failure_passes(blobs):
    doc = await _doc_env(blobs, "d1", ["intro", "setup", STATEMENT, "tail"])
    produced = [doc, _execution_env(chunks_failed=1), _corpus_env()]
    case = _case(_expected(expected_statements={}, expected_chunk_failures=1))
    result = await EvidenceEvaluator().evaluate(_ctx(case, produced, blobs))
    assert result.status == EvaluatorStatus.passed


async def test_insufficient_text_document(blobs):
    doc = await _doc_env(blobs, "d1", ["fragment"], text_status="insufficient_text")
    produced = [doc, _execution_env(), _corpus_env(without=["d1"])]
    case = _case(
        _expected(
            expected_statements={},
            expected_documents_without_evidence=["d1"],
        )
    )
    result = await EvidenceEvaluator().evaluate(_ctx(case, produced, blobs))
    assert result.status == EvaluatorStatus.passed
    assert result.value["documents_without_evidence"] == ["d1"]


async def test_no_execution_produced():
    result = await EvidenceEvaluator().evaluate(_ctx(_case(_expected()), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None


# --- regression: a missing blob store is a config fact, not a failure -----
#
# blob_store is an *optional* harness dependency, but the evaluator appended
# "statement grounding not verified (no blob store)" to failures_detail
# whenever it was absent and the case had evidence items. Every case of the
# benchmark therefore failed for a deployment reason the operator could not
# fix by improving the pipeline. When that is the only thing wrong, the
# grounding dimension is unverifiable rather than failed.


async def test_missing_blob_store_does_not_fail_otherwise_correct_case(blobs):
    doc = await _doc_env(blobs, "d1", ["intro", "setup", STATEMENT, "conclusion"])
    item = _item_env("e1", "d1", STATEMENT, [3])
    produced = [doc, item, _execution_env(), _corpus_env()]
    # Same artifacts, but no blob store attached.
    result = await EvidenceEvaluator().evaluate(_ctx(_case(_expected()), produced, None))

    assert result.status == EvaluatorStatus.skipped
    assert "no blob store" in (result.explanation or "")
    # The dimensions that do not need page text are still evaluated.
    metrics = result.value["metrics"]
    assert metrics["evidence_recall"]["value"] == 1.0
    assert metrics["locator_accuracy"]["value"] == 1.0
    assert metrics["category_accuracy"]["value"] == 1.0


async def test_missing_blob_store_never_reports_a_pass(blobs):
    """Unverifiable must never degrade into 'passed'."""
    doc = await _doc_env(blobs, "d1", ["intro", "setup", STATEMENT, "conclusion"])
    item = _item_env("e1", "d1", STATEMENT, [3])
    produced = [doc, item, _execution_env(), _corpus_env()]
    result = await EvidenceEvaluator().evaluate(_ctx(_case(_expected()), produced, None))
    assert result.status != EvaluatorStatus.passed


async def test_real_failure_still_fails_without_blob_store(blobs):
    """A genuine mismatch is still 'failed', not downgraded to 'skipped'."""
    doc = await _doc_env(blobs, "d1", ["intro", "setup", STATEMENT, "tail"])
    # Wrong category: fails independently of grounding.
    item = _item_env("e1", "d1", STATEMENT, [3], category="mechanism")
    produced = [doc, item, _execution_env(), _corpus_env()]
    result = await EvidenceEvaluator().evaluate(_ctx(_case(_expected()), produced, None))

    assert result.status == EvaluatorStatus.failed
    assert "CATEGORY MISMATCHES" in result.explanation
