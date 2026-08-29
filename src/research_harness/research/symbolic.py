"""Shared SymPy helpers for Phase 3C equilibrium derivation/verification.

The math is deterministic and shared between the deriver and the verifier so
verification can never be out of sync with the derivation's solution concept.
"""

from __future__ import annotations

import ast
from typing import Any

import sympy
from sympy.parsing.sympy_parser import (
    auto_symbol,
    parse_expr,
    standard_transformations,
)


class ExpressionSyntaxError(ValueError):
    """Raised when an expression string is not an allowed mathematical expression."""


# Functions an expression may call. Everything else (``__import__``, ``open``,
# ``eval``, ``getattr``, ...) is rejected by :func:`_validate_ast` before any
# evaluation happens.
_ALLOWED_FUNCS: dict[str, Any] = {
    name: getattr(sympy, name)
    for name in (
        "Abs",
        "Max",
        "Min",
        "ceiling",
        "cos",
        "exp",
        "floor",
        "log",
        "root",
        "sign",
        "sin",
        "sqrt",
        "tan",
    )
    if hasattr(sympy, name)
}

# Mathematical constants that may be referenced without being declared. These
# are the only undeclared names permitted: every other name must appear in the
# symbol table, which is what stops SymPy builtins (``beta``, ``gamma``, ``I``,
# ``N``, ``S``, ``Q``, ...) from silently shadowing a model's own identifiers.
_ALLOWED_CONSTANTS: dict[str, Any] = {
    name: getattr(sympy, name) for name in ("E", "pi") if hasattr(sympy, name)
}

# Minimal globals for ``parse_expr``: only the constructors its
# transformations inject. ``__builtins__`` is emptied so that even a bypass of
# the AST allow-list cannot reach ``open``, ``__import__`` or ``eval``.
_PARSE_GLOBALS: dict[str, Any] = {
    "__builtins__": {},
    "Symbol": sympy.Symbol,
    "Function": sympy.Function,
    "Integer": sympy.Integer,
    "Float": sympy.Float,
    "Rational": sympy.Rational,
    "Pow": sympy.Pow,
    "Mul": sympy.Mul,
    "Add": sympy.Add,
}

_TRANSFORMATIONS = standard_transformations + (auto_symbol,)

_ALLOWED_BINOPS: tuple[type[ast.operator], ...] = (
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Pow,
    ast.Mod,
)
_ALLOWED_UNARYOPS: tuple[type[ast.unaryop], ...] = (ast.UAdd, ast.USub)
_ALLOWED_COMPARES: tuple[type[ast.cmpop], ...] = (
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
)


