"""Phase 4A offline integration — full chain with fake models, no network:

FormalAnalyticalModel -> equilibrium (3C) -> statics + propositions (3D)
-> numerical experiment (3E) -> findings -> contributions -> implications
-> ResearchResultsPackage (4A) -> critique (4A), with provenance checks.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
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
from research_harness.research.schemas.results import (
    ContributionClaim,
    ResearchFinding,
    ResearchImplication,
    ResearchResultsPackage,
    ResultsAssemblyExecution,
    ResultsCritique,
)


class FakeRouter:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.calls = 0

    async def complete(self, role, request):
        self.calls += 1
        idx = min(self.calls - 1, len(self.responses) - 1)
        return ModelResponse(
            message=Message(role="assistant", content=self.responses[idx]),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


@pytest.mark.asyncio
async def test_phase4a_full_chain(tmp_path: pathlib.Path):
    from research_harness.plugins.research.comparative_statics.plugin import (
        ComparativeStaticsService,
    )
    from research_harness.plugins.research.equilibrium_deriver.plugin import (
        EquilibriumDeriverService,
    )
    from research_harness.plugins.research.equilibrium_verifier.plugin import (
        EquilibriumVerifierService,
    )
    from research_harness.plugins.research.numerical_analysis.plugin import (
        NumericalAnalysisService,
    )
    from research_harness.plugins.research.proposition_critic.plugin import (
        PropositionCriticService,
    )
    from research_harness.plugins.research.proposition_generator.plugin import (
        PropositionGeneratorService,
    )
    from research_harness.plugins.research.proposition_verifier.plugin import (
        PropositionVerifierService,
    )
    from research_harness.plugins.research.results_assembler.plugin import (
        ResultsAssemblerService,
    )
    from research_harness.plugins.research.results_critic.plugin import ResultsCriticService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")

    from research_harness.research.schemas.gap import GapType, ResearchGap
    from research_harness.research.schemas.mechanism import SelectedMechanism

    rg = ResearchGap(
        title="platform demand dynamics",
        gap_type=GapType.theoretical_gap,
        description="within the reviewed corpus, demand-driven quantity effects on platforms are unresolved",
    )
    rg_env = ArtifactEnvelope.create(
        payload=rg, artifact_type="research_gap", producer="test", artifact_id="gap1"
    )
    await store.put(rg_env)

    mech = SelectedMechanism(
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
    )
    mech_env = ArtifactEnvelope.create(
        payload=mech, artifact_type="selected_mechanism", producer="test", artifact_id="mech1"
    )
    await store.put(mech_env)

    # ---- Phase 3C input: Cournot model --------------------------------------
    model = FormalAnalyticalModel(
        selected_mechanism_id="mech1",
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
                stage_number=0, name="move", description="both choose", actor_ids=["f1", "f2"]
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
    m_env = ArtifactEnvelope.create(
        payload=model, artifact_type="formal_analytical_model", producer="test"
    )
    await store.put(m_env)
    model_id = m_env.artifact_id

    # ---- 1. Equilibrium (3C, fully symbolic) --------------------------------
    verifier = EquilibriumVerifierService(artifact_store=store)

    class NoLLM:
        async def complete(self, role, request):
            raise AssertionError("LLM must not be called")

    deriver = EquilibriumDeriverService(
        model_router=NoLLM(), artifact_store=store, verifier=verifier, model_role="reasoning"
    )
    await deriver.derive(model_id)
    leaves = []
    for env in await store.list(artifact_type="equilibrium_analysis"):
        children = await store.get_children(env.artifact_id)
        if not any(c.relation.value == "supersedes" for c in children):
            leaves.append(env)
    eq_analysis_id = max(leaves, key=lambda e: e.created_at).artifact_id

    # ---- 2. Comparative statics + proposition (3D) ---------------------------
    cs_svc = ComparativeStaticsService(artifact_store=store)
    cs_exec = await cs_svc.run(eq_analysis_id)
    cs_id = await cs_svc.resolve_analysis(cs_exec)
    from research_harness.research.schemas.proposition import ComparativeStatic

    sid = next(
        env.artifact_id
        for env in await store.list(artifact_type="comparative_static")
        if env.parse_payload(ComparativeStatic).outcome_variable == "q1"
        and env.parse_payload(ComparativeStatic).parameter == "a"
    )
    prop_resp = json.dumps(
        {
            "propositions": [
                {
                    "statement": "Increasing demand a raises platform 1's equilibrium quantity.",
                    "claim_type": "monotonicity",
                    "outcome_variable": "q1",
                    "parameter": "a",
                    "expected_sign": "positive",
                    "conditions": [],
                    "supporting_static_ids": [sid],
                }
            ]
        }
    )
    crit_resp = json.dumps(
        {"overall_assessment": "ok", "verdict": "keep", "recommendations": [], "issues": []}
    )
    interp_resp = json.dumps(
        {
            "mathematical_result": "dq1/da = 1/3 > 0.",
            "economic_interpretation": "Larger markets raise platform output.",
            "managerial_implication": "Scale up in growing markets.",
            "is_theoretical_implication": "Demand drives platform quantity.",
            "consistency_note": "within the verified sign",
        }
    )
    router = FakeRouter([prop_resp, crit_resp, interp_resp])
    p_verifier = PropositionVerifierService(artifact_store=store)
    critic = PropositionCriticService(model_router=router, artifact_store=store)
    gen = PropositionGeneratorService(
        model_router=router, artifact_store=store, verifier=p_verifier, critic=critic
    )
    prop_ids = await gen.generate(cs_id)
    assert prop_ids

    # ---- 3. Numerical experiment (3E) -----------------------------------------
    num_svc = NumericalAnalysisService(artifact_store=store)
    exec_id = await num_svc.run(eq_analysis_id)
    from research_harness.research.schemas.numerical import NumericalExperiment

    exp = next(
        env.parse_payload(NumericalExperiment)
        for env in await store.list(artifact_type="numerical_experiment")
    )
    exp_id = next(env.artifact_id for env in await store.list(artifact_type="numerical_experiment"))
    assert exp.results

    # ---- 4. Results assembly (4A) --------------------------------------------
    asm_resp = json.dumps(
        {
            "findings": [
                {
                    "statement": "Equilibrium platform quantity rises with market demand.",
                    "finding_type": "analytical_result",
                    "supporting_proposition_ids": [prop_ids[0]],
                    "supporting_comparative_static_ids": [sid],
                    "supporting_numerical_result_ids": [exp.results[0]],
                    "conditions": [],
                    "confidence": "high",
                    "knowledge_basis": "research_inference",
                }
            ],
            "contributions": [
                {
                    "claim": "The model establishes how demand drives equilibrium platform quantities.",
                    "contribution_type": "theoretical",
                    "finding_ids": ["FINDING0"],
                    "advances_literature": "within the reviewed corpus, this mechanism was unresolved",
                    "novelty_claim": None,
                }
            ],
            "implications": [
                {
                    "text": "Platforms should expand capacity in growing markets.",
                    "implication_kind": "platform_firm_strategy",
                    "claim_type": "managerial_implication",
                    "grounded_in_finding_ids": ["FINDING0"],
                    "note": None,
                }
            ],
            "limitations": ["static single-period analysis"],
        }
    )
    crit_resp2 = json.dumps(
        {
            "overall_assessment": "claims match the verified results",
            "verdict": "approve",
            "recommendations": ["none"],
            "issues": [],
        }
    )
    asm_router = FakeRouter([asm_resp, crit_resp2])
    asm = ResultsAssemblerService(model_router=asm_router, artifact_store=store)
    asm_exec_id = await asm.assemble(exp_id)
    rec = (await store.get(asm_exec_id)).parse_payload(ResultsAssemblyExecution)
    assert rec.findings_created == 1
    assert rec.contributions_created == 1
    assert rec.implications_created == 1
    assert rec.numerical_experiment_id == exp_id
    assert rec.equilibrium_analysis_id == eq_analysis_id

    pkg_env = next(env for env in await store.list(artifact_type="results_package"))
    pkg = pkg_env.parse_payload(ResearchResultsPackage)
    assert pkg.equilibrium_analysis_id == eq_analysis_id
    assert pkg.equilibrium_candidate_id == exp.equilibrium_candidate_id
    assert pkg.model_id == model_id
    assert pkg.numerical_experiment_id == exp_id
    assert pkg.finding_ids == [rec.findings_created and pkg.finding_ids[0]]

    finding = (await store.get(pkg.finding_ids[0])).parse_payload(ResearchFinding)
    assert finding.supporting_proposition_ids == prop_ids
    assert finding.supporting_numerical_result_ids == [exp.results[0]]
    contribution = (await store.get(pkg.contribution_claim_ids[0])).parse_payload(ContributionClaim)
    assert contribution.finding_ids == pkg.finding_ids
    assert contribution.novelty_normalized is False
    implication = (await store.get(pkg.implication_ids[0])).parse_payload(ResearchImplication)
    assert implication.grounded_in_finding_ids == pkg.finding_ids

    # ---- 5. Critique (4A) -----------------------------------------------------
    rcritic = ResultsCriticService(model_router=asm_router, artifact_store=store)
    crit_id = await rcritic.critique(pkg_env.artifact_id)
    c = (await store.get(crit_id)).parse_payload(ResultsCritique)
    assert c.verdict.value == "approve"
    assert c.package_id == pkg_env.artifact_id

    # ---- 6. Provenance after reopen --------------------------------------------
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")
    pkg2 = (await store2.get(pkg_env.artifact_id)).parse_payload(ResearchResultsPackage)

    # package -> contribution (edge from contribution to package)
    pkg_parents = await store2.get_parents(pkg_env.artifact_id)
    assert any(
        pp.source_artifact_id == cid for cid in pkg2.contribution_claim_ids for pp in pkg_parents
    )
    # contribution -> finding (edge from finding to contribution)
    c_parents = await store2.get_parents(pkg2.contribution_claim_ids[0])
    assert any(p.source_artifact_id == pkg2.finding_ids[0] for p in c_parents)
    # finding -> proposition / numerical result / equilibrium candidate
    f_parents = await store2.get_parents(pkg2.finding_ids[0])
    assert any(fp.source_artifact_id == pid for pid in prop_ids for fp in f_parents)
    assert any(p.source_artifact_id == exp.results[0] for p in f_parents)
    assert any(p.source_artifact_id == pkg2.equilibrium_candidate_id for p in f_parents)
    # critique -> package
    crit_parents = await store2.get_parents(crit_id)
    assert any(p.source_artifact_id == pkg_env.artifact_id for p in crit_parents)
    await store2.close()
