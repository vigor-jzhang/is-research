"""evaluator.task_model_qualification — deterministic task-specific live-model
qualification evaluator (Phase 7D.3).

Checks the TaskQualificationMatrix against known-answer expectations: per-task
qualified model sets, per-task ranked order (qualified only), per-model task
verdicts, role vs task consistency, structured rejection reasons, and the
critical metric `unsafe_task_qualification_rate` which must be 0 (a task marked
qualified while its own rejection reasons are non-empty, or a model ranked for
a task it is not qualified for, is a critical failure).
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


class TaskModelQualificationEvaluator:
    evaluator_id = "evaluator.task_model_qualification"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        matrices = [
            e for e in ctx.produced_artifacts if e.artifact_type == "task_qualification_matrix"
        ]
        if not matrices:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no task_qualification_matrix produced for the case",
            )
        m = envelope_payload_dict(max(matrices, key=lambda e: e.created_at))
        reference = ctx.case.reference or {}

        rows = list(m.get("rows") or [])
        qualified_by_task = {
            str(k): {str(x) for x in v}
            for k, v in (m.get("qualified_models_by_task") or {}).items()
        }
        ranked_by_task = {
            str(k): [str(x) for x in v] for k, v in (m.get("ranked_models_by_task") or {}).items()
        }
        role_qualified_models = {str(x) for x in (m.get("role_qualified_models") or [])}
        matrix_role = str(m.get("role") or "")
        by_model_task: dict[tuple[str, str], dict[str, Any]] = {}
        for r in rows:
            by_model_task[(str(r.get("candidate_id")), str(r.get("task")))] = r

        failures: list[str] = []
        unsafe = 0
        task_ok = True
        role_ok = True
        rank_ok = True

        # ---- per-task qualified model sets --------------------------------
        expected_qualified = reference.get("expected_qualified_by_task")
        if expected_qualified is not None:
            for task, models in expected_qualified.items():
                expected_set = {str(x) for x in models}
                got_set = qualified_by_task.get(str(task), set())
                if got_set != expected_set:
                    failures.append(
                        f"QUALIFIED[{task}]: expected {sorted(expected_set)}, got {sorted(got_set)}"
                    )
                    task_ok = False

        # ---- per-model task verdicts ---------------------------------------
        expected_verdicts = reference.get("expected_task_verdicts") or {}
        for model_id, verdicts in expected_verdicts.items():
            for task, verdict in verdicts.items():
                row = by_model_task.get((str(model_id), str(task)))
                if row is None:
                    failures.append(f"VERDICT: no row for {model_id}/{task}")
                    task_ok = False
                    continue
                got = "qualified_for_task" if row.get("qualified") else "not_qualified_for_task"
                if got != verdict:
                    failures.append(f"VERDICT {model_id}/{task}: got {got!r}, expected {verdict!r}")
                    task_ok = False

        # ---- role vs task consistency --------------------------------------
        expected_role_models = reference.get("expected_role_qualified_models")
        if expected_role_models is not None:
            expected_set = {str(x) for x in expected_role_models}
            if role_qualified_models != expected_set:
                failures.append(
                    f"ROLE-QUALIFIED: expected {sorted(expected_set)}, "
                    f"got {sorted(role_qualified_models)}"
                )
                role_ok = False
        expected_role = reference.get("expected_role")
        if expected_role is not None and matrix_role != expected_role:
            failures.append(f"ROLE: got {matrix_role!r}, expected {expected_role!r}")
            role_ok = False

        # ---- rejection reasons (stale / provider / grounding / reps) -------
        expected_rejections = reference.get("expected_rejections") or {}
        for key, substring in expected_rejections.items():
            model_id, task = key.split("/")
            row = by_model_task.get((model_id, task))
            if row is None:
                failures.append(f"REJECTION: no row for {key}")
                task_ok = False
                continue
            reasons = " ".join(str(x) for x in (row.get("rejection_reasons") or []))
            if substring and substring not in reasons:
                failures.append(f"REJECTION {key}: expected {substring!r} in {reasons!r}")
                task_ok = False
            if substring == "" and reasons:
                failures.append(f"REJECTION {key}: expected no rejection, got {reasons!r}")
                task_ok = False

        # ---- per-task ranking ----------------------------------------------
        expected_ranked = reference.get("expected_ranked_by_task")
        if expected_ranked is not None:
            for task, models in expected_ranked.items():
                expected_list = [str(x) for x in models]
                got_list = ranked_by_task.get(str(task), [])
                if got_list != expected_list:
                    failures.append(f"RANK[{task}]: expected {expected_list}, got {got_list}")
                    rank_ok = False

        # ---- unsafe task qualification (critical) --------------------------
        for r in rows:
            if r.get("qualified") and (r.get("rejection_reasons") or []):
                unsafe += 1
                failures.append(
                    f"UNSAFE: {r.get('candidate_id')}/{r.get('task')} qualified with rejections"
                )
        for task, ranked in ranked_by_task.items():
            for model_id in ranked:
                row = by_model_task.get((model_id, task))
                if row is None or not row.get("qualified"):
                    unsafe += 1
                    failures.append(f"UNSAFE: ranked {model_id} for {task} but not qualified")

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "task_model_qualification",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "task_qualification_accuracy": _metric(
                "task_qualification_accuracy",
                1.0 if task_ok else 0.0,
                1,
                "rate",
                "per-task qualified sets and per-model task verdicts match expectations",
            ),
            "role_task_consistency_accuracy": _metric(
                "role_task_consistency_accuracy",
                1.0 if role_ok else 0.0,
                1,
                "rate",
                "role qualification is consistent and separate from task qualification",
            ),
            "ranking_accuracy": _metric(
                "ranking_accuracy",
                1.0 if rank_ok else 0.0,
                1,
                "rate",
                "per-task ranking considers only qualified models, ordered by "
                "correctness/reliability/structured-output/latency/cost",
            ),
            "unsafe_task_qualification_rate": _metric(
                "unsafe_task_qualification_rate",
                float(unsafe),
                max(unsafe, 1),
                "rate",
                "any task qualified with rejection reasons or ranked unqualified model (critical)",
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
                "role": matrix_role,
                "qualified_models_by_task": {k: sorted(v) for k, v in qualified_by_task.items()},
                "ranked_models_by_task": ranked_by_task,
                "role_qualified_models": sorted(role_qualified_models),
                "unsafe_task_qualification_count": unsafe,
                "metrics": metrics,
                "dimension_scores": {
                    "task_qualification_accuracy": float(task_ok),
                    "role_task_consistency_accuracy": float(role_ok),
                    "ranking_accuracy": float(rank_ok),
                    "unsafe_task_qualification_rate": float(unsafe),
                },
            },
            status=result_status,
            explanation="; ".join(failures)
            if failures
            else "all task-qualification checks matched",
            evidence_artifact_ids=[e.artifact_id for e in matrices],
        )


class TaskModelQualificationEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.task_model_qualification",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic task-specific live-model qualification evaluator (Phase 7D.3)",
            provides=["evaluator.task_model_qualification"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.task_model_qualification", TaskModelQualificationEvaluator())
