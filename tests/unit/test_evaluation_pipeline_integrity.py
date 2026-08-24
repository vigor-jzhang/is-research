"""Phase 6H unit tests — pipeline-integrity evaluator metrics and gates."""

from __future__ import annotations

from typing import Any

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_pipeline_integrity.plugin import (
    PipelineIntegrityEvaluator,
)
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus


def _env(artifact_id: str, artifact_type: str, payload: dict) -> ArtifactEnvelope:
    return ArtifactEnvelope[dict[str, Any]].create(
        payload=payload,
        artifact_type=artifact_type,
        producer="test",
        artifact_id=artifact_id,
    )


def _case(reference: dict) -> BenchmarkCase:
    return BenchmarkCase(
        id="c1",
        benchmark_id="b",
        version=1,
        name="case",
        input={},
        reference=reference,
        evaluation_dimensions=["pipeline"],
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


def _ref(**overrides) -> dict:
    ref = {
        "expected_stages": {"equilibrium": "equilibrium_analysis"},
        "expected_provenance": [],
        "expected_equilibrium": {},
        "expected_conditions": [],
        "expected_baseline": {},
        "expected_citation_identity": {},
    }
    ref.update(overrides)
    return ref


def _base_produced() -> list:
    return [
        _env("eq-analysis", "equilibrium_analysis", {"selected_candidate_id": "cand"}),
        _env(
            "cand",
            "equilibrium_candidate",
            {
                "expressions": [
                    {
                        "variable": "q",
                        "expression": {"expression": "(a-c)/2"},
                        "conditions": ["2*b != 0"],
                    }
                ]
            },
        ),
    ]


async def test_all_stages_complete_passes():
    case = _case(_ref())
    result = await PipelineIntegrityEvaluator().evaluate(_ctx(case, _base_produced()))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["stage_completion_rate"]["value"] == 1.0
    assert result.value["metrics"]["end_to_end_pass"]["value"] == 1.0
    assert result.value["metrics"]["deterministic_failure_count"]["value"] == 0.0


async def test_missing_stage_fails():
    case = _case(
        _ref(
            expected_stages={
                "equilibrium": "equilibrium_analysis",
                "results": "results_package",
            }
        )
    )
    result = await PipelineIntegrityEvaluator().evaluate(_ctx(case, _base_produced()))
    assert result.status == EvaluatorStatus.failed
    assert "STAGE MISSING results" in result.explanation
    assert result.value["metrics"]["stage_completion_rate"]["value"] == 1.0
    assert result.value["metrics"]["stage_completion_rate"]["count"] == 2


async def test_broken_provenance_chain_fails():
    case = _case(_ref(expected_provenance=[["evidence_item", "synthesis_statement"]]))
    produced = _base_produced() + [
        _env("ev-1", "evidence_item", {"statement": "x"}),
        _env("stmt-1", "synthesis_statement", {"statement": "y"}),
    ]
    result = await PipelineIntegrityEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.failed
    assert "BROKEN PROVENANCE CHAIN" in result.explanation
    assert result.value["metrics"]["provenance_integrity_rate"]["value"] == 0.0


async def test_provenance_chain_satisfied_passes():
    case = _case(_ref(expected_provenance=[["evidence_item", "synthesis_statement"]]))
    produced = _base_produced() + [
        _env("ev-1", "evidence_item", {"statement": "x"}),
        _env("stmt-1", "synthesis_statement", {"statement": "y"}),
    ]
    provenance = {"stmt-1": [_fake_link("ev-1")]}
    result = await PipelineIntegrityEvaluator().evaluate(_ctx(case, produced, provenance))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["provenance_integrity_rate"]["value"] == 1.0


class _FakeLink:
    def __init__(self, source_artifact_id: str) -> None:
        self.source_artifact_id = source_artifact_id
        self.relation = "derived_from"


def _fake_link(source_artifact_id: str) -> _FakeLink:
    return _FakeLink(source_artifact_id)


async def test_unsupported_evidence_reference_fails():
    case = _case(_ref())
    produced = _base_produced() + [
        _env("stmt-1", "synthesis_statement", {"supporting_evidence_ids": ["ghost-ev"]})
    ]
    result = await PipelineIntegrityEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.failed
    assert "UNSUPPORTED EVIDENCE" in result.explanation
    assert result.value["metrics"]["grounding_integrity_rate"]["value"] == 0.0


async def test_invalid_equilibrium_accepted_fails():
    case = _case(_ref(expected_equilibrium={"q": "(a-c)/2"}))
    produced = _base_produced()
    produced[1] = _env(
        "cand",
        "equilibrium_candidate",
        {"expressions": [{"variable": "q", "expression": {"expression": "a"}, "conditions": []}]},
    )
    result = await PipelineIntegrityEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.failed
    assert "INVALID EQUILIBRIUM ACCEPTED" in result.explanation


async def test_equilibrium_symbolic_equivalence():
    # (a-c)/2 written differently still matches
    case = _case(_ref(expected_equilibrium={"q": "(a-c)/2"}))
    produced = _base_produced()
    produced[1] = _env(
        "cand",
        "equilibrium_candidate",
        {
            "expressions": [
                {"variable": "q", "expression": {"expression": "a/2 - c/2"}, "conditions": []}
            ]
        },
    )
    result = await PipelineIntegrityEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.passed


async def test_condition_lost_downstream_fails():
    case = _case(_ref(expected_conditions=["2*b != 0"]))
    produced = _base_produced()
    produced[1] = _env(
        "cand",
        "equilibrium_candidate",
        {
            "expressions": [
                {"variable": "q", "expression": {"expression": "(a-c)/2"}, "conditions": []}
            ]
        },
    )
    result = await PipelineIntegrityEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.failed
    assert "CONDITION LOST DOWNSTREAM" in result.explanation
    assert result.value["metrics"]["condition_preservation_rate"]["value"] == 0.0


async def test_condition_preserved_passes():
    case = _case(_ref(expected_conditions=["b > 0"]))
    produced = _base_produced() + [
        _env(
            "prop-1",
            "proposition",
            {"conditions": ["b > 0"], "statement": "p"},
        )
    ]
    result = await PipelineIntegrityEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["condition_preservation_rate"]["value"] == 1.0


async def test_numerical_mismatch_fails():
    case = _case(_ref(expected_baseline={"q": 4.5}))
    produced = _base_produced() + [
        _env(
            "result-1",
            "numerical_result",
            {"scenario": "baseline", "outcomes": {"q": 0.45}},
        )
    ]
    result = await PipelineIntegrityEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.failed
    assert "NUMERICAL MISMATCH" in result.explanation


async def test_numerical_agreement_passes():
    case = _case(_ref(expected_baseline={"q": 4.5}))
    produced = _base_produced() + [
        _env(
            "result-1",
            "numerical_result",
            {"scenario": "baseline", "outcomes": {"q": 4.5}},
        )
    ]
    result = await PipelineIntegrityEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.passed


async def test_wrong_citation_identity_fails():
    case = _case(_ref(expected_citation_identity={"lit-1": "Paper A"}))
    produced = _base_produced() + [
        _env("rec-1", "paper_record", {"title": "Paper B"}),
        _env("identity-1", "paper_identity", {"member_paper_artifact_ids": ["rec-1"]}),
        _env("ev-1", "evidence_item", {"statement": "x"}),
        _env(
            "sec-1",
            "manuscript_section",
            {
                "citations": [
                    {
                        "citation_id": "lit-1",
                        "paper_identity_id": "identity-1",
                        "evidence_item_id": "ev-1",
                    }
                ]
            },
        ),
    ]
    result = await PipelineIntegrityEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.failed
    assert "WRONG CITATION IDENTITY" in result.explanation
    assert result.value["metrics"]["citation_integrity_rate"]["value"] == 0.0


async def test_citation_identity_correct_passes():
    case = _case(_ref(expected_citation_identity={"lit-1": "Paper A"}))
    produced = _base_produced() + [
        _env("rec-1", "paper_record", {"title": "Paper A"}),
        _env("identity-1", "paper_identity", {"member_paper_artifact_ids": ["rec-1"]}),
        _env("ev-1", "evidence_item", {"statement": "x"}),
        _env(
            "sec-1",
            "manuscript_section",
            {
                "citations": [
                    {
                        "citation_id": "lit-1",
                        "paper_identity_id": "identity-1",
                        "evidence_item_id": "ev-1",
                    }
                ]
            },
        ),
    ]
    result = await PipelineIntegrityEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["citation_integrity_rate"]["value"] == 1.0


async def test_invented_bibliography_metadata_fails():
    case = _case(_ref())
    produced = _base_produced() + [
        _env("rec-1", "paper_record", {"title": "Paper A", "year": 2020, "authors": []}),
        _env("identity-1", "paper_identity", {"member_paper_artifact_ids": ["rec-1"]}),
        _env(
            "bib-1",
            "bibliography",
            {
                "entries": [
                    {
                        "paper_identity_id": "identity-1",
                        "title": "Fabricated Title",
                        "authors": ["Made Up"],
                        "year": 1999,
                    }
                ]
            },
        ),
    ]
    result = await PipelineIntegrityEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.failed
    assert "INVENTED BIBLIOGRAPHY" in result.explanation
    assert result.value["metrics"]["bibliography_fidelity_rate"]["value"] == 0.0


async def test_bibliography_fidelity_passes():
    case = _case(_ref())
    produced = _base_produced() + [
        _env("rec-1", "paper_record", {"title": "Paper A", "year": 2020, "authors": []}),
        _env("identity-1", "paper_identity", {"member_paper_artifact_ids": ["rec-1"]}),
        _env(
            "bib-1",
            "bibliography",
            {
                "entries": [
                    {
                        "paper_identity_id": "identity-1",
                        "title": "Paper A",
                        "year": 2020,
                        "authors": [],
                    }
                ]
            },
        ),
    ]
    result = await PipelineIntegrityEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["bibliography_fidelity_rate"]["value"] == 1.0


async def test_no_artifacts_produced():
    result = await PipelineIntegrityEvaluator().evaluate(_ctx(_case(_ref()), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score == 0.0
