"""evaluator.live_quality_critic — deterministic live-quality evaluator for the
critic role (Phase 7D.0).

Reference cases contain known INJECTED DEFECTS (per-category, optionally with
expected severity). The evaluator measures how well the real critic service's
model output detects them: defect_recall, false_positive_rate, severity_accuracy,
required-category coverage, actionable_revision_rate, structured-output success.
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

_ARTIFACT_BY_TASK = {
    "mechanism_critique": "mechanism_critique",
    "model_critique": "model_specification_critique",
    "proposition_critique": "proposition_critique",
    "results_critique": "results_critique",
    "manuscript_critique": "manuscript_critique",
}

_RECOMMENDATIONS_BY_TASK = {
    "mechanism_critique": "revision_recommendations",
    "model_critique": "revision_recommendations",
    "proposition_critique": "recommendations",
    "results_critique": "recommendations",
    "manuscript_critique": "recommendations",
}


class LiveQualityCriticEvaluator:
    evaluator_id = "evaluator.live_quality_critic"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        reference = ctx.case.reference or {}
        task = str(reference.get("task") or "")
        injected = list(reference.get("injected_defects") or [])
        artifact_type = _ARTIFACT_BY_TASK.get(task)
        rec_field = _RECOMMENDATIONS_BY_TASK.get(task, "revision_recommendations")
        required_recall = float(reference.get("required_defect_recall") or 1.0)

        if artifact_type is None:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation=f"live-quality critic: unknown task {task!r}",
            )

        critiques = [e for e in ctx.produced_artifacts if e.artifact_type == artifact_type]
        if not critiques:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation=f"live-quality critic: no {artifact_type} produced",
            )
        critique = envelope_payload_dict(max(critiques, key=lambda e: e.created_at))
        issues = list(critique.get("issues") or [])
        recommendations = list(critique.get(rec_field) or [])
        failures: list[str] = []

        injected_categories = [str(d.get("category") or "") for d in injected]
        required_categories = {
            str(d.get("category") or "") for d in injected if d.get("required", True) is not False
        }
        issue_categories = [str(i.get("category") or "") for i in issues]

        # defect_recall
        matched = [c for c in injected_categories if c in issue_categories]
        matched_set = set(matched)
        recall = len(matched_set) / len(injected_categories) if injected_categories else 1.0
        missed = [c for c in injected_categories if c not in matched_set]
        if recall < required_recall:
            failures.append(
                f"defect_recall {recall:.3f} < required {required_recall:.3f}; "
                f"missed defects: {sorted(set(missed))}"
            )

        # false_positive_rate (issues not explained by an injected defect)
        false_positives = [c for c in issue_categories if c not in set(injected_categories)]
        fpr = len(false_positives) / len(issue_categories) if issue_categories else 0.0

        # severity accuracy for matched defects with expected severity
        severity_total = 0
        severity_hits = 0
        for d in injected:
            cat = str(d.get("category") or "")
            expected_sev = d.get("severity")
            if cat in matched_set and expected_sev is not None:
                severity_total += 1
                issue_sev = next(
                    (
                        str(i.get("severity") or "")
                        for i in issues
                        if str(i.get("category") or "") == cat
                    ),
                    None,
                )
                if issue_sev == str(expected_sev):
                    severity_hits += 1
        severity_accuracy = severity_hits / severity_total if severity_total else None

        # required-category coverage
        covered = sum(1 for c in required_categories if c in matched_set)
        coverage = covered / len(required_categories) if required_categories else 1.0

        # actionable revision rate
        actionable = len(matched_set) if recommendations else 0
        actionable_rate = actionable / len(matched_set) if matched_set else 0.0
        if matched_set and not recommendations:
            failures.append("actionable: matched defects produced no revision recommendations")

        structured_ok = bool(critique.get("overall_assessment"))
        if not structured_ok:
            failures.append("structured_output: critique missing overall_assessment")

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "live_quality_critic",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "defect_recall": _metric(
                "defect_recall",
                recall,
                max(len(injected_categories), 1),
                "rate",
                "fraction of injected defects detected by the critique",
            ),
            "false_positive_rate": _metric(
                "false_positive_rate",
                fpr,
                max(len(issue_categories), 1),
                "rate",
                "critique issues not attributable to an injected defect",
            ),
            "severity_accuracy": _metric(
                "severity_accuracy",
                severity_accuracy if severity_accuracy is not None else 0.0,
                severity_total if severity_accuracy is not None else 0,
                "rate",
                "matched defects reported with the expected severity",
            ),
            "required_category_coverage": _metric(
                "required_category_coverage",
                coverage,
                max(len(required_categories), 1),
                "rate",
                "fraction of required defect categories detected",
            ),
            "actionable_revision_rate": _metric(
                "actionable_revision_rate",
                actionable_rate,
                max(len(matched_set), 1),
                "rate",
                "matched defects accompanied by revision recommendations",
            ),
            "structured_output_success": _metric(
                "structured_output_success",
                1.0 if structured_ok else 0.0,
                1,
                "rate",
                "critique parsed with the required fields",
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
                "task": task,
                "injected_defects": injected_categories,
                "detected_defects": sorted(matched_set),
                "false_positives": false_positives,
                "metrics": metrics,
                "dimension_scores": {
                    "defect_recall": recall,
                    "false_positive_rate": fpr,
                    "severity_accuracy": severity_accuracy
                    if severity_accuracy is not None
                    else None,
                    "required_category_coverage": coverage,
                    "actionable_revision_rate": actionable_rate,
                    "structured_output_success": float(structured_ok),
                },
            },
            status=status,
            explanation="; ".join(failures) if failures else "all critic defect checks matched",
            evidence_artifact_ids=[e.artifact_id for e in critiques],
        )


class LiveQualityCriticEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.live_quality_critic",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic live-quality critic evaluator (Phase 7D.0)",
            provides=["evaluator.live_quality_critic"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.live_quality_critic", LiveQualityCriticEvaluator())