def _validate_ast(expr: str, *, allow_comparison: bool) -> ast.Expression:
    """Reject any expression that is not pure arithmetic over names.

    ``sympy.sympify`` is a compiler, not a parser: it ``eval``s its input, so
    ``sympify("__import__('os').system(...)")`` executes. Expressions here
    originate from LLM output whose prompts embed retrieved literature, so
    they must be treated as untrusted.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ExpressionSyntaxError(f"expression is not valid syntax: {expr!r}") from e

    for node in ast.walk(tree):
        if isinstance(node, ast.Expression):
            continue
        if isinstance(node, ast.BinOp):
            if not isinstance(node.op, _ALLOWED_BINOPS):
                raise ExpressionSyntaxError(
                    f"disallowed operator {type(node.op).__name__} in {expr!r}"
                )
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, _ALLOWED_UNARYOPS):
                raise ExpressionSyntaxError(
                    f"disallowed unary operator {type(node.op).__name__} in {expr!r}"
                )
        elif isinstance(node, ast.Compare):
            if not allow_comparison:
                raise ExpressionSyntaxError(f"comparison not allowed in {expr!r}")
            for op in node.ops:
                if not isinstance(op, _ALLOWED_COMPARES):
                    raise ExpressionSyntaxError(
                        f"disallowed comparison {type(op).__name__} in {expr!r}"
                    )
        elif isinstance(node, (ast.operator, ast.unaryop, ast.cmpop)):
            # Operator nodes are visited on their own by ``ast.walk`` as well
            # as via their parent; they were already validated against the
            # operator allow-lists when the parent BinOp/UnaryOp/Compare was
            # checked, so reaching one here means it is permitted.
            continue
        elif isinstance(node, ast.Name):
            # Dunder / private names are never legitimate identifiers.
            if node.id.startswith("__"):
                raise ExpressionSyntaxError(f"disallowed name {node.id!r} in {expr!r}")
        elif isinstance(node, ast.Call):
            # Only a bare allow-listed name may be called: no attribute access,
            # no lambdas, no computed callee.
            func = node.func
            if not isinstance(func, ast.Name):
                raise ExpressionSyntaxError(f"disallowed call target in {expr!r}")
            if func.id not in _ALLOWED_FUNCS:
                raise ExpressionSyntaxError(f"disallowed function {func.id!r} in {expr!r}")
            if node.keywords:
                raise ExpressionSyntaxError(f"keyword arguments not allowed in {expr!r}")
        elif isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float)):
                raise ExpressionSyntaxError(
                    f"disallowed literal in {expr!r} (only numbers are allowed)"
                )
        elif isinstance(node, (ast.Load,)):
            continue
        else:
            # Attribute, Subscript, Lambda, comprehensions, f-strings,
            # Starred, BoolOp, IfExp, ... are all rejected.
            raise ExpressionSyntaxError(
                f"disallowed syntax {type(node).__name__} in expression {expr!r}"
            )
    return tree


def safe_sympify(
    expr: str,
    table: dict[str, Any] | None = None,
    *,
    auto_symbols: bool = False,
    allow_comparison: bool = False,
) -> Any:
    """Parse an untrusted expression string into a SymPy object, safely.

    Defence in depth:

    1. The string is parsed with :mod:`ast` and every node is checked against an
       allow-list, so ``__import__``/``open``/``getattr``/attribute access can
       never be evaluated.
    2. The result is built with ``parse_expr`` over a namespace that has no
       builtins and contains only declared symbols, allow-listed functions and
       two constants.

    Args:
        expr: The expression to parse.
        table: Mapping of symbol name to SymPy object (or anything coercible).
        auto_symbols: Create a ``Symbol`` for names that are not in ``table``.
            Use only where free symbols are the desired output (e.g. computing
            an expression's free symbols).
        allow_comparison: Permit ``==``/``!=``/``<``/``<=``/``>``/``>=`` so that
            condition strings such as ``"2*b != 0"`` can be parsed.
    """
    # Callers may hand over unvalidated LLM output, so reject non-strings at
    # runtime even though the annotation says ``str``.
    if type(expr) is not str:  # noqa: E721
        raise ExpressionSyntaxError(f"expression must be a string, got {type(expr).__name__}")
    if len(expr) > 10_000:
        raise ExpressionSyntaxError("expression is too long")
    # Callers split expressions on "=" and similar operators, which leaves
    # surrounding whitespace ("q1 = q2" -> "q1 ", " q2"). ``ast.parse`` in eval
    # mode rejects leading whitespace, and ``sympify`` stripped it internally,
    # so strip here to preserve that behaviour.
    expr = expr.strip()
    if not expr:
        raise ExpressionSyntaxError("expression is empty")

    _validate_ast(expr, allow_comparison=allow_comparison)

    local: dict[str, Any] = {}
    if table:
        for name, value in table.items():
            local[str(name)] = (
                value if isinstance(value, sympy.Basic) else sympy.Symbol(str(name))
            )
    local.update(_ALLOWED_CONSTANTS)
    local.update(_ALLOWED_FUNCS)

    if auto_symbols:
        for node in ast.walk(ast.parse(expr, mode="eval")):
            if isinstance(node, ast.Name) and node.id not in local:
                local[node.id] = sympy.Symbol(node.id)

    return parse_expr(
        expr,
        local_dict=local,
        global_dict=dict(_PARSE_GLOBALS),
        transformations=_TRANSFORMATIONS,
        evaluate=True,
    )


def parse_sympy(expr: str, table: dict[str, Any]) -> Any:
    """Parse a model expression into a SymPy object using the symbol table.

    Undeclared names become ``Symbol`` objects rather than an error: the
    equilibrium verifier relies on this to *report* undefined symbols as a
    failed ``symbol_validation`` check instead of raising. Names are therefore
    never resolved against SymPy's own namespace, so a model parameter called
    ``beta``, ``gamma``, ``I``, ``N``, ``S`` or ``Q`` is no longer silently
    captured by the SymPy builtin of the same name.

    Safe against code execution: see :func:`safe_sympify`.
    """
    return safe_sympify(expr, table, auto_symbols=True)


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


def _solve_stage_equilibrium(
    payoffs: dict[str, Any], stage_pairs: list[tuple[str, str]]
) -> dict[Any, Any] | None:
    """Solve one stage's simultaneous-move equilibrium, or ``None``.

    Actors at the same stage move simultaneously, so the stage is solved as a
    *system*: every actor's own first-order condition, solved jointly for every
    decision variable of the stage. Substituting reaction functions one at a
    time is not equivalent — it leaves the other actors' variables in the
    payoff, so earlier movers would be optimized against a non-equilibrium
    continuation.

    Returns the substitution dict when the system has a unique solution that
    assigns every decision variable, and ``None`` otherwise. ``None`` means
    "backward induction is unresolved here"; callers must then leave the payoff
    untouched rather than substitute a wrong value, because a payoff that is
    merely wrong still looks solvable and can be certified against.
    """
    if not stage_pairs:
        return None
    symbols = [sympy.Symbol(dv) for _a, dv in stage_pairs]
    equations: list[Any] = []
    for actor, dv in stage_pairs:
        try:
            equations.append(sympy.diff(payoffs[actor], sympy.Symbol(dv)))
        except Exception:  # noqa: BLE001
            return None
    try:
        solutions = sympy.solve(equations, symbols, dict=True)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(solutions, list):
        return None
    complete = [s for s in solutions if all(sym in s for sym in symbols)]
    if len(complete) != 1:
        # Zero solutions (no interior optimum) or several (multiple
        # equilibria): neither can be substituted silently.
        return None
    return complete[0]


def game_consistent_payoffs(model: Any) -> dict[str, Any]:
    """Payoffs with later movers' stage equilibria substituted in (backward induction).

    For each actor, the returned expression is what they actually optimize:
    - last movers (and simultaneous movers): raw payoff
    - earlier movers: payoff with all later-stage decision variables replaced by
      the later stage's *joint* equilibrium, so a Stackelberg leader optimizes
      against the followers' simultaneous-move outcome rather than against an
      arbitrary one of their reaction functions.
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
        # Only each actor's OWN decision variables: differentiating one actor's
        # payoff by another actor's variable is not a first-order condition.
        stage_pairs = [(a, dv) for a, dv in decision_pairs if a in actors]
        solution = _solve_stage_equilibrium(payoffs, stage_pairs)
        if solution is None:
            continue
        earlier_actors = {
            x for st2 in plan["stages"] if st2 < stage for x in plan["stage_groups"][st2]
        }
        for actor in earlier_actors:
            payoffs[actor] = payoffs[actor].subs(solution)
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
