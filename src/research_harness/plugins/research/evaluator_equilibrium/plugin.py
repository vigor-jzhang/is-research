"""evaluator.equilibrium — deterministic equilibrium evaluator (Phase 6E).

Evaluates produced Phase 3C artifacts (EquilibriumExecution,
EquilibriumAnalysis, EquilibriumCandidate, EquilibriumVerification,
FirstOrderCondition, BestResponse) against known-answer references using
SymPy symbolic equivalence (never string equality).

Critical deterministic failures:
- incorrect equilibrium marked verified
- nonzero FOC residual at the selected candidate
- wrong sequential solution order
- required conditions dropped
- unsolvable model treated as solved
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
from research_harness.research.symbolic import game_consistent_focs, parse_sympy


def _sympy_equivalent(a: str, b: str, table: dict[str, Any]) -> bool:
    """Symbolic equivalence via SymPy (difference simplifies to zero)."""
    try:
        expr_a = parse_sympy(a, table)
        expr_b = parse_sympy(b, table)
        return bool(sympy.simplify(sympy.cancel(sympy.together(expr_a - expr_b))) == 0)
    except Exception:  # noqa: BLE001
        return False


def _condition_equivalent(a: str, b: str, table: dict[str, Any]) -> bool:
    """Condition equivalence for `!= 0` / `< 0` style inequalities: parse both
    sides and compare symbolically."""

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


class EquilibriumEvaluator:
    evaluator_id = "evaluator.equilibrium"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        executions = [
            e for e in ctx.produced_artifacts if e.artifact_type == "equilibrium_execution"
        ]
        if not executions:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no equilibrium_execution produced for the case",
            )
        exec_env = max(executions, key=lambda e: e.created_at)
        execution = envelope_payload_dict(exec_env)
        execution_status = execution.get("status")

        analyses = [
            envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "equilibrium_analysis"
        ]
        analyses = sorted(analyses, key=lambda a: a.get("created_at") or 0)
        analysis = analyses[-1] if analyses else {}
        selected_candidate_id = analysis.get("selected_candidate_id")

        candidate_envs = [
            e for e in ctx.produced_artifacts if e.artifact_type == "equilibrium_candidate"
        ]
        verifications = [
            envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "equilibrium_verification"
        ]
        model_envs = [
            e for e in ctx.produced_artifacts if e.artifact_type == "formal_analytical_model"
        ]
        if not model_envs:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no formal_analytical_model produced for the case",
            )
        model_env = max(model_envs, key=lambda e: e.created_at)
        model = model_env.parse_payload(FormalAnalyticalModel)
        table = _symbol_table(model)

        reference = ctx.case.reference or {}
        expected_solution = dict(reference.get("expected_solution") or {})
        expected_status = reference.get("expected_status")
        expected_verification = reference.get("expected_verification")
        expected_order = list(reference.get("expected_solution_order") or [])
        expected_conditions = list(reference.get("expected_conditions") or [])
        expected_rejections = int(reference.get("expected_rejections") or 0)
        expected_revisions = int(reference.get("expected_revisions") or 0)
        expected_foc_rejected = bool(reference.get("expected_foc_residual_rejected") or False)
        expected_method = reference.get("expected_method")

        candidate_by_id = {e.artifact_id: envelope_payload_dict(e) for e in candidate_envs}
        selected_candidate = (
            candidate_by_id.get(selected_candidate_id) if selected_candidate_id else None
        )
        selected_verification = None
        if selected_candidate_id:
            selected_verification = next(
                (v for v in verifications if v.get("candidate_id") == selected_candidate_id),
                None,
            )
        candidate_exprs: dict[str, str] = {}
        if selected_candidate is not None:
            candidate_exprs = {
                e.get("variable"): e.get("expression", {}).get("expression", "")
                for e in (selected_candidate.get("expressions") or [])
            }
        candidate_conditions: list[str] = []
        if selected_candidate is not None:
            candidate_conditions = [
                c
                for e in (selected_candidate.get("expressions") or [])
                for c in e.get("conditions") or []
            ]
        candidate_conditions += list((selected_verification or {}).get("conditions_required") or [])

        # ---- expression accuracy (symbolic equivalence) ----------------
        expression_matches = 0
        expression_failures: list[str] = []
        for variable, expected_expr in expected_solution.items():
            produced_expr = candidate_exprs.get(variable)
            if produced_expr is None:
                expression_failures.append(
                    f"{variable}: no produced expression (expected {expected_expr!r})"
                )
                continue
            if _sympy_equivalent(produced_expr, expected_expr, table):
                expression_matches += 1
            else:
                expression_failures.append(
                    f"{variable}: produced {produced_expr!r} not equivalent to "
                    f"expected {expected_expr!r}"
                )

        # ---- FOC residual at the selected candidate (always recomputed) -
        foc_residual_failures: list[str] = []
        # nonzero residuals are only a defect for candidates expected to be
        # (partially) verified; rejected candidates are rejected precisely
        # because their residual is nonzero
        residual_gated = expected_verification in (None, "verified", "partially_verified")
        if (
            selected_candidate_id
            and residual_gated
            and expected_status not in (None, "not_solvable")
        ):
            try:
                candidate_map = {
                    sympy.Symbol(var): parse_sympy(expr, table)
                    for var, expr in candidate_exprs.items()
                }
                for actor_id, dv, foc in game_consistent_focs(model):
                    residual = sympy.simplify(sympy.cancel(foc.subs(candidate_map)))
                    if residual != 0:
                        foc_residual_failures.append(
                            f"FOC of {actor_id} on {dv}: residual {residual} != 0"
                        )
            except Exception as e:  # noqa: BLE001
                foc_residual_failures.append(f"FOC residual recomputation failed: {e}")

        # ---- verification / status accuracy ----------------------------
        produced_verification = (
            (selected_verification or {}).get("status") if selected_verification else None
        )
        verification_ok = (
            expected_verification is None or produced_verification == expected_verification
        )
        produced_status = execution_status
        status_ok = expected_status is None or produced_status == expected_status

        # ---- solution order --------------------------------------------
        produced_order = list(analysis.get("solution_order") or [])
        order_ok = not expected_order or produced_order == expected_order

        # ---- conditions -------------------------------------------------
        missing_conditions = [
            cond
            for cond in expected_conditions
            if not any(
                _condition_equivalent(cond, produced, table) for produced in candidate_conditions
            )
        ]

        # ---- rejections / revisions ------------------------------------
        rejected_count = sum(1 for v in verifications if v.get("status") == "failed")
        revisions_used = int(execution.get("revisions_used") or 0)
        rejection_ok = rejected_count >= expected_rejections
        revision_ok = revisions_used >= expected_revisions

        # ---- foc-residual-specific rejection ---------------------------
        foc_rejected = any(
            v.get("status") == "failed"
            and any(
                check.get("check_type") == "foc_residual" and check.get("passed") is False
                for check in v.get("checks") or []
            )
            for v in verifications
        )
        foc_rejection_ok = foc_rejected if expected_foc_rejected else True

        # ---- method -----------------------------------------------------
        produced_method = analysis.get("solution_method")
        method_ok = expected_method is None or produced_method == expected_method

        # ---- best-response accuracy (symbolic vs model FOC solutions) --
        br_matches = 0
        br_total = 0
        # Best responses that could not be checked at all, as opposed to ones
        # that were checked and disagreed.
        br_uncheckable = 0
        br_failures: list[str] = []
        raw_foc_by_key: dict[tuple[str, str], Any] = {}
        # Record *why* the FOCs could not be derived instead of discarding the
        # reason: without them every best response becomes "uncheckable" and
        # ``br_accuracy`` would fall through to its 1.0 default, reporting a
        # perfect score for a case that was never actually checked.
        foc_error: str | None = None
        try:
            for payoff in model.payoffs:
                for dv in payoff.decision_variables:
                    foc = sympy.diff(
                        parse_sympy(payoff.expression.expression, table),
                        sympy.Symbol(dv),
                    )
                    raw_foc_by_key[(payoff.actor_id, dv)] = foc
        except Exception as e:  # noqa: BLE001
            foc_error = f"{type(e).__name__}: {e}"
            raw_foc_by_key = {}
        for env in ctx.produced_artifacts:
            if env.artifact_type != "best_response":
                continue
            br = envelope_payload_dict(env)
            response_expr = (br.get("response_expression") or {}).get("expression")
            if not response_expr or br.get("implicit"):
                continue
            variable = br.get("decision_variable")
            actor_id = br.get("actor_id")
            foc = raw_foc_by_key.get((str(actor_id or ""), str(variable or "")))
            if foc is None:
                br_uncheckable += 1
                continue
            br_total += 1
            try:
                solutions = sympy.solve(sympy.Eq(foc, 0), sympy.Symbol(variable))
                expected_br = next(
                    (sol for sol in solutions if not sol.has(sympy.Symbol(variable))),
                    None,
                )
                if expected_br is None:
                    continue
                if _sympy_equivalent(response_expr, str(expected_br), table):
                    br_matches += 1
                else:
                    br_failures.append(f"{actor_id}/{variable}: {response_expr!r} != {expected_br}")
            except Exception:  # noqa: BLE001
                continue
        br_accuracy = br_matches / br_total if br_total else 1.0

        # ---- metrics ---------------------------------------------------
        expected_total = len(expected_solution)
        # A malformed model (e.g. a payoff that will not parse) makes the FOCs
        # underivable. Report that as an unverifiable result rather than
        # letting the exception escape and abort the whole evaluation.
        foc_derivation_error: str | None = None
        try:
            foc_total = len(list(game_consistent_focs(model)))
        except Exception as e:  # noqa: BLE001
            foc_derivation_error = f"{type(e).__name__}: {e}"
            foc_total = 0
        foc_accuracy = (
            max(0, foc_total - len(foc_residual_failures)) / foc_total if foc_total else 1.0
        )

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "equilibrium",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "equilibrium_expression_accuracy": _metric(
                "equilibrium_expression_accuracy",
                float(expression_matches),
                expected_total,
                "rate",
                "produced equilibrium expressions symbolically equivalent to the reference",
            ),
            "foc_accuracy": _metric(
                "foc_accuracy",
                float(max(0, foc_total - len(foc_residual_failures))),
                foc_total,
                "rate",
                "model FOCs with zero residual at the selected candidate",
            ),
            "best_response_accuracy": _metric(
                "best_response_accuracy",
                float(br_matches),
                br_total,
                "rate",
                "produced best responses consistent with the model FOCs",
            ),
            "verification_accuracy": _metric(
                "verification_accuracy",
                1.0 if verification_ok else 0.0,
                1,
                "rate",
                "produced verification status matches the reference",
            ),
            "solution_order_accuracy": _metric(
                "solution_order_accuracy",
                (1.0 if order_ok else 0.0) * (len(expected_order) if expected_order else 1),
                len(expected_order) if expected_order else 1,
                "rate",
                "produced solution order matches the reference",
            ),
            "condition_accuracy": _metric(
                "condition_accuracy",
                float(len(expected_conditions) - len(missing_conditions)),
                len(expected_conditions),
                "rate",
                "required equilibrium conditions preserved",
            ),
            "unsolvable_detection_accuracy": _metric(
                "unsolvable_detection_accuracy",
                1.0 if status_ok else 0.0,
                1,
                "rate",
                "unsolvable models detected as not_solvable (or solved models derived)",
            ),
            "incorrect_candidate_rejection_rate": _metric(
                "incorrect_candidate_rejection_rate",
                float(rejected_count),
                max(rejected_count, 1),
                "rate",
                "candidates rejected by the symbolic verifier",
            ),
        }

        failures_detail: list[str] = []
        failures_detail.extend(expression_failures)
        if foc_residual_failures:
            failures_detail.append("NONZERO FOC RESIDUAL: " + "; ".join(foc_residual_failures))
        if not verification_ok:
            failures_detail.append(
                f"VERIFICATION MISMATCH: expected {expected_verification!r}, "
                f"produced {produced_verification!r}"
            )
        if not status_ok:
            failures_detail.append(
                f"STATUS MISMATCH: expected {expected_status!r}, produced {produced_status!r}"
            )
        if not order_ok:
            failures_detail.append(
                f"SOLUTION ORDER MISMATCH: expected {expected_order}, produced {produced_order}"
            )
        if missing_conditions:
            failures_detail.append("CONDITIONS DROPPED: " + "; ".join(missing_conditions))
        if not rejection_ok:
            failures_detail.append(
                f"REJECTION MISMATCH: {rejected_count} rejected, expected >= {expected_rejections}"
            )
        if not revision_ok:
            failures_detail.append(
                f"REVISION MISMATCH: {revisions_used} revisions, expected >= {expected_revisions}"
            )
        if not foc_rejection_ok:
            failures_detail.append("no candidate rejected for a nonzero FOC residual")
        if not method_ok:
            failures_detail.append(
                f"METHOD MISMATCH: expected {expected_method!r}, produced {produced_method!r}"
            )
        if br_failures:
            failures_detail.append("BEST RESPONSE MISMATCHES: " + "; ".join(br_failures))
        if br_uncheckable:
            # Never let an inability to check read as success. Either the model
            # FOCs could not be derived, or a produced best response refers to
            # an actor/variable the model does not define; both leave the
            # response unverified rather than correct.
            reason = f"model FOCs could not be derived ({foc_error})" if foc_error else (
                "no matching first-order condition in the model"
            )
            failures_detail.append(
                f"BEST RESPONSE UNVERIFIABLE: {reason}; "
                f"{br_uncheckable} produced best response(s) were not checked"
            )
        if foc_derivation_error is not None:
            failures_detail.append(
                f"FOC UNVERIFIABLE: the model's first-order conditions could not be "
                f"derived ({foc_derivation_error})"
            )
        if selected_candidate_id is None and expected_status not in (
            None,
            "failed",
            "not_solvable",
        ):
            failures_detail.append("no equilibrium candidate was selected")

        status = EvaluatorStatus.failed if failures_detail else EvaluatorStatus.passed

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=(expression_matches / expected_total) if expected_total else None,
            value={
                "execution_status": execution_status,
                "analysis_status": produced_status,
                "solution_order": produced_order,
                "solution_method": produced_method,
                "selected_candidate_id": selected_candidate_id,
                "produced_verification": produced_verification,
                "candidate_expressions": candidate_exprs,
                "candidate_conditions": candidate_conditions,
                "rejected_count": rejected_count,
                "revisions_used": revisions_used,
                "foc_residual_rejected": foc_rejected,
                "expression_failures": expression_failures,
                "foc_residual_failures": foc_residual_failures,
                "missing_conditions": missing_conditions,
                "metrics": metrics,
                "dimension_scores": {
                    k: v
                    for k, v in {
                        "equilibrium_expression_accuracy": (
                            expression_matches / expected_total if expected_total else None
                        ),
                        "foc_accuracy": foc_accuracy,
                        "best_response_accuracy": br_accuracy,
                        "verification_accuracy": 1.0 if verification_ok else 0.0,
                        "solution_order_accuracy": 1.0 if order_ok else 0.0,
                        "condition_accuracy": (
                            (len(expected_conditions) - len(missing_conditions))
                            / len(expected_conditions)
                            if expected_conditions
                            else None
                        ),
                        "unsolvable_detection_accuracy": 1.0 if status_ok else 0.0,
                        "incorrect_candidate_rejection_rate": (
                            rejected_count / max(rejected_count, 1)
                        ),
                    }.items()
                    if v is not None
                },
            },
            status=status,
            explanation="; ".join(failures_detail)
            if failures_detail
            else "all equilibrium checks matched",
            evidence_artifact_ids=[
                exec_env.artifact_id,
                *[e.artifact_id for e in candidate_envs],
            ],
        )


def _symbol_table(model: Any) -> dict[str, Any]:
    table: dict[str, Any] = {}
    for v in model.variables:
        table[v.symbol] = sympy.Symbol(v.symbol)
    for p in model.parameters:
        table[p.symbol] = sympy.Symbol(p.symbol)
    return table


class EquilibriumEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.equilibrium",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic equilibrium evaluator (Phase 6E)",
            provides=["evaluator.equilibrium"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.equilibrium", EquilibriumEvaluator())
