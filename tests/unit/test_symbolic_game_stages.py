"""Unit tests for the shared symbolic helpers (Phase 3C).

These pin the solution concept shared by the equilibrium deriver and the
verifier: simultaneous movers at a stage are solved as a *system*, not as a
sequence of reaction functions.
"""

from __future__ import annotations

import sympy

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
from research_harness.research.symbolic import (
    decision_stage_plan,
    game_consistent_focs,
    game_consistent_payoffs,
)


def _model(
    actors: list[tuple[str, str]],
    variables: dict[str, str],
    payoffs: list[tuple[str, str, list[str]]],
    timing: list[tuple[int, list[str]]],
    parameters: dict[str, str] | None = None,
) -> FormalAnalyticalModel:
    """Build a model. ``variables`` maps symbol -> kind ('decision'/'parameter')."""
    return FormalAnalyticalModel(
        selected_mechanism_id="mech-1",
        title="test model",
        description="model used to pin the solution concept",
        game_type="sequential",
        actors=[ModelActor(actor_id=a, name=a, role=r) for a, r in actors],
        variables=[
            ModelVariable(
                symbol=s,
                name=s,
                domain="R_+",
                meaning=s,
                kind=(
                    SymbolKind.decision_variable if k == "decision" else SymbolKind.parameter
                ),
            )
            for s, k in variables.items()
        ],
        parameters=[
            ModelParameter(symbol=s, name=s, domain="R_+", meaning=s)
            for s in (parameters or {})
        ],
        assumptions=[],
        timing=[
            ModelTimingStage(
                stage_number=n, name=f"s{n}", description=f"stage {n}", actor_ids=ids
            )
            for n, ids in timing
        ],
        information_structure=InformationStructure(summary="complete information"),
        payoffs=[
            PayoffFunction(
                actor_id=a,
                objective_type="maximize",
                expression=Expression(expression=expr),
                decision_variables=dvs,
            )
            for a, expr, dvs in payoffs
        ],
    )


def _stackelberg_two_followers() -> FormalAnalyticalModel:
    """Leader L picks Q, then F1 and F2 pick q1 and q2 simultaneously."""
    return _model(
        actors=[("L", "leader"), ("F1", "follower"), ("F2", "follower")],
        variables={"Q": "decision", "q1": "decision", "q2": "decision", "a": "parameter"},
        parameters={"a": "R_+"},
        payoffs=[
            ("L", "(a - Q - q1 - q2)*Q", ["Q"]),
            ("F1", "(a - Q - q1 - q2)*q1", ["q1"]),
            ("F2", "(a - Q - q1 - q2)*q2", ["q2"]),
        ],
        timing=[(1, ["L"]), (2, ["F1", "F2"])],
    )


# --- regression: multi-decision stages must be solved jointly --------------
#
# The previous implementation differentiated every actor's payoff with respect
# to every decision variable in the stage (including variables that actor does
# not choose) and substituted the last actor's reaction functions one at a
# time. For a stage with more than one decision variable that leaves the other
# followers' variables in the leader's payoff, so the leader optimised against
# a non-equilibrium continuation.


def test_leader_payoff_uses_followers_equilibrium_not_reaction_functions():
    model = _stackelberg_two_followers()
    payoffs = game_consistent_payoffs(model)
    a, Q, q1, q2 = sympy.symbols("a Q q1 q2")

    # Followers play Cournot: q1 = q2 = (a - Q)/3, so the leader faces
    # pi_L = (a - Q - 2(a-Q)/3)*Q = Q*(a - Q)/3.
    assert sympy.simplify(payoffs["L"] - Q * (a - Q) / 3) == 0
    # The later stage's variables must be fully eliminated.
    assert q1 not in payoffs["L"].free_symbols
    assert q2 not in payoffs["L"].free_symbols


def test_leader_first_order_condition_matches_true_spne():
    model = _stackelberg_two_followers()
    focs = {a: expr for a, _dv, expr in game_consistent_focs(model) if a == "L"}
    Q = sympy.Symbol("Q")
    (q_star,) = sympy.solve(sympy.Eq(focs["L"], 0), Q)
    assert sympy.simplify(q_star - sympy.Symbol("a") / 2) == 0


def test_single_decision_per_stage_is_unchanged():
    """A plain two-stage chain (one variable each) must still work."""
    model = _model(
        actors=[("L", "leader"), ("F", "follower")],
        variables={"Q": "decision", "q": "decision", "a": "parameter"},
        parameters={"a": "R_+"},
        payoffs=[("L", "(a - Q - q)*Q", ["Q"]), ("F", "(a - Q - q)*q", ["q"])],
        timing=[(1, ["L"]), (2, ["F"])],
    )
    payoffs = game_consistent_payoffs(model)
    a, Q, q = sympy.symbols("a Q q")
    # follower best response q = (a - Q)/2 -> pi_L = Q*(a - Q)/2
    assert sympy.simplify(payoffs["L"] - Q * (a - Q) / 2) == 0
    assert q not in payoffs["L"].free_symbols


def test_simultaneous_movers_keep_raw_payoffs():
    """One stage, two actors: nobody's variables are substituted."""
    model = _model(
        actors=[("F1", "firm"), ("F2", "firm")],
        variables={"q1": "decision", "q2": "decision", "a": "parameter"},
        parameters={"a": "R_+"},
        payoffs=[("F1", "(a - q1 - q2)*q1", ["q1"]), ("F2", "(a - q1 - q2)*q2", ["q2"])],
        timing=[(1, ["F1", "F2"])],
    )
    payoffs = game_consistent_payoffs(model)
    a, q1, q2 = sympy.symbols("a q1 q2")
    assert sympy.simplify(payoffs["F1"] - (a - q1 - q2) * q1) == 0
    assert sympy.simplify(payoffs["F2"] - (a - q1 - q2) * q2) == 0


def test_unresolved_stage_leaves_payoff_untouched():
    """When the stage system has no unique solution, substitute nothing.

    Substituting a wrong value is worse than substituting nothing: a merely
    wrong payoff still looks solvable and can be certified against.
    """
    model = _model(
        actors=[("L", "leader"), ("F", "follower")],
        variables={"Q": "decision", "q": "decision", "a": "parameter"},
        parameters={"a": "R_+"},
        # F's payoff is linear in q, so its FOC has no interior solution.
        payoffs=[("L", "(a - Q - q)*Q", ["Q"]), ("F", "q", ["q"])],
        timing=[(1, ["L"]), (2, ["F"])],
    )
    payoffs = game_consistent_payoffs(model)
    a, Q, q = sympy.symbols("a Q q")
    assert sympy.simplify(payoffs["L"] - (a - Q - q) * Q) == 0
    assert q in payoffs["L"].free_symbols


def test_decision_stage_plan_groups_by_stage():
    model = _stackelberg_two_followers()
    pairs = [("L", "Q"), ("F1", "q1"), ("F2", "q2")]
    plan = decision_stage_plan(model, pairs)
    assert plan["stage_groups"] == {1: ["L"], 2: ["F1", "F2"]}
    assert plan["sequential"] is True
