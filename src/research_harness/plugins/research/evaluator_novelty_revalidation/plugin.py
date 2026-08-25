"""evaluator.novelty_revalidation — deterministic novelty-revalidation
evaluator (Phase 7A.1).

Compares the two real novelty runs (baseline vs changed literature) against
known-answer expectations. Any incompatible stale novelty reuse is a
deterministic failure.
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


class NoveltyRevalidationEvaluator:
    evaluator_id = "evaluator.novelty_revalidation"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        reports = [
            e for e in ctx.produced_artifacts if e.artifact_type == "novelty_revalidation_report"
        ]
        if not reports:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no novelty_revalidation_report produced for the case",
            )
        report = envelope_payload_dict(max(reports, key=lambda e: e.created_at))
        report_a = str(report.get("report_a") or "")
        report_b = str(report.get("report_b") or "")
        overall_a = str(report.get("overall_a") or "")
        overall_b = str(report.get("overall_b") or "")
        package_id = str(report.get("package_id") or "")
        assessments_a = {str(a) for a in (report.get("assessments_a") or [])}
        assessments_b = {str(a) for a in (report.get("assessments_b") or [])}
        supersedes_a = bool(report.get("report_b_supersedes_a") or False)

        reference = ctx.case.reference or {}
        expected_changed = reference.get("expected_overall_changed")
        expected_trigger = bool(reference.get("expected_trigger") or False)
        expected_threatened = bool(reference.get("expected_threatened") or False)
        expected_stale_reuse = bool(reference.get("expected_stale_reuse") or False)
        expected_supersession = bool(reference.get("expected_supersession") or False)
        expected_irrelevant = bool(reference.get("expected_irrelevant") or False)

        failures_detail: list[str] = []

        # ---- threat detection ----------------------------------------------
        threat_ok = True
        if expected_changed is not None and overall_b != expected_changed:
            failures_detail.append(
                f"NOVELTY THREAT: changed-literal overall {overall_b!r}, "
                f"expected {expected_changed!r}"
            )
            threat_ok = False

        # ---- revalidation trigger ------------------------------------------
        trigger_ok = True
        if expected_trigger:
            trigger_ok = overall_b != overall_a or expected_threatened
            if not trigger_ok:
                failures_detail.append(
                    "REVALIDATION NOT TRIGGERED: changed literature produced the "
                    "same verdict as baseline"
                )
        else:
            # unchanged / irrelevant -> no re-trigger
            if overall_b != overall_a:
                failures_detail.append(
                    f"FALSE REVALIDATION TRIGGER: literature unchanged but verdict "
                    f"changed {overall_a!r} -> {overall_b!r}"
                )
                trigger_ok = False

        # ---- irrelevant update ---------------------------------------------
        irrelevant_ok = True
        if expected_irrelevant and overall_b != "clear":
            irrelevant_ok = False
            failures_detail.append(
                f"IRRELEVANT UPDATE INVALIDATED NOVELTY: overall {overall_b!r} after "
                "only irrelevant papers"
            )

        # ---- stale reuse ---------------------------------------------------
        stale = 0
        if expected_trigger:
            overlap = assessments_a & assessments_b
            stale = len(overlap)
            if expected_stale_reuse is False and stale:
                failures_detail.append(
                    f"STALE REUSE: {stale} claim assessment(s) reused across reports "
                    "despite changed literature"
                )
            if expected_stale_reuse and stale == 0:
                failures_detail.append(
                    "STALE REUSE EXPECTED but no assessment overlap (unexpected recomputation)"
                )

        # ---- supersession --------------------------------------------------
        supersession_ok = True
        if expected_supersession:
            if not supersedes_a:
                failures_detail.append(
                    f"SUPERSESSION MISSING: {report_a} does not supersede into {report_b}"
                )
                supersession_ok = False

        # ---- provenance version --------------------------------------------
        provenance_ok = True
        if package_id:
            parents_b = {
                str(getattr(p, "source_artifact_id", "")) for p in ctx.provenance.get(report_b, [])
            }
            if package_id not in parents_b:
                failures_detail.append(
                    "PROVENANCE VERSION: changed-literature report not derived from the submission package"
                )
                provenance_ok = False

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "novelty_revalidation",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "revalidation_trigger_accuracy": _metric(
                "revalidation_trigger_accuracy",
                1.0 if trigger_ok else 0.0,
                1,
                "rate",
                "changed literature re-triggers validation; unchanged does not",
            ),
            "stale_reuse_rate": _metric(
                "stale_reuse_rate",
                float(stale),
                max(stale, 1),
                "rate",
                "claim assessments reused across reports despite changed literature",
            ),
            "novelty_threat_detection_accuracy": _metric(
                "novelty_threat_detection_accuracy",
                1.0 if threat_ok else 0.0,
                1,
                "rate",
                "changed-literature report matches the expected threat verdict",
            ),
            "irrelevant_update_accuracy": _metric(
                "irrelevant_update_accuracy",
                float(1.0 if (expected_irrelevant and irrelevant_ok) else 0.0),
                1 if expected_irrelevant else 0,
                "rate",
                "irrelevant new papers do not invalidate novelty",
            ),
            "supersession_accuracy": _metric(
                "supersession_accuracy",
                float(1.0 if (expected_supersession and supersession_ok) else 0.0),
                1 if expected_supersession else 0,
                "rate",
                "new report supersedes the previous one",
            ),
            "provenance_version_accuracy": _metric(
                "provenance_version_accuracy",
                1.0 if provenance_ok else 0.0,
                1,
                "rate",
                "changed-literature report derived from the submission package",
            ),
        }

        status = EvaluatorStatus.failed if failures_detail else EvaluatorStatus.passed

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=1.0 if not failures_detail else 0.0,
            value={
                "overall_baseline": overall_a,
                "overall_changed": overall_b,
                "report_a": report_a,
                "report_b": report_b,
                "stale_assessment_overlap": stale,
                "metrics": metrics,
                "dimension_scores": {
                    k: v
                    for k, v in {
                        "revalidation_trigger_accuracy": float(trigger_ok),
                        "stale_reuse_rate": float(stale / max(stale, 1)) if stale else 0.0,
                        "novelty_threat_detection_accuracy": float(threat_ok),
                        "irrelevant_update_accuracy": float(irrelevant_ok),
                        "supersession_accuracy": (
                            float(supersession_ok) if expected_supersession else None
                        ),
                        "provenance_version_accuracy": float(provenance_ok),
                    }.items()
                    if v is not None
                },
            },
            status=status,
            explanation="; ".join(failures_detail)
            if failures_detail
            else "all novelty-revalidation checks matched",
            evidence_artifact_ids=[e.artifact_id for e in reports],
        )


class NoveltyRevalidationEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.novelty_revalidation",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic novelty-revalidation evaluator (Phase 7A.1)",
            provides=["evaluator.novelty_revalidation"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.novelty_revalidation", NoveltyRevalidationEvaluator())
