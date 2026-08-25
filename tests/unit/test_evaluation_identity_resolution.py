"""Phase 7A.1 unit tests — ingestion + identity-resolution evaluator.

Covers: canonical mapping, duplicate collapse, false-merge rejection, false
split, DOI normalization, supersession, and partial ingestion.
"""

from __future__ import annotations

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_identity_resolution.plugin import (
    IdentityResolutionEvaluator,
)
from research_harness.research.benchmarks.workflows import IngestionIdentityReport
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus
from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod


def _report(paper_ids: dict[str, str], superseded: list[str] | None = None) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=IngestionIdentityReport(
            benchmark_case_id="c1",
            paper_ids=paper_ids,
            superseded_identity_ids=list(superseded or []),
        ),
        artifact_type="ingestion_identity_report",
        producer="test",
    )


def _identity_env(iid: str, members: list[str]) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=PaperIdentity(
            member_paper_artifact_ids=members,
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.exact_identifier,
            resolution_evidence=[],
        ),
        artifact_type="paper_identity",
        producer="test",
        artifact_id=iid,
    )


def _case(reference: dict) -> BenchmarkCase:
    return BenchmarkCase(
        id="c1",
        benchmark_id="b",
        version=1,
        name="case",
        input={},
        reference=reference,
        evaluation_dimensions=["ingestion_identity"],
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


async def test_doi_collapse_passes():
    paper_ids = {"ing-paper-0": "p0", "ing-paper-1": "p1"}
    ref = {
        "expected_identities": [
            {"members": ["ing-paper-0", "ing-paper-1"], "method": "exact_identifier"}
        ]
    }
    produced = [
        _report(paper_ids),
        _identity_env("i0", ["p0", "p1"]),
    ]
    result = await IdentityResolutionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.passed
    m = result.value["metrics"]
    assert m["canonical_mapping_accuracy"]["value"] == 1.0
    assert m["duplicate_collapse_accuracy"]["value"] == 1.0
    assert m["false_merge_rate"]["value"] == 0.0
    assert m["false_split_rate"]["value"] == 0.0


async def test_similar_title_stays_separate_passes():
    paper_ids = {"ing-paper-0": "p0", "ing-paper-1": "p1"}
    ref = {
        "expected_identities": [
            {"members": ["ing-paper-0"], "method": "exact_identifier"},
            {"members": ["ing-paper-1"], "method": "exact_identifier"},
        ]
    }
    produced = [
        _report(paper_ids),
        _identity_env("i0", ["p0"]),
        _identity_env("i1", ["p1"]),
    ]
    result = await IdentityResolutionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["false_merge_rate"]["value"] == 0.0


async def test_false_semantic_merge_fails():
    # two similar-titled papers wrongly merged into one identity
    paper_ids = {"ing-paper-0": "p0", "ing-paper-1": "p1"}
    ref = {
        "expected_identities": [
            {"members": ["ing-paper-0"], "method": "exact_identifier"},
            {"members": ["ing-paper-1"], "method": "exact_identifier"},
        ]
    }
    produced = [_report(paper_ids), _identity_env("i0", ["p0", "p1"])]
    result = await IdentityResolutionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.failed
    assert "FALSE MERGE" in result.explanation
    assert result.value["metrics"]["false_merge_rate"]["value"] == 2.0
    assert result.value["dimension_scores"]["false_merge_rate"] == 1.0


async def test_false_split_fails():
    # expected one identity but produced two
    paper_ids = {"ing-paper-0": "p0", "ing-paper-1": "p1"}
    ref = {
        "expected_identities": [
            {"members": ["ing-paper-0", "ing-paper-1"], "method": "exact_identifier"}
        ]
    }
    produced = [_report(paper_ids), _identity_env("i0", ["p0"]), _identity_env("i1", ["p1"])]
    result = await IdentityResolutionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.failed
    assert "FALSE SPLIT" in result.explanation
    assert result.value["metrics"]["false_split_rate"]["value"] == 1.0


async def test_doi_normalization_passes():
    paper_ids = {"ing-paper-0": "p0", "ing-paper-1": "p1", "ing-paper-2": "p2"}
    ref = {
        "expected_identities": [
            {"members": ["ing-paper-0", "ing-paper-1", "ing-paper-2"], "normalized": True}
        ]
    }
    produced = [_report(paper_ids), _identity_env("i0", ["p0", "p1", "p2"])]
    result = await IdentityResolutionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["identifier_normalization_accuracy"]["value"] == 1.0


async def test_supersession_passes():
    from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation

    paper_ids = {"ing-paper-0": "p0", "ing-paper-1": "p1"}
    ref = {
        "expected_identities": [
            {"members": ["ing-paper-0", "ing-paper-1"], "method": "exact_identifier"}
        ],
        "expected_superseded": ["ing-paper-0"],
    }
    provenance = {
        "i-new": [],
        "i-old": [
            ProvenanceLink(
                relation=ProvenanceRelation.supersedes,
                source_artifact_id="i-old",
                target_artifact_id="i-new",
                producer="test",
            )
        ],
    }
    produced = [
        _report(paper_ids, superseded=["i-old"]),
        _identity_env("i-old", ["p0"]),
        _identity_env("i-new", ["p0", "p1"]),
    ]
    result = await IdentityResolutionEvaluator().evaluate(_ctx(_case(ref), produced, provenance))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["supersession_accuracy"]["value"] == 1.0


async def test_partial_ingestion_passes():
    paper_ids = {"ing-paper-0": "p0"}
    ref = {
        "expected_identities": [{"members": ["ing-paper-0"], "method": "exact_identifier"}],
        "expected_failed_providers": ["crossref"],
    }
    produced = [_report(paper_ids, superseded=[]), _identity_env("i0", ["p0"])]
    result = await IdentityResolutionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["partial_ingestion_accuracy"]["value"] == 1.0


async def test_missing_expected_group_fails():
    paper_ids = {"ing-paper-0": "p0", "ing-paper-1": "p1"}
    ref = {
        "expected_identities": [
            {"members": ["ing-paper-0", "ing-paper-1"], "method": "exact_identifier"}
        ]
    }
    produced = [_report(paper_ids), _identity_env("i0", ["p0"]), _identity_env("i1", ["p1"])]
    result = await IdentityResolutionEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.failed
    assert "IDENTITY GROUP MISMATCH" in result.explanation


async def test_no_report_fails():
    result = await IdentityResolutionEvaluator().evaluate(_ctx(_case({}), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None
