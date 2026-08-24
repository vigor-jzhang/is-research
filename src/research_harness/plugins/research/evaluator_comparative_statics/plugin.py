"""evaluator.comparative_statics — deterministic comparative-statics evaluator
(Phase 6F).

Evaluates produced Phase 3D ComparativeStatic artifacts against known-answer
references using SymPy symbolic equivalence (never string equality). The
evaluator recomputes every derivative from the produced candidate expressions
itself, so a wrong derivative or an over-claimed definite sign is caught even
if the production service regressed.

Critical deterministic failures:
- wrong derivative (produced or recomputed mismatch)
- wrong sign
- definite sign asserted when the derivative's sign is ambiguous
- required conditions dropped (or spurious conditions added)
- expected outcome/parameter pair missing from the produced statics
"""

from __future__ import annotations

from typing import Any

import sympy

from research_harness.contracts.evaluator import EvaluatorContext, envelope_payload_dict
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.schemas.evaluation import (
    EvaluatorCategory,
    EvaluatorResult,
    EvaluatorStatus,
)
from research_harness.research.schemas.model import FormalAnalyticalModel
from research_harness.research.symbolic import parse_sympy


def sympy_equivalent(a: str, b: str, table: dict[str, Any]) -> bool:
    """Symbolic equivalence via SymPy (difference simplifies to zero)."""
    try:
        expr_a = parse_sympy(a, table)
        expr_b = parse_sympy(b, table)
        return bool(sympy.simplify(sympy.cancel(sympy.together(expr_a - expr_b))) == 0)
    except Exception:  # noqa: BLE001
        return False


def condition_equivalent(a: str, b: str, table: dict[str, Any]) -> bool:
    """Condition equivalence: exact string equality after normalization, or
    symbolic side-by-side equivalence for operator conditions."""

    def _split(cond: str) -> tuple[str, str, str]:
        for op in ("!=", "<=", ">=", "==", "<", ">"):
            if op in cond:
                left, right = cond.split(op, 1)
                return left.strip(), right.strip(), op
        return cond.strip(), "", ""

    la, ra, oa = _split(a)
    lb, rb, ob = _split(b)
    if oa != ob:
        return False
    if not oa:
        return la == lb and ra == rb
    try:
        lhs_eq = (
            sympy.simplify(
                sympy.cancel(sympy.together(parse_sympy(la, table) - parse_sympy(lb, table)))
            )
            == 0
        )
        rhs_eq = (
            sympy.simplify(
                sympy.cancel(
                    sympy.together(parse_sympy(ra or "0", table) - parse_sympy(rb or "0", table))
                )
            )
            == 0
        )
        return bool(lhs_eq and rhs_eq)
    except Exception:  # noqa: BLE001
        return False


def defensible_sign(deriv: Any, param: str) -> str:
    """Decide whether a definite sign is defensible for the derivative, or
    whether the sign is ambiguous (mirrors the production sign logic)."""
    if deriv == 0:
        return "zero"
    free = deriv.free_symbols
    if not free:
        if sympy.ask(sympy.Q.positive(deriv)) is True:
            return "positive"
        if sympy.ask(sympy.Q.negative(deriv)) is True:
            return "negative"
        return "ambiguous"
    try:
        num, den = sympy.fraction(sympy.together(deriv))
        factors = list(sympy.Mul.make_args(num)) + list(sympy.Mul.make_args(den))
        const_factors = [f for f in factors if not f.free_symbols]
        pos_factors = [
            f for f in factors if f.free_symbols and sympy.ask(sympy.Q.positive(f)) is True
        ]
        if len(const_factors) + len(pos_factors) == len(factors) and const_factors:
            const_part = sympy.prod(const_factors)
            if isinstance(const_part, sympy.Number):
                if const_part > 0:
                    return "positive"
                if const_part < 0:
                    return "negative"
    except Exception:  # noqa: BLE001
        pass
    return "ambiguous"


