"""evaluator.numerical — deterministic numerical-analysis evaluator (Phase 6E).

Evaluates produced Phase 3E artifacts (NumericalExperiment,
NumericalExperimentExecution, NumericalResult, RobustnessCheck,
WelfareAnalysis) against explicit numeric references using deterministic
floating-point tolerances (never rendered-string comparison).
"""

from __future__ import annotations

from typing import Any

from research_harness.contracts.evaluator import EvaluatorContext, envelope_payload_dict
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.schemas.evaluation import (
    EvaluatorCategory,
    EvaluatorResult,
    EvaluatorStatus,
)

_TOLERANCE = 1e-6


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= _TOLERANCE


class NumericalEvaluator:
    evaluator_id = "evaluator.numerical"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        executions = [
            e for e in ctx.produced_artifacts if e.artifact_type == "numerical_experiment_execution"
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
                explanation="no numerical_experiment_execution produced for the case",
            )
        exec_env = max(executions, key=lambda e: e.created_at)
        execution = envelope_payload_dict(exec_env)

        results = [
            envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "numerical_result"
        ]
        robustness = [
            envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "robustness_check"
        ]
        welfare_envs = [e for e in ctx.produced_artifacts if e.artifact_type == "welfare_analysis"]
        welfare = (
            envelope_payload_dict(max(welfare_envs, key=lambda e: e.created_at))
            if welfare_envs
            else None
        )

        reference = ctx.case.reference or {}
        expected_baseline: dict[str, float] = dict(reference.get("expected_baseline") or {})
        expected_sweep = reference.get("expected_sweep")
        expected_grid = reference.get("expected_grid_points")
        expected_infeasible_reasons = list(reference.get("expected_infeasible_reasons") or [])
        expected_infeasible_count = int(reference.get("expected_infeasible_count") or 0)
        expected_propositions: dict[str, str] = dict(reference.get("expected_propositions") or {})
        expected_welfare = reference.get("expected_welfare")
        expected_reproducible = bool(reference.get("expected_reproducible") or False)

        # ---- baseline outcomes ------------------------------------------
        baseline = next(
            (r for r in results if r.get("scenario") == "baseline" and r.get("feasible")),
            None,
        )
        baseline_matches = 0
        baseline_failures: list[str] = []
        if baseline is not None:
            outcomes = baseline.get("outcomes") or {}
            for variable, expected_value in expected_baseline.items():
                produced = outcomes.get(variable)
                if produced is None:
                    baseline_failures.append(f"{variable}: no produced value")
                elif not _close(float(produced), float(expected_value)):
                    baseline_failures.append(
                        f"{variable}: produced {produced}, expected {expected_value}"
                    )
                else:
                    baseline_matches += 1

        # ---- infeasible handling ----------------------------------------
        infeasible = [r for r in results if not r.get("feasible")]

        def _classify(reason: str) -> str:
            if reason.startswith("parameter") and "=" in reason:
                return reason.split("=")[0].strip() + " violates domain"
            return reason.split(":")[0].strip()

        infeasible_reasons = sorted(
            {_classify(r.get("infeasible_reason") or "") for r in infeasible}
        )
        missing_reasons = [
            reason for reason in expected_infeasible_reasons if reason not in infeasible_reasons
        ]
        infeasible_count_ok = (
            expected_infeasible_count == 0 or len(infeasible) >= expected_infeasible_count
        )

        # ---- condition enforcement --------------------------------------
        condition_violations = [
            r
            for r in infeasible
            if "equilibrium condition violated" in (r.get("infeasible_reason") or "")
        ]
        expected_condition_violations = int(reference.get("expected_condition_violations") or 0)
        condition_ok = len(condition_violations) >= expected_condition_violations

        # ---- sweep ------------------------------------------------------
        sweep_ok = True
        sweep_failures: list[str] = []
        if expected_sweep:
            parameter = expected_sweep.get("parameter")
            expected_points = int(expected_sweep.get("points", 0))
            sweep_points = [
                r
                for r in results
                if r.get("scenario") == "sweep"
                and r.get("x_parameter") == parameter
                and r.get("feasible")
            ]
            if len(sweep_points) != expected_points:
                sweep_ok = False
                sweep_failures.append(
                    f"sweep {parameter}: {len(sweep_points)} points, expected {expected_points}"
                )
            if expected_sweep.get("monotonic"):
                first_outcome = None
                for r in sorted(sweep_points, key=lambda r: r.get("x_value") or 0):
                    values = list((r.get("outcomes") or {}).values())
                    if not values:
                        continue
                    current = float(values[0])
                    if first_outcome is not None and current < first_outcome - _TOLERANCE:
                        sweep_ok = False
                        sweep_failures.append(
                            f"sweep {parameter} not monotonic at x={r.get('x_value')}"
                        )
                    first_outcome = current if first_outcome is None else first_outcome

        # ---- grid -------------------------------------------------------
        if expected_grid is not None:
            grid_points = [r for r in results if r.get("scenario") == "grid" and r.get("feasible")]
            if len(grid_points) != int(expected_grid):
                sweep_failures.append(
                    f"grid: {len(grid_points)} feasible points, expected {expected_grid}"
                )

        # ---- robustness -------------------------------------------------
        prop_failures: list[str] = []
        for statement, expected_outcome in expected_propositions.items():
            check = next(
                (
                    c
                    for c in robustness
                    if c.get("check_type") == "proposition_support"
                    and (statement[:40] in (c.get("description") or ""))
                ),
                None,
            )
            if check is None:
                prop_failures.append(f"proposition check missing for {statement[:40]!r}")
                continue
            if (check.get("outcome") or "").lower() != expected_outcome.lower():
                prop_failures.append(
                    f"proposition {statement[:40]!r}: outcome {check.get('outcome')}, "
                    f"expected {expected_outcome}"
                )

        # ---- welfare ----------------------------------------------------
        welfare_ok = True
        welfare_failures: list[str] = []
        if expected_welfare is not None:
            if welfare is None:
                welfare_ok = False
                welfare_failures.append("no welfare analysis produced")
            else:
                expected_metrics = int(expected_welfare.get("metrics", 0))
                if expected_metrics and len(welfare.get("metrics") or []) != expected_metrics:
                    welfare_ok = False
                    welfare_failures.append(
                        f"welfare metrics: {len(welfare.get('metrics') or [])}, "
                        f"expected {expected_metrics}"
                    )
                expected_total = expected_welfare.get("total")
                if expected_total is not None:
                    produced_total = welfare.get("total_welfare")
                    if produced_total is None or not _close(
                        float(produced_total), float(expected_total)
                    ):
                        welfare_ok = False
                        welfare_failures.append(
                            f"welfare total: {produced_total}, expected {expected_total}"
                        )

        # ---- reproducibility --------------------------------------------
        seed = execution.get("seed")
        reproducible = bool(
            execution.get("engine") == "sympy+python" and seed is not None and int(seed) == 0
        )
        reproducible_ok = reproducible if expected_reproducible else True

        # ---- metrics ---------------------------------------------------
        baseline_total = len(expected_baseline)

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "numerical",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "numerical_value_accuracy": _metric(
                "numerical_value_accuracy",
                float(baseline_matches),
                baseline_total,
                "rate",
                "baseline outcomes within the deterministic tolerance",
            ),
            "feasibility_classification_accuracy": _metric(
                "feasibility_classification_accuracy",
                float(len(expected_infeasible_reasons) - len(missing_reasons)),
                len(expected_infeasible_reasons),
                "rate",
                "expected infeasible-reason classes detected",
            ),
            "condition_enforcement_accuracy": _metric(
                "condition_enforcement_accuracy",
                1.0 if condition_ok else 0.0,
                1,
                "rate",
                "expected equilibrium-condition violations enforced as infeasible",
            ),
            "sweep_accuracy": _metric(
                "sweep_accuracy",
                1.0 if sweep_ok else 0.0,
                1,
                "rate",
                "sweep point counts and monotonicity match the reference",
            ),
            "robustness_classification_accuracy": _metric(
                "robustness_classification_accuracy",
                float(len(expected_propositions) - len(prop_failures)),
                len(expected_propositions),
                "rate",
                "proposition support outcomes match the reference",
            ),
            "welfare_accuracy": _metric(
                "welfare_accuracy",
                1.0 if welfare_ok else 0.0,
                1,
                "rate",
                "welfare metrics and total within the deterministic tolerance",
            ),
            "reproducibility_accuracy": _metric(
                "reproducibility_accuracy",
                1.0 if reproducible_ok else 0.0,
                1,
                "rate",
                "deterministic engine metadata (sympy+python, seed 0)",
            ),
        }

        failures_detail: list[str] = []
        failures_detail.extend(baseline_failures)
        if missing_reasons:
            failures_detail.append("MISSING INFEASIBLE REASONS: " + "; ".join(missing_reasons))
        if not infeasible_count_ok:
            failures_detail.append(
                f"INFEASIBLE COUNT: {len(infeasible)}, expected >= {expected_infeasible_count}"
            )
        if not condition_ok:
            failures_detail.append(
                f"CONDITION VIOLATIONS: {len(condition_violations)}, "
                f"expected >= {expected_condition_violations}"
            )
        failures_detail.extend(sweep_failures)
        failures_detail.extend(prop_failures)
        failures_detail.extend(welfare_failures)
        if not reproducible_ok:
            failures_detail.append(
                f"NOT REPRODUCIBLE: engine {execution.get('engine')!r}, "
                f"seed {execution.get('seed')}"
            )

        status = EvaluatorStatus.failed if failures_detail else EvaluatorStatus.passed

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=(baseline_matches / baseline_total) if baseline_total else None,
            value={
                "baseline": (baseline or {}).get("outcomes") if baseline else None,
                "infeasible_reasons": infeasible_reasons,
                "condition_violations": len(condition_violations),
                "engine": execution.get("engine"),
                "seed": execution.get("seed"),
                "baseline_failures": baseline_failures,
                "missing_reasons": missing_reasons,
                "prop_failures": prop_failures,
                "welfare_failures": welfare_failures,
                "metrics": metrics,
                "dimension_scores": {
                    k: v
                    for k, v in {
                        "numerical_value_accuracy": (
                            baseline_matches / baseline_total if baseline_total else None
                        ),
                        "feasibility_classification_accuracy": (
                            (len(expected_infeasible_reasons) - len(missing_reasons))
                            / len(expected_infeasible_reasons)
                            if expected_infeasible_reasons
                            else None
                        ),
                        "condition_enforcement_accuracy": (
                            len(condition_violations) / max(len(condition_violations), 1)
                        ),
                        "sweep_accuracy": 1.0 if sweep_ok else 0.0,
                        "robustness_classification_accuracy": (
                            (len(expected_propositions) - len(prop_failures))
                            / len(expected_propositions)
                            if expected_propositions
                            else None
                        ),
                        "welfare_accuracy": 1.0 if welfare_ok else 0.0,
                        "reproducibility_accuracy": 1.0 if reproducible_ok else 0.0,
                    }.items()
                    if v is not None
                },
            },
            status=status,
            explanation="; ".join(failures_detail)
            if failures_detail
            else "all numerical checks matched",
            evidence_artifact_ids=[exec_env.artifact_id],
        )


class NumericalEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.numerical",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic numerical-analysis evaluator (Phase 6E)",
            provides=["evaluator.numerical"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.numerical", NumericalEvaluator())
