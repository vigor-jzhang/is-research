"""evaluator.model_qualification — deterministic live-model-qualification
evaluator (Phase 7D.1).

Checks the qualification summary against known-answer expectations. The
critical metric `unsafe_model_qualification_rate` must be 0: a primary or
fallback selected from an unqualified candidate is a critical failure.
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


class ModelQualificationEvaluator:
    evaluator_id = "evaluator.model_qualification"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        summaries = [
            e for e in ctx.produced_artifacts if e.artifact_type == "qualification_summary"
        ]
        if not summaries:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no qualification_summary produced for the case",
            )
        s = envelope_payload_dict(max(summaries, key=lambda e: e.created_at))
        status = str(s.get("status") or "")
        primary = s.get("primary")
        fallback = s.get("fallback")
        qualified_models = {str(x) for x in (s.get("qualified_models") or [])}
        candidates = list(s.get("candidates") or [])
        summary_role = str(s.get("role") or "")
        rejection_counts = s.get("rejection_counts") or {}

        qualified_candidate_ids = {
            str(c.get("candidate_id")) for c in candidates if c.get("qualified")
        }

        reference = ctx.case.reference or {}
        expected_status = reference.get("expected_status")
        expected_primary = reference.get("expected_primary")
        expected_fallback = reference.get("expected_fallback")
        expected_qualified_models_raw = reference.get("expected_qualified_models")
        expected_qualified_models = (
            {str(x) for x in expected_qualified_models_raw}
            if expected_qualified_models_raw is not None
            else None
        )
        expected_rejection_kinds = reference.get("expected_rejection_kinds") or {}
        expected_role = reference.get("expected_role")

        failures: list[str] = []

        # ---- core verdict ---------------------------------------------------
        decision_ok = True
        if expected_status is not None and status != expected_status:
            failures.append(f"STATUS: got {status!r}, expected {expected_status!r}")
            decision_ok = False
        if expected_primary is not None and primary != expected_primary:
            failures.append(f"PRIMARY: got {primary!r}, expected {expected_primary!r}")
            decision_ok = False
        if expected_fallback is not None and fallback != expected_fallback:
            failures.append(f"FALLBACK: got {fallback!r}, expected {expected_fallback!r}")
            decision_ok = False
        if expected_role is not None and summary_role != expected_role:
            failures.append(f"ROLE: got {summary_role!r}, expected {expected_role!r}")

        # ---- qualified models ------------------------------------------------
        if expected_qualified_models is not None:
            missing = expected_qualified_models - qualified_models
            extra = qualified_models - expected_qualified_models
            if missing or extra:
                failures.append(
                    f"QUALIFIED: expected {sorted(expected_qualified_models)}, "
                    f"got {sorted(qualified_models)}"
                )

        # ---- rejection classification ----------------------------------------
        for candidate_id, expected_kinds in expected_rejection_kinds.items():
            c = next((x for x in candidates if str(x.get("candidate_id")) == candidate_id), None)
            if c is None:
                failures.append(f"REJECTION: candidate {candidate_id!r} not evaluated")
                continue
            actual_kinds = {str(k) for k in (c.get("rejection_kinds") or [])}
            expected_set = {str(k) for k in expected_kinds}
            if actual_kinds != expected_set:
                failures.append(
                    f"REJECTION {candidate_id}: expected {sorted(expected_set)}, "
                    f"got {sorted(actual_kinds)}"
                )

        # ---- unsafe model qualification (critical) ---------------------------
        unsafe = 0
        for selected in (primary, fallback):
            if selected is None:
                continue
            if selected not in qualified_models:
                unsafe += 1
                failures.append(f"UNSAFE: selected model {selected!r} is not qualified")
            if selected not in qualified_candidate_ids:
                unsafe += 1
                failures.append(f"UNSAFE: selected candidate {selected!r} was rejected")
        # a qualified-without-fallback must not have a fallback; no_qualified
        # must not select anything
        if status == "no_qualified_model" and (primary is not None or fallback is not None):
            unsafe += 1
            failures.append("UNSAFE: no_qualified_model but a model was selected")
        if status == "qualified_without_fallback" and fallback is not None:
            unsafe += 1
            failures.append("UNSAFE: qualified_without_fallback but a fallback was selected")

        # role isolation: every candidate is for the summary role
        role_ok = all(str(c.get("role") or "") == summary_role for c in candidates)

        # deterministic tie-break
        tiebreak_ok = True
        if reference.get("expected_tiebreak") is not None:
            if primary != reference.get("expected_tiebreak"):
                failures.append(
                    f"TIE-BREAK: expected {reference.get('expected_tiebreak')!r}, got {primary!r}"
                )
                tiebreak_ok = False

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "model_qualification",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "qualification_decision_accuracy": _metric(
                "qualification_decision_accuracy",
                1.0 if decision_ok else 0.0,
                1,
                "rate",
                "role status and primary/fallback selection match expectations",
            ),
            "rejection_classification_accuracy": _metric(
                "rejection_classification_accuracy",
                1.0 if not failures or not expected_rejection_kinds else 0.0,
                1,
                "rate",
                "structured rejection kinds match expectations",
            ),
            "role_isolation_accuracy": _metric(
                "role_isolation_accuracy",
                1.0 if role_ok else 0.0,
                1,
                "rate",
                "qualification considers only the requested role's evidence",
            ),
            "deterministic_tiebreak_accuracy": _metric(
                "deterministic_tiebreak_accuracy",
                1.0 if tiebreak_ok else 0.0,
                1 if reference.get("expected_tiebreak") is not None else 0,
                "rate",
                "ties between qualified models break deterministically",
            ),
            "unsafe_model_qualification_rate": _metric(
                "unsafe_model_qualification_rate",
                float(unsafe),
                max(unsafe, 1),
                "rate",
                "any unqualified model selected as primary or fallback (critical)",
            ),
        }

        result_status = EvaluatorStatus.failed if failures else EvaluatorStatus.passed
        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=1.0 if not failures else 0.0,
            value={
                "status": status,
                "primary": primary,
                "fallback": fallback,
                "unsafe_model_qualification_count": unsafe,
                "rejection_counts": rejection_counts,
                "metrics": metrics,
                "dimension_scores": {
                    "qualification_decision_accuracy": float(decision_ok),
                    "rejection_classification_accuracy": (
                        1.0 if not failures or not expected_rejection_kinds else 0.0
                    ),
                    "role_isolation_accuracy": float(role_ok),
                    "deterministic_tiebreak_accuracy": float(tiebreak_ok),
                    "unsafe_model_qualification_rate": float(unsafe),
                },
            },
            status=result_status,
            explanation="; ".join(failures) if failures else "all qualification checks matched",
            evidence_artifact_ids=[e.artifact_id for e in summaries],
        )


class ModelQualificationEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.model_qualification",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic live-model-qualification evaluator (Phase 7D.1)",
            provides=["evaluator.model_qualification"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.model_qualification", ModelQualificationEvaluator())
