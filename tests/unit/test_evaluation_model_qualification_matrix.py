"""Unit tests for M82: the qualification matrix path must not claim measurements.

`_check_matrix` hard-codes stability, rejection, role and tiebreak checks to
`True`. Two of them were nonetheless reported with `count=1, value=1.0`, so a
matrix case asserted a perfect score for checks that never ran -- the same
shape as M8 (a check that looks meaningful and is not).

The verdict is unaffected (it derives from `failures`); the defect is false
confidence in the reported metrics.
"""

from __future__ import annotations

import pytest

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_model_qualification.plugin import (
    ModelQualificationEvaluator,
)
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase
from research_harness.research.schemas.qualification import (
    ProductionQualificationMatrix,
    ProductionQualificationMatrixRow,
)


def _case(reference: dict) -> BenchmarkCase:
    return BenchmarkCase(
        id="c1",
        benchmark_id="b",
        version=1,
        name="qualification matrix",
        input={},
        reference=reference,
        evaluation_dimensions=["model_qualification"],
        tags=[],
    )


def _matrix(role: str, rows: list[dict]) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=ProductionQualificationMatrix(
            role=role,
            benchmark_id="model-qualification-policy-v1",
            status="qualified",
            primary="m-a",
            rows=[ProductionQualificationMatrixRow.model_validate(r) for r in rows],
        ),
        artifact_type="production_qualification_matrix",
        producer="test",
    )


def _ctx(case: BenchmarkCase, produced: list[ArtifactEnvelope]) -> EvaluatorContext:
    return EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=produced,
        config={},
    )


MATRIX_ROW = {
    "role": "reasoning",
    "candidate": "m-a",
    "qualified": True,
    "stability": "stable",
    "primary_eligible": True,
    "fallback_eligible": False,
}

MATRIX_REF = {"expected_matrix_rows": [dict(MATRIX_ROW)]}


@pytest.mark.asyncio
async def test_matrix_path_reports_unchecked_checks_as_unmeasured():
    """M82: hard-coded checks must report count 0, not a perfect 1.0.

    Both halves matter: the harness sums values and counts across cases, so a
    value of 1.0 with a count of 0 inflates the aggregate above 1.0.
    """
    matrix = _matrix("reasoning", [MATRIX_ROW])
    result = await ModelQualificationEvaluator().evaluate(
        _ctx(_case(MATRIX_REF), [matrix])
    )

    metrics = result.value["metrics"]
    for metric_id in ("rejection_classification_accuracy", "role_isolation_accuracy"):
        assert metric_id in metrics
        assert metrics[metric_id]["count"] == 0, f"{metric_id} claimed a measurement"
        assert metrics[metric_id]["value"] == 0.0


@pytest.mark.asyncio
async def test_matrix_path_still_checks_the_rows_it_can():
    """The fix must not disable the checks the matrix path does perform."""
    matrix = _matrix("reasoning", [MATRIX_ROW])
    result = await ModelQualificationEvaluator().evaluate(
        _ctx(_case(MATRIX_REF), [matrix])
    )

    # The matrix rows themselves are still evaluated.
    assert result.value["metrics"]["qualification_decision_accuracy"]["count"] == 1
