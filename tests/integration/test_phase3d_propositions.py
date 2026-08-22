"""Phase 3D offline integration — full chain with fake models, no network:

FormalAnalyticalModel -> equilibrium (Phase 3C services) -> comparative
statics -> propositions (generate + verify + critique + interpret).
Provenance verified after SQLite reopen.
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
from research_harness.research.schemas.proposition import (
    ComparativeStatic,
    ComparativeStaticsAnalysis,
    ComparativeStaticsExecution,
    EconomicInterpretation,
    PropositionCritique,
    PropositionVerification,
    PropositionVerificationStatus,
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
async def test_phase3d_full_chain(tmp_path: pathlib.Path):
    from research_harness.plugins.research.comparative_statics.plugin import (
        ComparativeStaticsService,
    )
    from research_harness.plugins.research.equilibrium_deriver.plugin import (
        EquilibriumDeriverService,
    )
    from research_harness.plugins.research.equilibrium_verifier.plugin import (
        EquilibriumVerifierService,
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

    # ---- 1. Equilibrium (Phase 3C, fully symbolic) --------------------------
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

    # ---- 2. Comparative statics (deterministic) ------------------------------
    cs_svc = ComparativeStaticsService(artifact_store=store)
    cs_exec = await cs_svc.run(eq_analysis_id)
    rec = (await store.get(cs_exec)).parse_payload(ComparativeStaticsExecution)
    assert rec.statics_created == 4  # 2 outcomes x 2 parameters
    cs_id = await cs_svc.resolve_analysis(cs_exec)
    cs = (await store.get(cs_id)).parse_payload(ComparativeStaticsAnalysis)
    statics = [(await store.get(sid)).parse_payload(ComparativeStatic) for sid in cs.static_ids]
    dq1a = next(s for s in statics if s.outcome_variable == "q1" and s.parameter == "a")
    assert dq1a.sign.value == "positive"
    assert dq1a.derivative_expression.expression == "1/3"

    # ---- 3. Propositions: generate -> verify -> critique -> interpret --------
    sid = next(
        env.artifact_id
        for env in await store.list(artifact_type="comparative_static")
        if env.parse_payload(ComparativeStatic).outcome_variable == "q1"
        and env.parse_payload(ComparativeStatic).parameter == "a"
    )
    sid_c = next(
        env.artifact_id
        for env in await store.list(artifact_type="comparative_static")
        if env.parse_payload(ComparativeStatic).outcome_variable == "q2"
        and env.parse_payload(ComparativeStatic).parameter == "c"
    )
    prop_resp = json.dumps(
        {
            "propositions": [
                {
                    "statement": "Increasing demand a raises each platform's equilibrium quantity.",
                    "claim_type": "monotonicity",
                    "outcome_variable": "q1",
                    "parameter": "a",
                    "expected_sign": "positive",
                    "conditions": [],
                    "supporting_static_ids": [sid],
                },
                {
                    "statement": "Higher cost c lowers each platform's equilibrium quantity.",
                    "claim_type": "monotonicity",
                    "outcome_variable": "q2",
                    "parameter": "c",
                    "expected_sign": "negative",
                    "conditions": [],
                    "supporting_static_ids": [sid_c],
                },
            ]
        }
    )
    crit_resp = json.dumps(
        {
            "overall_assessment": "Both propositions are supported by the math.",
            "verdict": "keep",
            "recommendations": [],
            "issues": [],
        }
    )
    interp_resp = json.dumps(
        {
            "mathematical_result": "dq1/da = 1/3 > 0 at the verified equilibrium.",
            "economic_interpretation": "Market size raises platform output.",
            "managerial_implication": "Scale up in growing markets.",
            "is_theoretical_implication": "Demand is a core driver of platform quantity.",
            "consistency_note": "Restated within the verified sign.",
        }
    )
    router = FakeRouter([prop_resp, crit_resp, interp_resp, crit_resp, interp_resp])
    p_verifier = PropositionVerifierService(artifact_store=store)
    critic = PropositionCriticService(model_router=router, artifact_store=store)
    gen = PropositionGeneratorService(
        model_router=router, artifact_store=store, verifier=p_verifier, critic=critic
    )
    prop_ids = await gen.generate(cs_id)
    assert len(prop_ids) == 2

    from research_harness.research.schemas.proposition import Proposition as P

    props = [(await store.get(pid)).parse_payload(P) for pid in prop_ids]
    assert {p.expected_sign for p in props} == {"positive", "negative"}

    verifications = [
        env.parse_payload(PropositionVerification)
        for env in await store.list(artifact_type="proposition_verification")
    ]
    assert len(verifications) == 2
    assert all(v.status == PropositionVerificationStatus.verified for v in verifications)

    critiques = [
        env.parse_payload(PropositionCritique)
        for env in await store.list(artifact_type="proposition_critique")
    ]
    assert len(critiques) == 2
    assert all(c.verdict.value == "keep" for c in critiques)

    interps = [
        env.parse_payload(EconomicInterpretation)
        for env in await store.list(artifact_type="economic_interpretation")
    ]
    assert len(interps) == 2
    assert all(i.economic_interpretation for i in interps)
    assert all(i.managerial_implication for i in interps)
    assert all(i.is_theoretical_implication for i in interps)

    # ---- 4. Provenance after reopen -------------------------------------------
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")

    interp_envs = await store2.list(artifact_type="economic_interpretation")
    for env in interp_envs:
        parents = await store2.get_parents(env.artifact_id)
        assert any(p.source_artifact_id in prop_ids for p in parents)
    prop_envs = await store2.list(artifact_type="proposition")
    for env in prop_envs:
        parents = await store2.get_parents(env.artifact_id)
        assert any(p.source_artifact_id == cs_id for p in parents)
        assert any(p.source_artifact_id in (sid, sid_c) for p in parents)
    # chain: proposition -> static -> candidate -> model
    s_parents = await store2.get_parents(sid)
    assert any(p.source_artifact_id == dq1a.equilibrium_candidate_id for p in s_parents)
    c_parents = await store2.get_parents(dq1a.equilibrium_candidate_id)
    assert any(p.source_artifact_id == model_id for p in c_parents)
    await store2.close()
