"""Phase 6D unit tests — gap-analysis evaluator.

Covers: gap type accuracy, precision/recall/F1, grounding, corpus-bounded
claims, support counts, ranking, unsupported gaps, hallucinated references,
and the critical deterministic failures.
"""

from __future__ import annotations

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_gap_analysis.plugin import GapAnalysisEvaluator
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus
from research_harness.research.schemas.evidence import EvidenceItem
from research_harness.research.schemas.gap import GapAnalysis, GapAnalysisExecution, ResearchGap
from research_harness.research.schemas.synthesis import SynthesisStatement

STATEMENT = "Within the reviewed corpus, algorithmic pricing lowers consumer welfare."


def _statement_env(sid: str, text: str = STATEMENT) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=SynthesisStatement(
            statement=text,
            type="consensus",
            supporting_evidence_ids=["ev-1"],
            supporting_paper_identity_ids=["paper-1"],
            papers_supporting=1,
            evidence_items_supporting=1,
        ),
        artifact_type="synthesis_statement",
        producer="test",
        artifact_id=sid,
    )


def _evidence_env(eid: str) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=EvidenceItem(
            statement="Algorithmic pricing lowers welfare.",
            source_artifact_id="doc-1",
            category="result",
        ),
        artifact_type="evidence_item",
        producer="test",
        artifact_id=eid,
    )


def _gap_env(
    gid: str,
    title: str,
    gap_type: str = "contradiction_gap",
    *,
    stmt_ids: list[str] | None = None,
    ev_ids: list[str] | None = None,
    description: str = "Within the reviewed corpus, the evidence is contradictory.",
    supporting_papers: int = 1,
    supporting_evidence_items: int = 1,
    strength: str = "tentative",
) -> ArtifactEnvelope:
    if stmt_ids is None:
        stmt_ids = ["s1"]
    if ev_ids is None:
        ev_ids = ["ev-1"]
    return ArtifactEnvelope.create(
        payload=ResearchGap(
            title=title,
            gap_type=gap_type,
            description=description,
            supporting_synthesis_statement_ids=list(stmt_ids or []),
            supporting_evidence_ids=list(ev_ids or []),
            supporting_papers=supporting_papers,
            supporting_evidence_items=supporting_evidence_items,
            strength=strength,
            confidence=0.8,
        ),
        artifact_type="research_gap",
        producer="test",
        artifact_id=gid,
    )


def _analysis_env(gap_ids: list[str], ranked: list[str]) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=GapAnalysis(
            literature_synthesis_id="syn",
            evidence_corpus_id="corpus",
            gap_ids=gap_ids,
            ranked_gap_ids=ranked,
        ),
        artifact_type="gap_analysis",
        producer="test",
    )


