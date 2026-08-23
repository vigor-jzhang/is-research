"""Phase 4B offline integration — full chain with fake models, no network:

ResearchResultsPackage -> outline -> manuscript sections -> draft V1
-> critique -> revised draft V2 (immutable, supersedes), with provenance
checks after reopen.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.gap import GapType, ResearchGap
from research_harness.research.schemas.manuscript import (
    ManuscriptCritique,
    ManuscriptDraft,
    ManuscriptDraftExecution,
    ManuscriptOutline,
    ManuscriptSection,
)


class ScriptedRouter:
    """Returns responses keyed by section title; last match wins."""

    def __init__(self, section_responses: dict[str, dict], critique: dict):
        self.section_responses = section_responses
        self.critique = critique
        self.calls = 0

    async def complete(self, role, request):
        self.calls += 1
        content = request.messages[-1].content
        if content.startswith("Critique the following manuscript draft"):
            resp = self.critique
        else:
            for key, r in self.section_responses.items():
                if key in content:
                    resp = r
                    break
            else:
                resp = {"title": "Section", "body": "Text.", "claims": [], "citations": []}
        return ModelResponse(
            message=Message(role="assistant", content=json.dumps(resp)),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


@pytest.mark.asyncio
async def test_phase4b_full_chain(tmp_path: pathlib.Path):
    from research_harness.contracts.blob import BlobReference
    from research_harness.plugins.research.manuscript_critic.plugin import (
        ManuscriptCriticService,
    )
    from research_harness.plugins.research.manuscript_drafter.plugin import (
        ManuscriptDrafterService,
    )
    from research_harness.research.schemas.equilibrium import (
        EquilibriumAnalysis,
        EquilibriumCandidate,
        EquilibriumExpression,
    )
    from research_harness.research.schemas.evidence import EvidenceItem
    from research_harness.research.schemas.full_text import FullTextDocument, TextStatus

    # ---- build a minimal package chain (same shape as unit tests) ----------
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
    from research_harness.research.schemas.synthesis import (
        SynthesisStatement,
        SynthesisStatementType,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")

    identity_env = ArtifactEnvelope.create(
        payload=PaperIdentity(
            member_paper_artifact_ids=["p1"],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.manual,
            resolution_evidence=[],
        ),
        artifact_type="paper_identity",
        producer="test",
    )
    await store.put(identity_env)
    doc_env = ArtifactEnvelope.create(
        payload=FullTextDocument(
            paper_identity_id=identity_env.artifact_id,
            document_acquisition_id="acq1",
            source_blob=BlobReference(digest="bb", size_bytes=1, storage_key="bb/bb"),
            extractor="test",
            page_count=5,
            pages_with_text=5,
            character_count=500,
            text_status=TextStatus.extracted,
        ),
        artifact_type="full_text_document",
        producer="test",
    )
    await store.put(doc_env)
    ev_env = ArtifactEnvelope.create(
        payload=EvidenceItem(
            statement="Platform competition follows Cournot patterns.",
            source_artifact_id=doc_env.artifact_id,
        ),
        artifact_type="evidence_item",
        producer="test",
    )
    await store.put(ev_env)
    syn_env = ArtifactEnvelope.create(
        payload=SynthesisStatement(
            statement="The reviewed corpus documents platform quantity competition.",
            type=SynthesisStatementType.consensus,
            supporting_evidence_ids=[ev_env.artifact_id],
            supporting_paper_identity_ids=[identity_env.artifact_id],
            papers_supporting=1,
            evidence_items_supporting=1,
        ),
        artifact_type="synthesis_statement",
        producer="test",
    )
    await store.put(syn_env)
    gap_env = ArtifactEnvelope.create(
        payload=ResearchGap(
            title="Demand-driven platform quantity dynamics",
            gap_type=GapType.theoretical_gap,
            description="within the reviewed corpus, demand-driven quantity effects on platforms are unresolved",
            supporting_synthesis_statement_ids=[syn_env.artifact_id],
            supporting_evidence_ids=[ev_env.artifact_id],
        ),
        artifact_type="research_gap",
        producer="test",
        artifact_id="gap1",
    )
    await store.put(gap_env)
    mech_env = ArtifactEnvelope.create(
        payload=SelectedMechanism(
            gap_id="gap1",
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
        ),
        artifact_type="selected_mechanism",
        producer="test",
    )
    await store.put(mech_env)
    model_env = ArtifactEnvelope.create(
        payload=FormalAnalyticalModel(
            selected_mechanism_id=mech_env.artifact_id,
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
        ),
        artifact_type="formal_analytical_model",
        producer="test",
    )
    await store.put(model_env)
    cand_env = ArtifactEnvelope.create(
        payload=EquilibriumCandidate(
            model_id=model_env.artifact_id,
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
        ),
        artifact_type="equilibrium_candidate",
        producer="test",
    )
    await store.put(cand_env)
    eq_env = ArtifactEnvelope.create(
        payload=EquilibriumAnalysis(
            model_id=model_env.artifact_id,
            candidate_ids=[cand_env.artifact_id],
            selected_candidate_id=cand_env.artifact_id,
            status="derived",
            solution_method="simultaneous",
        ),
        artifact_type="equilibrium_analysis",
        producer="test",
    )
    await store.put(eq_env)
    prop_env = ArtifactEnvelope.create(
        payload=Proposition(
            model_id=model_env.artifact_id,
            equilibrium_candidate_id=cand_env.artifact_id,
            comparative_statics_analysis_id="cs-a",
            statement="Increasing demand a raises each platform's equilibrium quantity.",
            claim_type=PropositionClaimType.monotonicity,
            outcome_variable="q1",
            parameter="a",
            expected_sign="positive",
            conditions=["a > 0"],
            supporting_static_ids=[],
            status="candidate",
        ),
        artifact_type="proposition",
        producer="test",
    )
    await store.put(prop_env)
    await store.put(
        ArtifactEnvelope.create(
            payload=PropositionVerification(
                proposition_id=prop_env.artifact_id,
                model_id=model_env.artifact_id,
                status=PropositionVerificationStatus.verified,
                checks=[],
            ),
            artifact_type="proposition_verification",
            producer="test",
        )
    )
    res_env = ArtifactEnvelope.create(
        payload=NumericalResult(
            model_id=model_env.artifact_id,
            equilibrium_candidate_id=cand_env.artifact_id,
            experiment_id="exp1",
            scenario="baseline",
            parameter_values={"a": 10.0, "c": 1.0},
            outcomes={"q1": 3.0, "q2": 3.0},
            feasible=True,
        ),
        artifact_type="numerical_result",
        producer="test",
    )
    await store.put(res_env)
    exp_env = ArtifactEnvelope.create(
        payload=NumericalExperiment(
            model_id=model_env.artifact_id,
            equilibrium_candidate_id=cand_env.artifact_id,
            sweeps=[],
            results=[res_env.artifact_id],
            robustness=[],
            welfare=[],
            status="completed",
            summary="baseline only",
        ),
        artifact_type="numerical_experiment",
        producer="test",
    )
    await store.put(exp_env)
    f_env = ArtifactEnvelope.create(
        payload=ResearchFinding(
            model_id=model_env.artifact_id,
            equilibrium_candidate_id=cand_env.artifact_id,
            statement="Larger markets raise equilibrium platform quantities.",
            finding_type="analytical_result",
            supporting_proposition_ids=[prop_env.artifact_id],
            supporting_comparative_static_ids=[],
            supporting_numerical_result_ids=[res_env.artifact_id],
            conditions=["a > 0"],
            confidence="high",
            knowledge_basis="research_inference",
        ),
        artifact_type="research_finding",
        producer="test",
    )
    await store.put(f_env)
    c_env = ArtifactEnvelope.create(
        payload=ContributionClaim(
            gap_id="gap1",
            finding_ids=[f_env.artifact_id],
            claim="Demand growth raises equilibrium platform quantities.",
            contribution_type="theoretical",
            advances_literature="within the reviewed corpus, this mechanism was unresolved",
        ),
        artifact_type="contribution_claim",
        producer="test",
    )
    await store.put(c_env)
    i_env = ArtifactEnvelope.create(
        payload=ResearchImplication(
            implication_kind="management",
            claim_type="managerial_implication",
            text="Platforms should scale capacity in growing markets.",
            grounded_in_finding_ids=[f_env.artifact_id],
        ),
        artifact_type="research_implication",
        producer="test",
    )
    await store.put(i_env)
    pkg_env = ArtifactEnvelope.create(
        payload=ResearchResultsPackage(
            research_question_id="rq1",
            gap_id="gap1",
            selected_mechanism_id=mech_env.artifact_id,
            model_id=model_env.artifact_id,
            equilibrium_analysis_id=eq_env.artifact_id,
            equilibrium_candidate_id=cand_env.artifact_id,
            numerical_experiment_id=exp_env.artifact_id,
            finding_ids=[f_env.artifact_id],
            contribution_claim_ids=[c_env.artifact_id],
            implication_ids=[i_env.artifact_id],
            limitations=["single-period setting"],
            status="assembled",
            summary="1 finding, 1 contribution, 1 implication",
        ),
        artifact_type="results_package",
        producer="test",
    )
    await store.put(pkg_env)

    # ---- section responses for the 12 sections ------------------------------
    def generic(
        title: str, grounding_type: str, gid: str, conditions: list[str] | None = None
    ) -> dict:
        return {
            "title": title,
            "body": f"{title} content.",
            "claims": [
                {
                    "text": f"{title} claim.",
                    "grounding_type": grounding_type,
                    "grounding_artifact_id": gid,
                    "citation_id": None,
                    "conditions": conditions or [],
                }
            ],
            "citations": [],
        }

    responses = {
        "Draft the 'Introduction' section": generic(
            "Introduction", "research_finding", f_env.artifact_id, ["a > 0"]
        ),
        "Draft the 'Literature Review' section": {
            "title": "Literature Review",
            "body": "Prior work documented platform competition patterns [CITE:c1].",
            "claims": [
                {
                    "text": "Prior work documented platform competition patterns.",
                    "grounding_type": "evidence_item",
                    "grounding_artifact_id": ev_env.artifact_id,
                    "citation_id": "c1",
                    "conditions": [],
                }
            ],
            "citations": [
                {
                    "citation_id": "c1",
                    "paper_identity_id": identity_env.artifact_id,
                    "evidence_item_id": ev_env.artifact_id,
                    "page_locator": "p. 12",
                }
            ],
        },
        "Draft the 'Research Gap' section": generic("Research Gap", "research_gap", "gap1"),
        "Draft the 'Theory / Mechanism' section": generic(
            "Theory / Mechanism", "selected_mechanism", mech_env.artifact_id
        ),
        "Draft the 'Analytical Model' section": generic(
            "Analytical Model", "formal_analytical_model", model_env.artifact_id
        ),
        "Draft the 'Equilibrium Analysis' section": generic(
            "Equilibrium Analysis", "verified_proposition", prop_env.artifact_id, ["a > 0"]
        ),
        "Draft the 'Propositions' section": generic(
            "Propositions", "verified_proposition", prop_env.artifact_id, ["a > 0"]
        ),
        "Draft the 'Numerical Analysis' section": generic(
            "Numerical Analysis", "numerical_result", res_env.artifact_id
        ),
        "Draft the 'Discussion' section": generic(
            "Discussion", "research_finding", f_env.artifact_id, ["a > 0"]
        ),
        "Draft the 'Contributions' section": generic(
            "Contributions", "contribution_claim", c_env.artifact_id
        ),
        "Draft the 'Limitations' section": generic(
            "Limitations", "research_finding", f_env.artifact_id
        ),
        "Draft the 'Conclusion' section": generic(
            "Conclusion", "research_finding", f_env.artifact_id
        ),
    }
    critique_resp = {
        "overall_assessment": "solid; propositions section needs tightening",
        "verdict": "revise",
        "recommendations": ["tighten propositions"],
        "issues": [
            {
                "category": "overclaiming",
                "description": "propositions section overclaims the scope",
                "severity": "high",
                "location": "propositions",
            }
        ],
    }
    router = ScriptedRouter(responses, critique_resp)

    # ---- outline ------------------------------------------------------------
    drafter = ManuscriptDrafterService(model_router=router, artifact_store=store)
    oid = await drafter.outline(pkg_env.artifact_id)
    outline = (await store.get(oid)).parse_payload(ManuscriptOutline)
    assert len(outline.section_specs) == 12

    # ---- draft V1 -------------------------------------------------------------
    exec1 = await drafter.draft(oid)
    rec1 = (await store.get(exec1)).parse_payload(ManuscriptDraftExecution)
    assert rec1.sections_created == 12
    v1 = (await store.get(rec1.draft_id)).parse_payload(ManuscriptDraft)
    assert v1.version == 1
    assert len(v1.section_ids) == 12

    # ---- critique -------------------------------------------------------------
    critic = ManuscriptCriticService(model_router=router, artifact_store=store)
    crit_id = await critic.critique(rec1.draft_id)
    critique = (await store.get(crit_id)).parse_payload(ManuscriptCritique)
    assert critique.verdict.value == "revise"
    assert any(i.location == "propositions" for i in critique.issues)

    # ---- revise -> V2 -----------------------------------------------------------
    exec2 = await drafter.revise(rec1.draft_id)
    rec2 = (await store.get(exec2)).parse_payload(ManuscriptDraftExecution)
    assert rec2.sections_reused == 11
    assert rec2.sections_created == 1
    v2 = (await store.get(rec2.draft_id)).parse_payload(ManuscriptDraft)
    assert v2.version == 2
    assert v2.supersedes == rec1.draft_id
    assert len(v2.section_ids) == 12

    # ---- provenance after reopen -----------------------------------------------
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")
    v2b = (await store2.get(rec2.draft_id)).parse_payload(ManuscriptDraft)
    # supersedes edge V1 -> V2
    children = await store2.get_children(rec1.draft_id)
    assert any(
        e.relation.value == "supersedes" and e.target_artifact_id == rec2.draft_id for e in children
    )
    # draft -> sections -> package
    assert any(
        p.source_artifact_id == pkg_env.artifact_id for p in await store2.get_parents(rec2.draft_id)
    )
    # section -> evidence -> document -> identity
    lit_id = None
    for sid in v2b.section_ids:
        s = (await store2.get(sid)).parse_payload(ManuscriptSection)
        if s.section_id.value == "literature_review":
            lit_id = sid
            break
    assert lit_id is not None
    assert any(p.source_artifact_id == ev_env.artifact_id for p in await store2.get_parents(lit_id))
    assert any(
        p.source_artifact_id == identity_env.artifact_id for p in await store2.get_parents(lit_id)
    )
    # critique -> draft
    assert any(p.source_artifact_id == rec1.draft_id for p in await store2.get_parents(crit_id))
    await store2.close()
