"""Phase 6G unit tests — results-assembly evaluator.

Covers: finding grounding, failed-proposition support, condition
preservation, contribution gap alignment, implication grounding, novelty
handling, contradiction detection, and unsupported-claim rate.
"""

from __future__ import annotations

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_results_grounding.plugin import (
    ResultsGroundingEvaluator,
)
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus
from research_harness.research.schemas.results import (
    ContributionClaim,
    ResearchFinding,
    ResearchImplication,
    ResearchResultsPackage,
    ResultsCritique,
    ResultsCritiqueCategory,
    ResultsCritiqueIssue,
    ResultsCritiqueVerdict,
)


def _package_env(
    *,
    finding_ids: list[str] | None = None,
    contribution_ids: list[str] | None = None,
    implication_ids: list[str] | None = None,
    gap_id: str = "gap-1",
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=ResearchResultsPackage(
            gap_id=gap_id,
            selected_mechanism_id="mech-1",
            model_id="m1",
            equilibrium_analysis_id="eq-1",
            equilibrium_candidate_id="cand-1",
            finding_ids=finding_ids or [],
            contribution_claim_ids=contribution_ids or [],
            implication_ids=implication_ids or [],
            limitations=[],
        ),
        artifact_type="results_package",
        producer="test",
    )


def _finding_env(
    fid: str,
    *,
    prop_ids: list[str] | None = None,
    static_ids: list[str] | None = None,
    result_ids: list[str] | None = None,
    conditions: list[str] | None = None,
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=ResearchFinding(
            model_id="m1",
            equilibrium_candidate_id="cand-1",
            statement="fixture finding",
            supporting_proposition_ids=prop_ids or [],
            supporting_comparative_static_ids=static_ids or [],
            supporting_numerical_result_ids=result_ids or [],
            conditions=conditions or [],
        ),
        artifact_type="research_finding",
        producer="test",
        artifact_id=fid,
    )


def _prop_env(
    pid: str,
    verification: str = "verified",
    conditions: list[str] | None = None,
) -> ArtifactEnvelope:
    from research_harness.research.schemas.proposition import (
        Proposition,
        PropositionStatus,
    )

    return ArtifactEnvelope.create(
        payload=Proposition(
            model_id="m1",
            equilibrium_candidate_id="cand-1",
            comparative_statics_analysis_id="cs-1",
            statement="fixture proposition",
            conditions=conditions or [],
            status=(
                PropositionStatus.failed if verification == "failed" else PropositionStatus.verified
            ),
            proposed_by="llm",
        ),
        artifact_type="proposition",
        producer="test",
        artifact_id=pid,
    )


def _verification_env(pid: str, verification: str) -> ArtifactEnvelope:
    from research_harness.research.schemas.proposition import (
        PropositionVerification,
        PropositionVerificationStatus,
    )

    return ArtifactEnvelope.create(
        payload=PropositionVerification(
            proposition_id=pid,
            model_id="m1",
            status=PropositionVerificationStatus(verification),
            checks=[],
        ),
        artifact_type="proposition_verification",
        producer="test",
    )


def _contribution_env(
    cid: str,
    *,
    gap_id: str = "gap-1",
    finding_ids: list[str] | None = None,
    claim: str = "fixture contribution",
    novelty_claim: str | None = None,
    novelty_normalized: bool = False,
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=ContributionClaim(
            gap_id=gap_id,
            finding_ids=finding_ids or [],
            claim=claim,
            novelty_claim=novelty_claim,
            novelty_normalized=novelty_normalized,
        ),
        artifact_type="contribution_claim",
        producer="test",
        artifact_id=cid,
    )


def _implication_env(
    iid: str,
    *,
    claim_type: str = "interpretation",
    finding_ids: list[str] | None = None,
) -> ArtifactEnvelope:
    from research_harness.research.schemas.results import ImplicationClaimType, ImplicationKind

    return ArtifactEnvelope.create(
        payload=ResearchImplication(
            implication_kind=ImplicationKind.theory,
            claim_type=ImplicationClaimType(claim_type),
            text="fixture implication",
            grounded_in_finding_ids=finding_ids or [],
        ),
        artifact_type="research_implication",
        producer="test",
        artifact_id=iid,
    )


def _critique_env(*categories: str) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=ResultsCritique(
            package_id="pkg-1",
            issues=[
                ResultsCritiqueIssue(
                    category=ResultsCritiqueCategory(c),
                    description="fixture issue",
                    severity="medium",
                )
                for c in categories
            ],
            overall_assessment="fixture",
            verdict=ResultsCritiqueVerdict.revise,
        ),
        artifact_type="results_critique",
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
        evaluation_dimensions=["results"],
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


def _base_produced(
    *,
    verification: str = "verified",
    prop_conditions: list[str] | None = None,
) -> list:
    finding = _finding_env("f-1", prop_ids=["p-1"])
    contribution = _contribution_env("c-1", finding_ids=["f-1"])
    implication = _implication_env("i-1", finding_ids=["f-1"])
    return [
        _package_env(finding_ids=["f-1"], contribution_ids=["c-1"], implication_ids=["i-1"]),
        _prop_env("p-1", verification=verification, conditions=prop_conditions),
        _verification_env("p-1", verification),
        finding,
        contribution,
        implication,
    ]


def _base_reference(**overrides) -> dict:
    ref = {
        "expected_novelty_normalized": 0,
        "expected_critique_categories": [],
        "expected_unsupported": 0,
    }
    ref.update(overrides)
    return ref