def _execution_env(
    gaps_created: int = 1, gaps_rejected: int = 0, failures: list | None = None
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=GapAnalysisExecution(
            literature_synthesis_id="syn",
            evidence_corpus_id="corpus",
            statements_processed=1,
            themes_processed=1,
            gaps_created=gaps_created,
            gaps_rejected=gaps_rejected,
            failures=list(failures or []),
        ),
        artifact_type="gap_analysis_execution",
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
        evaluation_dimensions=["gap_analysis"],
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


def _expected(**overrides) -> dict:
    base = {
        "expected_gaps": {
            "Contradictory evidence on pricing": {
                "gap_type": "contradiction_gap",
                "supporting_papers": 1,
                "supporting_evidence_items": 1,
            }
        },
        "expected_hallucinated": 0,
        "expected_unsupported": 0,
        "expected_sweeping": False,
    }
    base.update(overrides)
    return base


async def test_matching_case_passes():
    stmt = _statement_env("s1")
    ev = _evidence_env("ev-1")
    gap = _gap_env("g1", "Contradictory evidence on pricing", strength="strongly_supported")
    produced = [stmt, ev, gap, _analysis_env(["g1"], ["g1"]), _execution_env()]
    result = await GapAnalysisEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["gap_type_accuracy"]["value"] == 1.0
    assert result.value["metrics"]["gap_precision"]["value"] == 1.0
    assert result.value["metrics"]["gap_recall"]["value"] == 1.0
    assert result.value["metrics"]["grounding_accuracy"]["value"] == 1.0
    assert result.value["metrics"]["corpus_bounded_claim_accuracy"]["value"] == 1.0
    assert result.value["unsupported_gap_ids"] == []
    assert result.value["sweeping_gap_ids"] == []


async def test_gap_type_mismatch_fails():
    stmt = _statement_env("s1")
    ev = _evidence_env("ev-1")
    gap = _gap_env("g1", "Contradictory evidence on pricing", gap_type="mechanism_gap")
    produced = [stmt, ev, gap, _analysis_env(["g1"], ["g1"]), _execution_env()]
    result = await GapAnalysisEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "TYPE MISMATCHES" in result.explanation
    assert result.value["metrics"]["gap_type_accuracy"]["value"] == 0.0


async def test_missing_expected_gap_fails():
    stmt = _statement_env("s1")
    ev = _evidence_env("ev-1")
    produced = [stmt, ev, _analysis_env([], []), _execution_env(gaps_created=0)]
    result = await GapAnalysisEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "GAPS MISSING" in result.explanation
    assert result.value["metrics"]["gap_recall"]["value"] == 0.0


async def test_extra_unexpected_gap_fails():
    stmt = _statement_env("s1")
    ev = _evidence_env("ev-1")
    gap = _gap_env("g1", "Extra unexpected gap")
    produced = [stmt, ev, gap, _analysis_env(["g1"], ["g1"]), _execution_env()]
    result = await GapAnalysisEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "EXTRA GAPS" in result.explanation
    assert result.value["metrics"]["gap_precision"]["value"] == 0.0


async def test_unsupported_gap_persisted_fails():
    stmt = _statement_env("s1")
    ev = _evidence_env("ev-1")
    gap = _gap_env(
        "g1",
        "Unsupported gap",
        stmt_ids=[],
        ev_ids=[],
        supporting_papers=0,
        supporting_evidence_items=0,
    )
    produced = [stmt, ev, gap, _analysis_env(["g1"], ["g1"]), _execution_env()]
    result = await GapAnalysisEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.failed
    assert result.value["unsupported_gap_ids"] == ["g1"]
    assert "UNSUPPORTED GAP PERSISTED" in result.explanation
    assert result.value["metrics"]["unsupported_gap_rate"]["value"] == 1.0


async def test_global_novelty_claim_fails():
    stmt = _statement_env("s1")
    ev = _evidence_env("ev-1")
    gap = _gap_env(
        "g1",
        "Contradictory evidence on pricing",
        description="No research has studied this phenomenon anywhere.",
    )
    produced = [stmt, ev, gap, _analysis_env(["g1"], ["g1"]), _execution_env()]
    result = await GapAnalysisEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.failed
    assert result.value["sweeping_gap_ids"] == ["g1"]
    assert "GLOBAL NOVELTY CLAIM AS FACT" in result.explanation


async def test_support_count_mismatch_fails():
    stmt = _statement_env("s1")
    ev = _evidence_env("ev-1")
    gap = _gap_env("g1", "Contradictory evidence on pricing", supporting_papers=3)
    produced = [stmt, ev, gap, _analysis_env(["g1"], ["g1"]), _execution_env()]
    result = await GapAnalysisEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "SUPPORT COUNT MISMATCHES" in result.explanation
    assert result.value["metrics"]["support_count_accuracy"]["value"] == 0.0


async def test_ranking_mismatch_fails():
    stmt = _statement_env("s1")
    ev = _evidence_env("ev-1")
    g1 = _gap_env("g1", "Alpha gap", strength="strongly_supported")
    g2 = _gap_env("g2", "Beta gap", strength="strongly_supported")
    produced = [
        stmt,
        ev,
        g1,
        g2,
        _analysis_env(["g1", "g2"], ["g2", "g1"]),
        _execution_env(gaps_created=2),
    ]
    case = _case(
        _expected(
            expected_gaps={
                "Alpha gap": {"gap_type": "contradiction_gap"},
                "Beta gap": {"gap_type": "contradiction_gap"},
            },
            expected_rank_order=["Alpha gap", "Beta gap"],
        )
    )
    result = await GapAnalysisEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.failed
    assert "RANK MISMATCHES" in result.explanation
    # one of two ordered pairs is violated -> pooled rate 1/2
    ra = result.value["metrics"]["ranking_accuracy"]
    assert ra["value"] / ra["count"] == 0.5


async def test_hallucinated_references():
    stmt = _statement_env("s1")
    ev = _evidence_env("ev-1")
    produced = [
        stmt,
        ev,
        _analysis_env([], []),
        _execution_env(
            gaps_created=0,
            gaps_rejected=1,
            failures=[{"gap": "g", "error": "hallucinated synthesis statement id 'ghost'"}],
        ),
    ]
    case = _case(_expected(expected_gaps={}, expected_hallucinated=1))
    result = await GapAnalysisEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["hallucinated_reference_count"]["value"] == 1.0


async def test_ungrounded_gap_fails():
    # a gap citing a statement id that does not exist in the produced set
    stmt = _statement_env("s1")
    ev = _evidence_env("ev-1")
    gap = _gap_env("g1", "Contradictory evidence on pricing", stmt_ids=["s-ghost"])
    produced = [stmt, ev, gap, _analysis_env(["g1"], ["g1"]), _execution_env()]
    result = await GapAnalysisEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.failed
    assert result.value["ungrounded_gap_ids"] == ["g1"]
    assert result.value["metrics"]["grounding_accuracy"]["value"] == 0.0


async def test_no_execution_produced():
    result = await GapAnalysisEvaluator().evaluate(_ctx(_case(_expected()), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None