class ComparativeStaticsEvaluator:
    evaluator_id = "evaluator.comparative_statics"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        statics = [
            envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "comparative_static"
        ]
        if not statics:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no comparative_static produced for the case",
            )

        model_envs = [
            e for e in ctx.produced_artifacts if e.artifact_type == "formal_analytical_model"
        ]
        candidate_envs = [
            e for e in ctx.produced_artifacts if e.artifact_type == "equilibrium_candidate"
        ]
        table: dict[str, Any] = {}
        candidate_exprs: dict[str, str] = {}
        if model_envs:
            model_env = max(model_envs, key=lambda e: e.created_at)
            model = model_env.parse_payload(FormalAnalyticalModel)
            table = {v.symbol: sympy.Symbol(v.symbol) for v in model.variables}
            table.update({p.symbol: sympy.Symbol(p.symbol) for p in model.parameters})
        if candidate_envs:
            candidate_env = max(candidate_envs, key=lambda e: e.created_at)
            candidate = envelope_payload_dict(candidate_env)
            candidate_exprs = {
                e.get("variable"): e.get("expression", {}).get("expression", "")
                for e in candidate.get("expressions") or []
            }

        produced_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
        for s in statics:
            produced_by_pair[
                (str(s.get("outcome_variable") or ""), str(s.get("parameter") or ""))
            ] = s

        reference = ctx.case.reference or {}
        expected_statics = dict(reference.get("expected_statics") or {})
        if not expected_statics:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="reference defines no expected_statics",
            )

        coverage_matches = 0
        derivative_matches = 0
        sign_matches = 0
        condition_ok = 0
        ambiguous_ok = 0
        ambiguous_total = 0
        failures_detail: list[str] = []

        for key, expected in expected_statics.items():
            outcome, _, param = key.partition("/")
            expected_derivative = str(expected.get("derivative") or "")
            expected_sign = str(expected.get("sign") or "")
            expected_conditions = [str(c) for c in expected.get("conditions") or []]

            produced = produced_by_pair.get((outcome, param))
            if produced is None:
                coverage_matches += 0
                failures_detail.append(f"COVERAGE: no static for d{outcome}/d{param}")
                continue
            coverage_matches += 1

            produced_derivative = (produced.get("derivative_expression") or {}).get(
                "expression", ""
            )
            produced_sign = produced.get("sign")

            # ---- derivative (produced vs expected, then recomputed) -----
            derivative_ok = sympy_equivalent(produced_derivative, expected_derivative, table)
            if derivative_ok:
                derivative_matches += 1
            else:
                failures_detail.append(
                    f"WRONG DERIVATIVE d{outcome}/d{param}: produced "
                    f"{produced_derivative!r} not equivalent to expected {expected_derivative!r}"
                )

            recomputed: Any | None = None
            if outcome in candidate_exprs:
                try:
                    expr = parse_sympy(candidate_exprs[outcome], table)
                    recomputed = sympy.simplify(sympy.diff(expr, sympy.Symbol(param)))
                except Exception:  # noqa: BLE001
                    recomputed = None
            if recomputed is not None and derivative_ok:
                if not sympy_equivalent(str(recomputed), produced_derivative, table):
                    failures_detail.append(
                        f"WRONG DERIVATIVE d{outcome}/d{param}: produced "
                        f"{produced_derivative!r} contradicts recomputed {recomputed}"
                    )

            # ---- sign ----------------------------------------------------
            if produced_sign == expected_sign:
                sign_matches += 1
            else:
                failures_detail.append(
                    f"WRONG SIGN d{outcome}/d{param}: produced {produced_sign!r}, "
                    f"expected {expected_sign!r}"
                )

            # ---- definite sign asserted when ambiguous ------------------
            if recomputed is not None and produced_sign != "ambiguous":
                defensible = defensible_sign(recomputed, param)
                if defensible == "ambiguous":
                    failures_detail.append(
                        f"DEFINITE SIGN OVERCLAIM d{outcome}/d{param}: produced "
                        f"{produced_sign!r} but the derivative is ambiguous"
                    )

            # ---- conditions ----------------------------------------------
            produced_conditions = [str(c) for c in produced.get("conditions") or []]
            missing = [
                cond
                for cond in expected_conditions
                if not any(condition_equivalent(cond, p, table) for p in produced_conditions)
            ]
            if not expected_conditions and produced_conditions:
                failures_detail.append(
                    f"SPURIOUS CONDITIONS d{outcome}/d{param}: produced "
                    f"{produced_conditions} for a provable sign"
                )
            elif missing:
                failures_detail.append(
                    f"CONDITIONS DROPPED d{outcome}/d{param}: missing {missing} "
                    f"(produced {produced_conditions})"
                )
            else:
                condition_ok += 1

            # ---- ambiguous handling --------------------------------------
            if expected_sign == "ambiguous":
                ambiguous_total += 1
                if produced_sign == "ambiguous" and produced_conditions:
                    ambiguous_ok += 1
                else:
                    failures_detail.append(
                        f"AMBIGUOUS SIGN d{outcome}/d{param}: produced "
                        f"{produced_sign!r} with conditions {produced_conditions}; "
                        "expected ambiguous with recorded conditions"
                    )

        expected_total = len(expected_statics)
        status = EvaluatorStatus.failed if failures_detail else EvaluatorStatus.passed

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "comparative_statics",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "derivative_accuracy": _metric(
                "derivative_accuracy",
                float(derivative_matches),
                expected_total,
                "rate",
                "produced derivatives symbolically equivalent to the reference",
            ),
            "sign_accuracy": _metric(
                "sign_accuracy",
                float(sign_matches),
                expected_total,
                "rate",
                "produced signs match the reference",
            ),
            "condition_preservation_accuracy": _metric(
                "condition_preservation_accuracy",
                float(condition_ok),
                expected_total,
                "rate",
                "required conditions preserved with no spurious conditions",
            ),
            "outcome_parameter_coverage": _metric(
                "outcome_parameter_coverage",
                float(coverage_matches),
                expected_total,
                "rate",
                "expected outcome/parameter pairs produced",
            ),
            "ambiguous_sign_accuracy": _metric(
                "ambiguous_sign_accuracy",
                float(ambiguous_ok),
                ambiguous_total,
                "rate",
                "ambiguous derivatives recorded as ambiguous with conditions",
            ),
        }

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=(derivative_matches / expected_total) if expected_total else None,
            value={
                "produced_statics": [
                    {
                        "outcome_variable": s.get("outcome_variable"),
                        "parameter": s.get("parameter"),
                        "derivative_expression": (s.get("derivative_expression") or {}).get(
                            "expression"
                        ),
                        "sign": s.get("sign"),
                        "conditions": s.get("conditions") or [],
                    }
                    for s in statics
                ],
                "metrics": metrics,
                "dimension_scores": {
                    k: v
                    for k, v in {
                        "derivative_accuracy": (
                            derivative_matches / expected_total if expected_total else None
                        ),
                        "sign_accuracy": (
                            sign_matches / expected_total if expected_total else None
                        ),
                        "condition_preservation_accuracy": (
                            condition_ok / expected_total if expected_total else None
                        ),
                        "outcome_parameter_coverage": (
                            coverage_matches / expected_total if expected_total else None
                        ),
                        "ambiguous_sign_accuracy": (
                            ambiguous_ok / ambiguous_total if ambiguous_total else None
                        ),
                    }.items()
                    if v is not None
                },
            },
            status=status,
            explanation="; ".join(failures_detail)
            if failures_detail
            else "all comparative statics matched",
            evidence_artifact_ids=[
                e.artifact_id
                for e in ctx.produced_artifacts
                if e.artifact_type == "comparative_static"
            ],
        )


class ComparativeStaticsEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.comparative_statics",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic comparative statics evaluator (Phase 6F)",
            provides=["evaluator.comparative_statics"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.comparative_statics", ComparativeStaticsEvaluator())
