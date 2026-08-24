"""Phase 6B unit tests — literature-retrieval evaluator.

Covers: precision/recall/F1, MRR, duplicate handling, multi-provider identity
dedup, missed relevant papers, irrelevant retrieval, and the no-relevant
case.
"""

from __future__ import annotations

import pytest

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_retrieval.plugin import RetrievalEvaluator
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus
from research_harness.research.schemas.execution import LiteratureSearchExecution
from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
from research_harness.research.schemas.paper import PaperRecord
from research_harness.research.schemas.search_record import LiteratureSearchRecord

CLAIM_KEY = "10.6000/ret-1"


def _paper(title: str, doi: str | None, year: int = 2020) -> tuple[ArtifactEnvelope, str]:
    env = ArtifactEnvelope.create(
        payload=PaperRecord(title=title, year=year, doi=doi, venue="J"),
        artifact_type="paper_record",
        producer="test",
    )
    return env, env.artifact_id


def _identity(identity_id: str, paper_ids: list[str]) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=PaperIdentity(
            member_paper_artifact_ids=paper_ids,
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.exact_identifier,
            resolution_evidence=[],
        ),
        artifact_type="paper_identity",
        producer="test",
        artifact_id=identity_id,
    )


def _record(record_id: str, paper_ids: list[str]) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=LiteratureSearchRecord(
            provider="crossref",
            query="q",
            query_artifact_id="q1",
            returned_count=len(paper_ids),
            paper_artifact_ids=paper_ids,
        ),
        artifact_type="literature_search_record",
        producer="test",
        artifact_id=record_id,
    )


def _execution(record_ids: list[str], counts: dict | None = None) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=LiteratureSearchExecution(
            strategy_artifact_id="s",
            query_artifact_ids=["q1"],
            search_record_artifact_ids=record_ids,
            paper_identity_artifact_ids=[],
            counts=counts
            or {
                "raw_paper_records": 0,
                "unique_paper_identities": 0,
                "duplicate_records_collapsed": 0,
            },
        ),
        artifact_type="literature_search_execution",
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
        evaluation_dimensions=["retrieval"],
        tags=[],
    )


def _ctx(case: BenchmarkCase, produced: list) -> EvaluatorContext:
    return EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=produced,
        config={"k": [5, 10]},
    )


