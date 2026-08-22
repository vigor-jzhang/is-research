"""Shared SymPy helpers for Phase 3C equilibrium derivation/verification.

The math is deterministic and shared between the deriver and the verifier so
verification can never be out of sync with the derivation's solution concept.
"""

from __future__ import annotations

from typing import Any

import sympy


def parse_sympy(expr: str, table: dict[str, Any]) -> Any:
    """Parse a model expression into a SymPy object using the symbol table."""
    known: dict[str, Any] = {s: sympy.Symbol(s) for s in table}
    return sympy.sympify(expr, locals=known)  # type: ignore[arg-type]


def decision_stage_plan(model: Any, decision_pairs: list[tuple[str, str]]) -> dict[str, Any]:
    """Group decision makers by timing stage.

    Actors deciding at the same stage move simultaneously; different stages are
    solved by backward induction (last stage first). Returns:
    sequential, stage_groups (stage -> [actors]), solution_order (first solved
    first), stages (sorted stage numbers).
    """
    decision_actor_stage: dict[str, int] = {}
    for t in model.timing:
        for aid in t.actor_ids:
            if aid not in decision_actor_stage:
                decision_actor_stage[aid] = t.stage_number
    stage_groups: dict[int, list[str]] = {}
    for actor in sorted(decision_actor_stage):
        if any(a == actor for a, _ in decision_pairs):
            stage_groups.setdefault(decision_actor_stage[actor], []).append(actor)
    stages = sorted(stage_groups)
    return {
        "sequential": len(stages) > 1,
        "stage_groups": stage_groups,
        "solution_order": [actor for st in reversed(stages) for actor in stage_groups[st]],
        "stages": stages,
    }


def game_consistent_payoffs(model: Any) -> dict[str, Any]:
    """Payoffs with later movers' best responses substituted in (backward induction).

    For each actor, the returned expression is what they actually optimize:
    - last movers (and simultaneous movers): raw payoff
    - earlier movers: payoff with all later-stage decision variables replaced by
      their best-response functions.
    """
    table = {v.symbol: v for v in model.variables}
    table.update({p.symbol: p for p in model.parameters})
    payoffs: dict[str, Any] = {
        p.actor_id: parse_sympy(p.expression.expression, table) for p in model.payoffs
    }
    decision_pairs = [(p.actor_id, dv) for p in model.payoffs for dv in p.decision_variables]
    plan = decision_stage_plan(model, decision_pairs)
    payoffs = dict(payoffs)
    # last stage first; only substitute into EARLIER stages (same-stage and
    # later actors keep their own payoffs: simultaneous moves).
    for stage in reversed(plan["stages"]):
        actors = plan["stage_groups"][stage]
        dvs = [dv for a, dv in decision_pairs if a in actors]
        brs: dict[str, Any] = {}
        for a in actors:
            for dv in dvs:
                try:
                    sols = sympy.solve(sympy.diff(payoffs[a], sympy.Symbol(dv)), sympy.Symbol(dv))
                except Exception:  # noqa: BLE001
                    sols = []
                if len(sols) == 1:
                    brs[dv] = sols[0]
        earlier_actors = {
            x for st2 in plan["stages"] if st2 < stage for x in plan["stage_groups"][st2]
        }
        for dv, br in brs.items():
            for actor in earlier_actors:
                payoffs[actor] = payoffs[actor].subs(sympy.Symbol(dv), br)
    return payoffs


def game_consistent_focs(model: Any) -> list[tuple[str, str, Any]]:
    """Backward-induction-consistent first-order conditions.

    Returns [(actor_id, decision_variable, foc_expr)] where earlier movers'
    FOCs are derived after substituting later movers' best responses into their
    payoffs. For simultaneous games this equals raw ∂payoff/∂decision.
    """
    payoffs = game_consistent_payoffs(model)
    decision_pairs = [(p.actor_id, dv) for p in model.payoffs for dv in p.decision_variables]
    plan = decision_stage_plan(model, decision_pairs)
    focs: list[tuple[str, str, Any]] = []
    for stage in reversed(plan["stages"]):
        actors = plan["stage_groups"][stage]
        for a, dv in decision_pairs:
            if a in actors:
                focs.append((a, dv, sympy.diff(payoffs[a], sympy.Symbol(dv))))
    return focs
