"""Phase 3C unit tests — equilibrium derivation + symbolic verification.

Simple models with known closed-form equilibria so expected answers are
deterministic. Fake models only, offline. SymPy performs the math.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.equilibrium import (
    EquilibriumAnalysis,
    EquilibriumCandidate,
    EquilibriumExecution,
    EquilibriumExecutionStatus,
    EquilibriumVerification,
    FirstOrderCondition,
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


class FakeRouter:
    def __init__(self, responses: list[str] | None = None):
        self.responses = responses or []
        self.calls = 0
        self.last_role = None

    async def complete(self, role, request):
        self.calls += 1
        self.last_role = role
        idx = min(self.calls - 1, len(self.responses) - 1)
        content = self.responses[idx] if self.responses else "{}"
        return ModelResponse(
            message=Message(role="assistant", content=content),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


def _expr(s: str) -> Expression:
    return Expression(expression=s, symbols_used=[])


def _dv(symbol: str, owner: str) -> ModelVariable:
    return ModelVariable(
        symbol=symbol,
        name=symbol,
        meaning=symbol,
        domain="R",
        kind=SymbolKind.decision_variable,
        owner_actor_id=owner,
    )


async def _model_env(
    store,
    *,
    payoffs: list[PayoffFunction] | None = None,
    variables: list[ModelVariable] | None = None,
    parameters: list[ModelParameter] | None = None,
    timing: list[ModelTimingStage] | None = None,
    actors: list[ModelActor] | None = None,
) -> str:
    model = FormalAnalyticalModel(
        selected_mechanism_id="mech1",
        title="Test model",
        description="test",
        actors=actors or [ModelActor(actor_id="a1", name="Actor 1", strategic=True)],
        variables=variables or [],
        parameters=parameters or [],
        assumptions=[],
        timing=timing
        or [ModelTimingStage(stage_number=0, name="move", description="move", actor_ids=[])],
        information_structure=InformationStructure(),
        payoffs=payoffs or [],
    )
    env = ArtifactEnvelope.create(
        payload=model, artifact_type="formal_analytical_model", producer="test"
    )
    await store.put(env)
    return env.artifact_id


async def _monopoly_model(store) -> str:
    """Payoff (a - p)*p - c*(a - p); optimum p* = (a + c)/2."""
    return await _model_env(
        store,
        actors=[ModelActor(actor_id="platform", name="Platform", strategic=True)],
        variables=[_dv("p", "platform")],
        parameters=[
            ModelParameter(symbol="a", name="demand", meaning="demand", domain="R_+"),
            ModelParameter(symbol="c", name="cost", meaning="cost", domain="R_+"),
        ],
        payoffs=[
            PayoffFunction(
                actor_id="platform",
                objective_type="profit",
                expression=_expr("(a - p)*p - c*(a - p)"),
                decision_variables=["p"],
                parameters=["a", "c"],
            )
        ],
        timing=[
            ModelTimingStage(
                stage_number=0, name="parameters", description="types realized", actor_ids=[]
            ),
            ModelTimingStage(
                stage_number=1,
                name="platform sets p",
                description="choose p",
                actor_ids=["platform"],
            ),
            ModelTimingStage(
                stage_number=2,
                name="payoffs",
                description="payoffs realized",
                actor_ids=["platform"],
            ),
        ],
    )


async def _transcendental_model(store) -> str:
    """Payoff a*exp(p): FOC a*exp(p) has no closed form -> LLM path."""
    model_id = await _monopoly_model(store)
    m = (await store.get(model_id)).parse_payload(FormalAnalyticalModel)
    m2 = m.model_copy(
        update={
            "payoffs": [
                PayoffFunction(
                    actor_id="platform",
                    objective_type="profit",
                    expression=_expr("a*exp(p)"),
                    decision_variables=["p"],
                    parameters=["a"],
                )
            ]
        }
    )
    env2 = ArtifactEnvelope.create(
        payload=m2, artifact_type="formal_analytical_model", producer="test"
    )
    await store.put(env2)
    return env2.artifact_id


def _candidate_resp(*exprs: tuple[str, str]) -> str:
    return json.dumps(
        {
            "expressions": [
                {"variable": var, "expression": e, "symbols_used": []} for var, e in exprs
            ]
        }
    )


async def _derive(store, model_id: str, router: FakeRouter, max_revisions: int = 2):
    from research_harness.plugins.research.equilibrium_deriver.plugin import (
        EquilibriumDeriverService,
    )
    from research_harness.plugins.research.equilibrium_verifier.plugin import (
        EquilibriumVerifierService,
    )

    verifier = EquilibriumVerifierService(artifact_store=store)
    svc = EquilibriumDeriverService(
        model_router=router,
        artifact_store=store,
        verifier=verifier,
        model_role="reasoning",
        max_revisions=max_revisions,
    )
    return svc


# ---------------------------------------------------------------------------
# Solvability gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_payoff_model_rejected(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    model_id = await _model_env(store, payoffs=[])
    svc = await _derive(store, model_id, FakeRouter())
    exec_id = await svc.derive(model_id)
    rec = (await store.get(exec_id)).parse_payload(EquilibriumExecution)
    assert rec.status == EquilibriumExecutionStatus.not_solvable
    assert any("zero payoffs" in f["error"] for f in rec.failures)
    assert not (await store.list(artifact_type="equilibrium_candidate"))


@pytest.mark.asyncio
async def test_strategic_actor_without_payoff_rejected(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    model_id = await _model_env(
        store,
        actors=[
            ModelActor(actor_id="a1", name="A1", strategic=True),
            ModelActor(actor_id="a2", name="A2", strategic=True),
        ],
        variables=[_dv("x", "a2")],
        payoffs=[
            PayoffFunction(
                actor_id="a1",
                objective_type="profit",
                expression=_expr("x*x"),
                decision_variables=["x"],
            )
        ],
    )
    svc = await _derive(store, model_id, FakeRouter())
    exec_id = await svc.derive(model_id)
    rec = (await store.get(exec_id)).parse_payload(EquilibriumExecution)
    assert rec.status == EquilibriumExecutionStatus.not_solvable
    assert any("no payoff function" in f["error"] for f in rec.failures)


@pytest.mark.asyncio
async def test_decision_variable_not_in_any_payoff_rejected(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    model_id = await _model_env(
        store,
        variables=[_dv("x", "a1"), _dv("y", "a1")],
        payoffs=[
            PayoffFunction(
                actor_id="a1",
                objective_type="profit",
                expression=_expr("x*x"),
                decision_variables=["x"],
            )
        ],
        timing=[
            ModelTimingStage(stage_number=0, name="move", description="move", actor_ids=["a1"])
        ],
    )
    svc = await _derive(store, model_id, FakeRouter())
    exec_id = await svc.derive(model_id)
    rec = (await store.get(exec_id)).parse_payload(EquilibriumExecution)
    assert rec.status == EquilibriumExecutionStatus.not_solvable
    assert any("does not appear in any payoff" in f["error"] for f in rec.failures)


# ---------------------------------------------------------------------------
# Simple monopoly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_monopoly_equilibrium(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    model_id = await _monopoly_model(store)
    router = FakeRouter()
    svc = await _derive(store, model_id, router)
    exec_id = await svc.derive(model_id)
    assert router.calls == 0  # fully symbolic

    rec = (await store.get(exec_id)).parse_payload(EquilibriumExecution)
    assert rec.status == EquilibriumExecutionStatus.derived
    assert rec.optimization_problems_created == 1
    assert rec.focs_created == 1
    assert rec.best_responses_created == 1
    assert rec.candidates_created == 1
    assert rec.verification_status == VerificationStatus.verified

    focs = [
        env.parse_payload(FirstOrderCondition)
        for env in await store.list(artifact_type="first_order_condition")
    ]
    assert len(focs) == 1
    assert focs[0].decision_variable == "p"
    assert focs[0].applicable is True
    assert len(focs[0].candidate_solutions) == 1
    import sympy

    foc = sympy.sympify(focs[0].foc_expression.expression)
    assert (
        sympy.simplify(foc - (sympy.Symbol("a") - 2 * sympy.Symbol("p") + sympy.Symbol("c"))) == 0
    )

    cands = [
        env.parse_payload(EquilibriumCandidate)
        for env in await store.list(artifact_type="equilibrium_candidate")
    ]
    assert len(cands) == 1
    c = cands[0]
    assert c.proposed_by == "sympy"
    p_expr = next(e for e in c.expressions if e.variable == "p")
    got = sympy.simplify(
        sympy.sympify(p_expr.expression.expression) - (sympy.Symbol("a") + sympy.Symbol("c")) / 2
    )
    assert got == 0

    v = (await store.list(artifact_type="equilibrium_verification"))[0].parse_payload(
        EquilibriumVerification
    )
    assert v.status == VerificationStatus.verified
    checks = {chk.check_type.value: chk for chk in v.checks}
    assert checks["foc_residual"].passed
    assert checks["second_order_condition"].passed  # -2 < 0
    assert checks["best_response_consistency"].passed
    assert checks["symbol_validation"].passed

    # latest analysis = leaf of the supersedes chain
    a_envs = await store.list(artifact_type="equilibrium_analysis")
    leaves = []
    for env in a_envs:
        children = await store.get_children(env.artifact_id)
        if not any(c.relation.value == "supersedes" for c in children):
            leaves.append(env)
    a = max(leaves, key=lambda e: e.created_at).parse_payload(EquilibriumAnalysis)
    assert a.status.value == "derived"
    assert a.selected_candidate_id is not None
    assert len(a.candidate_ids) == 1


# ---------------------------------------------------------------------------
# Two-player simultaneous (Cournot)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simultaneous_cournot(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    model_id = await _model_env(
        store,
        actors=[
            ModelActor(actor_id="f1", name="Firm 1", strategic=True),
            ModelActor(actor_id="f2", name="Firm 2", strategic=True),
        ],
        variables=[_dv("q1", "f1"), _dv("q2", "f2")],
        parameters=[
            ModelParameter(symbol="a", name="demand", meaning="demand", domain="R_+"),
            ModelParameter(symbol="c", name="cost", meaning="cost", domain="R_+"),
        ],
        payoffs=[
            PayoffFunction(
                actor_id="f1",
                objective_type="profit",
                expression=_expr("q1*(a - q1 - q2) - c*q1"),
                decision_variables=["q1"],
                parameters=["a", "c"],
            ),
            PayoffFunction(
                actor_id="f2",
                objective_type="profit",
                expression=_expr("q2*(a - q1 - q2) - c*q2"),
                decision_variables=["q2"],
                parameters=["a", "c"],
            ),
        ],
        timing=[
            ModelTimingStage(
                stage_number=0,
                name="simultaneous move",
                description="both choose",
                actor_ids=["f1", "f2"],
            ),
            ModelTimingStage(
                stage_number=1,
                name="payoffs",
                description="payoffs realized",
                actor_ids=["f1", "f2"],
            ),
        ],
    )
    svc = await _derive(store, model_id, FakeRouter())
    exec_id = await svc.derive(model_id)
    rec = (await store.get(exec_id)).parse_payload(EquilibriumExecution)
    assert rec.status == EquilibriumExecutionStatus.derived
    assert rec.verification_status == VerificationStatus.verified

    cand = (await store.list(artifact_type="equilibrium_candidate"))[0].parse_payload(
        EquilibriumCandidate
    )
    assert cand.solution_method == "simultaneous"
    import sympy

    q1 = next(e for e in cand.expressions if e.variable == "q1")
    q2 = next(e for e in cand.expressions if e.variable == "q2")
    target = (sympy.Symbol("a") - sympy.Symbol("c")) / 3
    assert sympy.simplify(sympy.sympify(q1.expression.expression) - target) == 0
    assert sympy.simplify(sympy.sympify(q2.expression.expression) - target) == 0

    v = (await store.list(artifact_type="equilibrium_verification"))[0].parse_payload(
        EquilibriumVerification
    )
    checks = {chk.check_type.value: chk for chk in v.checks}
    assert checks["best_response_consistency"].passed

    brs = await store.list(artifact_type="best_response")
    assert len(brs) == 2


# ---------------------------------------------------------------------------
# Sequential / backward induction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sequential_backward_induction(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    model_id = await _model_env(
        store,
        actors=[
            ModelActor(actor_id="leader", name="Leader", strategic=True),
            ModelActor(actor_id="follower", name="Follower", strategic=True),
        ],
        variables=[_dv("x", "leader"), _dv("y", "follower")],
        parameters=[
            ModelParameter(symbol="a", name="demand", meaning="demand", domain="R_+"),
            ModelParameter(symbol="c", name="cost", meaning="cost", domain="R_+"),
        ],
        payoffs=[
            PayoffFunction(
                actor_id="leader",
                objective_type="profit",
                expression=_expr("(a - x - y)*x - c*x"),
                decision_variables=["x"],
                parameters=["a", "c"],
            ),
            PayoffFunction(
                actor_id="follower",
                objective_type="profit",
                expression=_expr("(a - x - y)*y - c*y"),
                decision_variables=["y"],
                parameters=["a", "c"],
            ),
        ],
        timing=[
            ModelTimingStage(
                stage_number=0,
                name="leader moves",
                description="leader chooses x",
                actor_ids=["leader"],
            ),
            ModelTimingStage(
                stage_number=1,
                name="follower moves",
                description="follower chooses y",
                actor_ids=["follower"],
            ),
            ModelTimingStage(
                stage_number=2,
                name="payoffs",
                description="payoffs realized",
                actor_ids=["leader", "follower"],
            ),
        ],
    )
    svc = await _derive(store, model_id, FakeRouter())
    exec_id = await svc.derive(model_id)
    rec = (await store.get(exec_id)).parse_payload(EquilibriumExecution)
    assert rec.status == EquilibriumExecutionStatus.derived

    cand = (await store.list(artifact_type="equilibrium_candidate"))[0].parse_payload(
        EquilibriumCandidate
    )
    assert cand.solution_method == "backward_induction"
    import sympy

    x = next(e for e in cand.expressions if e.variable == "x")
    y = next(e for e in cand.expressions if e.variable == "y")
    a, c = sympy.Symbol("a"), sympy.Symbol("c")
    assert sympy.simplify(sympy.sympify(x.expression.expression) - (a - c) / 2) == 0
    assert sympy.simplify(sympy.sympify(y.expression.expression) - (a - c) / 4) == 0

    v = (await store.list(artifact_type="equilibrium_verification"))[0].parse_payload(
        EquilibriumVerification
    )
    timing_checks = [chk for chk in v.checks if chk.check_type.value == "timing_order"]
    assert timing_checks
    assert all(chk.passed for chk in timing_checks)

    a_env = (await store.list(artifact_type="equilibrium_analysis"))[0]
    a = a_env.parse_payload(EquilibriumAnalysis)
    assert a.solution_order == ["follower", "leader"]
    assert a.solution_method == "backward_induction"


# ---------------------------------------------------------------------------
# LLM-proposed candidates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_incorrect_llm_candidate_rejected(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    model_id = await _transcendental_model(store)
    router = FakeRouter([_candidate_resp(("p", "a"))])  # wrong: residual != 0
    svc = await _derive(store, model_id, router, max_revisions=0)
    exec_id = await svc.derive(model_id)
    assert router.calls == 1
    rec = (await store.get(exec_id)).parse_payload(EquilibriumExecution)
    assert rec.candidates_created == 1
    assert rec.verification_status == VerificationStatus.failed

    v = (await store.list(artifact_type="equilibrium_verification"))[0].parse_payload(
        EquilibriumVerification
    )
    assert v.status == VerificationStatus.failed
    foc = [chk for chk in v.checks if chk.check_type.value == "foc_residual"]
    assert any(not chk.passed for chk in foc)
    assert v.checks[0].check_type.value == "symbol_validation"


@pytest.mark.asyncio
async def test_undefined_symbol_in_candidate_rejected(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    model_id = await _transcendental_model(store)
    router = FakeRouter([_candidate_resp(("p", "(a + z)/2"))])
    svc = await _derive(store, model_id, router, max_revisions=0)
    exec_id = await svc.derive(model_id)
    rec = (await store.get(exec_id)).parse_payload(EquilibriumExecution)
    assert rec.verification_status == VerificationStatus.failed
    v = (await store.list(artifact_type="equilibrium_verification"))[0].parse_payload(
        EquilibriumVerification
    )
    sym_chk = [chk for chk in v.checks if chk.check_type.value == "symbol_validation"]
    assert sym_chk and not sym_chk[0].passed
    assert "undefined" in sym_chk[0].detail


@pytest.mark.asyncio
async def test_parameter_restrictions_and_partial_verification(tmp_path: pathlib.Path):
    """p* = (a + c)/(2b): denominator condition + symbolic SOC -> partial."""
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    model_id = await _model_env(
        store,
        actors=[ModelActor(actor_id="platform", name="Platform", strategic=True)],
        variables=[_dv("p", "platform")],
        parameters=[
            ModelParameter(symbol="a", name="demand", meaning="demand", domain="R_+"),
            ModelParameter(symbol="c", name="cost", meaning="cost", domain="R_+"),
            ModelParameter(symbol="b", name="slope", meaning="slope", domain="R_+"),
        ],
        payoffs=[
            PayoffFunction(
                actor_id="platform",
                objective_type="profit",
                expression=_expr("b*p*(a - p) - c*(a - p)"),
                decision_variables=["p"],
                parameters=["a", "c", "b"],
            )
        ],
        timing=[
            ModelTimingStage(
                stage_number=0, name="move", description="choose p", actor_ids=["platform"]
            )
        ],
    )
    svc = await _derive(store, model_id, FakeRouter())
    exec_id = await svc.derive(model_id)
    rec = (await store.get(exec_id)).parse_payload(EquilibriumExecution)
    assert rec.status.value == "partially_derived"
    assert rec.verification_status == VerificationStatus.partially_verified

    cand = (await store.list(artifact_type="equilibrium_candidate"))[0].parse_payload(
        EquilibriumCandidate
    )
    p = next(e for e in cand.expressions if e.variable == "p")
    import sympy

    got = sympy.simplify(
        sympy.sympify(p.expression.expression)
        - (sympy.Symbol("a") * sympy.Symbol("b") + sympy.Symbol("c")) / (2 * sympy.Symbol("b"))
    )
    assert got == 0
    assert p.conditions  # denominator != 0 recorded

    v = (await store.list(artifact_type="equilibrium_verification"))[0].parse_payload(
        EquilibriumVerification
    )
    assert v.status == VerificationStatus.partially_verified
    assert v.conditions_required
    assert any("!= 0" in c for c in v.conditions_required)
    soc = [chk for chk in v.checks if chk.check_type.value == "second_order_condition"]
    assert soc and not soc[0].passed  # symbolic SOC -> condition recorded
    foc = [chk for chk in v.checks if chk.check_type.value == "foc_residual"]
    assert all(chk.passed for chk in foc)  # residuals still verified


# ---------------------------------------------------------------------------
# Bounded revision, idempotency, provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bounded_revision_loop(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    model_id = await _transcendental_model(store)
    wrong = _candidate_resp(("p", "a"))
    router = FakeRouter([wrong, wrong, wrong])  # proposal + 2 revisions, all wrong
    svc = await _derive(store, model_id, router, max_revisions=2)
    exec_id = await svc.derive(model_id)
    assert router.calls == 3
    rec = (await store.get(exec_id)).parse_payload(EquilibriumExecution)
    assert rec.candidates_created == 3  # initial + 2 bounded revisions
    assert rec.revisions_used == 2
    assert rec.verification_status == VerificationStatus.failed

    cands = [
        env.parse_payload(EquilibriumCandidate)
        for env in await store.list(artifact_type="equilibrium_candidate")
    ]
    rounds = sorted(c.revision_round for c in cands)
    assert rounds == [0, 1, 2]
    assert all(c.revision_round > 0 and c.revision_notes for c in cands if c.revision_round > 0)
    assert len(cands) == 3  # bounded: no infinite loop


@pytest.mark.asyncio
async def test_idempotency_and_role_change(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    model_id = await _monopoly_model(store)
    router = FakeRouter()
    from research_harness.plugins.research.equilibrium_deriver.plugin import (
        EquilibriumDeriverService,
    )
    from research_harness.plugins.research.equilibrium_verifier.plugin import (
        EquilibriumVerifierService,
    )

    verifier = EquilibriumVerifierService(artifact_store=store)
    svc = EquilibriumDeriverService(
        model_router=router, artifact_store=store, verifier=verifier, model_role="reasoning"
    )
    first = await svc.derive(model_id)
    second = await svc.derive(model_id)
    assert first == second
    assert len(await store.list(artifact_type="equilibrium_candidate")) == 1

    svc2 = EquilibriumDeriverService(
        model_router=router, artifact_store=store, verifier=verifier, model_role="long_context"
    )
    third = await svc2.derive(model_id)
    assert third != first
    assert len(await store.list(artifact_type="equilibrium_candidate")) == 2


@pytest.mark.asyncio
async def test_provenance_after_reopen(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    model_id = await _monopoly_model(store)
    svc = await _derive(store, model_id, FakeRouter())
    exec_id = await svc.derive(model_id)

    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")

    a_envs = await store2.list(artifact_type="equilibrium_analysis")
    a = a_envs[-1].parse_payload(EquilibriumAnalysis)
    assert a.model_id == model_id
    cand_id = a.candidate_ids[0]
    cand = (await store2.get(cand_id)).parse_payload(EquilibriumCandidate)
    assert cand.model_id == model_id
    cand_parents = await store2.get_parents(cand_id)
    assert any(p.source_artifact_id == model_id for p in cand_parents)

    v_id = a.verification_ids[0]
    v_parents = await store2.get_parents(v_id)
    assert any(p.source_artifact_id == cand_id for p in v_parents)

    for t in ("optimization_problem", "first_order_condition", "best_response"):
        envs = await store2.list(artifact_type=t)
        assert envs
        parents = await store2.get_parents(envs[0].artifact_id)
        assert any(x.source_artifact_id == model_id for x in parents)

    ex_parents = await store2.get_parents(exec_id)
    assert any(p.source_artifact_id == model_id for p in ex_parents)
    await store2.close()
