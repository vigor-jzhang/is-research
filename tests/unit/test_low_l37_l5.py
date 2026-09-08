"""Regression tests for round 36 — L37 (empty plugin set composes a runtime
that silently does nothing) and L5 (pipeline-integrity evidence graph
degenerates to "every produced artifact").

L37: `AppConfig.plugins` may legitimately be empty as a base that callers
extend with `extra_plugins`, so the schema cannot reject it. The composition
root can see both, and is where an empty resolved set is a hard error: every
run over a plugin-less runtime reports success while producing nothing.

L5: `evaluator.pipeline_integrity` filtered `evidence_artifact_ids` by
membership in `types_present` — the types of the produced artifacts themselves
— so the filter could never exclude anything. The evidence list now names the
artifacts the audit actually enumerates.
"""

from __future__ import annotations

from typing import Any

import pytest

from research_harness.app.bootstrap import build_runtime
from research_harness.config.schema import AppConfig
from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.kernel.errors import PluginError
from research_harness.kernel.plugin import Plugin, PluginMetadata
from research_harness.plugins.research.evaluator_pipeline_integrity.plugin import (
    PipelineIntegrityEvaluator,
)
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase

# ---------------------------------------------------------------------------
# L37 — empty plugin set
# ---------------------------------------------------------------------------


class _DummyPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="tool.dummy_r36",
            version="0.1.0",
            plugin_type="tool",
            description="round 36 extra-plugin guard",
            provides=["tool.dummy_r36"],
            requires=[],
        )


def test_build_runtime_rejects_an_empty_plugin_set() -> None:
    """No config plugins and no extra_plugins must be a loud composition
    error, not a runtime whose runs silently succeed at doing nothing."""
    with pytest.raises(PluginError, match="no plugins to compose"):
        build_runtime(AppConfig(plugins=[]))


def test_build_runtime_still_accepts_extra_plugins_over_an_empty_list() -> None:
    """Guard: the empty-config-list + extra_plugins pattern (test fakes) keeps
    working — only the truly empty set is rejected."""
    runtime = build_runtime(AppConfig(plugins=[]), extra_plugins=[_DummyPlugin()])
    ids = [p.metadata.id for p in runtime.plugins.list_plugins()]
    assert ids == ["tool.dummy_r36"]


# ---------------------------------------------------------------------------
# L5 — pipeline-integrity evidence graph
# ---------------------------------------------------------------------------


def _env(artifact_id: str, artifact_type: str, payload: dict) -> ArtifactEnvelope:
    return ArtifactEnvelope[dict[str, Any]].create(
        payload=payload,
        artifact_type=artifact_type,
        producer="test",
        artifact_id=artifact_id,
    )


async def test_pipeline_integrity_evidence_names_audited_artifacts_only() -> None:
    from research_harness.research.schemas.evaluation import EvaluatorStatus

    case = BenchmarkCase(
        id="c1",
        benchmark_id="b",
        version=1,
        name="case",
        input={},
        reference={
            "expected_stages": {"equilibrium": "equilibrium_candidate"},
            "expected_provenance": [],
        },
        evaluation_dimensions=["pipeline"],
        tags=[],
    )
    ctx = EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=[
            _env(
                "cand",
                "equilibrium_candidate",
                {
                    "expressions": [
                        {
                            "variable": "q",
                            "expression": {"expression": "(a-c)/2"},
                            "conditions": [],
                        }
                    ]
                },
            ),
            # acquisition-stage artifact the audit never reads
            _env("paper-1", "paper_record", {"title": "Some Paper"}),
        ],
        config={},
        provenance={},
    )
    result = await PipelineIntegrityEvaluator().evaluate(ctx)
    assert result.status is EvaluatorStatus.passed
    assert "cand" in result.evidence_artifact_ids
    # pre-fix this failed: the type filter was against the produced types
    # themselves, so "all produced artifacts" flowed into the evidence graph.
    assert "paper-1" not in result.evidence_artifact_ids


def test_pipeline_integrity_evidence_covers_provenance_and_stage_types() -> None:
    """Guard: stage and provenance types from the benchmark reference stay in
    the audited set even when they are outside the fixed scanned types."""
    audited = PipelineIntegrityEvaluator._audited_types(
        {"equilibrium": "equilibrium_analysis"},
        [("literature_synthesis", "research_gap")],
    )
    assert "equilibrium_analysis" in audited
    assert "literature_synthesis" in audited
    assert "research_gap" in audited
    assert "paper_record" not in audited
