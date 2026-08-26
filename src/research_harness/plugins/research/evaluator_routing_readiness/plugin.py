"""evaluator.routing_readiness — deterministic production-routing-readiness
evaluator (Phase 7D.0).

Checks the real readiness assessment against known-answer expectations. The
critical metric `unsafe_production_qualification_rate` must be 0: a role is
never qualified without live-quality evidence, and the assessment's own
`unsafe_production_qualification` flag must never flip.
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


class RoutingReadinessEvaluator:
    evaluator_id = "evaluator.routing_readiness"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        assessments = [
            e for e in ctx.produced_artifacts if e.artifact_type == "routing_readiness_assessment"
        ]
        if not assessments:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no routing_readiness_assessment produced for the case",
            )
        a = envelope_payload_dict(max(assessments, key=lambda e: e.created_at))
        qualified = bool(a.get("qualified") or False)
        reasons = list(a.get("reasons") or [])
        qualified_models = list(a.get("qualified_models") or [])
        fallback_qualified = bool(a.get("fallback_qualified") or False)
        fallback_model = a.get("fallback_model")
        configured_model = a.get("configured_model")
        assessment_role = str(a.get("role") or "")
        unsafe_flag = bool(a.get("unsafe_production_qualification") or False)
        evidence = a.get("evidence") or {}

        reference = ctx.case.reference or {}
        expected_qualified = reference.get("expected_qualified")
        expected_reason = reference.get("expected_reason_substring")
        expected_fallback = reference.get("expected_fallback_qualified")
        expected_fallback_model = reference.get("expected_fallback_model")
        expected_role = reference.get("expected_role")
        expected_qualified_models_raw = reference.get("expected_qualified_models")
        expected_qualified_models = (
            {str(x) for x in expected_qualified_models_raw}
            if expected_qualified_models_raw is not None
            else None
        )

        failures: list[str] = []

        # ---- core verdict ---------------------------------------------------
        decision_ok = True
        if expected_qualified is not None and qualified != expected_qualified:
            failures.append(f"QUALIFIED: got {qualified!r}, expected {expected_qualified!r}")
            decision_ok = False
        if expected_role is not None and assessment_role != expected_role:
            failures.append(f"ROLE: got {assessment_role!r}, expected {expected_role!r}")

        # ---- reasons ---------------------------------------------------------
        if expected_reason and not any(expected_reason.lower() in r.lower() for r in reasons):
            failures.append(
                f"REASONS: expected a reason containing {expected_reason!r}; got {reasons}"
            )

        # ---- fallback -------------------------------------------------------
        if expected_fallback is not None and fallback_qualified != expected_fallback:
            failures.append(
                f"FALLBACK QUALIFIED: got {fallback_qualified!r}, expected {expected_fallback!r}"
            )
        if expected_fallback_model is not None and fallback_model != expected_fallback_model:
            failures.append(
                f"FALLBACK MODEL: got {fallback_model!r}, expected {expected_fallback_model!r}"
            )

        # ---- qualified models ------------------------------------------------
        if expected_qualified_models is not None:
            missing = expected_qualified_models - set(qualified_models)
            extra = set(qualified_models) - expected_qualified_models
            if missing or extra:
                failures.append(
                    f"QUALIFIED MODELS: expected {sorted(expected_qualified_models)}, "
                    f"got {sorted(qualified_models)}"
                )

        # ---- unsafe production qualification (critical) ----------------------
        unsafe = 0
        if unsafe_flag:
            unsafe += 1
            failures.append("UNSAFE: assessment flagged unsafe_production_qualification")
        # a qualified verdict requires live evidence for the configured model
        if qualified:
            if not evidence:
                unsafe += 1
                failures.append("UNSAFE: qualified with no live-quality evidence")
            if configured_model and configured_model not in qualified_models:
                unsafe += 1
                failures.append(
                    f"UNSAFE: qualified but configured model {configured_model!r} "
                    "is not in qualified_models"
                )

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "routing_readiness",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "readiness_decision_accuracy": _metric(
                "readiness_decision_accuracy",
                1.0 if decision_ok else 0.0,
                1,
                "rate",
                "the qualified/not_qualified verdict matches expectations",
            ),
            "qualification_gate_accuracy": _metric(
                "qualification_gate_accuracy",
                1.0
                if not failures
                or (
                    not expected_reason
                    and not expected_fallback
                    and not expected_fallback_model
                    and not expected_qualified_models
                )
                else 0.0,
                1,
                "rate",
                "qualification reasons and fallback requirements match expectations",
            ),
            "role_isolation_accuracy": _metric(
                "role_isolation_accuracy",
                1.0 if expected_role is None or assessment_role == expected_role else 0.0,
                1,
                "rate",
                "the assessment is for the requested role only",
            ),
            "unsafe_production_qualification_rate": _metric(
                "unsafe_production_qualification_rate",
                float(unsafe),
                max(unsafe, 1),
                "rate",
                "any production qualification from unsafe/insufficient evidence (critical)",
            ),
        }

        status = EvaluatorStatus.failed if failures else EvaluatorStatus.passed
        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=1.0 if not failures else 0.0,
            value={
                "qualified": qualified,
                "reasons": reasons,
                "unsafe_production_qualification_count": unsafe,
                "metrics": metrics,
                "dimension_scores": {
                    "readiness_decision_accuracy": float(decision_ok),
                    "qualification_gate_accuracy": (
                        1.0
                        if not failures
                        or (
                            not expected_reason
                            and not expected_fallback
                            and not expected_fallback_model
                            and not expected_qualified_models
                        )
                        else 0.0
                    ),
                    "role_isolation_accuracy": (
                        1.0 if expected_role is None or assessment_role == expected_role else 0.0
                    ),
                    "unsafe_production_qualification_rate": float(unsafe),
                },
            },
            status=status,
            explanation="; ".join(failures) if failures else "routing-readiness checks matched",
            evidence_artifact_ids=[e.artifact_id for e in assessments],
        )


class RoutingReadinessEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.routing_readiness",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic production-routing-readiness evaluator (Phase 7D.0)",
            provides=["evaluator.routing_readiness"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.routing_readiness", RoutingReadinessEvaluator())
