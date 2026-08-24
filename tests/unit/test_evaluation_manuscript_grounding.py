"""Phase 6G unit tests — manuscript-grounding evaluator.

Covers: claim grounding, hallucinated citations, failed-proposition
presentation, condition preservation, novelty, critique coverage, and
revision success/reuse.
"""

from __future__ import annotations

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_manuscript_grounding.plugin import (
    ManuscriptGroundingEvaluator,
)
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus
from research_harness.research.schemas.manuscript import (
    ManuscriptCritique,
    ManuscriptCritiqueCategory,
    ManuscriptCritiqueIssue,
    ManuscriptCritiqueVerdict,
    ManuscriptDraft,
    ManuscriptDraftStatus,
    ManuscriptSection,
    ManuscriptSectionId,
)
from research_harness.research.schemas.results import ResearchResultsPackage


def _outline_env(
    allowed: list[str] | None = None,
) -> ArtifactEnvelope:
    from research_harness.research.schemas.manuscript import (
        ManuscriptOutline,
        SectionArtifactType,
        SectionSpec,
    )

    allowed = allowed or [
        "evidence_item",
        "verified_proposition",
        "research_gap",
        "contribution_claim",
        "research_finding",
        "numerical_result",
        "synthesis_statement",
    ]
    return ArtifactEnvelope.create(
        payload=ManuscriptOutline(
            results_package_id="pkg-1",
            title="Fixture manuscript",
            section_specs=[
                SectionSpec(
                    section_id=ManuscriptSectionId.propositions,
                    title="Propositions",
                    description="d",
                    allowed_artifact_types=[SectionArtifactType(v) for v in allowed],
                    artifact_ids=[],
                )
            ],
        ),
        artifact_type="manuscript_outline",
        producer="test",
    )


def _package_env() -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=ResearchResultsPackage(
            gap_id="gap-1",
            selected_mechanism_id="mech-1",
            model_id="m1",
            equilibrium_analysis_id="eq-1",
            equilibrium_candidate_id="cand-1",
            finding_ids=[],
            contribution_claim_ids=[],
            implication_ids=[],
            limitations=[],
            metadata={"robustness_ids": []},
        ),
        artifact_type="results_package",
        producer="test",
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


def _evidence_env(eid: str) -> ArtifactEnvelope:
    from research_harness.research.schemas.evidence import EvidenceItem

    return ArtifactEnvelope.create(
        payload=EvidenceItem(statement="fixture evidence", source_artifact_id="doc-1"),
        artifact_type="evidence_item",
        producer="test",
        artifact_id=eid,
    )


def _paper_env(pid: str) -> ArtifactEnvelope:
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod

    return ArtifactEnvelope.create(
        payload=PaperIdentity(
            member_paper_artifact_ids=["record-1"],
            resolution_method=ResolutionMethod.exact_identifier,
        ),
        artifact_type="paper_identity",
        producer="test",
        artifact_id=pid,
    )


def _ms_section(
    sid: str,
    *,
    claims: list[dict] | None = None,
    citations: list[dict] | None = None,
    body: str = "fixture body",
) -> ArtifactEnvelope:
    from research_harness.research.schemas.manuscript import (
        CitationReference,
        ManuscriptClaim,
    )

    return ArtifactEnvelope.create(
        payload=ManuscriptSection(
            outline_id="outline-1",
            section_id=ManuscriptSectionId.propositions,
            title="Propositions",
            body=body,
            claims=[
                ManuscriptClaim(
                    text=c["text"],
                    grounding_type=c.get("grounding_type"),
                    grounding_artifact_id=c.get("grounding_artifact_id"),
                    citation_id=c.get("citation_id"),
                    conditions=c.get("conditions") or [],
                )
                for c in (claims or [])
            ],
            citations=[
                CitationReference(
                    citation_id=ci["citation_id"],
                    paper_identity_id=ci["paper_identity_id"],
                    evidence_item_id=ci["evidence_item_id"],
                )
                for ci in (citations or [])
            ],
        ),
        artifact_type="manuscript_section",
        producer="test",
        artifact_id=sid,
    )


