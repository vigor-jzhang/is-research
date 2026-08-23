"""Phase 4B unit tests — manuscript outline, section-by-section drafting,
claim/citation validation, critic, immutable revision (offline, fake model).

Covers: outline generation; section-by-section drafting; unsupported claim
rejection; missing citation rejection; condition preservation; failed
proposition exclusion; novelty normalization; citation provenance; critic
output; immutable revision; idempotency; provenance after reopen.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.blob import BlobReference
from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.equilibrium import (
    EquilibriumAnalysis,
    EquilibriumCandidate,
    EquilibriumExpression,
)
from research_harness.research.schemas.evidence import EvidenceItem
from research_harness.research.schemas.full_text import FullTextDocument, TextStatus
from research_harness.research.schemas.gap import GapType, ResearchGap
from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
from research_harness.research.schemas.mechanism import SelectedMechanism
from research_harness.research.schemas.model import (
    Expression,
    FormalAnalyticalModel,
    InformationStructure,
    ModelActor,
    ModelParameter,
    ModelTimingStage,
    ModelVariable,
    PayoffFunction,
    SymbolKind,
)
from research_harness.research.schemas.numerical import NumericalExperiment, NumericalResult
from research_harness.research.schemas.proposition import (
    Proposition,
    PropositionClaimType,
    PropositionVerification,
    PropositionVerificationStatus,
)
from research_harness.research.schemas.results import (
    ContributionClaim,
    ResearchFinding,
    ResearchImplication,
    ResearchResultsPackage,
)
from research_harness.research.schemas.synthesis import SynthesisStatement, SynthesisStatementType


class KeyedRouter:
    """Returns responses keyed by section title / command seen in the prompt."""

    def __init__(self, builders: dict[str, dict], default: dict | None = None):
        self.builders = builders
        self.default = default or {
            "title": "Section",
            "body": "Default text.",
            "claims": [],
            "citations": [],
        }
        self.calls: list[str] = []

    async def complete(self, role, request):
        content = request.messages[-1].content
        self.calls.append(content[:120])
        for key, resp in self.builders.items():
            if key in content:
                return ModelResponse(
                    message=Message(role="assistant", content=json.dumps(resp)),
                    tool_calls=[],
                    finish_reason="stop",
                    model="fake",
                )
        return ModelResponse(
            message=Message(role="assistant", content=json.dumps(self.default)),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


async def build_chain(store: SQLiteArtifactStore) -> dict[str, str]:
    """Persist a full Phase 2+3+4A chain needed by manuscript drafting."""
    ids: dict[str, str] = {}

    identity = PaperIdentity(
        member_paper_artifact_ids=["paper1"],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.manual,
        resolution_evidence=[],
    )
    id_env = ArtifactEnvelope.create(
        payload=identity, artifact_type="paper_identity", producer="test"
    )
    await store.put(id_env)
    ids["identity"] = id_env.artifact_id

    doc = FullTextDocument(
        paper_identity_id=ids["identity"],
        document_acquisition_id="acq1",
        source_blob=BlobReference(digest="aa", size_bytes=1, storage_key="aa/aa"),
        extractor="test",
        page_count=10,
        pages_with_text=10,
        character_count=1000,
        text_status=TextStatus.extracted,
    )
    d_env = ArtifactEnvelope.create(
        payload=doc, artifact_type="full_text_document", producer="test"
    )
    await store.put(d_env)
    ids["document"] = d_env.artifact_id
    await store.add_provenance(
        ProvenanceLink(
            relation=ProvenanceRelation.derived_from,
            source_artifact_id=ids["identity"],
            target_artifact_id=ids["document"],
            producer="test",
        )
    )

    ev = EvidenceItem(
        statement="Platform competition follows Cournot patterns.",
        source_artifact_id=ids["document"],
    )
    e_env = ArtifactEnvelope.create(payload=ev, artifact_type="evidence_item", producer="test")
    await store.put(e_env)
    ids["evidence"] = e_env.artifact_id
    await store.add_provenance(
        ProvenanceLink(
            relation=ProvenanceRelation.derived_from,
            source_artifact_id=ids["document"],
            target_artifact_id=ids["evidence"],
            producer="test",
        )
    )

    syn = SynthesisStatement(
        statement="The reviewed corpus documents platform quantity competition.",
        type=SynthesisStatementType.consensus,
        supporting_evidence_ids=[ids["evidence"]],
        supporting_paper_identity_ids=[ids["identity"]],
        papers_supporting=1,
        evidence_items_supporting=1,
    )
    s_env = ArtifactEnvelope.create(
        payload=syn, artifact_type="synthesis_statement", producer="test"
    )
    await store.put(s_env)
    ids["synthesis"] = s_env.artifact_id

    gap = ResearchGap(
        title="Demand-driven platform quantity dynamics",
        gap_type=GapType.theoretical_gap,
        description="within the reviewed corpus, demand-driven quantity effects on platforms are unresolved",
        supporting_synthesis_statement_ids=[ids["synthesis"]],
        supporting_evidence_ids=[ids["evidence"]],
    )
    g_env = ArtifactEnvelope.create(
        payload=gap, artifact_type="research_gap", producer="test", artifact_id="gap1"
    )
    await store.put(g_env)
    ids["gap"] = "gap1"

    mech = SelectedMechanism(
        gap_id=ids["gap"],
        gap_selection_id="sel1",
        mechanism_candidate_id="cand-mech",
        name="Cournot competition",
        description="quantity competition",
        actors=["f1", "f2"],
        strategic_interactions=["quantity"],
        causal_logic="q",
        key_assumptions=[],
        expected_outcomes=[],
        boundary_conditions=[],
        grounding=[],
    )
    m_env = ArtifactEnvelope.create(
        payload=mech, artifact_type="selected_mechanism", producer="test"
    )
    await store.put(m_env)
    ids["mechanism"] = m_env.artifact_id

    model = FormalAnalyticalModel(
        selected_mechanism_id=ids["mechanism"],
        title="Cournot platforms",
        description="simultaneous quantities",
        actors=[
            ModelActor(actor_id="f1", name="Firm 1", strategic=True),
            ModelActor(actor_id="f2", name="Firm 2", strategic=True),
        ],
        variables=[
            ModelVariable(
                symbol="q1",
                name="q1",
                meaning="q1",
                domain="R_+",
                kind=SymbolKind.decision_variable,
                owner_actor_id="f1",
            ),
            ModelVariable(
                symbol="q2",
                name="q2",
                meaning="q2",
                domain="R_+",
                kind=SymbolKind.decision_variable,
                owner_actor_id="f2",
            ),
        ],
        parameters=[
            ModelParameter(symbol="a", name="demand", meaning="demand", domain="R_+"),
            ModelParameter(symbol="c", name="cost", meaning="cost", domain="R_+"),
        ],
        assumptions=[],
        timing=[
            ModelTimingStage(
                stage_number=0, name="move", description="both", actor_ids=["f1", "f2"]
            )
        ],
        information_structure=InformationStructure(),
        payoffs=[
            PayoffFunction(
                actor_id="f1",
                objective_type="profit",
                expression=Expression(expression="q1*(a - q1 - q2) - c*q1", symbols_used=[]),
                decision_variables=["q1"],
                parameters=["a", "c"],
            ),
            PayoffFunction(
                actor_id="f2",
                objective_type="profit",
                expression=Expression(expression="q2*(a - q1 - q2) - c*q2", symbols_used=[]),
                decision_variables=["q2"],
                parameters=["a", "c"],
            ),
        ],
    )
    mod_env = ArtifactEnvelope.create(
        payload=model, artifact_type="formal_analytical_model", producer="test"
    )
    await store.put(mod_env)
    ids["model"] = mod_env.artifact_id

    cand = EquilibriumCandidate(
        model_id=ids["model"],
        proposed_by="sympy",
        revision_round=0,
        solution_method="simultaneous",
        expressions=[
            EquilibriumExpression(
                variable="q1",
                expression=Expression(expression="a/3 - c/3", symbols_used=[]),
                conditions=[],
            ),
            EquilibriumExpression(
                variable="q2",
                expression=Expression(expression="a/3 - c/3", symbols_used=[]),
                conditions=[],
            ),
        ],
    )
    c_env = ArtifactEnvelope.create(
        payload=cand, artifact_type="equilibrium_candidate", producer="test"
    )
    await store.put(c_env)
    ids["candidate"] = c_env.artifact_id

    analysis = EquilibriumAnalysis(
        model_id=ids["model"],
        candidate_ids=[ids["candidate"]],
        selected_candidate_id=ids["candidate"],
        status="derived",
        solution_method="simultaneous",
    )
    a_env = ArtifactEnvelope.create(
        payload=analysis, artifact_type="equilibrium_analysis", producer="test"
    )
    await store.put(a_env)
    ids["equilibrium_analysis"] = a_env.artifact_id

    prop = Proposition(
        model_id=ids["model"],
        equilibrium_candidate_id=ids["candidate"],
        comparative_statics_analysis_id="cs-a",
        statement="Increasing demand a raises each platform's equilibrium quantity.",
        claim_type=PropositionClaimType.monotonicity,
        outcome_variable="q1",
        parameter="a",
        expected_sign="positive",
        conditions=["a > 0"],
        supporting_static_ids=[],
        status="candidate",
    )
    p_env = ArtifactEnvelope.create(payload=prop, artifact_type="proposition", producer="test")
    await store.put(p_env)
    ids["prop_ok"] = p_env.artifact_id
    v = PropositionVerification(
        proposition_id=ids["prop_ok"],
        model_id=ids["model"],
        status=PropositionVerificationStatus.verified,
        checks=[],
    )
    v_env = ArtifactEnvelope.create(
        payload=v, artifact_type="proposition_verification", producer="test"
    )
    await store.put(v_env)

    prop_failed = Proposition(
        model_id=ids["model"],
        equilibrium_candidate_id=ids["candidate"],
        comparative_statics_analysis_id="cs-a",
        statement="Failed claim.",
        claim_type=PropositionClaimType.monotonicity,
        outcome_variable="q1",
        parameter="c",
        expected_sign="negative",
        conditions=[],
        supporting_static_ids=[],
        status="candidate",
    )
    pf_env = ArtifactEnvelope.create(
        payload=prop_failed, artifact_type="proposition", producer="test"
    )
    await store.put(pf_env)
    ids["prop_failed"] = pf_env.artifact_id
    vf = PropositionVerification(
        proposition_id=ids["prop_failed"],
        model_id=ids["model"],
        status=PropositionVerificationStatus.failed,
        checks=[],
    )
    vf_env = ArtifactEnvelope.create(
        payload=vf, artifact_type="proposition_verification", producer="test"
    )
    await store.put(vf_env)

    result = NumericalResult(
        model_id=ids["model"],
        equilibrium_candidate_id=ids["candidate"],
        experiment_id="exp1",
        scenario="baseline",
        parameter_values={"a": 10.0, "c": 1.0},
        outcomes={"q1": 3.0, "q2": 3.0},
        feasible=True,
    )
    r_env = ArtifactEnvelope.create(
        payload=result, artifact_type="numerical_result", producer="test"
    )
    await store.put(r_env)
    ids["result"] = r_env.artifact_id

    exp = NumericalExperiment(
        model_id=ids["model"],
        equilibrium_candidate_id=ids["candidate"],
        sweeps=[],
        results=[ids["result"]],
        robustness=[],
        welfare=[],
        status="completed",
        summary="baseline only",
    )
    e_env = ArtifactEnvelope.create(
        payload=exp, artifact_type="numerical_experiment", producer="test"
    )
    await store.put(e_env)
    ids["experiment"] = e_env.artifact_id

    finding = ResearchFinding(
        model_id=ids["model"],
        equilibrium_candidate_id=ids["candidate"],
        statement="Larger markets raise equilibrium platform quantities.",
        finding_type="analytical_result",
        supporting_proposition_ids=[ids["prop_ok"]],
        supporting_comparative_static_ids=[],
        supporting_numerical_result_ids=[ids["result"]],
        conditions=["a > 0"],
        confidence="high",
        knowledge_basis="research_inference",
    )
    f_env = ArtifactEnvelope.create(
        payload=finding, artifact_type="research_finding", producer="test"
    )
    await store.put(f_env)
    ids["finding"] = f_env.artifact_id

    claim = ContributionClaim(
        gap_id=ids["gap"],
        finding_ids=[ids["finding"]],
        claim="Demand growth raises equilibrium platform quantities.",
        contribution_type="theoretical",
        advances_literature="within the reviewed corpus, this mechanism was unresolved",
    )
    cl_env = ArtifactEnvelope.create(
        payload=claim, artifact_type="contribution_claim", producer="test"
    )
    await store.put(cl_env)
    ids["contribution"] = cl_env.artifact_id

    impl = ResearchImplication(
        implication_kind="management",
        claim_type="managerial_implication",
        text="Platforms should scale capacity in growing markets.",
        grounded_in_finding_ids=[ids["finding"]],
    )
    i_env = ArtifactEnvelope.create(
        payload=impl, artifact_type="research_implication", producer="test"
    )
    await store.put(i_env)
    ids["implication"] = i_env.artifact_id

    pkg = ResearchResultsPackage(
        research_question_id="rq1",
        gap_id=ids["gap"],
        selected_mechanism_id=ids["mechanism"],
        model_id=ids["model"],
        equilibrium_analysis_id=ids["equilibrium_analysis"],
        equilibrium_candidate_id=ids["candidate"],
        numerical_experiment_id=ids["experiment"],
        finding_ids=[ids["finding"]],
        contribution_claim_ids=[ids["contribution"]],
        implication_ids=[ids["implication"]],
        limitations=["single-period setting"],
        status="assembled",
        summary="1 finding, 1 contribution, 1 implication",
    )
    pk_env = ArtifactEnvelope.create(payload=pkg, artifact_type="results_package", producer="test")
    await store.put(pk_env)
    ids["package"] = pk_env.artifact_id
    return ids


def section_response(ids: dict[str, str]) -> dict:
    """One valid section response per section id (keyed by prompt text)."""
    return {
        "Draft the 'Introduction' section": {
            "title": "Introduction",
            "body": "Platform competition is central to the studied markets. This manuscript studies demand-driven quantity effects.",
            "claims": [
                {
                    "text": "Demand growth raises equilibrium platform quantities.",
                    "grounding_type": "research_finding",
                    "grounding_artifact_id": ids["finding"],
                    "citation_id": None,
                    "conditions": ["a > 0"],
                }
            ],
            "citations": [],
        },
        "Draft the 'Literature Review' section": {
            "title": "Literature Review",
            "body": "Prior work documented platform competition patterns [CITE:c1].",
            "claims": [
                {
                    "text": "Prior work documented platform competition patterns.",
                    "grounding_type": "evidence_item",
                    "grounding_artifact_id": ids["evidence"],
                    "citation_id": "c1",
                    "conditions": [],
                }
            ],
            "citations": [
                {
                    "citation_id": "c1",
                    "paper_identity_id": ids["identity"],
                    "evidence_item_id": ids["evidence"],
                    "page_locator": "p. 12",
                    "claim_context": "platform competition patterns",
                }
            ],
        },
        "Draft the 'Research Gap' section": {
            "title": "Research Gap",
            "body": "The reviewed corpus leaves demand-driven quantity effects unresolved.",
            "claims": [
                {
                    "text": "Demand-driven quantity effects are unresolved in the reviewed corpus.",
                    "grounding_type": "research_gap",
                    "grounding_artifact_id": ids["gap"],
                    "citation_id": None,
                    "conditions": [],
                }
            ],
            "citations": [],
        },
        "Draft the 'Theory / Mechanism' section": {
            "title": "Theory / Mechanism",
            "body": "The proposed mechanism is Cournot quantity competition.",
            "claims": [
                {
                    "text": "The mechanism is Cournot quantity competition.",
                    "grounding_type": "selected_mechanism",
                    "grounding_artifact_id": ids["mechanism"],
                    "citation_id": None,
                    "conditions": [],
                }
            ],
            "citations": [],
        },
        "Draft the 'Analytical Model' section": {
            "title": "Analytical Model",
            "body": "Two platforms choose quantities simultaneously.",
            "claims": [
                {
                    "text": "Two platforms choose quantities simultaneously.",
                    "grounding_type": "formal_analytical_model",
                    "grounding_artifact_id": ids["model"],
                    "citation_id": None,
                    "conditions": [],
                }
            ],
            "citations": [],
        },
        "Draft the 'Equilibrium Analysis' section": {
            "title": "Equilibrium Analysis",
            "body": "Equilibrium quantity rises with demand.",
            "claims": [
                {
                    "text": "Equilibrium quantity rises with demand.",
                    "grounding_type": "verified_proposition",
                    "grounding_artifact_id": ids["prop_ok"],
                    "citation_id": None,
                    "conditions": ["a > 0"],
                }
            ],
            "citations": [],
        },
        "Draft the 'Propositions' section": {
            "title": "Propositions",
            "body": "Proposition 1: demand raises equilibrium quantity.",
            "claims": [
                {
                    "text": "Demand raises equilibrium quantity.",
                    "grounding_type": "verified_proposition",
                    "grounding_artifact_id": ids["prop_ok"],
                    "citation_id": None,
                    "conditions": ["a > 0"],
                }
            ],
            "citations": [],
        },
        "Draft the 'Numerical Analysis' section": {
            "title": "Numerical Analysis",
            "body": "Baseline evaluation confirms q1 = q2 = 3.",
            "claims": [
                {
                    "text": "Baseline evaluation confirms the equilibrium quantities.",
                    "grounding_type": "numerical_result",
                    "grounding_artifact_id": ids["result"],
                    "citation_id": None,
                    "conditions": [],
                }
            ],
            "citations": [],
        },
        "Draft the 'Discussion' section": {
            "title": "Discussion",
            "body": "The results indicate managers should expect symmetric responses.",
            "claims": [
                {
                    "text": "Results indicate symmetric quantity responses.",
                    "grounding_type": "research_finding",
                    "grounding_artifact_id": ids["finding"],
                    "citation_id": None,
                    "conditions": ["a > 0"],
                }
            ],
            "citations": [],
        },
        "Draft the 'Contributions' section": {
            "title": "Contributions",
            "body": "This work contributes a demand-driven quantity effect.",
            "claims": [
                {
                    "text": "The contribution is the demand-driven quantity effect.",
                    "grounding_type": "contribution_claim",
                    "grounding_artifact_id": ids["contribution"],
                    "citation_id": None,
                    "conditions": [],
                }
            ],
            "citations": [],
        },
        "Draft the 'Limitations' section": {
            "title": "Limitations",
            "body": "The analysis is limited to a single-period setting.",
            "claims": [
                {
                    "text": "The analysis is limited to a single-period setting.",
                    "grounding_type": "research_finding",
                    "grounding_artifact_id": ids["finding"],
                    "citation_id": None,
                    "conditions": [],
                }
            ],
            "citations": [],
        },
        "Draft the 'Conclusion' section": {
            "title": "Conclusion",
            "body": "Demand growth raises equilibrium platform quantities.",
            "claims": [
                {
                    "text": "Demand growth raises equilibrium platform quantities.",
                    "grounding_type": "research_finding",
                    "grounding_artifact_id": ids["finding"],
                    "citation_id": None,
                    "conditions": [],
                }
            ],
            "citations": [],
        },
    }


@pytest.mark.asyncio
async def test_outline_generation(tmp_path: pathlib.Path):
    from research_harness.plugins.research.manuscript_drafter.plugin import (
        ManuscriptDrafterService,
    )
    from research_harness.research.schemas.manuscript import ManuscriptOutline

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    svc = ManuscriptDrafterService(model_router=KeyedRouter({}), artifact_store=store)
    oid = await svc.outline(ids["package"])
    outline = (await store.get(oid)).parse_payload(ManuscriptOutline)
    assert [s.section_id.value for s in outline.section_specs] == [
        "introduction",
        "literature_review",
        "research_gap",
        "theory_mechanism",
        "analytical_model",
        "equilibrium_analysis",
        "propositions",
        "numerical_analysis",
        "discussion",
        "contributions",
        "limitations",
        "conclusion",
    ]
    props_spec = next(s for s in outline.section_specs if s.section_id.value == "propositions")
    assert ids["prop_ok"] in props_spec.artifact_ids
    lit_spec = next(s for s in outline.section_specs if s.section_id.value == "literature_review")
    assert ids["evidence"] in lit_spec.artifact_ids


@pytest.mark.asyncio
async def test_section_by_section_drafting(tmp_path: pathlib.Path):
    from research_harness.plugins.research.manuscript_drafter.plugin import (
        ManuscriptDrafterService,
    )
    from research_harness.research.schemas.manuscript import (
        ManuscriptDraft,
        ManuscriptDraftExecution,
        ManuscriptSection,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    svc = ManuscriptDrafterService(
        model_router=KeyedRouter(section_response(ids)), artifact_store=store
    )
    oid = await svc.outline(ids["package"])
    exec_id = await svc.draft(oid)
    rec = (await store.get(exec_id)).parse_payload(ManuscriptDraftExecution)
    assert rec.sections_created == 12
    assert rec.claims_created == 12
    assert rec.citations_created == 1

    draft = (await store.get(rec.draft_id)).parse_payload(ManuscriptDraft)
    assert draft.version == 1
    assert len(draft.section_ids) == 12
    lit = None
    for sid in draft.section_ids:
        s = (await store.get(sid)).parse_payload(ManuscriptSection)
        if s.section_id.value == "literature_review":
            lit = s
            break
    assert lit is not None
    assert "[CITE:c1]" in lit.body
    assert lit.citations[0].paper_identity_id == ids["identity"]
    assert lit.citations[0].evidence_item_id == ids["evidence"]


@pytest.mark.asyncio
async def test_unsupported_claim_rejection(tmp_path: pathlib.Path):
    from research_harness.plugins.research.manuscript_drafter.plugin import (
        ManuscriptDrafterService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    responses = section_response(ids)
    responses["Draft the 'Analytical Model' section"]["claims"][0]["grounding_artifact_id"] = (
        "bogus-id"
    )
    svc = ManuscriptDrafterService(model_router=KeyedRouter(responses), artifact_store=store)
    oid = await svc.outline(ids["package"])
    with pytest.raises(ValueError, match="unknown artifact"):
        await svc.draft(oid)


@pytest.mark.asyncio
async def test_missing_citation_rejection(tmp_path: pathlib.Path):
    from research_harness.plugins.research.manuscript_drafter.plugin import (
        ManuscriptDrafterService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    responses = section_response(ids)
    lit = responses["Draft the 'Literature Review' section"]
    lit["claims"][0]["citation_id"] = None
    svc = ManuscriptDrafterService(model_router=KeyedRouter(responses), artifact_store=store)
    oid = await svc.outline(ids["package"])
    with pytest.raises(ValueError, match="uncited literature claim"):
        await svc.draft(oid)


@pytest.mark.asyncio
async def test_dropped_conditions_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.research.manuscript_drafter.plugin import (
        ManuscriptDrafterService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    responses = section_response(ids)
    responses["Draft the 'Propositions' section"]["claims"][0]["conditions"] = []
    svc = ManuscriptDrafterService(model_router=KeyedRouter(responses), artifact_store=store)
    oid = await svc.outline(ids["package"])
    with pytest.raises(ValueError, match="drops proposition conditions"):
        await svc.draft(oid)


@pytest.mark.asyncio
async def test_failed_proposition_excluded(tmp_path: pathlib.Path):
    from research_harness.plugins.research.manuscript_drafter.plugin import (
        ManuscriptDrafterService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    responses = section_response(ids)
    responses["Draft the 'Propositions' section"]["claims"][0]["grounding_artifact_id"] = ids[
        "prop_failed"
    ]
    responses["Draft the 'Propositions' section"]["claims"][0]["conditions"] = []
    svc = ManuscriptDrafterService(model_router=KeyedRouter(responses), artifact_store=store)
    oid = await svc.outline(ids["package"])
    with pytest.raises(ValueError, match="not verified"):
        await svc.draft(oid)


@pytest.mark.asyncio
async def test_novelty_normalization(tmp_path: pathlib.Path):
    from research_harness.plugins.research.manuscript_drafter.plugin import (
        ManuscriptDrafterService,
    )
    from research_harness.research.schemas.manuscript import (
        ManuscriptDraftExecution,
        ManuscriptSection,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    responses = section_response(ids)
    intro = responses["Draft the 'Introduction' section"]
    intro["body"] = "This is the first study of demand-driven quantity effects. " + intro["body"]
    svc = ManuscriptDrafterService(model_router=KeyedRouter(responses), artifact_store=store)
    oid = await svc.outline(ids["package"])
    exec_id = await svc.draft(oid, ["introduction"])
    rec = (await store.get(exec_id)).parse_payload(ManuscriptDraftExecution)
    assert rec.novelty_claims_normalized >= 1
    from research_harness.research.schemas.manuscript import ManuscriptDraft

    draft = (await store.get(rec.draft_id)).parse_payload(ManuscriptDraft)
    section = (await store.get(draft.section_ids[0])).parse_payload(ManuscriptSection)
    assert "first study" not in section.body.lower()


@pytest.mark.asyncio
async def test_citation_provenance(tmp_path: pathlib.Path):
    from research_harness.plugins.research.manuscript_drafter.plugin import (
        ManuscriptDrafterService,
    )
    from research_harness.research.schemas.manuscript import (
        ManuscriptDraft,
        ManuscriptDraftExecution,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    svc = ManuscriptDrafterService(
        model_router=KeyedRouter(section_response(ids)), artifact_store=store
    )
    oid = await svc.outline(ids["package"])
    exec_id = await svc.draft(oid, ["literature_review"])
    draft = (
        await store.get((await store.get(exec_id)).parse_payload(ManuscriptDraftExecution).draft_id)
    ).parse_payload(ManuscriptDraft)
    lit_id = draft.section_ids[0]
    parents = await store.get_parents(lit_id)
    assert any(p.source_artifact_id == ids["evidence"] for p in parents)
    assert any(p.source_artifact_id == ids["identity"] for p in parents)
    # chain: evidence -> document -> identity (Phase 2 edges)
    ev_parents = await store.get_parents(ids["evidence"])
    assert any(p.source_artifact_id == ids["document"] for p in ev_parents)
    doc_parents = await store.get_parents(ids["document"])
    assert any(p.source_artifact_id == ids["identity"] for p in doc_parents)


@pytest.mark.asyncio
async def test_critic_output_and_deterministic_checks(tmp_path: pathlib.Path):
    from research_harness.plugins.research.manuscript_critic.plugin import ManuscriptCriticService
    from research_harness.plugins.research.manuscript_drafter.plugin import (
        ManuscriptDrafterService,
    )
    from research_harness.research.schemas.manuscript import (
        ManuscriptCritique,
        ManuscriptCritiqueCategory,
        ManuscriptDraftExecution,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    responses = section_response(ids)
    responses["Critique the following manuscript draft"] = {
        "overall_assessment": "solid but repetitive",
        "verdict": "revise",
        "recommendations": ["cut repetition"],
        "issues": [
            {
                "category": "repetition",
                "description": "discussion repeats the propositions section",
                "severity": "medium",
                "location": "discussion",
            }
        ],
    }
    svc = ManuscriptDrafterService(model_router=KeyedRouter(responses), artifact_store=store)
    oid = await svc.outline(ids["package"])
    exec_id = await svc.draft(oid, ["introduction", "contributions"])  # no limitations section
    draft_id = (await store.get(exec_id)).parse_payload(ManuscriptDraftExecution).draft_id
    critic = ManuscriptCriticService(model_router=KeyedRouter(responses), artifact_store=store)
    crit_id = await critic.critique(draft_id)
    c = (await store.get(crit_id)).parse_payload(ManuscriptCritique)
    cats = [i.category for i in c.issues]
    assert ManuscriptCritiqueCategory.missing_limitations in cats
    assert ManuscriptCritiqueCategory.repetition in cats
    assert c.verdict.value == "revise"


@pytest.mark.asyncio
async def test_immutable_revision(tmp_path: pathlib.Path):
    from research_harness.plugins.research.manuscript_critic.plugin import ManuscriptCriticService
    from research_harness.plugins.research.manuscript_drafter.plugin import (
        ManuscriptDrafterService,
    )
    from research_harness.research.schemas.manuscript import (
        ManuscriptDraft,
        ManuscriptDraftExecution,
        ManuscriptSection,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    responses = section_response(ids)
    responses["Critique the following manuscript draft"] = {
        "overall_assessment": "propositions need tightening",
        "verdict": "revise",
        "recommendations": ["rewrite propositions"],
        "issues": [
            {
                "category": "overclaiming",
                "description": "propositions section overclaims",
                "severity": "high",
                "location": "propositions",
            }
        ],
    }
    svc = ManuscriptDrafterService(model_router=KeyedRouter(responses), artifact_store=store)
    oid = await svc.outline(ids["package"])
    exec_id = await svc.draft(oid, ["introduction", "propositions", "contributions"])
    v1 = (
        await store.get((await store.get(exec_id)).parse_payload(ManuscriptDraftExecution).draft_id)
    ).parse_payload(ManuscriptDraft)
    critic = ManuscriptCriticService(model_router=KeyedRouter(responses), artifact_store=store)
    v1_env = next(env for env in await store.list(artifact_type="manuscript_draft"))
    await critic.critique(v1_env.artifact_id)

    rev_exec = await svc.revise(v1_env.artifact_id)
    v2 = (
        await store.get(
            (await store.get(rev_exec)).parse_payload(ManuscriptDraftExecution).draft_id
        )
    ).parse_payload(ManuscriptDraft)
    assert v2.version == 2
    assert v2.supersedes == v1_env.artifact_id
    # propositions re-drafted (new id); introduction + contributions reused
    v1_sections = {
        sid: (await store.get(sid)).parse_payload(ManuscriptSection).section_id
        for sid in v1.section_ids
    }
    v2_sections = {
        sid: (await store.get(sid)).parse_payload(ManuscriptSection).section_id
        for sid in v2.section_ids
    }
    v1_props = next(sid for sid, name in v1_sections.items() if name.value == "propositions")
    v2_props = next(sid for sid, name in v2_sections.items() if name.value == "propositions")
    assert v1_props != v2_props
    for sid, name in v1_sections.items():
        if name.value != "propositions":
            assert sid in v2_sections
    # V1 unchanged
    v1_after = (await store.get(v1_env.artifact_id)).parse_payload(ManuscriptDraft)
    assert v1_after.version == 1
    assert v1_after.section_ids == v1.section_ids
    # supersedes edge
    edges = await store.get_children(v1_env.artifact_id)
    assert any(e.relation.value == "supersedes" for e in edges)


@pytest.mark.asyncio
async def test_idempotent_draft(tmp_path: pathlib.Path):
    from research_harness.plugins.research.manuscript_drafter.plugin import (
        ManuscriptDrafterService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    svc = ManuscriptDrafterService(
        model_router=KeyedRouter(section_response(ids)), artifact_store=store
    )
    oid = await svc.outline(ids["package"])
    exec1 = await svc.draft(oid, ["introduction"])
    exec2 = await svc.draft(oid, ["introduction"])
    assert exec1 == exec2
    drafts = await store.list(artifact_type="manuscript_draft")
    assert len(drafts) == 1


@pytest.mark.asyncio
async def test_provenance_after_reopen(tmp_path: pathlib.Path):
    from research_harness.plugins.research.manuscript_drafter.plugin import (
        ManuscriptDrafterService,
    )
    from research_harness.research.schemas.manuscript import (
        ManuscriptDraft,
        ManuscriptDraftExecution,
        ManuscriptSection,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    svc = ManuscriptDrafterService(
        model_router=KeyedRouter(section_response(ids)), artifact_store=store
    )
    oid = await svc.outline(ids["package"])
    exec_id = await svc.draft(oid, ["introduction", "literature_review"])
    draft_id = (await store.get(exec_id)).parse_payload(ManuscriptDraftExecution).draft_id
    await store.close()

    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")
    draft = (await store2.get(draft_id)).parse_payload(ManuscriptDraft)
    # draft -> section -> finding -> package
    draft_parents = await store2.get_parents(draft_id)
    assert any(p.source_artifact_id == oid for p in draft_parents)
    assert any(p.source_artifact_id == ids["package"] for p in draft_parents)
    intro = None
    for sid in draft.section_ids:
        s = (await store2.get(sid)).parse_payload(ManuscriptSection)
        if s.section_id.value == "introduction":
            intro = sid
            break
    assert intro is not None
    intro_parents = await store2.get_parents(intro)
    assert any(p.source_artifact_id == ids["finding"] for p in intro_parents)
    lit = None
    for sid in draft.section_ids:
        s = (await store2.get(sid)).parse_payload(ManuscriptSection)
        if s.section_id.value == "literature_review":
            lit = sid
            break
    assert lit is not None
    lit_parents = await store2.get_parents(lit)
    assert any(p.source_artifact_id == ids["evidence"] for p in lit_parents)
    await store2.close()
