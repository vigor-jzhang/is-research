"""Phase 7A unit tests — incremental-revalidation evaluator.

Covers: stale-reuse detection, required recomputation, unchanged deterministic
reuse, and provenance-version accuracy (transitive derivation).
"""

from __future__ import annotations

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_revalidation.plugin import RevalidationEvaluator
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus


def _report_env(stages: dict) -> ArtifactEnvelope:
    from research_harness.research.benchmarks.workflows import RevalidationReportRecord

    return ArtifactEnvelope.create(
        payload=RevalidationReportRecord(
            benchmark_case_id="c1",
            stages=stages,
        ),
        artifact_type="revalidation_report",
        producer="test",
    )


def _provenance(*edges: tuple[str, str]) -> dict[str, list]:
    out: dict[str, list] = {}
    for source, target in edges:
        out.setdefault(target, []).append(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=source,
                target_artifact_id=target,
                producer="test",
            )
        )
    return out


def _case(reference: dict) -> BenchmarkCase:
    return BenchmarkCase(
        id="c1",
        benchmark_id="b",
        version=1,
        name="case",
        input={},
        reference=reference,
        evaluation_dimensions=["revalidation"],
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


async def test_all_stages_recomputed_passes():
    stages = {
        "synthesis": {
            "recomputed": True,
            "reused": False,
            "upstream_b": "corpus-b",
            "downstream_a": "syn-a",
            "downstream_b": "syn-b",
        }
    }
    provenance = _provenance(("corpus-b", "syn-b"))
    result = await RevalidationEvaluator().evaluate(
        _ctx(
            _case({"expected_recomputed": ["synthesis"], "expected_reused": []}),
            [_report_env(stages)],
            provenance,
        )
    )
    assert result.status == EvaluatorStatus.passed
    m = result.value["metrics"]
    assert m["stale_reuse_rate"]["value"] == 0.0
    assert m["required_recomputation_accuracy"]["value"] == 1.0
    assert m["provenance_version_accuracy"]["value"] == 1.0


async def test_stale_reuse_fails():
    stages = {
        "synthesis": {
            "recomputed": False,
            "reused": True,
            "upstream_b": "corpus-b",
            "downstream_a": "syn-a",
            "downstream_b": "syn-a",
        }
    }
    result = await RevalidationEvaluator().evaluate(
        _ctx(
            _case({"expected_recomputed": ["synthesis"], "expected_reused": []}),
            [_report_env(stages)],
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "STALE REUSE" in result.explanation
    assert result.value["metrics"]["stale_reuse_rate"]["value"] == 1.0
    assert result.value["metrics"]["required_recomputation_accuracy"]["value"] == 0.0


async def test_unchanged_reuse_passes():
    stages = {
        "unchanged_reuse": {
            "recomputed": False,
            "reused": True,
            "execution_reused": True,
            "upstream_b": "corpus",
            "downstream_a": "exec-1",
            "downstream_b": "exec-1",
        }
    }
    result = await RevalidationEvaluator().evaluate(
        _ctx(
            _case({"expected_recomputed": [], "expected_reused": ["unchanged_reuse"]}),
            [_report_env(stages)],
        )
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["unchanged_reuse_accuracy"]["value"] == 1.0


async def test_unnecessary_recomputation_fails():
    stages = {
        "unchanged_reuse": {
            "recomputed": True,
            "reused": False,
            "execution_reused": False,
            "upstream_b": "corpus",
            "downstream_a": "exec-1",
            "downstream_b": "exec-2",
        }
    }
    result = await RevalidationEvaluator().evaluate(
        _ctx(
            _case({"expected_recomputed": [], "expected_reused": ["unchanged_reuse"]}),
            [_report_env(stages)],
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "UNNECESSARY RECOMPUTATION" in result.explanation


async def test_provenance_version_missing_fails():
    stages = {
        "synthesis": {
            "recomputed": True,
            "reused": False,
            "upstream_b": "corpus-b",
            "downstream_a": "syn-a",
            "downstream_b": "syn-b",
        }
    }
    # syn-b is NOT derived from corpus-b (empty provenance -> unreachable)
    result = await RevalidationEvaluator().evaluate(
        _ctx(
            _case({"expected_recomputed": ["synthesis"], "expected_reused": []}),
            [_report_env(stages)],
            provenance={},
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "PROVENANCE VERSION" in result.explanation
    assert result.value["metrics"]["provenance_version_accuracy"]["value"] == 0.0


async def test_transitive_provenance_passes():
    stages = {
        "equilibrium": {
            "recomputed": True,
            "reused": False,
            "upstream_b": "model-b",
            "downstream_a": "eq-a",
            "downstream_b": "eq-b",
        }
    }
    # eq-b -> eq-init -> exec-b -> model-b (transitive)
    provenance = _provenance(
        ("model-b", "exec-b"),
        ("exec-b", "eq-init"),
        ("eq-init", "eq-b"),
    )
    result = await RevalidationEvaluator().evaluate(
        _ctx(
            _case({"expected_recomputed": ["equilibrium"], "expected_reused": []}),
            [_report_env(stages)],
            provenance,
        )
    )
    assert result.status == EvaluatorStatus.passed


async def test_no_report_fails():
    result = await RevalidationEvaluator().evaluate(_ctx(_case({}), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None


async def test_missing_stage_fails():
    result = await RevalidationEvaluator().evaluate(
        _ctx(
            _case({"expected_recomputed": ["synthesis"], "expected_reused": []}),
            [_report_env({})],
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "missing from revalidation report" in result.explanation