def _draft_env(
    did: str, section_ids: list[str], *, supersedes: str | None = None
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=ManuscriptDraft(
            outline_id="outline-1",
            results_package_id="pkg-1",
            title="Fixture manuscript",
            version=2 if supersedes else 1,
            section_ids=section_ids,
            status=ManuscriptDraftStatus.revised if supersedes else ManuscriptDraftStatus.drafted,
            supersedes=supersedes,
        ),
        artifact_type="manuscript_draft",
        producer="test",
        artifact_id=did,
    )


def _ms_critique_env(location: str, category: str = "citation_gap") -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=ManuscriptCritique(
            draft_id="d-1",
            issues=[
                ManuscriptCritiqueIssue(
                    category=ManuscriptCritiqueCategory(category),
                    description="fixture issue",
                    severity="high",
                    location=location,
                )
            ],
            overall_assessment="fixture",
            verdict=ManuscriptCritiqueVerdict.revise,
        ),
        artifact_type="manuscript_critique",
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
        evaluation_dimensions=["manuscript"],
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


def _base_produced(section: ArtifactEnvelope) -> list:
    return [
        _outline_env(),
        _package_env(),
        _prop_env("p-1"),
        _verification_env("p-1", "verified"),
        _evidence_env("ev-1"),
        _paper_env("paper-1"),
        section,
    ]


def _base_reference(**overrides) -> dict:
    ref = {
        "expected_sections": ["propositions"],
        "expected_critique_categories": [],
        "expected_revision": False,
        "expected_novelty_normalized": 0,
    }
    ref.update(overrides)
    return ref


