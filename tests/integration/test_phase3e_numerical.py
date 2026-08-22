"""Phase 3E offline integration — full chain with fake models, no network:

FormalAnalyticalModel -> equilibrium (3C) -> statics (3D) -> propositions (3D)
-> numerical experiment (baseline + sweeps + probes + grid) -> robustness
(parameter range + proposition support) -> welfare. Provenance after reopen.
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
from research_harness.research.schemas.numerical import (
    NumericalExperiment,
    NumericalExperimentExecution,
    NumericalResult,
    ParameterSweep,
    RobustnessCheck,
    WelfareAnalysis,
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
async def test_phase3e_full_chain(tmp_path: pathlib.Path):
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

    store = SQLiteArtifactStore(path=tmp_path / "art.db")

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
    a_envs = await store.list(artifact_type="equilibrium_analysis")
    leaves = []
    for env in a_envs:
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
    rec = (await store.get(exec_id)).parse_payload(NumericalExperimentExecution)
    assert rec.results_created >= 1
    assert rec.robustness_created >= 1
    assert rec.welfare_created == 1
    assert rec.engine == "sympy+python"
    assert rec.seed == 0

    exp = next(
        env.parse_payload(NumericalExperiment)
        for env in await store.list(artifact_type="numerical_experiment")
    )
    sweeps = [(await store.get(sid2)).parse_payload(ParameterSweep) for sid2 in exp.sweeps]
    kinds = {s.kind.value for s in sweeps}
    assert "baseline" in kinds
    assert "sweep_1d" in kinds
    assert "grid" in kinds

    # proposition-support robustness check exists and is consistent
    checks = [(await store.get(cid)).parse_payload(RobustnessCheck) for cid in exp.robustness]
    prop_check = next(c for c in checks if c.check_type.value == "proposition_support")
    assert prop_check.proposition_id == prop_ids[0]
    assert prop_check.admissible_points >= 1

    # welfare from model payoffs
    w = (await store.get(exp.welfare[0])).parse_payload(WelfareAnalysis)
    assert w.total_welfare is not None
    assert len(w.metrics) == 2

    # baseline numeric values match the analytic equilibrium
    baseline = None
    for rid in exp.results:
        r = (await store.get(rid)).parse_payload(NumericalResult)
        if r.scenario == "baseline":
            baseline = r
            break
    assert baseline is not None
    assert baseline.outcomes["q1"] == pytest.approx(3.0, abs=1e-6)

    # ---- 4. Provenance after reopen ------------------------------------------
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")

    exp2 = next(
        env.parse_payload(NumericalExperiment)
        for env in await store2.list(artifact_type="numerical_experiment")
    )
    parents = await store2.get_parents(
        next(env.artifact_id for env in await store2.list(artifact_type="numerical_experiment"))
    )
    assert any(p.source_artifact_id == exp2.equilibrium_candidate_id for p in parents)
    rid = exp2.results[0]
    r_parents = await store2.get_parents(rid)
    assert any(p.source_artifact_id == exp2.equilibrium_candidate_id for p in r_parents)
    # robustness references the proposition tested
    rcheck = None
    for cid in exp2.robustness:
        c = (await store2.get(cid)).parse_payload(RobustnessCheck)
        if c.check_type.value == "proposition_support":
            rcheck = c
            break
    assert rcheck is not None
    assert rcheck.proposition_id in prop_ids
    await store2.close()
