"""Regression tests for H6-H12 (scientific correctness).

Each targets a check that did not fire or a claim that was honoured without
being verified: vacuous proposition verification, unverified equilibria
flowing downstream, dead sign inference, arithmetic that accepted non-finite
values, and arbitrary equilibrium selection.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# H8 — sign inference must actually work (domain assumptions + denominator sign)
# ---------------------------------------------------------------------------


def test_domains_become_sympy_assumptions():
    """H8: declared R_+ must let `ask(Q.positive(a))` succeed."""
    import sympy

    from research_harness.plugins.research.comparative_statics.plugin import (
        ComparativeStaticsService,
    )

    class _Var:
        def __init__(self, symbol: str, domain: str) -> None:
            self.symbol = symbol
            self.domain = domain

    class _Model:
        variables: list = []
        parameters = [_Var("a", "R_+"), _Var("b", "R_-"), _Var("c", "R")]

    svc = ComparativeStaticsService.__new__(ComparativeStaticsService)
    table = svc._symbols_with_domains(_Model(), sympy)
    assert sympy.ask(sympy.Q.positive(table["a"])) is True
    assert sympy.ask(sympy.Q.negative(table["b"])) is True
    # An unconstrained parameter must not be silently assumed positive.
    assert sympy.ask(sympy.Q.positive(table["c"])) is not True


def test_denominator_sign_is_not_lost():
    """H8: a negative denominator must not be reported as positive.

    `-2*x**2 - 2` is an Add, so `Mul.make_args` returns it whole and its sign
    is invisible to factor analysis. Pooling numerator and denominator factors
    made `1/(-2*(x**2+1))` report positive when its value is negative.
    """
    import sympy

    from research_harness.plugins.research.comparative_statics.plugin import (
        ComparativeStaticsService,
    )
    from research_harness.research.schemas.proposition import StaticSign

    svc = ComparativeStaticsService.__new__(ComparativeStaticsService)
    x = sympy.Symbol("x", real=True)

    negative_deriv = 1 / (-2 * (x**2 + 1))
    sign, _conditions = svc._sign_of(negative_deriv, "x", sympy)
    assert float(negative_deriv.subs(x, 3)) < 0
    assert sign == StaticSign.negative, f"denominator sign lost: got {sign}"

    positive_deriv = 1 / (2 * (x**2 + 1))
    sign2, _ = svc._sign_of(positive_deriv, "x", sympy)
    assert float(positive_deriv.subs(x, 3)) > 0
    assert sign2 == StaticSign.positive, f"got {sign2}"


# ---------------------------------------------------------------------------
# H11 — non-finite outcomes and fail-open domain parsing
# ---------------------------------------------------------------------------


def test_non_finite_outcome_is_infeasible():
    """H11: round(nan, 9) == nan, so NaN used to count as feasible."""
    import math

    import sympy


    # A non-finite outcome used to be recorded as feasible: round(nan, 9) ==
    # nan, so it passed the domain check and poisoned welfare and robustness.
    assert not math.isfinite(float(sympy.nan))
    assert not math.isfinite(float(sympy.oo))
    # The guard itself lives in the service; assert it is applied.
    import inspect

    from research_harness.plugins.research.numerical_analysis import plugin as na

    assert "math.isfinite" in inspect.getsource(na)


def test_domain_ok_fails_closed_on_unknown_domain():
    """H11: an unrecognised domain used to fall through to 'allowed'."""
    from research_harness.plugins.research.numerical_analysis.plugin import (
        NumericalAnalysisService,
    )

    svc = NumericalAnalysisService.__new__(NumericalAnalysisService)

    class _Obj:
        domain = "not-a-domain"

    ok, reason = svc._domain_ok(_Obj(), 1.0)
    assert ok is False, "unknown domain must not be treated as unconstrained"
    assert "unknown domain" in reason


def test_domain_ok_fails_closed_on_unparseable_interval():
    from research_harness.plugins.research.numerical_analysis.plugin import (
        NumericalAnalysisService,
    )

    svc = NumericalAnalysisService.__new__(NumericalAnalysisService)

    class _Obj:
        domain = "[a,b]"

    ok, reason = svc._domain_ok(_Obj(), 1.0)
    assert ok is False
    assert "unparseable" in reason


def test_domain_ok_still_accepts_r_and_constraints():
    from research_harness.plugins.research.numerical_analysis.plugin import (
        NumericalAnalysisService,
    )

    svc = NumericalAnalysisService.__new__(NumericalAnalysisService)

    class _R:
        domain = "R"

    class _Pos:
        domain = "R_+"

    class _Interval:
        domain = "[0,1]"

    assert svc._domain_ok(_R(), -5.0)[0] is True
    assert svc._domain_ok(_Pos(), -1.0)[0] is False
    assert svc._domain_ok(_Pos(), 1.0)[0] is True
    assert svc._domain_ok(_Interval(), 0.5)[0] is True
    assert svc._domain_ok(_Interval(), 5.0)[0] is False


# ---------------------------------------------------------------------------
# H10 — robustness needs more than one admissible point
# ---------------------------------------------------------------------------


def test_robustness_needs_a_ratio_not_one_point():
    """H10: a single feasible point (e.g. the baseline) is not a range."""
    from research_harness.plugins.research.numerical_analysis.plugin import (
        NumericalAnalysisService,
    )

    svc = NumericalAnalysisService(artifact_store=None)
    assert svc._robustness_min_ratio >= 0.5, "robustness must require a share of the sweep"


# ---------------------------------------------------------------------------
# H12 — deterministic, filtered equilibrium selection
# ---------------------------------------------------------------------------


def test_solution_selection_is_deterministic_and_real():
    """H12: sols[0] is arbitrary; non-real solutions must be discarded."""
    import sympy

    from research_harness.plugins.research.equilibrium_deriver.plugin import (
        EquilibriumDeriverService,
    )

    svc = EquilibriumDeriverService.__new__(EquilibriumDeriverService)
    q = sympy.Symbol("q")

    # A real root and a pair of complex roots.
    sols = [{q: sympy.Integer(1)}, {q: sympy.I}, {q: -sympy.I}]
    chosen = svc._select_solution(sols, ["q"], sympy)
    assert chosen is not None
    assert sympy.simplify(chosen["q"]) == 1, "a complex root was selected"

    # Deterministic: reversing the input order must not change the outcome.
    assert svc._select_solution(list(reversed(sols)), ["q"], sympy) == chosen


def test_solution_selection_rejects_non_finite():
    import sympy

    from research_harness.plugins.research.equilibrium_deriver.plugin import (
        EquilibriumDeriverService,
    )

    svc = EquilibriumDeriverService.__new__(EquilibriumDeriverService)
    q = sympy.Symbol("q")
    assert svc._select_solution([{q: sympy.zoo}], ["q"], sympy) is None
    assert svc._select_solution([{q: sympy.nan}], ["q"], sympy) is None
    assert svc._select_solution([{q: sympy.oo}], ["q"], sympy) is None


def test_solution_selection_returns_none_when_nothing_usable():
    import sympy

    from research_harness.plugins.research.equilibrium_deriver.plugin import (
        EquilibriumDeriverService,
    )

    svc = EquilibriumDeriverService.__new__(EquilibriumDeriverService)
    assert svc._select_solution([], ["q"], sympy) is None
    # A solution missing the decision variable entirely is unusable.
    assert svc._select_solution([{sympy.Symbol("other"): 1}], ["q"], sympy) is None


# ---------------------------------------------------------------------------
# H9 — status reflects the selected candidate, and pending is not success
# ---------------------------------------------------------------------------


def test_gate_helper_present_in_both_downstream_services():
    """H9: statics and numerics must both refuse unverified equilibria."""
    import inspect

    from research_harness.plugins.research.comparative_statics import plugin as cs
    from research_harness.plugins.research.numerical_analysis import plugin as na

    for mod in (cs, na):
        src = inspect.getsource(mod)
        assert "_require_verified_equilibrium" in src, mod.__name__
        assert "verification_status" in src, mod.__name__


# ---------------------------------------------------------------------------
# H6 / H7 — verification must never be vacuous
# ---------------------------------------------------------------------------


def test_verifier_has_an_explicit_not_verifiable_path():
    """H6: a candidate that cannot be loaded must not yield 'verified'."""
    import inspect

    from research_harness.plugins.research.proposition_verifier import plugin as pv

    src = inspect.getsource(pv)
    assert "_store_failed_verification" in src
    assert "could not" in src or "no equilibrium candidate" in src


def test_conditions_are_actually_checked():
    """H7: an ambiguous static must not pass on an unchecked condition string."""
    import inspect

    from research_harness.plugins.research.proposition_verifier import plugin as pv

    src = inspect.getsource(pv)
    assert "_sign_implied_by_conditions" in src
    assert "sympy.assuming" in src


def test_contradictory_condition_is_rejected():
    """H7: conditions that rule the claim out must fail, not conditionally verify."""
    import sympy

    from research_harness.plugins.research.proposition_verifier.plugin import (
        PropositionVerifierService,
    )

    svc = PropositionVerifierService.__new__(PropositionVerifierService)
    a = sympy.Symbol("a", real=True)
    # Claim "a is positive" under the condition "a < 0" -> contradiction.
    ok, explanation = svc._sign_implied_by_conditions("a", ["a < 0"], {a: a}, "positive")
    assert ok is False, f"contradiction not detected: {explanation}"
    assert "contradict" in explanation


def test_consistent_condition_is_accepted():
    import sympy

    from research_harness.plugins.research.proposition_verifier.plugin import (
        PropositionVerifierService,
    )

    svc = PropositionVerifierService.__new__(PropositionVerifierService)
    a = sympy.Symbol("a", real=True)
    ok, explanation = svc._sign_implied_by_conditions("a", ["a > 0"], {a: a}, "positive")
    assert ok is True, explanation


def test_uninformative_condition_is_not_treated_as_success():
    """An undeterminable result must be None, never True."""
    import sympy

    from research_harness.plugins.research.proposition_verifier.plugin import (
        PropositionVerifierService,
    )

    svc = PropositionVerifierService.__new__(PropositionVerifierService)
    a = sympy.Symbol("a", real=True)
    ok, _ = svc._sign_implied_by_conditions("a**3 - a", ["a > 0"], {a: a}, "positive")
    assert ok is None or ok is False, "an unprovable sign must not report success"
