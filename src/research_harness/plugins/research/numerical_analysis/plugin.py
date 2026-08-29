"""Phase 3E numerical analysis engine — deterministic evaluation of a
verified equilibrium across parameter scenarios, robustness checks, and
welfare outcomes.

All computation is deterministic Python/SymPy; no randomness, no LLM numbers.
Parameter domains and equilibrium conditions are validated before evaluation;
invalid points are recorded as infeasible, never silently evaluated.
Proposition robustness never overwrites the symbolic proposition artifacts.
Large result tables go to the BlobStore.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.equilibrium import EquilibriumAnalysis, EquilibriumCandidate
from research_harness.research.schemas.model import FormalAnalyticalModel, ModelParameter
from research_harness.research.schemas.numerical import (
    NumericalExperiment,
    NumericalExperimentExecution,
    NumericalResult,
    ParameterSweep,
    RobustnessCheck,
    RobustnessCheckType,
    RobustnessOutcome,
    RobustnessViolation,
    SweepDimension,
    SweepKind,
    WelfareAnalysis,
    WelfareMetric,
)
from research_harness.research.schemas.proposition import Proposition
from research_harness.research.symbolic import parse_sympy

logger = logging.getLogger(__name__)

_ARTIFACT_POINT_THRESHOLD = 500


class NumericalAnalysisService:
    def __init__(
        self,
        artifact_store: Any,
        blob_store: Any | None = None,
        model_role: str = "reasoning",
        max_points: int = 10000,
        artifact_point_threshold: int = _ARTIFACT_POINT_THRESHOLD,
    ) -> None:
        self._store = artifact_store
        self._blobs = blob_store
        self._model_role = model_role
        self._max_points = max_points
        self._artifact_threshold = artifact_point_threshold

    @property
    def service_id(self) -> str:
        return "research.numerical_analysis"

    async def run(self, equilibrium_analysis_id: str) -> str:
        """Run numerical experiments for the selected candidate of an analysis.

        Returns the NumericalExperimentExecution artifact id.
        """
        # Idempotency: reuse a completed run for same analysis + role
        existing = await self._store.list(artifact_type="numerical_experiment_execution")
        for env in existing:
            try:
                ex = NumericalExperimentExecution.model_validate(env.payload)
                if (
                    ex.model_role == self._model_role
                    and ex.completed_at is not None
                    and ex.results_created > 0
                ):
                    a_env = await self._store.get(equilibrium_analysis_id)
                    a = a_env.parse_payload(EquilibriumAnalysis)
                    if ex.equilibrium_candidate_id == a.selected_candidate_id:
                        return env.artifact_id
            except Exception:
                continue

        a_env = await self._store.get(equilibrium_analysis_id)
        analysis = a_env.parse_payload(EquilibriumAnalysis)
        candidate_id = analysis.selected_candidate_id
        if candidate_id is None:
            raise ValueError(
                f"EquilibriumAnalysis {equilibrium_analysis_id} has no selected candidate"
            )
        c_env = await self._store.get(candidate_id)
        candidate = c_env.parse_payload(EquilibriumCandidate)
        m_env = await self._store.get(candidate.model_id)
        model = m_env.parse_payload(FormalAnalyticalModel)

        started = datetime.now(UTC)
        import sympy

        exec_id = str(uuid.uuid4())
        exec_record = NumericalExperimentExecution(
            model_id=candidate.model_id,
            equilibrium_candidate_id=candidate_id,
            engine="sympy+python",
            engine_version=sympy.__version__,
            seed=0,
            failures=[],
            counts={"max_points": self._max_points},
            model_role=self._model_role,
            started_at=started,
        )

        table = {v.symbol: v for v in model.variables}
        table.update({p.symbol: p for p in model.parameters})
        params = {p.symbol: p for p in model.parameters}
        candidate_exprs = {
            e.variable: parse_sympy(e.expression.expression, table) for e in candidate.expressions
        }
        candidate_conditions = [c for e in candidate.expressions for c in e.conditions]
        payoff_exprs = {
            p.actor_id: parse_sympy(p.expression.expression, table) for p in model.payoffs
        }
        outcome_domains = {v.symbol: v for v in model.variables if v.symbol in candidate_exprs}

        # ------------------------------------------------------------------
        # Scenario design (deterministic defaults; LLM not used for numbers)
        # ------------------------------------------------------------------
        sweeps = await self._design_sweeps(candidate_id, candidate.model_id, params)
        exec_record.sweeps_created = len(sweeps)

        # ------------------------------------------------------------------
        # Evaluate points
        # ------------------------------------------------------------------
        results: list[str] = []
        point_rows: list[dict[str, Any]] = []
        all_results: list[NumericalResult] = []
        evaluated = 0
        infeasible = 0

        for sweep in sweeps:
            for point in self._points_of(sweep):
                res = self._evaluate_point(
                    candidate.model_id,
                    candidate_id,
                    sweep,
                    point,
                    params,
                    candidate_exprs,
                    candidate_conditions,
                    outcome_domains,
                )
                res = res.model_copy(update={"experiment_id": exec_id})
                evaluated += 1
                all_results.append(res)
                point_rows.append(
                    {
                        "scenario": res.scenario,
                        "group": res.group,
                        "x_parameter": res.x_parameter,
                        "x_value": res.x_value,
                        "parameters": res.parameter_values,
                        "outcomes": res.outcomes,
                        "feasible": res.feasible,
                        "infeasible_reason": res.infeasible_reason,
                    }
                )
                if not res.feasible:
                    infeasible += 1
                if evaluated <= self._artifact_threshold:
                    r_env = ArtifactEnvelope.create(
                        payload=res,
                        artifact_type="numerical_result",
                        producer="research.numerical_analysis",
                    )
                    await self._store.put(r_env)
                    await self._store.add_provenance(
                        ProvenanceLink(
                            relation=ProvenanceRelation.derived_from,
                            source_artifact_id=candidate_id,
                            target_artifact_id=r_env.artifact_id,
                            producer="research.numerical_analysis",
                        )
                    )
                    results.append(r_env.artifact_id)
                if evaluated >= self._max_points:
                    break
            if evaluated >= self._max_points:
                break

        # Large tables -> BlobStore (JSONL), not uncontrolled SQLite arrays
        blob_ref = None
        if len(point_rows) > self._artifact_threshold and self._blobs is not None:
            payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in point_rows).encode()
            blob_ref = (
                await self._blobs.put_bytes(payload, media_type="application/jsonl")
            ).model_dump()

        exec_record.counts["evaluated_points"] = evaluated
        exec_record.results_created = len(results)
        exec_record.results_infeasible = infeasible

        # ------------------------------------------------------------------
        # Robustness + welfare (reference the execution id)
        # ------------------------------------------------------------------
        robustness_ids = await self._robustness(
            exec_id, candidate.model_id, candidate_id, all_results, params, candidate_exprs
        )
        exec_record.robustness_created = len(robustness_ids)
        welfare_ids = await self._welfare(
            exec_id, candidate.model_id, candidate_id, model, all_results, params, payoff_exprs
        )
        exec_record.welfare_created = len(welfare_ids)
        exec_record.completed_at = datetime.now(UTC)

        exec_env = ArtifactEnvelope.create(
            payload=exec_record,
            artifact_type="numerical_experiment_execution",
            producer="research.numerical_analysis",
            artifact_id=exec_id,
        )
        await self._store.put(exec_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=candidate_id,
                target_artifact_id=exec_id,
                producer="research.numerical_analysis",
            )
        )

        experiment = NumericalExperiment(
            model_id=candidate.model_id,
            equilibrium_candidate_id=candidate_id,
            sweeps=[s.artifact_id for s in sweeps],
            results=results,
            robustness=robustness_ids,
            welfare=welfare_ids,
            status="completed",
            summary=(
                f"{len(results)} feasible results, {infeasible} infeasible points, "
                f"{len(robustness_ids)} robustness check(s), {len(welfare_ids)} welfare analysis(es)"
            ),
            metadata={"series_blob_ref": blob_ref} if blob_ref else {},
        )
        exp_env = ArtifactEnvelope.create(
            payload=experiment,
            artifact_type="numerical_experiment",
            producer="research.numerical_analysis",
        )
        await self._store.put(exp_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=candidate_id,
                target_artifact_id=exp_env.artifact_id,
                producer="research.numerical_analysis",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=exec_id,
                target_artifact_id=exp_env.artifact_id,
                producer="research.numerical_analysis",
            )
        )
        return exec_id

    # ------------------------------------------------------------------
    # Scenario design
    # ------------------------------------------------------------------

    async def _design_sweeps(
        self,
        candidate_id: str,
        model_id: str,
        params: dict[str, ModelParameter],
    ) -> list[Any]:
        sweep_envs: list[Any] = []
        param_names = sorted(params)
        defaults = self._default_parameters(params)

        baseline = ParameterSweep(
            model_id=model_id,
            equilibrium_candidate_id=candidate_id,
            name="baseline",
            kind=SweepKind.baseline,
            dimensions=[],
            fixed_parameters=defaults,
            total_points=1,
        )
        b_env = ArtifactEnvelope.create(
            payload=baseline,
            artifact_type="parameter_sweep",
            producer="research.numerical_analysis",
        )
        await self._store.put(b_env)
        sweep_envs.append(b_env)

        for pname in param_names:
            lo = defaults[pname] * 0.5
            hi = defaults[pname] * 2.0
            sweep = ParameterSweep(
                model_id=model_id,
                equilibrium_candidate_id=candidate_id,
                name=f"sweep {pname}",
                kind=SweepKind.sweep_1d,
                dimensions=[SweepDimension(parameter=pname, start=lo, end=hi, steps=7)],
                fixed_parameters={k: v for k, v in defaults.items() if k != pname},
                total_points=7,
            )
            s_env = ArtifactEnvelope.create(
                payload=sweep,
                artifact_type="parameter_sweep",
                producer="research.numerical_analysis",
            )
            await self._store.put(s_env)
            sweep_envs.append(s_env)

            # Domain-edge probe: pushes the parameter toward its lower domain
            # bound so infeasible points are exercised and recorded.
            probe = ParameterSweep(
                model_id=model_id,
                equilibrium_candidate_id=candidate_id,
                name=f"probe {pname}",
                kind=SweepKind.low_high,
                dimensions=[
                    SweepDimension(parameter=pname, start=0.0, end=defaults[pname] * 1.5, steps=4)
                ],
                fixed_parameters={k: v for k, v in defaults.items() if k != pname},
                total_points=4,
            )
            p_env = ArtifactEnvelope.create(
                payload=probe,
                artifact_type="parameter_sweep",
                producer="research.numerical_analysis",
            )
            await self._store.put(p_env)
            sweep_envs.append(p_env)

        if len(param_names) >= 2:
            p1, p2 = param_names[:2]
            grid = ParameterSweep(
                model_id=model_id,
                equilibrium_candidate_id=candidate_id,
                name="grid",
                kind=SweepKind.grid,
                dimensions=[
                    SweepDimension(
                        parameter=p1, start=defaults[p1] * 0.5, end=defaults[p1] * 1.5, steps=4
                    ),
                    SweepDimension(
                        parameter=p2, start=defaults[p2] * 0.5, end=defaults[p2] * 1.5, steps=4
                    ),
                ],
                fixed_parameters={k: v for k, v in defaults.items() if k not in (p1, p2)},
                total_points=16,
            )
            g_env = ArtifactEnvelope.create(
                payload=grid,
                artifact_type="parameter_sweep",
                producer="research.numerical_analysis",
            )
            await self._store.put(g_env)
            sweep_envs.append(g_env)
        return sweep_envs

    def _default_parameters(self, params: dict[str, ModelParameter]) -> dict[str, float]:
        """Deterministic baseline defaults: 1.0, or 10.0 for demand-like names."""
        defaults: dict[str, float] = {}
        for name, p in params.items():
            if "demand" in p.meaning.lower() or name == "a":
                defaults[name] = 10.0
            else:
                defaults[name] = 1.0
        return defaults

    def _points_of(self, sweep_env: Any) -> list[dict[str, float]]:
        import numpy as np  # type: ignore[import-untyped]

        sweep = sweep_env.parse_payload(ParameterSweep)
        base = dict(sweep.fixed_parameters)
        if sweep.kind == SweepKind.baseline:
            return [base]
        if sweep.kind in (SweepKind.sweep_1d, SweepKind.low_high):
            dim = sweep.dimensions[0]
            vals = np.linspace(dim.start, dim.end, dim.steps)
            return [{**base, dim.parameter: round(float(v), 12)} for v in vals]
        dims = sweep.dimensions
        v1 = np.linspace(dims[0].start, dims[0].end, dims[0].steps)
        v2 = np.linspace(dims[1].start, dims[1].end, dims[1].steps)
        return [
            {
                **base,
                dims[0].parameter: round(float(x), 12),
                dims[1].parameter: round(float(y), 12),
            }
            for x in v1
            for y in v2
        ]

    # ------------------------------------------------------------------
    # Point evaluation
    # ------------------------------------------------------------------

    def _evaluate_point(
        self,
        model_id: str,
        candidate_id: str,
        sweep_env: Any,
        point: dict[str, float],
        params: dict[str, ModelParameter],
        candidate_exprs: dict[str, Any],
        candidate_conditions: list[str],
        outcome_domains: dict[str, Any],
    ) -> NumericalResult:
        import sympy

        sweep = sweep_env.parse_payload(ParameterSweep)

        # 1. Parameter domain validation
        for name, value in point.items():
            p = params.get(name)
            if p is not None:
                ok, reason = self._domain_ok(p, value)
                if not ok:
                    return self._infeasible(
                        model_id,
                        candidate_id,
                        sweep_env,
                        point,
                        f"parameter {name}={value} violates domain: {reason}",
                    )
        # 2. Equilibrium condition enforcement (e.g. denominator != 0)
        subs = {sympy.Symbol(k): float(v) for k, v in point.items()}
        held: list[str] = []
        for cond in candidate_conditions:
            truth = self._eval_condition(cond, {str(k): v for k, v in subs.items()})
            if truth is False:
                return self._infeasible(
                    model_id,
                    candidate_id,
                    sweep_env,
                    point,
                    f"equilibrium condition violated: {cond}",
                )
            if truth is True:
                held.append(cond)
        # 3. Outcome evaluation + outcome domain check
        outcomes: dict[str, float] = {}
        for var, expr in candidate_exprs.items():
            try:
                val = float(sympy.N(expr.subs(subs), 12))  # type: ignore[arg-type]
            except Exception as e:  # noqa: BLE001
                return self._infeasible(
                    model_id, candidate_id, sweep_env, point, f"outcome {var} not evaluable: {e}"
                )
            outcomes[var] = round(val, 9)
            dom = outcome_domains.get(var)
            if dom is not None:
                ok, reason = self._domain_ok(dom, val)
                if not ok:
                    return self._infeasible(
                        model_id,
                        candidate_id,
                        sweep_env,
                        point,
                        f"outcome {var}={val:.4f} violates domain: {reason}",
                    )
        x_param = sweep.dimensions[0].parameter if sweep.dimensions else None
        return NumericalResult(
            model_id=model_id,
            equilibrium_candidate_id=candidate_id,
            experiment_id="",
            sweep_id=sweep_env.artifact_id,
            scenario="baseline"
            if sweep.kind == SweepKind.baseline
            else "probe"
            if sweep.kind == SweepKind.low_high
            else "sweep"
            if sweep.kind == SweepKind.sweep_1d
            else "grid",
            group=(
                "baseline"
                if sweep.kind == SweepKind.baseline
                else f"{sweep.dimensions[0].parameter}={point[sweep.dimensions[0].parameter]:.4g}"
                if sweep.dimensions
                else None
            ),
            x_parameter=x_param,
            x_value=point.get(x_param) if x_param else None,
            parameter_values={k: round(float(v), 9) for k, v in point.items()},
            outcomes=outcomes,
            feasible=True,
            conditions=held,
        )

    def _infeasible(
        self, model_id: str, candidate_id: str, sweep_env: Any, point: dict[str, float], reason: str
    ) -> NumericalResult:
        return NumericalResult(
            model_id=model_id,
            equilibrium_candidate_id=candidate_id,
            experiment_id="",
            sweep_id=sweep_env.artifact_id,
            scenario="invalid",
            parameter_values={k: round(float(v), 9) for k, v in point.items()},
            outcomes={},
            feasible=False,
            infeasible_reason=reason,
        )

    def _domain_ok(self, obj: Any, value: float) -> tuple[bool, str]:
        """Validate a value against a domain string like R, R_+, R_-, [0,1], > 0."""
        domain = getattr(obj, "domain", "R") or "R"
        d = domain.strip()
        if d == "R":
            return True, ""
        if d in ("R_+", "R+", "> 0"):
            return (True, "") if value > 0 else (False, "requires positive value")
        if d in ("R_-", "R-", "< 0"):
            return (True, "") if value < 0 else (False, "requires negative value")
        if d.startswith("[") and d.endswith("]"):
            try:
                lo, hi = d.strip("[]").split(",")
                lo_f, hi_f = float(lo), float(hi)
                if lo_f <= value <= hi_f:
                    return True, ""
                return False, f"requires value in [{lo_f}, {hi_f}]"
            except Exception:  # noqa: BLE001
                return True, ""
        if d.startswith("> "):
            try:
                k = float(d[2:])
                return (True, "") if value > k else (False, f"requires value > {k}")
            except Exception:  # noqa: BLE001
                return True, ""
        return True, ""

    def _eval_condition(self, cond: str, subs: dict[str, Any]) -> bool | None:
        """Evaluate a condition like '2*b != 0' or 'soc < 0' at a point."""
        import sympy

        for op in ("!=", "<=", ">=", "==", "<", ">"):
            if op in cond:
                lhs, _, rhs = cond.partition(op)
                try:
                    lv = float(sympy.N(sympy.sympify(lhs).subs(subs), 12))  # type: ignore[arg-type]
                    rv = float(sympy.N(sympy.sympify(rhs).subs(subs), 12))  # type: ignore[arg-type]
                except Exception:  # noqa: BLE001
                    return None
                if op == "!=":
                    return lv != rv
                if op == "<=":
                    return lv <= rv
                if op == ">=":
                    return lv >= rv
                if op == "==":
                    return lv == rv
                if op == "<":
                    return lv < rv
                if op == ">":
                    return lv > rv
        try:
            v = float(sympy.N(sympy.sympify(cond).subs(subs), 12))  # type: ignore[arg-type]
            return v != 0.0
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    # Robustness
    # ------------------------------------------------------------------

    async def _robustness(
        self,
        exec_id: str,
        model_id: str,
        candidate_id: str,
        numerical_results: list[NumericalResult | str],
        params: dict[str, ModelParameter],
        candidate_exprs: dict[str, Any],
    ) -> list[str]:
        out: list[str] = []

        # 1. Equilibrium-validity range per parameter
        for pname in sorted(params):
            feasible_count = 0
            for item in numerical_results:
                r = item if isinstance(item, NumericalResult) else (await self._store.get(item)).parse_payload(NumericalResult)
                if r.parameter_values.get(pname) is not None and r.feasible:
                    feasible_count += 1
            check = RobustnessCheck(
                model_id=model_id,
                equilibrium_candidate_id=candidate_id,
                experiment_id=exec_id,
                check_type=RobustnessCheckType.parameter_range,
                description=f"equilibrium remains well-defined across the {pname} sweep",
                outcome=(
                    RobustnessOutcome.supported
                    if feasible_count >= 1
                    else RobustnessOutcome.not_testable
                ),
                admissible_points=feasible_count,
                conclusion=f"{feasible_count} feasible point(s) across the {pname} sweep",
            )
            c_env = ArtifactEnvelope.create(
                payload=check,
                artifact_type="robustness_check",
                producer="research.numerical_analysis",
            )
            await self._store.put(c_env)
            out.append(c_env.artifact_id)

        # 2. Proposition support across admissible points
        for env in await self._store.list(artifact_type="proposition"):
            try:
                prop = env.parse_payload(Proposition)
            except Exception:  # noqa: BLE001
                continue
            if prop.model_id != model_id:
                continue
            check = await self._check_proposition_numerically(
                exec_id,
                model_id,
                candidate_id,
                env.artifact_id,
                prop,
                numerical_results,
                params,
                candidate_exprs,
            )
            c_env = ArtifactEnvelope.create(
                payload=check,
                artifact_type="robustness_check",
                producer="research.numerical_analysis",
            )
            await self._store.put(c_env)
            out.append(c_env.artifact_id)
        return out

    async def _check_proposition_numerically(
        self,
        exec_id: str,
        model_id: str,
        candidate_id: str,
        prop_id: str,
        prop: Proposition,
        numerical_results: list[NumericalResult | str],
        params: dict[str, ModelParameter],
        candidate_exprs: dict[str, Any],
    ) -> RobustnessCheck:
        import sympy

        description = f"numerical support of: {prop.statement[:90]}"
        if (
            prop.claim_type.value != "monotonicity"
            or not prop.outcome_variable
            or not prop.parameter
        ):
            return RobustnessCheck(
                model_id=model_id,
                equilibrium_candidate_id=candidate_id,
                experiment_id=exec_id,
                proposition_id=prop_id,
                check_type=RobustnessCheckType.proposition_support,
                description=description,
                outcome=RobustnessOutcome.not_testable,
                admissible_points=0,
                conclusion="proposition type not numerically testable",
            )
        outcome = candidate_exprs.get(prop.outcome_variable)
        if outcome is None:
            return RobustnessCheck(
                model_id=model_id,
                equilibrium_candidate_id=candidate_id,
                experiment_id=exec_id,
                proposition_id=prop_id,
                check_type=RobustnessCheckType.proposition_support,
                description=description,
                outcome=RobustnessOutcome.not_testable,
                admissible_points=0,
                conclusion="outcome variable not in the candidate",
            )
        deriv = sympy.simplify(sympy.diff(outcome, sympy.Symbol(prop.parameter)))
        prop_conditions = list(prop.conditions)
        admissible = 0
        violations: list[RobustnessViolation] = []
        for item in numerical_results:
            r = item if isinstance(item, NumericalResult) else (await self._store.get(item)).parse_payload(NumericalResult)
            if not r.feasible:
                continue
            if prop.parameter not in r.parameter_values:
                continue
            subs = {sympy.Symbol(k): float(v) for k, v in r.parameter_values.items()}
            cond_holds = True
            for cond in prop_conditions:
                t = self._eval_condition(cond, {str(k): v for k, v in subs.items()})
                if t is False:
                    cond_holds = False
                    break
            if not cond_holds:
                continue
            try:
                dval = float(sympy.N(deriv.subs(subs), 12))  # type: ignore[arg-type]
            except Exception:  # noqa: BLE001
                continue
            admissible += 1
            sign = "positive" if dval > 0 else "negative" if dval < 0 else "zero"
            if sign != prop.expected_sign:
                violations.append(
                    RobustnessViolation(
                        parameter_values=dict(r.parameter_values),
                        detail=(
                            f"d{prop.outcome_variable}/d{prop.parameter} = {dval:.6f} "
                            f"({sign}) vs claimed {prop.expected_sign}"
                        ),
                    )
                )
        if admissible == 0:
            outcome_r = RobustnessOutcome.not_testable
            conclusion = "no admissible points to test"
        elif violations:
            outcome_r = RobustnessOutcome.violated
            conclusion = f"{len(violations)} violation(s) across {admissible} admissible points"
        else:
            outcome_r = RobustnessOutcome.supported
            conclusion = f"sign holds at all {admissible} admissible points"
        return RobustnessCheck(
            model_id=model_id,
            equilibrium_candidate_id=candidate_id,
            experiment_id=exec_id,
            proposition_id=prop_id,
            check_type=RobustnessCheckType.proposition_support,
            description=description,
            outcome=outcome_r,
            admissible_points=admissible,
            violations=violations,
            conclusion=conclusion,
        )

    # ------------------------------------------------------------------
    # Welfare
    # ------------------------------------------------------------------

    async def _welfare(
        self,
        exec_id: str,
        model_id: str,
        candidate_id: str,
        model: FormalAnalyticalModel,
        numerical_results: list[NumericalResult | str],
        params: dict[str, ModelParameter],
        payoff_exprs: dict[str, Any],
    ) -> list[str]:
        import sympy

        if not payoff_exprs:
            return []
        baseline = None
        for item in numerical_results:
            r = item if isinstance(item, NumericalResult) else (await self._store.get(item)).parse_payload(NumericalResult)
            if r.feasible and r.scenario == "baseline":
                baseline = r
                break
        if baseline is None:
            return []
        subs = {sympy.Symbol(k): float(v) for k, v in baseline.parameter_values.items()}
        subs.update({sympy.Symbol(k): float(v) for k, v in baseline.outcomes.items()})
        metrics: list[WelfareMetric] = []
        notes: list[str] = []
        for payoff in model.payoffs:
            expr = payoff_exprs[payoff.actor_id]
            try:
                val = float(sympy.N(expr.subs(subs), 12))  # type: ignore[arg-type]
                metrics.append(
                    WelfareMetric(
                        name=f"{payoff.actor_id} payoff ({payoff.objective_type})",
                        actor_id=payoff.actor_id,
                        value=round(val, 9),
                        definition="model payoff evaluated at the equilibrium point",
                    )
                )
            except Exception as e:  # noqa: BLE001
                notes.append(f"payoff of {payoff.actor_id} not evaluable: {e}")
        if not metrics:
            return []
        total = round(sum(m.value for m in metrics), 9)
        notes.append(
            "Only metrics definable from the model payoffs are computed; no fabricated welfare formulas."
        )
        wa = WelfareAnalysis(
            model_id=model_id,
            equilibrium_candidate_id=candidate_id,
            experiment_id=exec_id,
            scenario="baseline",
            parameter_values=baseline.parameter_values,
            metrics=metrics,
            total_welfare=total,
            notes=notes,
            model_role=self._model_role,
        )
        w_env = ArtifactEnvelope.create(
            payload=wa, artifact_type="welfare_analysis", producer="research.numerical_analysis"
        )
        await self._store.put(w_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=model_id,
                target_artifact_id=w_env.artifact_id,
                producer="research.numerical_analysis",
            )
        )
        return [w_env.artifact_id]


class NumericalAnalysisPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.numerical_analysis",
            version="0.1.0",
            plugin_type="research",
            description="Deterministic numerical experiments, robustness, welfare (Phase 3E)",
            provides=["numerical_analysis.default"],
            requires=["artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        store = ctx.require("artifact_store.default")
        blobs = ctx.try_get("blob_store.default")
        self._service = NumericalAnalysisService(artifact_store=store, blob_store=blobs)
        ctx.register("numerical_analysis.default", self._service)
