"""Phase 7D.3A focused model qualification — regression tests.

Covers: repaired critic fixtures at full five-task coverage, the evidence-ID
scalar-reference evaluator fix (never char-splits a scalar id), critical
grounding failures, five-repetition borderline qualification, primary/fallback
selection among qualified models, and provider failure never counted as success.
"""

from __future__ import annotations

import pytest

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_live_quality_reasoning.plugin import (
    LiveQualityReasoningEvaluator,
)
from research_harness.research.benchmarks import (
    LIVE_QUALITY_CRITIC_V1,
)
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.routing.qualification import (
    build_task_matrix,
    qualify_task,
)
from research_harness.research.routing.readiness import criteria_for_role
from research_harness.research.schemas.evaluation import (
    BenchmarkCase,
    EvaluatorStatus,
)
from research_harness.research.schemas.live_quality import LiveQualityTaskPerformance

# ---------------------------------------------------------------------------
# repaired critic fixtures at full five-task coverage
# ---------------------------------------------------------------------------


def test_critic_fixtures_validate_all_five_tasks() -> None:
    """Every repaired critic fixture must validate against its artifact schema
    (regression for the 7D.3 fixture defects that silently errored
    results_critique / manuscript_critique)."""
    from research_harness.research.benchmarks.calibration import _fixture_schema

    assert len(LIVE_QUALITY_CRITIC_V1.cases) == 5
    for case in LIVE_QUALITY_CRITIC_V1.cases:
        fixtures = case.input.get("fixtures") or {}
        assert fixtures, case.id
        for atype, payloads in fixtures.items():
            schema = _fixture_schema(atype)
            assert schema is not None, f"{case.id}: no schema for {atype}"
            for _i, payload in enumerate(payloads or []):
                schema.model_validate(payload)  # raises if invalid


def test_critic_reference_cases_cover_all_tasks() -> None:
    tasks = {str((c.reference or {}).get("task")) for c in LIVE_QUALITY_CRITIC_V1.cases}
    assert tasks == {
        "mechanism_critique",
        "model_critique",
        "proposition_critique",
        "results_critique",
        "manuscript_critique",
    }


# ---------------------------------------------------------------------------
# evidence-ID scalar reference fix (option B: semantic candidates, IDs assigned
# deterministically by production code)
# ---------------------------------------------------------------------------


def _case(reference: dict) -> BenchmarkCase:
    return BenchmarkCase(
        id="lq-evidence-extraction",
        benchmark_id="live-quality-reasoning-v1",
        name="evidence extraction",
        reference=reference,
        evaluation_dimensions=["evidence_extraction"],
    )


def _evidence_context(reference: dict, *, source_id: str, produced_doc: bool) -> EvaluatorContext:
    from research_harness.contracts.blob import BlobReference
    from research_harness.research.schemas.evidence import EvidenceItem, Locator
    from research_harness.research.schemas.full_text import FullTextDocument, TextStatus

    doc_id = "lq-doc-1"
    blob = BlobReference(digest="deadbeef", size_bytes=4, storage_key="ab")
    doc = FullTextDocument(
        paper_identity_id="lq-pid",
        document_acquisition_id="lq-acq",
        source_blob=blob,
        text_blob=blob,
        extractor="documents.extractor.pypdf",
        page_count=2,
        pages_with_text=2,
        character_count=100,
        text_status=TextStatus.extracted,
    )
    evidence = EvidenceItem(
        statement="Algorithmic pricing reduces consumer welfare in online markets.",
        source_artifact_id=source_id,
        category="result",
        locator=Locator(page=1, pages=[1]),
        extraction_method="model-assisted",
    )
    produced = [ArtifactEnvelope.create(payload=evidence, artifact_type="evidence_item")]
    if produced_doc:
        produced.insert(
            0,
            ArtifactEnvelope.create(
                payload=doc, artifact_type="full_text_document", artifact_id=doc_id
            ),
        )
    return EvaluatorContext(case=_case(reference), case_envelope=None, produced_artifacts=produced)


@pytest.mark.asyncio
async def test_evidence_scalar_source_id_is_single_reference() -> None:
    """A scalar source_artifact_id must be treated as ONE reference, never
    char-split (regression: 'lq-doc-1' was iterated as 'l','q','-',...)."""
    reference = {
        "task": "evidence_extraction",
        "required_concepts": ["algorithmic pricing", "consumer welfare"],
    }
    result = await LiveQualityReasoningEvaluator().evaluate(
        _evidence_context(reference, source_id="lq-doc-1", produced_doc=True)
    )
    value = result.value or {}
    assert result.status == EvaluatorStatus.passed
    assert value.get("unsupported_references") == 0
    assert value.get("critical_grounding_failures") == 0


@pytest.mark.asyncio
async def test_evidence_genuine_unsupported_scalar_id_is_grounding_failure() -> None:
    """A scalar source_artifact_id pointing at a NON-produced artifact is a real
    critical grounding failure (the interface does not ask the model to invent
    ids, so this is a genuine model/provider outcome)."""
    reference = {
        "task": "evidence_extraction",
        "required_concepts": ["algorithmic pricing", "consumer welfare"],
    }
    result = await LiveQualityReasoningEvaluator().evaluate(
        _evidence_context(reference, source_id="hallucinated-doc-99", produced_doc=False)
    )
    value = result.value or {}
    assert result.status == EvaluatorStatus.failed
    assert value.get("unsupported_references") == 1
    assert value.get("critical_grounding_failures") == 1


