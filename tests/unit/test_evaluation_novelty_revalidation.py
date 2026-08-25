"""Phase 7A.1 unit tests — novelty-revalidation evaluator."""

from __future__ import annotations

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_novelty_revalidation.plugin import (
    NoveltyRevalidationEvaluator,
)
from research_harness.research.benchmarks.workflows import NoveltyRevalidationReport
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus


def _report(
    *,
    overall_a: str = "clear",
    overall_b: str = "clear",
    assessments_a: list[str] | None = None,
    assessments_b: list[str] | None = None,
    supersedes_a: bool = False,
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=NoveltyRevalidationReport(
            benchmark_case_id="c1",
            report_a="rep-a",
            report_b="rep-b",
            package_id="pkg-1",
            manuscript_id="ms-1",
            overall_a=overall_a,
            overall_b=overall_b,
            assessments_a=list(assessments_a or []),
            assessments_b=list(assessments_b or []),
            report_b_supersedes_a=supersedes_a,
        ),
        artifact_type="novelty_revalidation_report",
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
        evaluation_dimensions=["novelty_revalidation"],
        tags=[],
    )


def _ctx(case: BenchmarkCase, produced: list, provenance: dict | None = None) -> EvaluatorContext:
    return EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=produced,
        config={},
        provenance=provenance or {},
    )


async def test_unchanged_literature_reusable_passes():
    ref = {
        "expected_overall_baseline": "clear",
        "expected_overall_changed": "clear",
        "expected_trigger": False,
    }
    provenance = {
        "rep-b": [
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id="pkg-1",
                target_artifact_id="rep-b",
                producer="t",
            )
        ]
    }
    result = await NoveltyRevalidationEvaluator().evaluate(
        _ctx(_case(ref), [_report(overall_a="clear", overall_b="clear")], provenance)
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["revalidation_trigger_accuracy"]["value"] == 1.0


async def test_new_relevant_paper_threatens_passes():
    ref = {
        "expected_overall_baseline": "clear",
        "expected_overall_changed": "blocked",
        "expected_trigger": True,
        "expected_threatened": True,
    }
    result = await NoveltyRevalidationEvaluator().evaluate(
        _ctx(
            _case(ref),
            [
                _report(
                    overall_a="clear",
                    overall_b="blocked",
                    assessments_a=["a1"],
                    assessments_b=["b1"],
                )
            ],
            provenance={
                "rep-b": [
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id="pkg-1",
                        target_artifact_id="rep-b",
                        producer="t",
                    )
                ]
            },
        )
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["novelty_threat_detection_accuracy"]["value"] == 1.0
    assert result.value["metrics"]["stale_reuse_rate"]["value"] == 0.0


async def test_stale_reuse_fails():
    ref = {
        "expected_overall_baseline": "clear",
        "expected_overall_changed": "blocked",
        "expected_trigger": True,
        "expected_threatened": True,
    }
    # same assessment reused across reports despite changed literature
    result = await NoveltyRevalidationEvaluator().evaluate(
        _ctx(
            _case(ref),
            [
                _report(
                    overall_a="clear",
                    overall_b="blocked",
                    assessments_a=["a1"],
                    assessments_b=["a1"],
                )
            ],
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "STALE REUSE" in result.explanation
    assert result.value["metrics"]["stale_reuse_rate"]["value"] == 1.0


async def test_irrelevant_update_does_not_invalidate_passes():
    ref = {
        "expected_overall_baseline": "clear",
        "expected_overall_changed": "clear",
        "expected_trigger": False,
        "expected_irrelevant": True,
    }
    provenance = {
        "rep-b": [
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id="pkg-1",
                target_artifact_id="rep-b",
                producer="t",
            )
        ]
    }
    result = await NoveltyRevalidationEvaluator().evaluate(
        _ctx(_case(ref), [_report(overall_a="clear", overall_b="clear")], provenance)
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["irrelevant_update_accuracy"]["value"] == 1.0


async def test_irrelevant_invalidation_fails():
    ref = {
        "expected_overall_baseline": "clear",
        "expected_overall_changed": "clear",
        "expected_trigger": False,
        "expected_irrelevant": True,
    }
    result = await NoveltyRevalidationEvaluator().evaluate(
        _ctx(_case(ref), [_report(overall_a="clear", overall_b="blocked")])
    )
    assert result.status == EvaluatorStatus.failed
    assert "IRRELEVANT UPDATE INVALIDATED NOVELTY" in result.explanation


async def test_supersession_passes():
    ref = {
        "expected_overall_baseline": "blocked",
        "expected_overall_changed": "blocked",
        "expected_trigger": False,
        "expected_supersession": True,
    }
    provenance = {
        "rep-b": [
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id="pkg-1",
                target_artifact_id="rep-b",
                producer="t",
            )
        ]
    }
    result = await NoveltyRevalidationEvaluator().evaluate(
        _ctx(
            _case(ref),
            [_report(overall_a="blocked", overall_b="blocked", supersedes_a=True)],
            provenance,
        )
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["supersession_accuracy"]["value"] == 1.0


async def test_missing_supersession_fails():
    ref = {"expected_supersession": True}
    result = await NoveltyRevalidationEvaluator().evaluate(
        _ctx(_case(ref), [_report(supersedes_a=False)])
    )
    assert result.status == EvaluatorStatus.failed
    assert "SUPERSESSION MISSING" in result.explanation


async def test_provenance_version_missing_fails():
    ref = {
        "expected_overall_baseline": "clear",
        "expected_overall_changed": "blocked",
        "expected_trigger": True,
    }
    result = await NoveltyRevalidationEvaluator().evaluate(
        _ctx(_case(ref), [_report(overall_a="clear", overall_b="blocked")], provenance={})
    )
    assert result.status == EvaluatorStatus.failed
    assert "PROVENANCE VERSION" in result.explanation


async def test_no_report_fails():
    result = await NoveltyRevalidationEvaluator().evaluate(_ctx(_case({}), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None