async def test_grounded_finding_passes():
    result = await ResultsGroundingEvaluator().evaluate(
        _ctx(_case(_base_reference()), _base_produced())
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["finding_grounding_accuracy"]["value"] == 1.0


async def test_unsupported_finding_persisted_fails():
    produced = [
        _package_env(finding_ids=["f-1"]),
        _finding_env("f-1", prop_ids=["ghost-prop"]),
    ]
    result = await ResultsGroundingEvaluator().evaluate(_ctx(_case(_base_reference()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "UNSUPPORTED FINDING" in result.explanation
    assert result.value["metrics"]["finding_grounding_accuracy"]["value"] == 0.0
    assert result.value["metrics"]["unsupported_claim_rate"]["value"] == 1.0


async def test_failed_proposition_as_support_fails():
    produced = _base_produced(verification="failed")
    result = await ResultsGroundingEvaluator().evaluate(_ctx(_case(_base_reference()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "failed proposition" in result.explanation
    assert result.value["metrics"]["proposition_support_accuracy"]["value"] == 0.0


async def test_conditions_dropped_fails():
    produced = _base_produced(prop_conditions=["b > 0"])
    result = await ResultsGroundingEvaluator().evaluate(_ctx(_case(_base_reference()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "CONDITIONS DROPPED" in result.explanation
    assert result.value["metrics"]["condition_preservation_accuracy"]["value"] == 0.0


async def test_conditions_preserved_passes():
    produced = _base_produced(prop_conditions=["b > 0"])
    finding = _finding_env("f-1", prop_ids=["p-1"], conditions=["b > 0"])
    produced = [e for e in produced if e.artifact_type != "research_finding"] + [finding]
    result = await ResultsGroundingEvaluator().evaluate(_ctx(_case(_base_reference()), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["condition_preservation_accuracy"]["value"] == 1.0


async def test_contribution_gap_misalignment_fails():
    produced = _base_produced()
    produced = [e for e in produced if e.artifact_type != "contribution_claim"] + [
        _contribution_env("c-1", gap_id="other-gap", finding_ids=["f-1"])
    ]
    result = await ResultsGroundingEvaluator().evaluate(_ctx(_case(_base_reference()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "CONTRIBUTION without valid gap/finding support" in result.explanation
    assert result.value["metrics"]["contribution_gap_alignment_accuracy"]["value"] == 0.0


async def test_contribution_without_findings_fails():
    produced = _base_produced()
    produced = [e for e in produced if e.artifact_type != "contribution_claim"] + [
        _contribution_env("c-1", finding_ids=[])
    ]
    result = await ResultsGroundingEvaluator().evaluate(_ctx(_case(_base_reference()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "CONTRIBUTION without valid gap/finding support" in result.explanation


async def test_unsupported_implication_fails():
    produced = _base_produced()
    produced = [e for e in produced if e.artifact_type != "research_implication"] + [
        _implication_env("i-1", claim_type="managerial_implication")
    ]
    result = await ResultsGroundingEvaluator().evaluate(_ctx(_case(_base_reference()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "UNSUPPORTED IMPLICATION" in result.explanation
    assert result.value["metrics"]["implication_grounding_accuracy"]["value"] == 0.0


async def test_novelty_overclaim_persisted_fails():
    produced = _base_produced()
    produced = [e for e in produced if e.artifact_type != "contribution_claim"] + [
        _contribution_env(
            "c-1",
            finding_ids=["f-1"],
            claim="This is the first study of platform pricing.",
        )
    ]
    result = await ResultsGroundingEvaluator().evaluate(_ctx(_case(_base_reference()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "NOVELTY OVERCLAIM" in result.explanation
    assert result.value["metrics"]["novelty_claim_accuracy"]["value"] == 0.0


async def test_novelty_normalized_passes():
    produced = _base_produced()
    produced = [e for e in produced if e.artifact_type != "contribution_claim"] + [
        _contribution_env(
            "c-1",
            finding_ids=["f-1"],
            claim="We study platform pricing explicitly.",
            novelty_normalized=True,
        )
    ]
    result = await ResultsGroundingEvaluator().evaluate(
        _ctx(_case(_base_reference(expected_novelty_normalized=1)), produced)
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["novelty_claim_accuracy"]["value"] == 1.0


async def test_contradiction_hidden_fails():
    produced = _base_produced() + [_critique_env()]
    result = await ResultsGroundingEvaluator().evaluate(
        _ctx(
            _case(
                _base_reference(expected_critique_categories=["symbolic_numerical_contradiction"])
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "SYMBOLIC/NUMERICAL CONTRADICTION hidden" in result.explanation
    assert result.value["metrics"]["contradiction_detection_accuracy"]["value"] == 0.0


async def test_contradiction_surfaced_passes():
    produced = _base_produced() + [_critique_env("symbolic_numerical_contradiction")]
    result = await ResultsGroundingEvaluator().evaluate(
        _ctx(
            _case(
                _base_reference(expected_critique_categories=["symbolic_numerical_contradiction"])
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["contradiction_detection_accuracy"]["value"] == 1.0


async def test_expected_critique_category_missing_fails():
    produced = _base_produced() + [_critique_env("weak_gap_link")]
    result = await ResultsGroundingEvaluator().evaluate(
        _ctx(
            _case(_base_reference(expected_critique_categories=["causal_overstatement"])),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "CRITIQUE MISSING expected issue" in result.explanation


async def test_no_package_produced():
    result = await ResultsGroundingEvaluator().evaluate(_ctx(_case({}), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None
