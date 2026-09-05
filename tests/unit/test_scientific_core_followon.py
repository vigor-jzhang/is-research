"""Regression tests for the scientific-core follow-on (round 23).

Batch 5 of the §9 triage: L15, L16, L18, L19. L21 is covered by a note in the
round-23 report — it is blocked by the timing schema, and the attempt is pinned
by a guard test so nobody re-breaks the monopoly model the same way.
"""

from __future__ import annotations

import pathlib

import pytest

from research_harness.research.envelope import ArtifactEnvelope

MODEL_ID = "model-1"
CAND_ID = "cand-1"
EXEC_ID = "exec-1"


def _svc(store):
    from research_harness.plugins.research.numerical_analysis.plugin import (
        NumericalAnalysisService,
    )

    svc = NumericalAnalysisService.__new__(NumericalAnalysisService)
    svc._store = store
    svc._max_points = 100
    svc._artifact_threshold = 500
    svc._robustness_min_ratio = 0.5
    return svc


# ---------------------------------------------------------------------------
# L15 — baseline defaults must respect declared domains
# ---------------------------------------------------------------------------


def test_default_respects_a_bounded_domain():
    """L15: a parameter on [0,1] used to default to 1.0, the domain edge.

    Every sweep derived from it (0.5x to 2x, and 0 to 1.5x) then ran outside
    the domain, so most scenarios were infeasible before they began.
    """
    from research_harness.research.schemas.model import ModelParameter

    p = ModelParameter(symbol="rho", name="probability", meaning="success probability", domain="[0,1]")
    assert _svc(None)._default_within_domain(p, 1.0) == 0.5


def test_default_respects_a_sign_domain():
    from research_harness.research.schemas.model import ModelParameter

    svc = _svc(None)
    positive = ModelParameter(symbol="a", name="demand", meaning="demand", domain="R_+")
    assert svc._default_within_domain(positive, 10.0) == 10.0
    assert svc._default_within_domain(positive, -5.0) == 1.0
    negative = ModelParameter(symbol="b", name="slope", meaning="slope", domain="R_-")
    assert svc._default_within_domain(negative, 1.0) == -1.0


def test_default_unchanged_for_unconstrained_parameters():
    from research_harness.research.schemas.model import ModelParameter

    p = ModelParameter(symbol="x", name="x", meaning="x", domain="R")
    assert _svc(None)._default_within_domain(p, 1.0) == 1.0


def test_default_parameters_uses_the_domain():
    from research_harness.research.schemas.model import ModelParameter

    params = {
        "a": ModelParameter(symbol="a", name="demand", meaning="demand", domain="R_+"),
        "rho": ModelParameter(symbol="rho", name="p", meaning="probability", domain="[0,1]"),
    }
    defaults = _svc(None)._default_parameters(params)
    assert defaults["a"] == 10.0
    assert defaults["rho"] == 0.5


# ---------------------------------------------------------------------------
# L15 — _points_of must survive a degenerate grid
# ---------------------------------------------------------------------------


def test_points_of_handles_a_one_dimensional_grid():
    """L15: a grid sweep with one dimension raised IndexError."""
    from research_harness.research.schemas.numerical import (
        ParameterSweep,
        SweepDimension,
        SweepKind,
    )

    sweep = ParameterSweep(
        model_id=MODEL_ID,
        equilibrium_candidate_id=CAND_ID,
        name="degenerate grid",
        kind=SweepKind.grid,
        dimensions=[SweepDimension(parameter="a", start=1.0, end=3.0, steps=3)],
        fixed_parameters={"c": 2.0},
        total_points=3,
    )
    env = ArtifactEnvelope.create(
        payload=sweep, artifact_type="parameter_sweep", producer="test"
    )
    points = _svc(None)._points_of(env)
    assert [p["a"] for p in points] == [1.0, 2.0, 3.0]
    assert all(p["c"] == 2.0 for p in points)


def test_points_of_handles_a_grid_with_no_dimensions():
    from research_harness.research.schemas.numerical import ParameterSweep, SweepKind

    sweep = ParameterSweep(
        model_id=MODEL_ID,
        equilibrium_candidate_id=CAND_ID,
        name="empty grid",
        kind=SweepKind.grid,
        dimensions=[],
        fixed_parameters={"a": 1.0},
        total_points=1,
    )
    env = ArtifactEnvelope.create(
        payload=sweep, artifact_type="parameter_sweep", producer="test"
    )
    assert _svc(None)._points_of(env) == [{"a": 1.0}]


# ---------------------------------------------------------------------------
# L16 — proposition robustness: refuted claims and sign tolerance
# ---------------------------------------------------------------------------


