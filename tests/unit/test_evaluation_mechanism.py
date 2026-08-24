"""Phase 6D unit tests — mechanism evaluator.

Covers: gap alignment (trace), knowledge-basis discipline, grounding,
candidate validity, unsupported support, critic issue recall, revision
success, selected-mechanism validity, and the structural properties
(literature_supported source ids, new_hypothesis labeling, explicit
assumptions, immutability after revision).
"""

from __future__ import annotations

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_mechanism.plugin import MechanismEvaluator
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus
from research_harness.research.schemas.evidence import EvidenceItem
from research_harness.research.schemas.gap import ResearchGap
from research_harness.research.schemas.mechanism import (
    GapSelection,
    MechanismAnalysisExecution,
    MechanismCandidate,
    MechanismCritique,
    SelectedMechanism,
    SelectionStatus,
)
from research_harness.research.schemas.synthesis import SynthesisStatement

STATEMENT = "Within the reviewed corpus, fees shape entry decisions."


def _statement_env(sid: str) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=SynthesisStatement(
            statement=STATEMENT,
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
            statement="Fees shape entry decisions.",
            source_artifact_id="doc-1",
            category="mechanism",
        ),
        artifact_type="evidence_item",
        producer="test",
        artifact_id=eid,
    )


def _gap_env(gid: str, *, selection_id: str | None = None) -> ArtifactEnvelope:
    payload = ResearchGap(
        title="Fee effects on entry",
        gap_type="mechanism_gap",
        description="Within the reviewed corpus, fee effects are under-theorized.",
        supporting_synthesis_statement_ids=["s1"],
        supporting_evidence_ids=["ev-1"],
    )
    if selection_id:
        payload = payload.model_copy(update={"metadata": {"gap_selection_id": selection_id}})
    return ArtifactEnvelope.create(
        payload=payload,
        artifact_type="research_gap",
        producer="test",
        artifact_id=gid,
    )


def _candidate_env(
    cid: str,
    name: str,
    *,
    gap_id: str = "g1",
    basis: str = "literature_supported",
    source_ids: list[str] | None = None,
    support_ids: list[str] | None = None,
    assumptions: list[str] | None = None,
    actors: list[str] | None = None,
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=MechanismCandidate(
            gap_id=gap_id,
            gap_selection_id="sel-1",
            name=name,
            description=f"Mechanism {name}.",
            causal_logic="Fees reduce entry.",
            actors=actors or ["platform"],
            key_assumptions=assumptions or ["Sellers are profit-maximizing."],
            literature_support_ids=list(support_ids or []),
            grounding=[
                {"element": "fee effects", "basis": basis, "source_ids": list(source_ids or [])}
            ],
            status="candidate",
        ),
        artifact_type="mechanism_candidate",
        producer="test",
        artifact_id=cid,
    )


def _critique_env(
    kid: str, candidate_id: str, category: str, verdict: str = "revise"
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=MechanismCritique(
            mechanism_candidate_id=candidate_id,
            issues=[{"category": category, "description": "issue"}],
            overall_assessment="fixture",
            verdict=verdict,
        ),
        artifact_type="mechanism_critique",
        producer="test",
        artifact_id=kid,
    )


def _selected_env(
    sid: str,
    candidate_id: str,
    name: str,
    *,
    gap_id: str = "g1",
    revised: bool = False,
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=SelectedMechanism(
            gap_id=gap_id,
            gap_selection_id="sel-1",
            mechanism_candidate_id=candidate_id,
            critique_ids=["k1"],
            name=name,
            description=f"Mechanism {name}. (revised)" if revised else f"Mechanism {name}.",
            causal_logic="Fees reduce entry.",
            actors=["platform"],
            key_assumptions=["Sellers are profit-maximizing."],
            grounding=[
                {"element": "fee effects", "basis": "literature_supported", "source_ids": ["s1"]}
            ],
            revision_notes=["Revised per critique."]
            if revised
            else ["No substantive revision needed."],
        ),
        artifact_type="selected_mechanism",
        producer="test",
        artifact_id=sid,
    )