def test_evidence_interface_never_asks_model_for_ids() -> None:
    """The production evidence interface is option B: the model returns semantic
    candidates (category/statement/pages/confidence); artifact IDs are assigned
    deterministically by the orchestrator. The output schema must not include
    any id field."""
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceCandidate,
    )

    assert set(EvidenceCandidate.model_fields) == {
        "category",
        "statement",
        "page_numbers",
        "confidence",
        "excerpt",
    }
    assert "source_artifact_id" not in EvidenceCandidate.model_fields
    assert "id" not in EvidenceCandidate.model_fields


# ---------------------------------------------------------------------------
# five-repetition borderline qualification
# ---------------------------------------------------------------------------


def _perf(task_id: str, *, det: float, reps: int) -> LiveQualityTaskPerformance:
    return LiveQualityTaskPerformance(
        task_id=task_id,
        task_name=task_id,
        repetitions=reps,
        pass_rate_mean=det,
        pass_rate_worst=det,
        pass_rate_variance=0.0,
        pass_rates=[det] * reps,
        structured_output_success_rate=0.9,
        provider_error_frequency=0.0,
        critical_grounding_failures=0,
    )


def test_five_rep_borderline_qualifies_for_task() -> None:
    """A borderline candidate (worst just above the threshold) qualifies at the
    task level when evaluated over 5 repetitions."""
    from research_harness.research.routing.qualification import aggregate_task_performance
    from research_harness.research.schemas.live_quality import LiveQualityModelResult

    result = LiveQualityModelResult(
        candidate_id="m-border",
        model={},
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        repetitions=5,
        task_performance=[
            _perf("lq-literature-synthesis", det=0.86, reps=5),
            _perf("lq-evidence-extraction", det=0.5, reps=5),
        ],
    )
    tp = aggregate_task_performance(result, "synthesis")
    assert tp.repetitions == 5
    ok, reasons = qualify_task(result, "synthesis", criteria_for_role("reasoning"))
    assert ok is True
    assert reasons == []


def test_errored_repetition_never_counts_as_success() -> None:
    """A task with an errored repetition (no case data) has < min repetitions
    and is never qualified — provider failure is not success."""
    from research_harness.research.routing.qualification import aggregate_task_performance
    from research_harness.research.schemas.live_quality import LiveQualityModelResult

    result = LiveQualityModelResult(
        candidate_id="m-a",
        model={},
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        repetitions=3,
        task_performance=[_perf("lq-literature-synthesis", det=1.0, reps=2)],
    )
    tp = aggregate_task_performance(result, "synthesis")
    assert tp.repetitions == 2
    ok, reasons = qualify_task(result, "synthesis", criteria_for_role("reasoning"))
    assert ok is False
    assert any("repetitions" in r for r in reasons)


# ---------------------------------------------------------------------------
# primary/fallback selection among qualified models
# ---------------------------------------------------------------------------


def test_critic_primary_and_fallback_from_qualified_only() -> None:
    """For the critic role, only qualified models can be primary/fallback; a
    candidate failing any critic task is never selected."""
    from research_harness.research.schemas.live_quality import LiveQualityModelResult

    critic_tasks = {
        "lq-mechanism-critique": _perf("lq-mechanism-critique", det=0.9, reps=3),
        "lq-model-critique": _perf("lq-model-critique", det=0.9, reps=3),
        "lq-proposition-critique": _perf("lq-proposition-critique", det=0.9, reps=3),
        "lq-results-critique": _perf("lq-results-critique", det=0.9, reps=3),
        "lq-manuscript-critique": _perf("lq-manuscript-critique", det=0.9, reps=3),
    }
    good = LiveQualityModelResult(
        candidate_id="m-a",
        model={},
        role="critic",
        benchmark_id="live-quality-critic-v1",
        repetitions=3,
        task_results=[
            {
                "repetition": i,
                "run_id": f"r{i}",
                "report_id": f"p{i}",
                "report_status": "passed",
                "cases_total": 5,
                "cases_passed": 5,
                "cases_failed": 0,
                "cases_error": 0,
                "task_pass_rate": 1.0,
                "task_completed": True,
            }
            for i in range(3)
        ],
        task_performance=[critic_tasks[t] for t in critic_tasks],
        deterministic_pass_rate_mean=0.9,
        deterministic_pass_rate_worst=0.9,
        structured_output_success_rate=0.9,
        provider_error_frequency=0.0,
    )
    bad = good.model_copy(deep=True)
    bad.candidate_id = "m-bad"
    bad.task_performance = [
        _perf("lq-results-critique", det=0.4, reps=3) if tp.task_id == "lq-results-critique" else tp
        for tp in good.task_performance
    ]
    bad.deterministic_pass_rate_mean = (0.9 * 4 + 0.4) / 5
    matrix, _rows = build_task_matrix(
        {"m-a": good, "m-bad": bad},
        role="critic",
        benchmark_id="live-quality-critic-v1",
        repetitions=3,
    )
    assert sorted(matrix.role_qualified_models) == ["m-a"]
    assert sorted(matrix.qualified_models_by_task["results_critique"]) == ["m-a"]
    # m-bad is task-qualified for mechanism_critique but not role-qualified
    assert sorted(matrix.qualified_models_by_task["mechanism_critique"]) == ["m-a", "m-bad"]