def _result(*, feasible: bool = True, x_parameter: str | None = None, **params):
    from research_harness.research.schemas.numerical import NumericalResult

    return NumericalResult(
        model_id=MODEL_ID,
        equilibrium_candidate_id=CAND_ID,
        experiment_id=EXEC_ID,
        x_parameter=x_parameter,
        parameter_values=params,
        feasible=feasible,
    )


async def _proposition(store, *, status, expected_sign, statement="claim"):
    from research_harness.research.schemas.proposition import (
        Proposition,
        PropositionClaimType,
    )

    prop = Proposition(
        model_id=MODEL_ID,
        equilibrium_candidate_id=CAND_ID,
        comparative_statics_analysis_id="cs-1",
        statement=statement,
        claim_type=PropositionClaimType.monotonicity,
        outcome_variable="profit",
        parameter="a",
        expected_sign=expected_sign,
        status=status,
    )
    env = ArtifactEnvelope.create(
        payload=prop, artifact_type="proposition", producer="test"
    )
    await store.put(env)
    return env.artifact_id


@pytest.mark.asyncio
async def test_refuted_proposition_is_not_tested_for_support(tmp_path: pathlib.Path):
    """L16: a `failed` proposition was still tested and could report supported.

    The derivative here is positive and the claim says positive, so the old code
    reported "sign holds at all N admissible points" for a claim the verifier
    had already refuted.
    """
    import sympy

    from research_harness.plugins.storage.artifacts_sqlite.plugin import (
        SQLiteArtifactStore,
    )
    from research_harness.research.schemas.numerical import RobustnessCheck
    from research_harness.research.schemas.proposition import PropositionStatus

    store = SQLiteArtifactStore(path=tmp_path / "a.db")
    await _proposition(
        store, status=PropositionStatus.failed, expected_sign="positive"
    )
    check_ids = await _svc(store)._robustness(
        EXEC_ID,
        MODEL_ID,
        CAND_ID,
        [_result(a=2.0, c=1.0)],
        {},
        {"profit": sympy.Symbol("a")},
    )
    assert check_ids, "no robustness check was produced"
    checks = [(await store.get(cid)).parse_payload(RobustnessCheck) for cid in check_ids]
    prop_checks = [c for c in checks if c.proposition_id]
    assert prop_checks, "no proposition check was produced"
    assert prop_checks[0].outcome.value == "not_testable"
    assert "refuted" in prop_checks[0].conclusion
    await store.close()


@pytest.mark.asyncio
async def test_sign_is_compared_with_a_tolerance(tmp_path: pathlib.Path):
    """L16: a derivative of 1e-13 read as "positive" — a spurious violation.

    sympy.N(expr, 12) leaves residue of order 1e-13, so a mathematically zero
    derivative produced a definite sign and a spurious `violated` outcome.
    """
    import sympy

    from research_harness.plugins.storage.artifacts_sqlite.plugin import (
        SQLiteArtifactStore,
    )
    from research_harness.research.schemas.numerical import RobustnessCheck
    from research_harness.research.schemas.proposition import PropositionStatus

    store = SQLiteArtifactStore(path=tmp_path / "a.db")
    await _proposition(
        store, status=PropositionStatus.candidate, expected_sign="zero"
    )
    a = sympy.Symbol("a")
    # d/da (1e-13 * a) == 1e-13, i.e. zero to any sane precision.
    outcome_expr = sympy.Float("1e-13") * a
    check_ids = await _svc(store)._robustness(
        EXEC_ID, MODEL_ID, CAND_ID, [_result(a=2.0)], {}, {"profit": outcome_expr}
    )
    checks = [(await store.get(cid)).parse_payload(RobustnessCheck) for cid in check_ids]
    prop_checks = [c for c in checks if c.proposition_id]
    assert prop_checks, "no proposition check was produced"
    assert prop_checks[0].outcome.value == "supported", (
        f"a numerically zero derivative was reported as "
        f"{prop_checks[0].outcome.value}: {prop_checks[0].conclusion}"
    )
    await store.close()


# ---------------------------------------------------------------------------
# L18 — second-order conditions: cross-partials and the zero case
# ---------------------------------------------------------------------------


def test_soc_hessian_includes_cross_partials():
    """L18: the diagonal alone cannot see the cross-partial term."""
    import sympy

    from research_harness.plugins.research.equilibrium_verifier.plugin import (
        _soc_hessian,
    )

    q1, q2 = sympy.symbols("q1 q2")
    payoff = -(q1**2) - q2**2 + 4 * q1 * q2
    h = _soc_hessian(payoff, ["q1", "q2"], {})
    assert h[0, 0] == -2 and h[1, 1] == -2  # both diagonal entries look fine
    assert h[0, 1] == 4 and h[1, 0] == 4  # but the cross-partial is not


