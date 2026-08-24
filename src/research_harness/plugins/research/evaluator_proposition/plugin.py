"""evaluator.proposition — deterministic proposition evaluator (Phase 6F).

Evaluates produced Phase 3D Proposition + PropositionVerification artifacts
against known-answer references. Ground truth is recomputed by the evaluator
itself: equilibrium candidate expressions are re-derived from the produced
artifacts, monotonicity signs and equality differences are recomputed
symbolically, and support ids are re-checked against the produced
comparative statics of the same candidate.

Critical deterministic failures:
- incorrect proposition marked verified (or valid proposition rejected)
- wrong symbolic derivative / wrong sign for a monotonicity claim
- missing required conditions
- hallucinated support ids
- invalid equality accepted (or valid equality rejected)
"""

from __future__ import annotations

from typing import Any

import sympy

from research_harness.contracts.evaluator import EvaluatorContext, envelope_payload_dict
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.plugins.research.evaluator_comparative_statics.plugin import (
    condition_equivalent,
    defensible_sign,
    sympy_equivalent,
)
from research_harness.research.schemas.evaluation import (
    EvaluatorCategory,
    EvaluatorResult,
    EvaluatorStatus,
)
from research_harness.research.schemas.model import FormalAnalyticalModel
from research_harness.research.symbolic import parse_sympy


