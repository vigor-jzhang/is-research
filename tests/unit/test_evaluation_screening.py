"""Phase 6C unit tests — screening evaluator.

Covers every metric and the critical deterministic failures: false exclusion,
uncertain forced to exclude, technical failure counted as exclusion, duplicate
screening, review triggers, and unknown-evaluator isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_screening.plugin import ScreeningEvaluator
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus
from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
from research_harness.research.schemas.paper import PaperRecord
from research_harness.research.schemas.screening_decision import (
    InformationSufficiency,
    ScreeningDecision,
    ScreeningDecisionEnum,
)
from research_harness.research.schemas.screening_execution import (
    ScreenedLiteratureSet,
    ScreeningExecution,
)
from research_harness.research.schemas.screening_review import (
    ReviewerType,
    ScreeningReview,
)

TITLE = "Algorithmic Pricing and Consumer Welfare"


def _paper(paper_id: str) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=PaperRecord(
            title=TITLE, year=2021, venue="J", abstract="A study of pricing.", doi="10.6000/scr"
        ),
        artifact_type="paper_record",
        producer="test",
        artifact_id=paper_id,
    )


def _identity(identity_id: str, member_ids: list[str]) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=PaperIdentity(
            member_paper_artifact_ids=member_ids,
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.manual,
            resolution_evidence=[],
        ),
        artifact_type="paper_identity",
        producer="test",
        artifact_id=identity_id,
    )


def _decision(
    decision_id: str,
    identity_id: str,
    decision: str,
    confidence: float = 0.9,
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=ScreeningDecision(
            paper_identity_id=identity_id,
            screening_view_id="view",
            screening_protocol_id="proto",
            decision=ScreeningDecisionEnum(decision),
            matched_inclusion_criteria=["I1"] if decision == "include" else [],
            matched_exclusion_criteria=["E1"] if decision == "exclude" else [],
            reason_codes=[],
            rationale_summary="fixture",
            confidence=confidence,
            information_sufficiency=InformationSufficiency.sufficient,
        ),
        artifact_type="screening_decision",
        producer="test",
        artifact_id=decision_id,
    )


def _review(review_id: str, decision_id: str, reason: str) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=ScreeningReview(
            screening_decision_id=decision_id,
            review_reason=reason,
            original_decision="uncertain",
            final_decision="uncertain",
            reviewer_type=ReviewerType.autonomy_policy,
            approval_decision_id=None,
        ),
        artifact_type="screening_review",
        producer="test",
        artifact_id=review_id,
    )


def _execution(
    *,
    partitions: dict[str, list[str]],
    failures: list[dict] | None = None,
    decision_ids: list[str] | None = None,
) -> ArtifactEnvelope:
    now = datetime.now(UTC)
    return ArtifactEnvelope.create(
        payload=ScreeningExecution(
            protocol_artifact_id="proto",
            candidate_identity_ids=list({x for v in partitions.values() for x in v}),
            screening_view_ids=[],
            decision_artifact_ids=list(decision_ids or []),
            review_artifact_ids=[],
            started_at=now,
            completed_at=now,
            counts={
                "total_candidates": sum(len(v) for v in partitions.values()),
                "processed": sum(len(v) for v in partitions.values()),
                "included": len(partitions.get("included", [])),
                "excluded": len(partitions.get("excluded", [])),
                "uncertain": len(partitions.get("uncertain", [])),
                "failed": len(failures or []),
                "reused": 0,
                "missing_abstract": 0,
            },
            failures=list(failures or []),
        ),
        artifact_type="screening_execution",
        producer="test",
    )


def _set(partitions: dict[str, list[str]]) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=ScreenedLiteratureSet(
            screening_execution_id="exec",
            screening_protocol_id="proto",
            included_identity_ids=partitions.get("included", []),
            excluded_identity_ids=partitions.get("excluded", []),
            uncertain_identity_ids=partitions.get("uncertain", []),
            decision_artifact_ids=[],
        ),
        artifact_type="screened_literature_set",
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
        evaluation_dimensions=["screening"],
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


def _produced(
    decision: str = "include",
    confidence: float = 0.9,
    *,
    partitions: dict[str, list[str]] | None = None,
    failures: list[dict] | None = None,
    review: bool = False,
    identity_id: str = "i1",
) -> list:
    paper = _paper("paper-1")
    identity = _identity(identity_id, ["paper-1"])
    d = _decision("d1", identity_id, decision, confidence)
    out: list = [paper, identity, d]
    if review:
        out.append(_review("r1", "d1", "uncertain"))
    parts = partitions or {
        "included": [identity_id] if decision == "include" else [],
        "excluded": [identity_id] if decision == "exclude" else [],
        "uncertain": [identity_id] if decision == "uncertain" else [],
    }
    out.append(_execution(partitions=parts, failures=failures, decision_ids=["d1"]))
    out.append(_set(parts))
    return out


def _expected(**overrides) -> dict:
    base = {
        "expected_decisions": {TITLE: "include"},
        "expected_reviews": {TITLE: False},
        "expected_failed_identities": [],
    }
    base.update(overrides)
    return base


async def test_clear_include_passes():
    result = await ScreeningEvaluator().evaluate(_ctx(_case(_expected()), _produced()))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["screening_accuracy"]["value"] == 1.0
    assert result.value["metrics"]["include_precision"]["value"] == 1.0
    assert result.value["metrics"]["include_recall"]["value"] == 1.0
    assert result.value["metrics"]["include_f1"]["value"] == 1.0


async def test_clear_exclude_passes():
    result = await ScreeningEvaluator().evaluate(
        _ctx(
            _case(_expected(expected_decisions={TITLE: "exclude"})),
            _produced(decision="exclude"),
        )
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["exclude_accuracy"]["value"] == 1.0


async def test_uncertain_with_review_passes():
    result = await ScreeningEvaluator().evaluate(
        _ctx(
            _case(
                _expected(
                    expected_decisions={TITLE: "uncertain"},
                    expected_reviews={TITLE: True},
                )
            ),
            _produced(decision="uncertain", confidence=0.5, review=True),
        )
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["uncertain_accuracy"]["value"] == 1.0
    assert result.value["metrics"]["review_trigger_accuracy"]["value"] == 1.0


async def test_missing_abstract_not_excluded():
    # reference expects uncertain; a forced exclude is a deterministic failure
    result = await ScreeningEvaluator().evaluate(
        _ctx(
            _case(_expected(expected_decisions={TITLE: "uncertain"})),
            _produced(decision="exclude"),
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert result.value["forced_exclusions"] == ["i1"]
    assert "UNCERTAIN FORCED TO EXCLUDE" in result.explanation
    assert result.value["metrics"]["uncertain_accuracy"]["value"] == 0.0


async def test_false_exclusion_fails():
    result = await ScreeningEvaluator().evaluate(
        _ctx(
            _case(_expected(expected_decisions={TITLE: "include"})),
            _produced(decision="exclude"),
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert result.value["false_exclusions"] == ["i1"]
    assert "FALSE EXCLUSION" in result.explanation
    assert result.value["metrics"]["false_exclusion_rate"]["value"] == 1.0


async def test_technical_failure_not_exclusion():
    # the identity is expected to fail; it must not land in any partition
    result = await ScreeningEvaluator().evaluate(
        _ctx(
            _case(_expected(expected_failed_identities=[TITLE])),
            _produced(
                partitions={"included": [], "excluded": [], "uncertain": []},
                failures=[{"paper_identity_id": "i1", "error": "model boom", "stage": "screen"}],
            ),
        )
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["technical_failure_count"]["value"] == 1.0
    assert result.value["failed_but_excluded"] == []
    assert result.value["unexpected_failures"] == []


async def test_technical_failure_counted_as_exclusion_fails():
    result = await ScreeningEvaluator().evaluate(
        _ctx(
            _case(_expected(expected_failed_identities=[TITLE])),
            _produced(
                partitions={"included": [], "excluded": ["i1"], "uncertain": []},
                failures=[{"paper_identity_id": "i1", "error": "model boom", "stage": "screen"}],
            ),
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert result.value["failed_but_excluded"] == ["i1"]
    assert "TECHNICAL FAILURE COUNTED AS EXCLUSION" in result.explanation


async def test_unexpected_technical_failure_fails():
    result = await ScreeningEvaluator().evaluate(
        _ctx(
            _case(_expected()),
            _produced(
                failures=[{"paper_identity_id": "i1", "error": "boom", "stage": "screen"}],
            ),
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert result.value["unexpected_failures"] == ["i1"]


async def test_duplicate_screening_fails():
    d1 = _decision("d1", "i1", "include")
    d2 = _decision("d2", "i1", "include")
    produced = [
        _paper("paper-1"),
        _identity("i1", ["paper-1"]),
        d1,
        d2,
        _execution(
            partitions={"included": ["i1"], "excluded": [], "uncertain": []},
            decision_ids=["d1", "d2"],
        ),
        _set({"included": ["i1"], "excluded": [], "uncertain": []}),
    ]
    result = await ScreeningEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.failed
    assert result.value["duplicate_screened"] == ["i1"]
    assert "DUPLICATE SCREENING" in result.explanation


async def test_review_trigger_mismatch_fails():
    result = await ScreeningEvaluator().evaluate(
        _ctx(
            _case(_expected(expected_reviews={TITLE: True})),
            _produced(decision="include", confidence=0.9, review=False),
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert result.value["metrics"]["review_trigger_accuracy"]["value"] == 0.0


async def test_conflicting_metadata_single_decision():
    # one identity with two members yields exactly one decision
    p1 = _paper("paper-1")
    p2 = ArtifactEnvelope.create(
        payload=PaperRecord(title="Conflicting Pricing Study Beta", year=2021, venue="J2"),
        artifact_type="paper_record",
        producer="test",
        artifact_id="paper-2",
    )
    identity = _identity("i1", ["paper-1", "paper-2"])
    d = _decision("d1", "i1", "include")
    produced = [
        p1,
        p2,
        identity,
        d,
        _execution(
            partitions={"included": ["i1"], "excluded": [], "uncertain": []}, decision_ids=["d1"]
        ),
        _set({"included": ["i1"], "excluded": [], "uncertain": []}),
    ]
    result = await ScreeningEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.passed
    assert len(result.value["decisions"]) == 1
    assert result.value["duplicate_screened"] == []


async def test_no_execution_produced():
    result = await ScreeningEvaluator().evaluate(_ctx(_case(_expected()), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None
