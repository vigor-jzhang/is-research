"""evaluator.live_quality_fast — deterministic live-quality evaluator for the
fast role (Phase 7D.0).

Lightweight production-like tasks (screening decisions): decision accuracy,
uncertain-case handling, false-exclusion rate, structured-output success.
Latency/cost are aggregated by the live-quality service from model-call records.

Phase 7D.3B adds per-class screening diagnostics (include/exclude/uncertain
accuracy, false inclusion rate) persisted separately; the pass criteria are
unchanged and false exclusion remains a critical failure.
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


class LiveQualityFastEvaluator:
    evaluator_id = "evaluator.live_quality_fast"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        reference = ctx.case.reference or {}
        expected = {str(k): str(v) for k, v in (reference.get("expected_decisions") or {}).items()}
        required_accuracy = float(reference.get("required_decision_accuracy") or 0.8)

        # map produced paper identity id -> title
        title_by_identity: dict[str, str] = {}
        for env in ctx.produced_artifacts:
            if env.artifact_type != "paper_identity":
                continue
            payload = envelope_payload_dict(env)
            for member in payload.get("member_paper_artifact_ids") or []:
                rec_env = next(
                    (e for e in ctx.produced_artifacts if e.artifact_id == str(member)), None
                )
                if rec_env is not None and rec_env.artifact_type == "paper_record":
                    title_by_identity[str(env.artifact_id)] = str(
                        envelope_payload_dict(rec_env).get("title") or ""
                    )

        # produced decision per title
        produced: dict[str, str] = {}
        for env in ctx.produced_artifacts:
            if env.artifact_type != "screening_decision":
                continue
            payload = envelope_payload_dict(env)
            pid = str(payload.get("paper_identity_id") or "")
            title = title_by_identity.get(pid, "")
            if title:
                produced[title] = str(payload.get("decision") or "")

        failures: list[str] = []
        matched = 0
        false_exclusions = 0
        false_inclusions = 0
        uncertain_expected = 0
        uncertain_handled = 0
        total = len(expected)
        per_class = {cls: {"total": 0, "matched": 0} for cls in ("include", "exclude", "uncertain")}

        for title, expected_class in expected.items():
            actual = produced.get(title)
            per_class[expected_class]["total"] += 1
            # Counted before the match test, so a correctly classified
            # uncertain case is part of the denominator. Previously
            # uncertain_expected was only incremented on mismatch, and the
            # inner `if actual == "uncertain"` was unreachable (the branch is
            # entered only when actual != expected_class), so uncertain_rate
            # was a step function: 1.0 or 0.0, never a proportion.
            if expected_class == "uncertain":
                uncertain_expected += 1
                if actual == "uncertain":
                    uncertain_handled += 1
            if actual == expected_class:
                matched += 1
                per_class[expected_class]["matched"] += 1
            elif expected_class == "uncertain":
                if actual == "exclude":
                    false_exclusions += 1
                    failures.append(
                        f"false exclusion: expected uncertain, produced exclude for {title!r}"
                    )
                elif actual == "include":
                    false_inclusions += 1
            elif expected_class == "exclude" and actual == "include":
                false_inclusions += 1
            elif expected_class in ("include", "exclude") and actual == "exclude":
                # expected include but produced exclude -> false exclusion
                false_exclusions += 1
                failures.append(
                    f"false exclusion: expected {expected_class}, produced exclude for {title!r}"
                )

        def _class_accuracy(cls: str) -> float | None:
            info = per_class[cls]
            return info["matched"] / info["total"] if info["total"] else None

        accuracy = matched / total if total else 1.0
        if accuracy < required_accuracy:
            failures.append(f"decision_accuracy {accuracy:.3f} < required {required_accuracy:.3f}")
        if false_exclusions:
            failures.append(f"false_exclusion_rate {false_exclusions / total:.3f} > 0 (critical)")

        structured_ok = True
        missing = [t for t in expected if t not in produced]
        if missing:
            structured_ok = False
            failures.append(f"structured_output: no decision produced for {missing}")

        uncertain_rate = uncertain_handled / uncertain_expected if uncertain_expected else 1.0

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "live_quality_fast",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "decision_accuracy": _metric(
                "decision_accuracy",
                accuracy,
                max(total, 1),
                "rate",
                "screening decisions match the reference decision class",
            ),
            "uncertain_case_handling": _metric(
                "uncertain_case_handling",
                uncertain_rate,
                uncertain_expected if uncertain_expected else 0,
                "rate",
                "expected-uncertain cases are kept uncertain rather than forced",
            ),
            "false_exclusion_rate": _metric(
                "false_exclusion_rate",
                false_exclusions / total if total else 0.0,
                max(false_exclusions, 1),
                "rate",
                "expected include/uncertain cases produced as exclude (critical)",
            ),
            "structured_output_success": _metric(
                "structured_output_success",
                1.0 if structured_ok else 0.0,
                1,
                "rate",
                "a valid decision was produced for every reference paper",
            ),
        }

        # Phase 7D.3B: per-class screening diagnostics (persisted separately;
        # the pass criteria are unchanged and false exclusion stays critical).
        task_diagnostics: dict[str, int] = {
            "include_mismatch": per_class["include"]["total"] - per_class["include"]["matched"],
            "exclude_mismatch": per_class["exclude"]["total"] - per_class["exclude"]["matched"],
            "uncertain_mismatch": uncertain_expected - uncertain_handled,
            "false_exclusion": false_exclusions,
            "false_inclusion": false_inclusions,
            "structured_output_failure": 0 if structured_ok else 1,
            "provider_error": 0,
        }
        class_acc = {
            "include": _class_accuracy("include"),
            "exclude": _class_accuracy("exclude"),
            "uncertain": _class_accuracy("uncertain"),
        }

        status = EvaluatorStatus.failed if failures else EvaluatorStatus.passed
        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=1.0 if not failures else 0.0,
            value={
                "produced_decisions": produced,
                "false_exclusions": false_exclusions,
                "false_inclusions": false_inclusions,
                "class_accuracy": dict(class_acc),
                "task_diagnostics": task_diagnostics,
                "metrics": metrics,
                "dimension_scores": {
                    "decision_accuracy": accuracy,
                    "uncertain_case_handling": uncertain_rate,
                    "false_exclusion_rate": false_exclusions / total if total else 0.0,
                    "false_inclusion_rate": false_inclusions / total if total else 0.0,
                    "include_accuracy": class_acc["include"] or 0.0,
                    "exclude_accuracy": class_acc["exclude"] or 0.0,
                    "uncertain_accuracy": class_acc["uncertain"] or 0.0,
                    "structured_output_success": float(structured_ok),
                },
            },
            status=status,
            explanation="; ".join(failures) if failures else "all fast-role checks matched",
        )


class LiveQualityFastEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.live_quality_fast",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic live-quality fast-role evaluator (Phase 7D.0)",
            provides=["evaluator.live_quality_fast"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.live_quality_fast", LiveQualityFastEvaluator())