def test_negative_definite_detects_a_saddle():
    """L18: concave in each variable separately, but not jointly."""
    import sympy

    from research_harness.plugins.research.equilibrium_verifier.plugin import (
        _negative_definite,
        _soc_hessian,
    )

    q1, q2 = sympy.symbols("q1 q2")
    saddle = -(q1**2) - q2**2 + 4 * q1 * q2
    assert _negative_definite(_soc_hessian(saddle, ["q1", "q2"], {})) is False

    concave = -(q1**2) - 3 * q2**2
    assert _negative_definite(_soc_hessian(concave, ["q1", "q2"], {})) is True

    # Semidefinite: flat in one direction, so not a strict local maximum.
    flat = -(q1**2)
    assert _negative_definite(_soc_hessian(flat, ["q1", "q2"], {})) is False


def test_negative_definite_is_undecidable_when_symbolic():
    import sympy

    from research_harness.plugins.research.equilibrium_verifier.plugin import (
        _negative_definite,
    )

    b = sympy.Symbol("b")
    m = sympy.Matrix([[-b, 0], [0, -1]])
    assert _negative_definite(m) is None, "a symbolic minor must not be assumed positive"


# ---------------------------------------------------------------------------
# L19 — an actor with two payoffs keeps both
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_actor_with_two_payoffs_uses_each_own_expression(tmp_path: pathlib.Path):
    """L19: payoffs are keyed by actor_id, so all but the last were dropped.

    Both payoffs belong to one actor. The old actor-keyed dict kept only the
    second, so the FOC for q1 was differentiated from the q2 payoff, gave zero,
    and no candidate was produced.
    """
    from research_harness.plugins.research.equilibrium_deriver.plugin import (
        EquilibriumDeriverService,
    )
    from research_harness.plugins.storage.artifacts_sqlite.plugin import (
        SQLiteArtifactStore,
    )
    from research_harness.research.schemas.equilibrium import EquilibriumExecution
    from research_harness.research.schemas.model import (
        Expression,
        FormalAnalyticalModel,
        InformationStructure,
        ModelActor,
        ModelTimingStage,
        ModelVariable,
        PayoffFunction,
        SymbolKind,
    )

    class _NoLLM:
        async def complete(self, role, request):  # noqa: ANN201
            raise AssertionError("LLM must not be called for a symbolically solvable game")

    store = SQLiteArtifactStore(path=tmp_path / "a.db")
    model = FormalAnalyticalModel(
        selected_mechanism_id="mech1",
        title="Two-payoff actor",
        description="test",
        actors=[ModelActor(actor_id="a1", name="Actor 1", strategic=True)],
        variables=[
            ModelVariable(
                symbol=s, name=s, meaning=s, domain="R",
                kind=SymbolKind.decision_variable, owner_actor_id="a1",
            )
            for s in ("q1", "q2")
        ],
        parameters=[],
        assumptions=[],
        timing=[ModelTimingStage(stage_number=0, name="move", description="choose", actor_ids=["a1"])],
        information_structure=InformationStructure(),
        payoffs=[
            PayoffFunction(
                actor_id="a1",
                objective_type="profit",
                expression=Expression(expression="10*q1 - q1**2", symbols_used=[]),
                decision_variables=["q1"],
            ),
            PayoffFunction(
                actor_id="a1",
                objective_type="profit",
                expression=Expression(expression="20*q2 - q2**2", symbols_used=[]),
                decision_variables=["q2"],
            ),
        ],
    )
    m_env = ArtifactEnvelope.create(
        payload=model, artifact_type="formal_analytical_model", producer="test"
    )
    await store.put(m_env)

    from research_harness.plugins.research.equilibrium_verifier.plugin import (
        EquilibriumVerifierService,
    )

    svc = EquilibriumDeriverService(
        model_router=_NoLLM(),  # type: ignore[arg-type]
        artifact_store=store,
        verifier=EquilibriumVerifierService(artifact_store=store),
        model_role="reasoning",
    )
    exec_id = await svc.derive(m_env.artifact_id)
    rec = (await store.get(exec_id)).parse_payload(EquilibriumExecution)
    # L19 is about which payoff each FOC is differentiated from, so assert on
    # the candidate. The status is only partially_derived because the VERIFIER
    # keys payoffs by actor too (game_consistent_payoffs), so it also sees one
    # payoff for this actor — the same collision, in a place L19 does not name.
    assert rec.status.value in ("derived", "partially_derived"), (
        f"derivation failed: {rec.failures}"
    )

    from research_harness.research.schemas.equilibrium import EquilibriumCandidate

    cand = (await store.list(artifact_type="equilibrium_candidate"))[0].parse_payload(
        EquilibriumCandidate
    )
    solved = {e.variable: e.expression.expression for e in cand.expressions}
    # q1* = 5 from the first payoff, q2* = 10 from the second.
    assert solved.get("q1") == "5", f"q1 came from the wrong payoff: {solved}"
    assert solved.get("q2") == "10", f"q2 came from the wrong payoff: {solved}"
    await store.close()