class PropositionEvaluator:
    evaluator_id = "evaluator.proposition"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        prop_envs = [e for e in ctx.produced_artifacts if e.artifact_type == "proposition"]
        if not prop_envs:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no proposition produced for the case",
            )
        props = sorted(
            ((e.artifact_id, envelope_payload_dict(e)) for e in prop_envs),
            key=lambda item: item[1].get("created_at") or "",
        )
        verifications = [
            envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "proposition_verification"
        ]
        verification_by_prop = {v.get("proposition_id"): v.get("status") for v in verifications}
        statics = [
            (e.artifact_id, envelope_payload_dict(e))
            for e in ctx.produced_artifacts
            if e.artifact_type == "comparative_static"
        ]
        static_by_id = dict(statics)

        model_envs = [
            e for e in ctx.produced_artifacts if e.artifact_type == "formal_analytical_model"
        ]
        candidate_envs = [
            e for e in ctx.produced_artifacts if e.artifact_type == "equilibrium_candidate"
        ]
        table: dict[str, Any] = {}
        candidate_exprs: dict[str, str] = {}
        candidate_id: str | None = None
        if model_envs:
            model_env = max(model_envs, key=lambda e: e.created_at)
            model = model_env.parse_payload(FormalAnalyticalModel)
            table = {v.symbol: sympy.Symbol(v.symbol) for v in model.variables}
            table.update({p.symbol: sympy.Symbol(p.symbol) for p in model.parameters})
        if candidate_envs:
            candidate_env = max(candidate_envs, key=lambda e: e.created_at)
            candidate_id = candidate_env.artifact_id
            candidate = envelope_payload_dict(candidate_env)
            candidate_exprs = {
                e.get("variable"): e.get("expression", {}).get("expression", "")
                for e in candidate.get("expressions") or []
            }
        candidate_map: dict[Any, Any] = {}
        for var, expr in candidate_exprs.items():
            try:
                candidate_map[sympy.Symbol(var)] = parse_sympy(expr, table)
            except Exception:  # noqa: BLE001
                continue

        reference = ctx.case.reference or {}
        expected_propositions = list(reference.get("expected_propositions") or [])
        if not expected_propositions:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="reference defines no expected_propositions",
            )

        verification_matches = 0
        monotonicity_ok = 0
        monotonicity_total = 0
        equality_ok = 0
        equality_total = 0
        condition_ok = 0
        condition_total = 0
        support_ok = 0
        rejection_value = 0
        rejection_count = 0
        failures_detail: list[str] = []
        index = 0

        for index, expected in enumerate(expected_propositions):
            if index >= len(props):
                failures_detail.append(
                    f"PROPOSITION {index}: expected entry has no produced proposition"
                )
                continue
            prop_id, prop = props[index]
            claim_type = str(expected.get("claim_type") or "monotonicity")
            expected_verification = expected.get("expected_verification")
            expected_conditions = [str(c) for c in expected.get("expected_conditions") or []]
            expected_rejected = bool(expected.get("expected_rejected") or False)

            produced_verification = verification_by_prop.get(prop_id)
            if produced_verification == expected_verification:
                verification_matches += 1
            else:
                failures_detail.append(
                    f"PROPOSITION {index}: verification {produced_verification!r}, "
                    f"expected {expected_verification!r}"
                )

            # ---- support references (hallucinated ids) ------------------
            support_ids = [str(s) for s in prop.get("supporting_static_ids") or []]
            valid_support = True
            for sid in support_ids:
                s = static_by_id.get(sid)
                if s is None or s.get("equilibrium_candidate_id") != candidate_id:
                    valid_support = False
            expected_accepted = expected_verification in (
                "verified",
                "conditionally_verified",
            )
            if expected_accepted:
                if not valid_support:
                    failures_detail.append(
                        f"PROPOSITION {index}: hallucinated support id(s) accepted: {support_ids}"
                    )
                else:
                    support_ok += 1
            else:
                # a rejected proposition may be rejected precisely for its
                # hallucinated support; the rejection covers the defect
                support_ok += 1

            # ---- claim-specific recomputation ----------------------------
            if claim_type == "monotonicity":
                monotonicity_total += 1
                outcome = str(expected.get("outcome_variable") or "")
                param = str(expected.get("parameter") or "")
                expected_sign = expected.get("expected_sign")
                if outcome not in candidate_exprs:
                    failures_detail.append(
                        f"PROPOSITION {index}: outcome {outcome!r} not in produced candidate"
                    )
                    continue
                try:
                    expr = parse_sympy(candidate_exprs[outcome], table)
                    deriv = sympy.simplify(sympy.diff(expr, sympy.Symbol(param)))
                except Exception as e:  # noqa: BLE001
                    failures_detail.append(
                        f"PROPOSITION {index}: derivative recomputation failed: {e}"
                    )
                    continue
                defensible = defensible_sign(deriv, param)
                produced_sign = prop.get("expected_sign")
                matching_static = next(
                    (
                        s
                        for _, s in statics
                        if s.get("outcome_variable") == outcome and s.get("parameter") == param
                    ),
                    None,
                )
                if matching_static is None:
                    failures_detail.append(
                        f"PROPOSITION {index}: no produced comparative static for "
                        f"d{outcome}/d{param}"
                    )
                    continue
                static_sign = matching_static.get("sign")
                static_derivative = (matching_static.get("derivative_expression") or {}).get(
                    "expression", ""
                )
                if not sympy_equivalent(str(deriv), static_derivative, table):
                    failures_detail.append(
                        f"PROPOSITION {index}: wrong symbolic derivative d{outcome}/d{param}: "
                        f"static {static_derivative!r} not equivalent to recomputed {deriv}"
                    )
                if expected_verification == "conditionally_verified":
                    # the claim is only defensible under the declared conditions
                    if defensible != "ambiguous" or static_sign != "ambiguous":
                        failures_detail.append(
                            f"PROPOSITION {index}: conditional claim d{outcome}/d{param} "
                            f"on a globally provable sign (recomputed {defensible!r}, "
                            f"static {static_sign!r})"
                        )
                    elif not prop.get("conditions"):
                        failures_detail.append(
                            f"PROPOSITION {index}: conditionally verified without conditions"
                        )
                    else:
                        monotonicity_ok += 1
                elif expected_verification == "verified":
                    if (
                        defensible != expected_sign
                        or static_sign != expected_sign
                        or produced_sign != expected_sign
                    ):
                        failures_detail.append(
                            f"PROPOSITION {index}: d{outcome}/d{param} recomputed "
                            f"{defensible!r}, static {static_sign!r}, claimed "
                            f"{produced_sign!r}; expected {expected_sign!r}"
                        )
                    else:
                        monotonicity_ok += 1
                else:
                    # rejected: the rejection must be justified by a wrong
                    # claim, missing conditions, or hallucinated support
                    if not valid_support:
                        monotonicity_ok += 1
                    elif static_sign == "ambiguous":
                        if prop.get("conditions"):
                            failures_detail.append(
                                f"PROPOSITION {index}: failed despite declared conditions "
                                f"on ambiguous static d{outcome}/d{param}"
                            )
                        else:
                            monotonicity_ok += 1
                    elif produced_sign == static_sign:
                        failures_detail.append(
                            f"PROPOSITION {index}: rejected despite claimed sign "
                            f"{produced_sign!r} matching static {static_sign!r}"
                        )
                    else:
                        monotonicity_ok += 1
            elif claim_type == "equality":
                equality_total += 1
                expected_equality = expected.get("expected_equality")
                math_form = (prop.get("mathematical_form") or {}).get("expression") or ""
                diff: Any | None = None
                if "=" in math_form and "==" not in math_form:
                    lhs, _, rhs = math_form.partition("=")
                    try:
                        diff = sympy.simplify(
                            sympy.cancel(
                                sympy.together(
                                    parse_sympy(lhs, table).subs(candidate_map)
                                    - parse_sympy(rhs, table).subs(candidate_map)
                                )
                            )
                        )
                        holds = bool(diff == 0)
                    except Exception as e:  # noqa: BLE001
                        failures_detail.append(
                            f"PROPOSITION {index}: equality recomputation failed: {e}"
                        )
                        continue
                else:
                    holds = None
                    failures_detail.append(
                        f"PROPOSITION {index}: equality requires a mathematical_form 'lhs = rhs'"
                    )
                if holds is not None:
                    if (
                        produced_verification in ("verified", "conditionally_verified")
                        and not holds
                    ):
                        failures_detail.append(
                            f"PROPOSITION {index}: invalid equality accepted: {math_form!r} "
                            f"does not hold at the equilibrium (difference {diff})"
                        )
                    elif produced_verification == "failed" and holds:
                        failures_detail.append(
                            f"PROPOSITION {index}: valid equality rejected: {math_form!r} "
                            f"holds at the equilibrium"
                        )
                    if holds == bool(expected_equality):
                        equality_ok += 1
                    else:
                        failures_detail.append(
                            f"PROPOSITION {index}: equality {math_form!r} holds={holds}, "
                            f"expected {expected_equality}"
                        )
            elif claim_type == "threshold":
                if produced_verification != "failed":
                    failures_detail.append(
                        f"PROPOSITION {index}: threshold claim not rejected (produced "
                        f"{produced_verification!r})"
                    )
            else:
                failures_detail.append(
                    f"PROPOSITION {index}: unknown expected claim_type {claim_type!r}"
                )

            # ---- conditions ----------------------------------------------
            if expected_conditions:
                condition_total += 1
                produced_conditions = [str(c) for c in prop.get("conditions") or []]
                missing = [
                    cond
                    for cond in expected_conditions
                    if not any(condition_equivalent(cond, p, table) for p in produced_conditions)
                ]
                if missing:
                    failures_detail.append(
                        f"PROPOSITION {index}: conditions dropped: missing {missing} "
                        f"(produced {produced_conditions})"
                    )
                else:
                    condition_ok += 1
            if expected_verification == "conditionally_verified":
                produced_conditions = [str(c) for c in prop.get("conditions") or []]
                if not produced_conditions:
                    failures_detail.append(
                        f"PROPOSITION {index}: conditionally verified without conditions"
                    )

            # ---- rejection -----------------------------------------------
            if expected_rejected:
                rejection_count += 1
                if produced_verification == "failed":
                    rejection_value += 1

        total = len(expected_propositions)
        status = EvaluatorStatus.failed if failures_detail else EvaluatorStatus.passed

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "proposition",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "proposition_verification_accuracy": _metric(
                "proposition_verification_accuracy",
                float(verification_matches),
                total,
                "rate",
                "produced verification status matches the reference",
            ),
            "monotonicity_accuracy": _metric(
                "monotonicity_accuracy",
                float(monotonicity_ok),
                monotonicity_total,
                "rate",
                "recomputed monotonicity sign matches the reference and produced statics",
            ),
            "equality_accuracy": _metric(
                "equality_accuracy",
                float(equality_ok),
                equality_total,
                "rate",
                "recomputed equality at the equilibrium matches the reference",
            ),
            "condition_accuracy": _metric(
                "condition_accuracy",
                float(condition_ok),
                condition_total,
                "rate",
                "required proposition conditions preserved",
            ),
            "support_reference_accuracy": _metric(
                "support_reference_accuracy",
                float(support_ok),
                total,
                "rate",
                "supporting static ids exist for the same equilibrium candidate",
            ),
            "incorrect_proposition_rejection_rate": _metric(
                "incorrect_proposition_rejection_rate",
                float(rejection_value),
                max(rejection_count, 1),
                "rate",
                "incorrect propositions rejected by the symbolic verifier",
            ),
        }

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=(verification_matches / total) if total else None,
            value={
                "produced_propositions": [
                    {
                        "claim_type": p.get("claim_type"),
                        "outcome_variable": p.get("outcome_variable"),
                        "parameter": p.get("parameter"),
                        "expected_sign": p.get("expected_sign"),
                        "mathematical_form": (p.get("mathematical_form") or {}).get("expression"),
                        "conditions": p.get("conditions") or [],
                        "supporting_static_ids": p.get("supporting_static_ids") or [],
                        "verification": verification_by_prop.get(pid),
                    }
                    for pid, p in props
                ],
                "metrics": metrics,
                "dimension_scores": {
                    k: v
                    for k, v in {
                        "proposition_verification_accuracy": (
                            verification_matches / total if total else None
                        ),
                        "monotonicity_accuracy": (
                            monotonicity_ok / monotonicity_total if monotonicity_total else None
                        ),
                        "equality_accuracy": (
                            equality_ok / equality_total if equality_total else None
                        ),
                        "condition_accuracy": (
                            condition_ok / condition_total if condition_total else None
                        ),
                        "support_reference_accuracy": (support_ok / total if total else None),
                        "incorrect_proposition_rejection_rate": (
                            rejection_value / max(rejection_count, 1)
                        ),
                    }.items()
                    if v is not None
                },
            },
            status=status,
            explanation="; ".join(failures_detail)
            if failures_detail
            else "all proposition checks matched",
            evidence_artifact_ids=[e.artifact_id for e in prop_envs],
        )


class PropositionEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.proposition",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic proposition evaluator (Phase 6F)",
            provides=["evaluator.proposition"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.proposition", PropositionEvaluator())
