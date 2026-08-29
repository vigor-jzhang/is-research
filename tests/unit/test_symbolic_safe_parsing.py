"""Security tests for symbolic expression parsing.

Expressions originate from LLM output whose prompts embed retrieved
literature, so they are untrusted input. ``sympy.sympify`` evaluates its
argument and therefore executes arbitrary code; :func:`safe_sympify` must not.
"""

from __future__ import annotations

import os

import pytest
import sympy

from research_harness.research.symbolic import (
    ExpressionSyntaxError,
    parse_sympy,
    safe_sympify,
)

# Payloads that must never be evaluated. Several of them parse to a *valid*
# expression, so a "did it raise?" check alone is not sufficient -- the tests
# also assert no side effect occurred.
_CODE_EXECUTION_PAYLOADS = [
    "__import__('os').system('true')",
    "open('/etc/hostname').read()",
    "__import__('os').getcwd()",
    "getattr(__import__('os'), 'getcwd')()",
    "q1*b + __import__('os').system('true')*0",
    "q1 + __import__('builtins').eval('1+1')*0",
    "eval('1+1')",
    "exec('pass')",
    "compile('1', '<s>', 'eval')",
    "globals()",
    "locals()",
    "vars()",
    "dir()",
    "__import__",
    "q1.__class__",
    "q1.__class__.__bases__[0]",
    "().__class__.__bases__[0].__subclasses__()",
    "q1.__doc__",
    "getattr(q1, 'name')",
    "setattr(q1, 'x', 1)",
    "delattr(q1, 'x')",
    "q1.real",
    "q1.func",
    "[x for x in (1, 2)]",
    "{x: 1 for x in 'ab'}",
    "lambda: 1",
    "f'{q1}'",
    "q1 if b else 0",
    "q1 and b",
    "not q1",
    "(q1, b)",
    "q1[b]",
    "b'@'",
    "q1 @ b",
    "q1 << b",
    "q1 & b",
]

_TABLE = {"q1": sympy.Symbol("q1"), "b": sympy.Symbol("b"), "a": sympy.Symbol("a")}


@pytest.mark.parametrize("payload", _CODE_EXECUTION_PAYLOADS)
def test_code_execution_payloads_rejected(payload: str) -> None:
    with pytest.raises(Exception):
        safe_sympify(payload, _TABLE)


@pytest.mark.parametrize("payload", _CODE_EXECUTION_PAYLOADS)
def test_code_execution_payloads_rejected_via_parse_sympy(payload: str) -> None:
    """``parse_sympy`` auto-creates symbols but must stay safe."""
    with pytest.raises(Exception):
        parse_sympy(payload, _TABLE)


def test_injected_side_effect_does_not_run(tmp_path) -> None:
    """A payload hidden inside a valid expression must have no side effect."""
    marker = tmp_path / "pwned.txt"
    expr = f"q1*b + __import__('pathlib').Path({str(marker)!r}).write_text('x')*0"
    with pytest.raises(ExpressionSyntaxError):
        parse_sympy(expr, _TABLE)
    assert not marker.exists()


def test_no_process_side_effect() -> None:
    marker = "/tmp/research-harness-sympy-rce-marker"
    if os.path.exists(marker):
        os.remove(marker)
    expr = f"b + __import__('os').system('touch {marker}')*0"
    with pytest.raises(ExpressionSyntaxError):
        parse_sympy(expr, _TABLE)
    assert not os.path.exists(marker)


# --- legitimate expressions must still parse -------------------------------


@pytest.mark.parametrize(
    "expr",
    [
        "q1*b + b**2",
        "2*b - 1",
        "(a - q1 - b)*q1",
        "q1/2",
        "-q1",
        "2**q1",
        "log(q1) + exp(b)",
        "Max(q1, b)",
        "sqrt(q1)",
        "Abs(q1 - b)",
    ],
)
def test_legitimate_expressions_parse(expr: str) -> None:
    assert parse_sympy(expr, _TABLE) is not None


def test_whitespace_from_operator_splitting_is_tolerated() -> None:
    """Callers split on '=' leaving padded operands, e.g. 'q1 = q2'."""
    lhs, _, rhs = "q1 = q2".partition("=")
    assert parse_sympy(lhs, _TABLE) == sympy.Symbol("q1")
    assert parse_sympy(rhs, _TABLE) == sympy.Symbol("q2")


def test_unknown_names_become_symbols_not_sympy_builtins() -> None:
    """A model parameter called beta/I/N must not resolve to the SymPy object.

    ``sympify`` silently captured these, so ``beta - q1`` parsed as the beta
    *function* and ``I`` as the imaginary unit.
    """
    table = {"q1": sympy.Symbol("q1")}
    parsed = parse_sympy("beta - q1", table)
    assert {str(s) for s in parsed.free_symbols} == {"beta", "q1"}

    parsed_i = parse_sympy("2*I", table)
    assert parsed_i == 2 * sympy.Symbol("I")
    assert parsed_i != 2 * sympy.I


def test_comparisons_only_when_permitted() -> None:
    with pytest.raises(ExpressionSyntaxError):
        safe_sympify("2*b != 0", _TABLE)
    assert safe_sympify("2*b != 0", _TABLE, allow_comparison=True) is not None
    assert safe_sympify("b > 0", _TABLE, allow_comparison=True) is not None


def test_numeric_literals_only() -> None:
    with pytest.raises(ExpressionSyntaxError):
        safe_sympify("'a string'", _TABLE)


def test_oversized_expression_rejected() -> None:
    with pytest.raises(ExpressionSyntaxError):
        safe_sympify("b + " * 10_000 + "b", _TABLE)


def test_non_string_rejected() -> None:
    with pytest.raises(ExpressionSyntaxError):
        safe_sympify(None, _TABLE)  # type: ignore[arg-type]
    with pytest.raises(ExpressionSyntaxError):
        safe_sympify("", _TABLE)
