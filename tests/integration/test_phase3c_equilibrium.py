"""Phase 3C offline integration — full chain with fake models, no network:

FormalAnalyticalModel (sequential leader-follower)
  -> solvability gate
  -> optimization problems + FOCs + best responses
  -> candidate equilibrium (backward induction)
  -> deterministic symbolic verification (verified)
Provenance verified after SQLite reopen.
"""

from __future__ import annotations

import pathlib

import pytest

from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.equilibrium import (
    BestResponse,
    EquilibriumAnalysis,
    EquilibriumCandidate,
    EquilibriumExecution,
    EquilibriumExecutionStatus,
    EquilibriumVerification,
    FirstOrderCondition,
    OptimizationProblem,
    VerificationStatus,
)
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


@pytest.mark.asyncio
async def test_phase3c_full_chain(tmp_path: pathlib.Path):
    from research_harness.plugins.research.equilibrium_deriver.plugin import (
        EquilibriumDeriverService,
    )
    from research_harness.plugins.research.equilibrium_verifier.plugin import (
        EquilibriumVerifierService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")

    model = FormalAnalyticalModel(
        selected_mechanism_id="mech1",
        title="Leader-follower platform pricing",
        description="Sequential game: platform fee then seller quantity.",
        actors=[
            ModelActor(actor_id="platform", name="Platform", strategic=True),
            ModelActor(actor_id="seller", name="Seller", strategic=True),
        ],
        variables=[
            ModelVariable(
                symbol="p",
                name="platform fee",
                meaning="fee",
                domain="R_+",
                kind=SymbolKind.decision_variable,
                owner_actor_id="platform",
            ),
            ModelVariable(
                symbol="q",
                name="seller quantity",
                meaning="quantity",
                domain="R_+",
                kind=SymbolKind.decision_variable,
                owner_actor_id="seller",
            ),
        ],
        parameters=[
            ModelParameter(symbol="a", name="demand", meaning="demand", domain="R_+"),
            ModelParameter(symbol="c", name="cost", meaning="cost", domain="R_+"),
        ],
        assumptions=[],
        timing=[
            ModelTimingStage(
                stage_number=0,
                name="platform moves",
                description="platform chooses p",
                actor_ids=["platform"],
            ),
            ModelTimingStage(
                stage_number=1,
                name="seller moves",
                description="seller chooses q",
                actor_ids=["seller"],
            ),
            ModelTimingStage(
                stage_number=2,
                name="payoffs",
                description="payoffs realized",
                actor_ids=["platform", "seller"],
            ),
        ],
        information_structure=InformationStructure(),
        payoffs=[
            PayoffFunction(
                actor_id="platform",
                objective_type="profit",
                expression=Expression(expression="(a - p - q)*p - c*p", symbols_used=[]),
                decision_variables=["p"],
                parameters=["a", "c"],
            ),
            PayoffFunction(
                actor_id="seller",
                objective_type="profit",
                expression=Expression(expression="(a - p - q)*q - c*q", symbols_used=[]),
                decision_variables=["q"],
                parameters=["a", "c"],
            ),
        ],
    )
    m_env = ArtifactEnvelope.create(
        payload=model, artifact_type="formal_analytical_model", producer="test"
    )
    await store.put(m_env)
    model_id = m_env.artifact_id

    verifier = EquilibriumVerifierService(artifact_store=store)
    svc = EquilibriumDeriverService(
        model_router=type("R", (), {"complete": lambda self, r, q: None})(),
        artifact_store=store,
        verifier=verifier,
        model_role="reasoning",
    )

    # fully symbolic: the router is never called; use a stub that would raise
    async def _fail(_role, _req):
        raise AssertionError("LLM must not be called for a symbolically solvable game")

    class NoLLM:
        async def complete(self, role, request):
            await _fail(role, request)

    svc = EquilibriumDeriverService(
        model_router=NoLLM(),
        artifact_store=store,
        verifier=verifier,
        model_role="reasoning",
    )

    exec_id = await svc.derive(model_id)
    rec = (await store.get(exec_id)).parse_payload(EquilibriumExecution)
    assert rec.status == EquilibriumExecutionStatus.derived
    assert rec.verification_status == VerificationStatus.verified
    assert rec.optimization_problems_created == 2
    assert rec.focs_created == 2
    assert rec.best_responses_created == 2
    assert rec.candidates_created == 1
    assert rec.revisions_used == 0

    # artifacts
    problems = [
        env.parse_payload(OptimizationProblem)
        for env in await store.list(artifact_type="optimization_problem")
    ]
    assert len(problems) == 2
    focs = [
        env.parse_payload(FirstOrderCondition)
        for env in await store.list(artifact_type="first_order_condition")
    ]
    assert len(focs) == 2
    brs = [
        env.parse_payload(BestResponse) for env in await store.list(artifact_type="best_response")
    ]
    assert len(brs) == 2
    seller_br = next(b for b in brs if b.actor_id == "seller")
    assert seller_br.implicit is False
    assert "a" in seller_br.response_expression.expression

    # candidate: backward induction; p* = (a-c)/2, q* = (a-c)/4
    cand = (await store.list(artifact_type="equilibrium_candidate"))[0].parse_payload(
        EquilibriumCandidate
    )
    assert cand.solution_method == "backward_induction"
    import sympy

    p = next(e for e in cand.expressions if e.variable == "p")
    q = next(e for e in cand.expressions if e.variable == "q")
    a, c = sympy.Symbol("a"), sympy.Symbol("c")
    assert sympy.simplify(sympy.sympify(p.expression.expression) - (a - c) / 2) == 0
    assert sympy.simplify(sympy.sympify(q.expression.expression) - (a - c) / 4) == 0

    # verification: fully verified with game-consistent FOCs
    v = (await store.list(artifact_type="equilibrium_verification"))[0].parse_payload(
        EquilibriumVerification
    )
    assert v.status == VerificationStatus.verified
    assert all(chk.passed for chk in v.checks)
    foc_checks = [chk for chk in v.checks if chk.check_type.value == "foc_residual"]
    assert len(foc_checks) == 2
    timing_checks = [chk for chk in v.checks if chk.check_type.value == "timing_order"]
    assert timing_checks and all(chk.passed for chk in timing_checks)

    # analysis leaf
    a_envs = await store.list(artifact_type="equilibrium_analysis")
    leaves = []
    for env in a_envs:
        children = await store.get_children(env.artifact_id)
        if not any(x.relation.value == "supersedes" for x in children):
            leaves.append(env)
    a = max(leaves, key=lambda e: e.created_at).parse_payload(EquilibriumAnalysis)
    assert a.status.value == "derived"
    assert a.solution_order == ["seller", "platform"]
    assert a.solution_method == "backward_induction"
    assert a.selected_candidate_id is not None

    # provenance after reopen
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")

    cand_id = a.candidate_ids[0]
    cand_parents = await store2.get_parents(cand_id)
    assert any(p.source_artifact_id == model_id for p in cand_parents)

    v_id = a.verification_ids[0]
    v_parents = await store2.get_parents(v_id)
    assert any(p.source_artifact_id == cand_id for p in v_parents)

    for t in ("optimization_problem", "first_order_condition", "best_response"):
        envs = await store2.list(artifact_type=t)
        assert len(envs) == 2
        parents = await store2.get_parents(envs[0].artifact_id)
        assert any(x.source_artifact_id == model_id for x in parents)

    ex_parents = await store2.get_parents(exec_id)
    assert any(p.source_artifact_id == model_id for p in ex_parents)
    await store2.close()