async def test_grounded_proposition_claim_passes():
    section = _ms_section(
        "s-1",
        claims=[
            {
                "text": "q1 increases in a",
                "grounding_type": "verified_proposition",
                "grounding_artifact_id": "p-1",
            }
        ],
    )
    result = await ManuscriptGroundingEvaluator().evaluate(
        _ctx(_case(_base_reference()), _base_produced(section))
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["claim_grounding_accuracy"]["value"] == 1.0
    assert result.value["metrics"]["mathematical_claim_accuracy"]["value"] == 1.0


async def test_unsupported_literature_claim_fails():
    section = _ms_section(
        "s-1",
        claims=[
            {
                "text": "an ungrounded claim",
                "grounding_type": None,
                "grounding_artifact_id": None,
                "citation_id": None,
            }
        ],
    )
    result = await ManuscriptGroundingEvaluator().evaluate(
        _ctx(_case(_base_reference()), _base_produced(section))
    )
    assert result.status == EvaluatorStatus.failed
    assert "UNSUPPORTED LITERATURE CLAIM" in result.explanation
    assert result.value["metrics"]["unsupported_claim_rate"]["value"] == 1.0


async def test_hallucinated_citation_reference_fails():
    section = _ms_section(
        "s-1",
        body="evidence claim [CITE:lit-1]",
        claims=[
            {
                "text": "an evidence claim",
                "grounding_type": "evidence_item",
                "grounding_artifact_id": "ev-1",
                "citation_id": "lit-1",
            }
        ],
        citations=[
            {
                "citation_id": "lit-1",
                "paper_identity_id": "paper-1",
                "evidence_item_id": "ghost-evidence",
            }
        ],
    )
    result = await ManuscriptGroundingEvaluator().evaluate(
        _ctx(_case(_base_reference()), _base_produced(section))
    )
    assert result.status == EvaluatorStatus.failed
    assert "HALLUCINATED CITATION" in result.explanation
    assert result.value["metrics"]["citation_reference_accuracy"]["value"] == 0.0


async def test_literature_claim_with_citation_passes():
    section = _ms_section(
        "s-1",
        body="evidence claim [CITE:lit-1]",
        claims=[
            {
                "text": "an evidence claim",
                "grounding_type": "evidence_item",
                "grounding_artifact_id": "ev-1",
                "citation_id": "lit-1",
            }
        ],
        citations=[
            {
                "citation_id": "lit-1",
                "paper_identity_id": "paper-1",
                "evidence_item_id": "ev-1",
            }
        ],
    )
    result = await ManuscriptGroundingEvaluator().evaluate(
        _ctx(_case(_base_reference()), _base_produced(section))
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["literature_citation_coverage"]["value"] == 1.0


async def test_failed_proposition_presented_fails():
    section = _ms_section(
        "s-1",
        claims=[
            {
                "text": "q1 increases in a",
                "grounding_type": "verified_proposition",
                "grounding_artifact_id": "p-1",
            }
        ],
    )
    produced = [
        _outline_env(),
        _package_env(),
        _prop_env("p-1", verification="failed"),
        _verification_env("p-1", "failed"),
        section,
    ]
    result = await ManuscriptGroundingEvaluator().evaluate(_ctx(_case(_base_reference()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "FAILED PROPOSITION presented as verified" in result.explanation
    assert result.value["metrics"]["section_consistency_accuracy"]["value"] == 0.0


async def test_conditions_dropped_fails():
    section = _ms_section(
        "s-1",
        claims=[
            {
                "text": "q increases in a",
                "grounding_type": "verified_proposition",
                "grounding_artifact_id": "p-1",
                "conditions": [],
            }
        ],
    )
    produced = [
        _outline_env(),
        _package_env(),
        _prop_env("p-1", conditions=["b > 0"]),
        _verification_env("p-1", "verified"),
        section,
    ]
    result = await ManuscriptGroundingEvaluator().evaluate(_ctx(_case(_base_reference()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "drops proposition conditions" in result.explanation
    assert result.value["metrics"]["condition_preservation_accuracy"]["value"] == 0.0


async def test_conditions_preserved_passes():
    section = _ms_section(
        "s-1",
        claims=[
            {
                "text": "q increases in a",
                "grounding_type": "verified_proposition",
                "grounding_artifact_id": "p-1",
                "conditions": ["b > 0"],
            }
        ],
    )
    produced = [
        _outline_env(),
        _package_env(),
        _prop_env("p-1", conditions=["b > 0"]),
        _verification_env("p-1", "verified"),
        section,
    ]
    result = await ManuscriptGroundingEvaluator().evaluate(_ctx(_case(_base_reference()), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["condition_preservation_accuracy"]["value"] == 1.0


async def test_novelty_overclaim_in_body_fails():
    section = _ms_section(
        "s-1",
        body="This is the first study of Cournot competition.",
        claims=[
            {
                "text": "q1 increases in a",
                "grounding_type": "verified_proposition",
                "grounding_artifact_id": "p-1",
            }
        ],
    )
    result = await ManuscriptGroundingEvaluator().evaluate(
        _ctx(_case(_base_reference()), _base_produced(section))
    )
    assert result.status == EvaluatorStatus.failed
    assert "NOVELTY" in result.explanation
    assert result.value["metrics"]["novelty_claim_accuracy"]["value"] == 0.0


async def test_grounding_type_not_allowed_fails():
    section = _ms_section(
        "s-1",
        claims=[
            {
                "text": "an evidence claim",
                "grounding_type": "evidence_item",
                "grounding_artifact_id": "ev-1",
            }
        ],
    )
    produced = [
        _outline_env(allowed=["verified_proposition"]),
        _package_env(),
        _evidence_env("ev-1"),
        section,
    ]
    result = await ManuscriptGroundingEvaluator().evaluate(_ctx(_case(_base_reference()), produced))
    assert result.status == EvaluatorStatus.failed
    assert "not allowed in" in result.explanation


async def test_expected_critique_category_missing_fails():
    section = _ms_section(
        "s-1",
        claims=[
            {
                "text": "q1 increases in a",
                "grounding_type": "verified_proposition",
                "grounding_artifact_id": "p-1",
            }
        ],
    )
    produced = _base_produced(section) + [_ms_critique_env("propositions", "overclaiming")]
    result = await ManuscriptGroundingEvaluator().evaluate(
        _ctx(
            _case(_base_reference(expected_critique_categories=["missing_limitations"])),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "CRITIQUE MISSING expected issue" in result.explanation
    assert result.value["metrics"]["critique_issue_recall"]["value"] == 0.0


async def test_missing_expected_sections_fails():
    section = _ms_section(
        "s-1",
        claims=[
            {
                "text": "q1 increases in a",
                "grounding_type": "verified_proposition",
                "grounding_artifact_id": "p-1",
            }
        ],
    )
    result = await ManuscriptGroundingEvaluator().evaluate(
        _ctx(
            _case(_base_reference(expected_sections=["introduction", "propositions"])),
            _base_produced(section),
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "MISSING SECTIONS" in result.explanation


async def test_revision_success_passes():
    section_lit_v1 = _ms_section(
        "s-1",
        body="evidence claim",
        claims=[
            {
                "text": "an evidence claim",
                "grounding_type": "evidence_item",
                "grounding_artifact_id": "ev-1",
                "citation_id": "lit-1",
            }
        ],
        citations=[
            {
                "citation_id": "lit-1",
                "paper_identity_id": "paper-1",
                "evidence_item_id": "ev-1",
            }
        ],
    )
    section_prop_v1 = _ms_section(
        "s-2",
        claims=[
            {
                "text": "q1 increases in a",
                "grounding_type": "verified_proposition",
                "grounding_artifact_id": "p-1",
            }
        ],
    )
    section_lit_v2 = _ms_section(
        "s-3",
        body="evidence claim [CITE:lit-1]",
        claims=[
            {
                "text": "an evidence claim",
                "grounding_type": "evidence_item",
                "grounding_artifact_id": "ev-1",
                "citation_id": "lit-1",
            }
        ],
        citations=[
            {
                "citation_id": "lit-1",
                "paper_identity_id": "paper-1",
                "evidence_item_id": "ev-1",
            }
        ],
    )
    produced = [
        _outline_env(),
        _package_env(),
        _prop_env("p-1"),
        _verification_env("p-1", "verified"),
        _evidence_env("ev-1"),
        _paper_env("paper-1"),
        section_lit_v1,
        section_prop_v1,
        section_lit_v2,
        _draft_env("d-1", ["s-1", "s-2"]),
        _draft_env("d-2", ["s-3", "s-2"], supersedes="d-1"),
        _ms_critique_env("propositions", "citation_gap"),
    ]
    result = await ManuscriptGroundingEvaluator().evaluate(
        _ctx(
            _case(
                _base_reference(
                    expected_revision=True,
                    expected_critique_categories=["citation_gap"],
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["revision_success_rate"]["value"] == 1.0


async def test_revision_without_superseding_draft_fails():
    section_v1 = _ms_section(
        "s-1",
        claims=[
            {
                "text": "q1 increases in a",
                "grounding_type": "verified_proposition",
                "grounding_artifact_id": "p-1",
            }
        ],
    )
    produced = _base_produced(section_v1) + [
        _draft_env("d-1", ["s-1"]),
        _ms_critique_env("propositions", "citation_gap"),
    ]
    result = await ManuscriptGroundingEvaluator().evaluate(
        _ctx(_case(_base_reference(expected_revision=True)), produced)
    )
    assert result.status == EvaluatorStatus.failed
    assert "REVISION FAILED" in result.explanation


async def test_revision_flagged_section_not_redrafted_fails():
    section_v1 = _ms_section(
        "s-1",
        body="evidence claim",
        claims=[
            {
                "text": "an evidence claim",
                "grounding_type": "evidence_item",
                "grounding_artifact_id": "ev-1",
                "citation_id": "lit-1",
            }
        ],
        citations=[
            {
                "citation_id": "lit-1",
                "paper_identity_id": "paper-1",
                "evidence_item_id": "ev-1",
            }
        ],
    )
    section_v2 = _ms_section(
        "s-2",
        body="evidence claim",
        claims=[
            {
                "text": "an evidence claim",
                "grounding_type": "evidence_item",
                "grounding_artifact_id": "ev-1",
                "citation_id": "lit-1",
            }
        ],
        citations=[
            {
                "citation_id": "lit-1",
                "paper_identity_id": "paper-1",
                "evidence_item_id": "ev-1",
            }
        ],
    )
    produced = [
        _outline_env(),
        _package_env(),
        _prop_env("p-1"),
        _verification_env("p-1", "verified"),
        _evidence_env("ev-1"),
        _paper_env("paper-1"),
        section_v1,
        section_v2,
        _draft_env("d-1", ["s-1"]),
        _draft_env("d-2", ["s-1", "s-2"], supersedes="d-1"),
        _ms_critique_env("propositions", "citation_gap"),
    ]
    # s-2 is a new section but still declares an unused citation -> not repaired
    result = await ManuscriptGroundingEvaluator().evaluate(
        _ctx(
            _case(
                _base_reference(
                    expected_revision=True,
                    expected_critique_categories=["citation_gap"],
                )
            ),
            produced,
        )
    )
    assert result.status == EvaluatorStatus.failed
    assert "REVISION FAILED TO REPAIR" in result.explanation


async def test_no_sections_produced():
    result = await ManuscriptGroundingEvaluator().evaluate(_ctx(_case({}), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None
