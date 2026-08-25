"""evaluator.gap_selection — deterministic gap-selection evaluator
(Phase 7A.1).

Evaluates produced GapSelection artifacts against known-answer references
without judging a subjective "best gap": the fixture defines the expected
decision (selected gap, status, selected-by, fallback, approval, reuse).
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


class GapSelectionEvaluator:
    evaluator_id = "evaluator.gap_selection"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        reports = [e for e in ctx.produced_artifacts if e.artifact_type == "gap_selection_report"]
        selections = [
            envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "gap_selection"
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
                explanation="no gap_selection_report produced for the case",
            )
        report = envelope_payload_dict(max(reports, key=lambda e: e.created_at))
        gap_ids: dict[str, str] = dict(report.get("gap_ids") or {})
        reverse: dict[str, str] = {v: k for k, v in gap_ids.items()}
        selection_id = report.get("selection_id")
        reuse_selection_id = report.get("reuse_selection_id")
        error = report.get("error")

        selection = next(
            (s for s in selections if s.get("id") == selection_id),
            selections[-1] if selections else {},
        )

        reference = ctx.case.reference or {}
        expected_gap = reference.get("expected_selected_gap")
        expected_status = reference.get("expected_status")
        expected_selected_by = reference.get("expected_selected_by")
        expected_fallback = bool(reference.get("expected_fallback") or False)
        expected_approval = reference.get("expected_approval_required")
        expected_error = bool(reference.get("expected_error") or False)
        expected_reuse = bool(reference.get("expected_reuse") or False)

        failures_detail: list[str] = []
        selected_case = reverse.get(str(selection.get("selected_gap_id") or ""))
        alternatives = [str(a) for a in (selection.get("alternative_gap_ids") or [])]
        alternatives_case = sorted(reverse.get(a, a) for a in alternatives)
        all_gap_keys = sorted(reverse.get(v, v) for v in gap_ids.values())

        # for the expected-error case there is no selection artifact
        valid = True
        rationale_ok = True
        alt_ok = True
        fallback_ok = True
        autonomy_ok = True
        override_ok = True
        expected_alts: list[str] = []
        if not expected_error:
            # selected_gap_validity
            valid = selected_case in set(all_gap_keys)
            if not valid:
                failures_detail.append(
                    f"SELECTED GAP INVALID: {selected_case!r} not in analyzed set {all_gap_keys}"
                )

            # rationale grounding
            rationale = str(selection.get("selection_rationale") or "")
            rationale_ok = bool(rationale.strip())
            if not rationale_ok:
                failures_detail.append("SELECTION RATIONALE EMPTY")

            # alternative consideration: all other gaps listed as alternatives
            expected_alts = sorted(k for k in all_gap_keys if k != selected_case)
            alt_ok = alternatives_case == expected_alts
            if not alt_ok:
                failures_detail.append(
                    f"ALTERNATIVES MISMATCH: expected {expected_alts}, produced {alternatives_case}"
                )

            # expected selected gap
            if expected_gap is not None and selected_case != expected_gap:
                failures_detail.append(
                    f"SELECTED GAP MISMATCH: expected {expected_gap!r}, produced {selected_case!r}"
                )

            # fallback: rank #1 (gap-0) with fallback rationale
            if expected_fallback:
                fallback_ok = selected_case == "gap-0" and "fallback" in rationale.lower()
                if not fallback_ok:
                    failures_detail.append(
                        "FALLBACK NOT APPLIED: invalid model selection did not fall back to rank #1"
                    )

            # autonomy decision
            produced_status = str(selection.get("status") or "")
            if expected_status is not None and produced_status != expected_status:
                failures_detail.append(
                    f"AUTONOMY STATUS MISMATCH: expected {expected_status!r}, produced {produced_status!r}"
                )
                autonomy_ok = False
            if expected_approval is not None:
                produced_approval = bool(selection.get("approval_required"))
                if produced_approval != expected_approval:
                    failures_detail.append(
                        f"APPROVAL REQUIRED MISMATCH: expected {expected_approval}, "
                        f"produced {produced_approval}"
                    )
                    autonomy_ok = False

            # operator override
            produced_by = str(selection.get("selected_by") or "")
            if expected_selected_by is not None and produced_by != expected_selected_by:
                failures_detail.append(
                    f"SELECTED-BY MISMATCH: expected {expected_selected_by!r}, produced {produced_by!r}"
                )
                override_ok = False

        # error case
        error_ok = True
        if expected_error:
            error_ok = bool(error)
            if not error_ok:
                failures_detail.append("EXPECTED ERROR NOT RAISED for unsupported gap id")

        # reuse
        reuse_ok = True
        if expected_reuse:
            reuse_ok = bool(reuse_selection_id) and reuse_selection_id == selection_id
            if not reuse_ok:
                failures_detail.append(
                    f"DETERMINISTIC RERUN: expected selection reuse, got {reuse_selection_id} vs {selection_id}"
                )

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "gap_selection",
                "definition": definition,
            }

        n_alts = max(len(expected_alts), 1) if not expected_error else 1
        metrics: dict[str, dict[str, Any]] = {
            "selected_gap_validity": _metric(
                "selected_gap_validity",
                1.0 if valid else 0.0,
                1,
                "rate",
                "selected gap is a member of the analyzed gap set",
            ),
            "selection_rationale_grounding": _metric(
                "selection_rationale_grounding",
                1.0 if rationale_ok else 0.0,
                1,
                "rate",
                "selection carries a non-empty rationale",
            ),
            "alternative_consideration_accuracy": _metric(
                "alternative_consideration_accuracy",
                float(
                    n_alts if alt_ok else len([a for a in alternatives_case if a in expected_alts])
                ),
                n_alts,
                "rate",
                "all non-selected gaps recorded as alternatives",
            ),
            "fallback_accuracy": _metric(
                "fallback_accuracy",
                float(1.0 if (expected_fallback and fallback_ok) else 0.0),
                1 if expected_fallback else 0,
                "rate",
                "invalid model selections fall back deterministically to rank #1",
            ),
            "autonomy_decision_accuracy": _metric(
                "autonomy_decision_accuracy",
                1.0 if autonomy_ok else 0.0,
                1,
                "rate",
                "produced approval status / approval-required matches the reference",
            ),
            "operator_override_accuracy": _metric(
                "operator_override_accuracy",
                1.0 if override_ok else 0.0,
                1,
                "rate",
                "operator-supplied selection recorded as operator",
            ),
            "reuse_accuracy": _metric(
                "reuse_accuracy",
                float(1.0 if (expected_reuse and reuse_ok) else 0.0),
                1 if expected_reuse else 0,
                "rate",
                "re-running selection on the same analysis reuses the artifact",
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
                "selected_gap": selected_case,
                "status": str(selection.get("status") or ""),
                "selected_by": str(selection.get("selected_by") or ""),
                "alternatives": alternatives_case,
                "error": error,
                "metrics": metrics,
                "dimension_scores": {
                    k: v
                    for k, v in {
                        "selected_gap_validity": float(valid),
                        "selection_rationale_grounding": float(rationale_ok),
                        "alternative_consideration_accuracy": (
                            len([a for a in alternatives_case if a in expected_alts]) / n_alts
                            if n_alts
                            else 1.0
                        ),
                        "fallback_accuracy": float(fallback_ok) if expected_fallback else None,
                        "autonomy_decision_accuracy": float(autonomy_ok),
                        "operator_override_accuracy": float(override_ok),
                        "reuse_accuracy": float(reuse_ok) if expected_reuse else None,
                    }.items()
                    if v is not None
                },
            },
            status=status,
            explanation="; ".join(failures_detail)
            if failures_detail
            else "all gap-selection checks matched",
            evidence_artifact_ids=[
                e.artifact_id for e in ctx.produced_artifacts if e.artifact_type == "gap_selection"
            ],
        )


class GapSelectionEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.gap_selection",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic gap-selection evaluator (Phase 7A.1)",
            provides=["evaluator.gap_selection"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.gap_selection", GapSelectionEvaluator())
