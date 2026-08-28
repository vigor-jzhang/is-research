"""evaluator.evaluator_sanity — offline sanity audit of the live-quality
evaluators (Phase 7D.3B).

Because Phase 7D.3A found a genuine evaluator bug, every remaining live-quality
evaluator is audited before qualification results are trusted. Each sanity case
feeds a synthetic, model-shaped response to the REAL live-quality evaluator and
checks that:

- a known-good response PASSES the evaluator,
- a known-bad response FAILS it,
- scalar reference ids vs list reference ids are handled correctly,
- denominators include all legitimately exercised cases,
- provider errors (no produced artifacts) are never counted as successes.

The sanity evaluator reuses the exact evaluator implementations (no duplicated
logic) and never relaxes the expected quality.
"""

from __future__ import annotations

from typing import Any

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.schemas.evaluation import (
    EvaluatorCategory,
    EvaluatorResult,
    EvaluatorStatus,
)

_REASONING_TASKS = {
    "evidence_extraction",
    "literature_synthesis",
    "gap_analysis",
    "mechanism_development",
    "analytical_model_specification",
    "proposition_generation",
}
_CRITIC_TASKS = {
    "mechanism_critique",
    "model_critique",
    "proposition_critique",
    "results_critique",
    "manuscript_critique",
}


def _target_evaluator(task: str) -> Any:
    from research_harness.plugins.research.evaluator_live_quality_critic.plugin import (
        LiveQualityCriticEvaluator,
    )
    from research_harness.plugins.research.evaluator_live_quality_fast.plugin import (
        LiveQualityFastEvaluator,
    )
    from research_harness.plugins.research.evaluator_live_quality_reasoning.plugin import (
        LiveQualityReasoningEvaluator,
    )

    if task in _REASONING_TASKS:
        return LiveQualityReasoningEvaluator()
    if task in _CRITIC_TASKS:
        return LiveQualityCriticEvaluator()
    if task == "screening":
        return LiveQualityFastEvaluator()
    return None


class EvaluatorSanityEvaluator:
    evaluator_id = "evaluator.evaluator_sanity"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        reference = ctx.case.reference or {}
        task = str(reference.get("task") or "")
        expected_status = str(reference.get("expected_evaluator_status") or "failed")
        expected_diagnostics = [str(k) for k in (reference.get("expect_task_diagnostics") or [])]
        expect_positive = [
            str(k) for k in (reference.get("expect_task_diagnostics_positive") or [])
        ]

        evaluator = _target_evaluator(task)
        if evaluator is None:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation=f"evaluator sanity: unknown task {task!r}",
            )

        target_result = await evaluator.evaluate(ctx)
        got_status = target_result.status.value
        failures: list[str] = []
        if got_status != expected_status:
            failures.append(
                f"evaluator verdict mismatch for {task}: expected {expected_status}, "
                f"got {got_status} ({target_result.explanation})"
            )

        diag = (target_result.value or {}).get("task_diagnostics") or {}
        missing_diag = [k for k in expected_diagnostics if k not in diag]
        if missing_diag:
            failures.append(f"missing task diagnostics for {task}: {sorted(missing_diag)}")
        zero_diag = [k for k in expect_positive if int(diag.get(k, 0)) < 1]
        if zero_diag:
            failures.append(
                f"task diagnostics for {task} not positive as expected: {sorted(zero_diag)}"
            )

        # provider-error case: no produced artifacts must never be a success
        if (
            reference.get("expect_provider_not_success")
            and got_status == EvaluatorStatus.passed.value
        ):
            failures.append("provider-error case (no artifacts) must never pass the evaluator")

        result_status = EvaluatorStatus.failed if failures else EvaluatorStatus.passed
        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=1.0 if not failures else 0.0,
            value={
                "task": task,
                "target_evaluator": evaluator.evaluator_id,
                "target_status": got_status,
                "expected_status": expected_status,
                "target_explanation": target_result.explanation,
                "task_diagnostics": diag,
            },
            status=result_status,
            explanation="; ".join(failures) if failures else "evaluator sanity checks matched",
        )


class EvaluatorSanityEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.evaluator_sanity",
            version="0.1.0",
            plugin_type="evaluator",
            description="Offline sanity audit of live-quality evaluators (Phase 7D.3B)",
            provides=["evaluator.evaluator_sanity"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.evaluator_sanity", EvaluatorSanityEvaluator())