async def test_precision_recall_mrr_basic():
    p1, p1_id = _paper("Algorithmic Pricing and Welfare", CLAIM_KEY)
    p2, p2_id = _paper("Dynamic Pricing in Markets", "10.6000/ret-2")
    i1 = _identity("i1", [p1_id])
    i2 = _identity("i2", [p2_id])
    rec = _record("r1", [p1_id, p2_id])
    exec_env = _execution(
        ["r1"],
        {"raw_paper_records": 2, "unique_paper_identities": 2, "duplicate_records_collapsed": 0},
    )
    produced = [p1, p2, i1, i2, rec, exec_env]
    result = await RetrievalEvaluator().evaluate(_ctx(_case({"relevant": [CLAIM_KEY]}), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["mrr"] == 1.0  # first hit is relevant
    assert result.value["k_metrics"]["precision@5"] == pytest.approx(1 / 5)
    assert result.value["k_metrics"]["recall@5"] == 1.0
    assert result.value["k_metrics"]["f1@5"] == pytest.approx(2 * 0.2 / 1.2)
    assert result.value["k_metrics"]["precision@10"] == pytest.approx(1 / 10)
    assert result.value["ranked_identity_ids"] == ["i1", "i2"]


async def test_mrr_second_rank():
    p1, p1_id = _paper("Unrelated Title", "10.6000/ret-9")
    p2, p2_id = _paper("Algorithmic Pricing and Welfare", CLAIM_KEY)
    i1 = _identity("i1", [p1_id])
    i2 = _identity("i2", [p2_id])
    rec = _record("r1", [p1_id, p2_id])
    exec_env = _execution(["r1"])
    produced = [p1, p2, i1, i2, rec, exec_env]
    result = await RetrievalEvaluator().evaluate(_ctx(_case({"relevant": [CLAIM_KEY]}), produced))
    assert result.status == EvaluatorStatus.passed  # nothing missed
    assert result.value["mrr"] == pytest.approx(0.5)
    assert result.value["relevant_papers_missed"] == 0
    assert result.value["irrelevant_papers_retrieved"] == 1


async def test_missed_relevant_paper_fails():
    p1, p1_id = _paper("Algorithmic Pricing and Welfare", CLAIM_KEY)
    p2, p2_id = _paper("Dynamic Pricing in Markets", "10.6000/ret-2")
    i1 = _identity("i1", [p1_id])
    i2 = _identity("i2", [p2_id])
    rec = _record("r1", [p1_id])
    exec_env = _execution(["r1"])
    produced = [p1, p2, i1, i2, rec, exec_env]
    result = await RetrievalEvaluator().evaluate(
        _ctx(_case({"relevant": [CLAIM_KEY, "10.6000/ret-2"]}), produced)
    )
    assert result.status == EvaluatorStatus.failed
    assert result.value["relevant_papers_missed"] == 1
    assert result.value["mrr"] == 1.0
    assert "RELEVANT MISSED" in result.explanation


async def test_duplicate_rate_and_multi_provider_dedup():
    # same DOI from two providers -> one identity; duplicate_rate measured
    p1a, p1a_id = _paper("Algorithmic Pricing and Welfare", CLAIM_KEY)
    p1b, p1b_id = _paper("Algorithmic Pricing and Welfare", CLAIM_KEY)
    i1 = _identity("i1", [p1a_id, p1b_id])
    rec_a = _record("ra", [p1a_id])
    rec_b = _record("rb", [p1b_id])
    exec_env = _execution(
        ["ra", "rb"],
        {"raw_paper_records": 2, "unique_paper_identities": 1, "duplicate_records_collapsed": 1},
    )
    produced = [p1a, p1b, i1, rec_a, rec_b, exec_env]
    result = await RetrievalEvaluator().evaluate(_ctx(_case({"relevant": [CLAIM_KEY]}), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["duplicate_rate"] == pytest.approx(0.5)
    assert result.value["ranked_identity_ids"] == ["i1"]  # deduped to one
    assert result.value["mrr"] == 1.0
    assert result.value["k_metrics"]["recall@5"] == 1.0


async def test_no_relevant_result():
    p1, p1_id = _paper("Soil Microbiomes", "10.6000/ret-10")
    i1 = _identity("i1", [p1_id])
    rec = _record("r1", [p1_id])
    exec_env = _execution(["r1"])
    produced = [p1, i1, rec, exec_env]
    result = await RetrievalEvaluator().evaluate(_ctx(_case({"relevant": []}), produced))
    assert result.status == EvaluatorStatus.passed  # nothing relevant to miss
    assert result.value["mrr"] == 0.0
    assert result.value["k_metrics"]["recall@5"] == 0.0
    assert result.value["k_metrics"]["precision@5"] == 0.0
    assert result.value["relevant_papers_missed"] == 0


async def test_sparse_metadata_title_key():
    # no-DOI paper referenced by title; singleton identity still counted
    p1, p1_id = _paper("Working Notes on Platform Pricing", None)
    i1 = _identity("i1", [p1_id])
    rec = _record("r1", [p1_id])
    exec_env = _execution(["r1"])
    produced = [p1, i1, rec, exec_env]
    result = await RetrievalEvaluator().evaluate(
        _ctx(_case({"relevant": ["working notes on platform pricing"]}), produced)
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["mrr"] == 1.0
    assert result.value["k_metrics"]["recall@5"] == 1.0


async def test_no_execution_produced():
    result = await RetrievalEvaluator().evaluate(_ctx(_case({"relevant": []}), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None
