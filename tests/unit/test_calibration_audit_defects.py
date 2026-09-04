"""Unit tests for H5: the calibration audit must report every failed check.

Six of the eight checks set ``passed=False`` without calling ``_fail``, so they
produced no finding (the verdict stayed "ok") and no ``ConfirmedDefect`` (the
defect was never excluded from qualification). Only ``reference_grounded`` and
``schema_achievable`` actually recorded anything.
"""

from __future__ import annotations

import pytest

from research_harness.research.benchmarks import (
    BenchmarkCaseDefinition,
    BenchmarkDefinition,
    calibration,
)
from research_harness.research.benchmarks.calibration import audit_live_quality_benchmark
from research_harness.research.schemas.live_quality import FailureAttributionKind

SYNTHETIC_ID = "synthetic-live-quality-v1"


@pytest.fixture
def synthetic_benchmark(monkeypatch):
    """Install a benchmark that fails the checks which used to be inert."""

    def _install(cases: list[BenchmarkCaseDefinition], config: dict | None = None):
        definition = BenchmarkDefinition(
            benchmark_id=SYNTHETIC_ID,
            version=1,
            name="synthetic",
            description="synthetic benchmark for H5 regression coverage",
            category="live_quality",
            config=config if config is not None else {"evaluators": []},
            cases=cases,
        )
        patched = dict(calibration.BUILTIN_BENCHMARKS)
        patched[SYNTHETIC_ID] = definition
        monkeypatch.setattr(calibration, "BUILTIN_BENCHMARKS", patched)
        return definition

    return _install


def _case(case_id: str, *, reference: dict | None = None, inputs: dict | None = None):
    return BenchmarkCaseDefinition(
        id=case_id,
        name=case_id,
        description=case_id,
        input=inputs if inputs is not None else {},
        reference=reference if reference is not None else {},
    )


def test_missing_task_reference_is_a_confirmed_defect(synthetic_benchmark):
    """Check 1 (valid_reference) used to set passed=False and stop there."""
    synthetic_benchmark([_case("c1", reference={"task": ""})])
    audit = audit_live_quality_benchmark(SYNTHETIC_ID)

    failed = [c.name for c in audit.checks if not c.passed]
    assert "valid_reference" in failed
    assert audit.verdict == "repair_needed", "verdict ignored the failed check"
    assert audit.confirmed_defects, "failed check produced no confirmed defect"
    assert any(
        d.kind == FailureAttributionKind.benchmark_reference_defect.value
        for d in audit.confirmed_defects
    )


def test_provider_specific_assumption_is_a_confirmed_defect(synthetic_benchmark):
    """Check 8 (no_provider_assumptions) was inert."""
    synthetic_benchmark([_case("c1", inputs={"prompt": "use gpt-4o-mini for this"})])
    audit = audit_live_quality_benchmark(SYNTHETIC_ID)

    assert "no_provider_assumptions" in [c.name for c in audit.checks if not c.passed]
    assert audit.verdict == "repair_needed"
    assert audit.confirmed_defects


def test_evaluator_misconfiguration_is_attributed_to_the_evaluator(synthetic_benchmark):
    """Check 6 (evaluator_correctness) was inert, and is an evaluator defect."""
    synthetic_benchmark(
        [_case("c1")],
        config={"evaluators": ["evaluator.not_a_real_one"]},
    )
    audit = audit_live_quality_benchmark(SYNTHETIC_ID)

    assert "evaluator_correctness" in [c.name for c in audit.checks if not c.passed]
    assert audit.confirmed_defects
    # Attributed as an evaluator defect, not a benchmark reference defect.
    eval_defects = [
        d for d in audit.confirmed_defects if d.kind == FailureAttributionKind.evaluator_defect.value
    ]
    assert eval_defects, [d.kind for d in audit.confirmed_defects]
    assert any(f.check == "evaluator_correctness" for f in audit.findings)


def test_benchmark_level_evaluator_defect_covers_every_case(synthetic_benchmark):
    """A benchmark-level defect must be keyed to cases, or it excludes nothing.

    confirmed_defect_map is matched by exact case_id, so recording the defect
    against a placeholder like "*" would leave every case's failures counted
    against the model.
    """
    synthetic_benchmark(
        [_case("c1"), _case("c2")],
        config={"evaluators": ["evaluator.not_a_real_one"]},
    )
    audit = audit_live_quality_benchmark(SYNTHETIC_ID)

    covered = {d.case_id for d in audit.confirmed_defects}
    assert covered == {"c1", "c2"}, covered


def test_multiple_inert_checks_together_produce_multiple_defects(synthetic_benchmark):
    """The report's measurement: 2 failed checks gave verdict=ok, 0 defects."""
    synthetic_benchmark(
        [
            _case("c1", reference={"task": ""}),
            _case("c2", inputs={"prompt": "assume claude is available"}),
        ]
    )
    audit = audit_live_quality_benchmark(SYNTHETIC_ID)

    failed = {c.name for c in audit.checks if not c.passed}
    assert {"valid_reference", "no_provider_assumptions"} <= failed, failed
    assert audit.verdict == "repair_needed"
    assert len(audit.confirmed_defects) >= 2


def test_clean_benchmark_still_audits_ok(synthetic_benchmark):
    """The fix must not make a healthy benchmark look defective."""
    synthetic_benchmark(
        [_case("c1", reference={"task": "evidence_extraction"}, inputs={"prompt": "hello"})],
        config={"evaluators": ["evaluator.live_quality_reasoning"]},
    )
    audit = audit_live_quality_benchmark(SYNTHETIC_ID)

    assert all(c.passed for c in audit.checks), [c.name for c in audit.checks if not c.passed]
    assert audit.verdict == "ok"
    assert audit.confirmed_defects == []