def _selection_env(selection_id: str = "sel-1") -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=GapSelection(
            gap_analysis_id="analysis-1",
            selected_gap_id="g1",
            selection_rationale="fixture",
            status=SelectionStatus.approved,
        ),
        artifact_type="gap_selection",
        producer="test",
        artifact_id=selection_id,
    )


def _execution_env(candidates_rejected: int = 0) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=MechanismAnalysisExecution(
            gap_selection_id="sel-1",
            gap_id="g1",
            candidates_created=1,
            candidates_rejected=candidates_rejected,
        ),
        artifact_type="mechanism_analysis_execution",
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
        evaluation_dimensions=["mechanism"],
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
        "expected_candidates": {"Fee-channel mechanism": {}},
        "expected_invalid_candidates": 0,
        "expected_critic_issues": {},
        "expected_revision": {"Fee-channel mechanism": False},
        "expected_unsupported_support": 0,
    }
    base.update(overrides)
    return base


def _produced(
    *,
    basis: str = "literature_supported",
    source_ids: list[str] | None = None,
    support_ids: list[str] | None = None,
    selected_gap_id: str = "g1",
    name: str = "Fee-channel mechanism",
) -> list:
    stmt = _statement_env("s1")
    ev = _evidence_env("ev-1")
    gap = _gap_env("g1", selection_id="sel-1")
    cand = _candidate_env("c1", name, basis=basis, source_ids=source_ids, support_ids=support_ids)
    sel = _selected_env("sm1", "c1", name)
    return [
        stmt,
        ev,
        gap,
        cand,
        sel,
        _selection_env(),
        _execution_env(),
    ]


async def test_valid_candidate_passes():
    produced = _produced(source_ids=["s1"], support_ids=["s1"])
    result = await MechanismEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["gap_alignment_accuracy"]["value"] == 1.0
    assert result.value["metrics"]["knowledge_basis_accuracy"]["value"] == 1.0
    assert result.value["metrics"]["grounding_accuracy"]["value"] == 1.0
    assert result.value["metrics"]["candidate_validity_rate"]["value"] == 1.0
    assert result.value["metrics"]["selected_mechanism_validity"]["value"] == 1.0


async def test_literature_supported_without_source_ids_fails():
    produced = _produced(basis="literature_supported", source_ids=None, support_ids=[])
    result = await MechanismEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "KNOWLEDGE BASIS MISMATCHES" in result.explanation
    assert result.value["metrics"]["knowledge_basis_accuracy"]["value"] == 0.0


async def test_new_hypothesis_with_source_ids_fails():
    produced = _produced(basis="new_hypothesis", source_ids=["s1"], support_ids=[])
    result = await MechanismEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "KNOWLEDGE BASIS MISMATCHES" in result.explanation


async def test_unsupported_literature_support_fails():
    produced = _produced(source_ids=["s1"], support_ids=["evidence-ghost"])
    result = await MechanismEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "GROUNDING MISMATCHES" in result.explanation
    assert result.value["grounding_mismatches"]


async def test_gap_trace_mismatch_fails():
    stmt = _statement_env("s1")
    ev = _evidence_env("ev-1")
    gap = _gap_env("g1", selection_id="sel-1")
    cand = _candidate_env("c1", "Fee-channel mechanism", gap_id="other-gap")
    sel = _selected_env("sm1", "c1", "Fee-channel mechanism")
    produced = [stmt, ev, gap, cand, sel, _selection_env(), _execution_env()]
    result = await MechanismEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "GAP TRACE MISMATCHES" in result.explanation
    assert result.value["metrics"]["gap_alignment_accuracy"]["value"] == 0.0


async def test_missing_assumptions_fails():
    produced = _produced(source_ids=["s1"], support_ids=["s1"])
    cand_env = produced[3]
    cand = cand_env.parse_payload(MechanismCandidate)
    fixed = ArtifactEnvelope.create(
        payload=cand.model_copy(update={"key_assumptions": []}),
        artifact_type="mechanism_candidate",
        producer="test",
        artifact_id=cand_env.artifact_id,
    )
    produced[3] = fixed
    result = await MechanismEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "MODELING ASSUMPTIONS NOT EXPLICIT" in result.explanation


