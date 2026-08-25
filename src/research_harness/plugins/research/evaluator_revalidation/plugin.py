"""evaluator.revalidation — deterministic incremental-revalidation evaluator
(Phase 7A).

Reads the workflow's `revalidation_report` (per-stage recomputed/reused state
plus the baseline and changed downstream artifact ids) and verifies the
immutable recomputation contract:

- stale_reuse_rate: stages that reused incompatible upstream state (a
  materially-changed upstream must NEVER be met with stale reuse)
- required_recomputation_accuracy: stages that were supposed to recompute
  actually produced a new downstream artifact
- unchanged_reuse_accuracy: stages run with identical inputs reused the prior
  artifact deterministically
- provenance_version_accuracy: the new downstream artifact is a new version
  derived from the changed upstream artifact (via the store's provenance edges)
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


class RevalidationEvaluator:
    evaluator_id = "evaluator.revalidation"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    def _derived_from(self, ctx: EvaluatorContext, artifact_id: str, ancestor_id: str) -> bool:
        """Transitive provenance reachability of `ancestor_id` from
        `artifact_id` (BFS over the harness's direct-parent map)."""
        seen: set[str] = set()
        queue: list[str] = [artifact_id]
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.add(current)
            parents = ctx.provenance.get(current) or []
            for link in parents:
                source = str(getattr(link, "source_artifact_id", None) or "")
                if source == ancestor_id:
                    return True
                if source and source not in seen:
                    queue.append(source)
        return False

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        reports = [e for e in ctx.produced_artifacts if e.artifact_type == "revalidation_report"]
        if not reports:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no revalidation_report produced for the case",
            )
        report = envelope_payload_dict(max(reports, key=lambda e: e.created_at))
        stages: dict[str, Any] = dict(report.get("stages") or {})

        reference = ctx.case.reference or {}
        expected_recomputed = [str(s) for s in (reference.get("expected_recomputed") or [])]
        expected_reused = [str(s) for s in (reference.get("expected_reused") or [])]

        failures_detail: list[str] = []
        stale = 0
        recomputed_ok = 0
        recomputed_total = len(expected_recomputed)
        reused_ok = 0
        reused_total = len(expected_reused)
        provenance_ok = 0
        provenance_total = 0

        for kind in expected_recomputed:
            state = stages.get(kind)
            if state is None:
                failures_detail.append(f"stage {kind!r} missing from revalidation report")
                continue
            provenance_total += 1
            if state.get("recomputed"):
                recomputed_ok += 1
            else:
                stale += 1
                failures_detail.append(
                    f"STALE REUSE: {kind} reused downstream artifacts after a "
                    f"material upstream change"
                )
            # provenance version check: new downstream artifact is a distinct
            # version derived from the changed upstream
            downstream_b = state.get("downstream_b")
            downstream_a = state.get("downstream_a")
            upstream_b = state.get("upstream_b")
            version_ok = bool(downstream_b) and (
                downstream_a is None or downstream_b != downstream_a
            )
            if upstream_b and downstream_b:
                if self._derived_from(ctx, str(downstream_b), str(upstream_b)):
                    version_ok = True
                else:
                    failures_detail.append(
                        f"PROVENANCE VERSION: {kind} new downstream {downstream_b} is not "
                        f"derived from changed upstream {upstream_b}"
                    )
                    version_ok = False
            if version_ok:
                provenance_ok += 1

        for kind in expected_reused:
            state = stages.get(kind)
            if state is None:
                failures_detail.append(f"stage {kind!r} missing from revalidation report")
                continue
            if state.get("reused") or state.get("execution_reused"):
                reused_ok += 1
            else:
                failures_detail.append(
                    f"UNNECESSARY RECOMPUTATION: {kind} recomputed despite identical inputs"
                )

        # ---- metrics -------------------------------------------------------

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "incremental_revalidation",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "stale_reuse_rate": _metric(
                "stale_reuse_rate",
                float(stale),
                max(stale, 1),
                "rate",
                "materially-changed stages that reused incompatible upstream state",
            ),
            "required_recomputation_accuracy": _metric(
                "required_recomputation_accuracy",
                float(recomputed_ok),
                recomputed_total,
                "rate",
                "changed-upstream stages that produced a new downstream artifact",
            ),
            "unchanged_reuse_accuracy": _metric(
                "unchanged_reuse_accuracy",
                float(reused_ok),
                reused_total,
                "rate",
                "identical-input stages that deterministically reused prior artifacts",
            ),
            "provenance_version_accuracy": _metric(
                "provenance_version_accuracy",
                float(provenance_ok),
                provenance_total,
                "rate",
                "new downstream artifacts derived from the changed upstream artifact",
            ),
        }

        status = EvaluatorStatus.failed if failures_detail else EvaluatorStatus.passed

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=(
                (recomputed_ok + reused_ok + provenance_ok)
                / max(recomputed_total + reused_total + provenance_total, 1)
                if (recomputed_total + reused_total)
                else None
            ),
            value={
                "stage_state": stages,
                "stale_stages": [
                    kind
                    for kind in expected_recomputed
                    if not (stages.get(kind) or {}).get("recomputed")
                ],
                "metrics": metrics,
                "dimension_scores": {
                    k: v
                    for k, v in {
                        "stale_reuse_rate": float(stale / max(stale, 1)) if stale else 0.0,
                        "required_recomputation_accuracy": (
                            recomputed_ok / recomputed_total if recomputed_total else None
                        ),
                        "unchanged_reuse_accuracy": (
                            reused_ok / reused_total if reused_total else None
                        ),
                        "provenance_version_accuracy": (
                            provenance_ok / provenance_total if provenance_total else None
                        ),
                    }.items()
                    if v is not None
                },
            },
            status=status,
            explanation="; ".join(failures_detail)
            if failures_detail
            else "all revalidation checks matched",
            evidence_artifact_ids=[e.artifact_id for e in reports],
        )


class RevalidationEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.revalidation",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic incremental-revalidation evaluator (Phase 7A)",
            provides=["evaluator.revalidation"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.revalidation", RevalidationEvaluator())