async def _derive_two_variable_model(tmp_path: pathlib.Path, payoff: str) -> str:
    """Derive a one-actor, one-payoff, two-decision-variable model."""
    from research_harness.plugins.research.equilibrium_deriver.plugin import (
        EquilibriumDeriverService,
    )
    from research_harness.plugins.research.equilibrium_verifier.plugin import (
        EquilibriumVerifierService,
    )
    from research_harness.plugins.storage.artifacts_sqlite.plugin import (
        SQLiteArtifactStore,
    )
    from research_harness.research.schemas.equilibrium import EquilibriumExecution
    from research_harness.research.schemas.model import (
        Expression,
        FormalAnalyticalModel,
        InformationStructure,
        ModelActor,
        ModelTimingStage,
        ModelVariable,
        PayoffFunction,
        SymbolKind,
    )

    class _NoLLM:
        async def complete(self, role, request):  # noqa: ANN201
            raise AssertionError("LLM must not be called for a symbolically solvable game")

    store = SQLiteArtifactStore(path=tmp_path / "a.db")
    model = FormalAnalyticalModel(
        selected_mechanism_id="mech1",
        title="Two-variable actor",
        description="test",
        actors=[ModelActor(actor_id="a1", name="Actor 1", strategic=True)],
        variables=[
            ModelVariable(
                symbol=sym, name=sym, meaning=sym, domain="R",
                kind=SymbolKind.decision_variable, owner_actor_id="a1",
            )
            for sym in ("q1", "q2")
        ],
        parameters=[],
        assumptions=[],
        timing=[ModelTimingStage(stage_number=0, name="move", description="choose", actor_ids=["a1"])],
        information_structure=InformationStructure(),
        payoffs=[
            PayoffFunction(
                actor_id="a1",
                objective_type="profit",
                expression=Expression(expression=payoff, symbols_used=[]),
                decision_variables=["q1", "q2"],
            )
        ],
    )
    env = ArtifactEnvelope.create(
        payload=model, artifact_type="formal_analytical_model", producer="test"
    )
    await store.put(env)
    svc = EquilibriumDeriverService(
        model_router=_NoLLM(),  # type: ignore[arg-type]
        artifact_store=store,
        verifier=EquilibriumVerifierService(artifact_store=store),
        model_role="reasoning",
    )
    exec_id = await svc.derive(env.artifact_id)
    rec = (await store.get(exec_id)).parse_payload(EquilibriumExecution)
    await store.close()
    return rec.status.value


@pytest.mark.asyncio
async def test_saddle_payoff_does_not_verify(tmp_path: pathlib.Path):
    """L18: concave in each variable separately, but a saddle jointly.

    The diagonal second derivatives are both -2, so the old check passed it.
    The Hessian has eigenvalues -6 and +2, so there is no local maximum.
    """
    status = await _derive_two_variable_model(
        tmp_path / "saddle", "-(q1**2) - q2**2 + 4*q1*q2"
    )
    assert status != "derived", "a saddle was certified as a local optimum"


@pytest.mark.asyncio
async def test_jointly_concave_payoff_verifies(tmp_path: pathlib.Path):
    """Guard: the Hessian check must not reject a genuinely concave payoff."""
    status = await _derive_two_variable_model(
        tmp_path / "concave", "-(q1**2) - q2**2 + q1*q2"
    )
    assert status == "derived", f"a jointly concave payoff was rejected: {status}"


# ---------------------------------------------------------------------------
# L21 (blocked) — guard against the change that broke the monopoly model
# ---------------------------------------------------------------------------


def test_actor_listed_in_a_non_decision_stage_is_not_a_second_decision():
    """L21 (blocked): an actor may be listed in a stage where it decides nothing.

    The monopoly fixture lists the platform in a "payoffs realized" stage as
    well as its decision stage. Binding the actor to every stage it appears in
    — the obvious fix for L21 — makes a single-stage game look sequential and
    breaks backward induction. Fixing L21 needs ModelTimingStage to say which
    variables are decided in a stage; see the round-23 notes.
    """
    from research_harness.research.schemas.model import ModelTimingStage
    from research_harness.research.symbolic import decision_stage_plan

    class _Model:
        timing = [
            ModelTimingStage(stage_number=0, name="parameters", description="types", actor_ids=[]),
            ModelTimingStage(stage_number=1, name="platform sets p", description="choose p", actor_ids=["platform"]),
            ModelTimingStage(stage_number=2, name="payoffs", description="realized", actor_ids=["platform"]),
        ]

    plan = decision_stage_plan(_Model(), [("platform", "p")])
    assert plan["sequential"] is False, "a payoff-realization stage is not a decision"
    assert plan["stage_groups"] == {1: ["platform"]}