async def test_critic_issue_recall_and_revision():
    stmt = _statement_env("s1")
    ev = _evidence_env("ev-1")
    gap = _gap_env("g1", selection_id="sel-1")
    cand = _candidate_env("c1", "Fee-channel mechanism", source_ids=["s1"], support_ids=["s1"])
    critique = _critique_env("k1", "c1", "unclear_causal_direction", verdict="revise")
    sel = _selected_env("sm1", "c1", "Fee-channel mechanism", revised=True)
    produced = [stmt, ev, gap, cand, critique, sel, _selection_env(), _execution_env()]
    case = _case(
        _expected(
            expected_critic_issues={
                "Fee-channel mechanism": [
                    {"category": "unclear_causal_direction", "verdict": "revise"}
                ]
            },
            expected_revision={"Fee-channel mechanism": True},
        )
    )
    result = await MechanismEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["critic_issue_recall"]["value"] == 1.0
    assert result.value["metrics"]["revision_success_rate"]["value"] == 1.0


async def test_missing_critic_issue_fails():
    stmt = _statement_env("s1")
    ev = _evidence_env("ev-1")
    gap = _gap_env("g1", selection_id="sel-1")
    cand = _candidate_env("c1", "Fee-channel mechanism", source_ids=["s1"], support_ids=["s1"])
    critique = _critique_env("k1", "c1", "logical_inconsistency", verdict="revise")
    sel = _selected_env("sm1", "c1", "Fee-channel mechanism", revised=True)
    produced = [stmt, ev, gap, cand, critique, sel, _selection_env(), _execution_env()]
    case = _case(
        _expected(
            expected_critic_issues={
                "Fee-channel mechanism": [
                    {"category": "missing_actor_or_incentive", "verdict": "revise"}
                ]
            },
            expected_revision={"Fee-channel mechanism": True},
        )
    )
    result = await MechanismEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.failed
    assert "CRITIC ISSUES MISSING" in result.explanation
    assert result.value["metrics"]["critic_issue_recall"]["value"] == 0.0


async def test_revision_mismatch_fails():
    stmt = _statement_env("s1")
    ev = _evidence_env("ev-1")
    gap = _gap_env("g1", selection_id="sel-1")
    cand = _candidate_env("c1", "Fee-channel mechanism", source_ids=["s1"], support_ids=["s1"])
    sel = _selected_env("sm1", "c1", "Fee-channel mechanism", revised=False)
    produced = [stmt, ev, gap, cand, sel, _selection_env(), _execution_env()]
    case = _case(_expected(expected_revision={"Fee-channel mechanism": True}))
    result = await MechanismEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.failed
    assert "REVISION MISMATCHES" in result.explanation
    assert result.value["metrics"]["revision_success_rate"]["value"] == 0.0


async def test_invalid_candidate_rejection_mismatch_fails():
    stmt = _statement_env("s1")
    ev = _evidence_env("ev-1")
    gap = _gap_env("g1", selection_id="sel-1")
    cand = _candidate_env("c1", "Fee-channel mechanism", source_ids=["s1"], support_ids=["s1"])
    sel = _selected_env("sm1", "c1", "Fee-channel mechanism")
    produced = [stmt, ev, gap, cand, sel, _selection_env(), _execution_env(candidates_rejected=2)]
    case = _case(_expected(expected_invalid_candidates=1))
    result = await MechanismEvaluator().evaluate(_ctx(case, produced))
    assert result.status == EvaluatorStatus.failed
    assert "INVALID CANDIDATE REJECTION" in result.explanation


async def test_missing_expected_candidate_fails():
    stmt = _statement_env("s1")
    ev = _evidence_env("ev-1")
    gap = _gap_env("g1", selection_id="sel-1")
    produced = [stmt, ev, gap, _execution_env()]
    result = await MechanismEvaluator().evaluate(_ctx(_case(_expected()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "CANDIDATES MISSING" in result.explanation


async def test_no_execution_produced():
    result = await MechanismEvaluator().evaluate(_ctx(_case(_expected()), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None
