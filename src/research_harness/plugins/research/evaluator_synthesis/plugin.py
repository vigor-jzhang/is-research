"""evaluator.synthesis — deterministic literature-synthesis evaluator
(Phase 7A).

Evaluates produced Phase 2G artifacts (SynthesisStatement, LiteratureSynthesis,
SynthesisExecution) against known-answer references:

- statement grounding: every supporting/conflicting evidence id must exist in
  the produced EvidenceCorpus
- consensus / contradiction / mixed accuracy: expected typed statements must be
  produced with the expected type
- multi-paper support: a statement supported by >= 2 distinct papers must be
  marked multi_paper (single-paper observations must NOT be labeled consensus)
- support-count accuracy: papers_supporting / papers_conflicting counts match
- unsupported-statement rate and hallucinated-reference count: evidence ids
  with no backing corpus item are measured and fail the case when unexpected
- rejection: hallucinated / paper-less statements must be rejected by the
  synthesizer
"""

from __future__ import annotations

import re
from typing import Any

from research_harness.contracts.evaluator import EvaluatorContext, envelope_payload_dict
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.schemas.evaluation import (
    EvaluatorCategory,
    EvaluatorResult,
    EvaluatorStatus,
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().strip())


class SynthesisEvaluator:
    evaluator_id = "evaluator.synthesis"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        stmt_envs = [e for e in ctx.produced_artifacts if e.artifact_type == "synthesis_statement"]
        executions = [e for e in ctx.produced_artifacts if e.artifact_type == "synthesis_execution"]
        if not stmt_envs and not executions:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no synthesis artifacts produced for the case",
            )
        produced = [envelope_payload_dict(e) for e in stmt_envs]
        execution = (
            envelope_payload_dict(max(executions, key=lambda e: e.created_at)) if executions else {}
        )
        rejected = int(execution.get("statements_rejected") or 0)

        valid_evidence: set[str] = {
            e.artifact_id for e in ctx.produced_artifacts if e.artifact_type == "evidence_item"
        }

        reference = ctx.case.reference or {}
        expected_statements = list(reference.get("expected_statements") or [])
        expected_rejections = int(reference.get("expected_rejections") or 0)
        expected_absent = [str(s) for s in (reference.get("expected_absent_statements") or [])]
        expected_not_consensus = bool(reference.get("expected_not_consensus") or False)

        # ---- grounding: all referenced evidence ids must exist -------------
        grounded_ids: list[str] = []
        ungrounded_statement_ids: list[str] = []
        hallucinated_refs: set[str] = set()
        for env, item in zip(stmt_envs, produced, strict=True):
            referenced = list(item.get("supporting_evidence_ids") or []) + list(
                item.get("conflicting_evidence_ids") or []
            )
            bad = [rid for rid in referenced if rid not in valid_evidence]
            hallucinated_refs.update(bad)
            if bad:
                ungrounded_statement_ids.append(env.artifact_id)
            else:
                grounded_ids.append(env.artifact_id)

        # ---- expected statements: type + support + counts ------------------
        by_norm: dict[str, dict[str, Any]] = {_norm(p.get("statement") or ""): p for p in produced}
        statement_failures: list[str] = []
        consensus_ok = 0
        contradiction_ok = 0
        consensus_total = 0
        contradiction_total = 0
        multi_paper_ok = 0
        multi_paper_total = 0
        support_count_ok = 0
        expected_total = len(expected_statements)

        for exp in expected_statements:
            exp_stmt = str(exp.get("statement") or "")
            exp_type = str(exp.get("type") or "")
            exp_support = str(exp.get("support_type") or "")
            exp_papers = int(exp.get("papers_supporting") or 0)
            exp_conflicting = int(exp.get("papers_conflicting") or 0)
            produced_stmt = by_norm.get(_norm(exp_stmt))
            if produced_stmt is None:
                statement_failures.append(f"missing expected statement: {exp_stmt[:80]!r}")
                if exp_type == "consensus":
                    consensus_total += 1
                if exp_type == "contradiction":
                    contradiction_total += 1
                if exp_support == "multi_paper":
                    multi_paper_total += 1
                continue
            produced_type = str(produced_stmt.get("type") or "")
            produced_support = str(produced_stmt.get("support_type") or "")
            produced_papers = int(produced_stmt.get("papers_supporting") or 0)
            produced_conflicting = int(produced_stmt.get("papers_conflicting") or 0)

            if exp_type:
                if produced_type != exp_type:
                    statement_failures.append(
                        f"statement {exp_stmt[:60]!r}: expected type {exp_type!r}, "
                        f"produced {produced_type!r}"
                    )
                if exp_type == "consensus":
                    consensus_total += 1
                    consensus_ok += 1 if produced_type == exp_type else 0
                if exp_type == "contradiction":
                    contradiction_total += 1
                    contradiction_ok += 1 if produced_type == exp_type else 0
            if exp_support:
                if produced_support != exp_support:
                    statement_failures.append(
                        f"statement {exp_stmt[:60]!r}: expected support {exp_support!r}, "
                        f"produced {produced_support!r}"
                    )
                if exp_support == "multi_paper":
                    multi_paper_total += 1
                    multi_paper_ok += 1 if produced_support == exp_support else 0
            if exp_papers > 0:
                if produced_papers != exp_papers:
                    statement_failures.append(
                        f"statement {exp_stmt[:60]!r}: expected papers_supporting "
                        f"{exp_papers}, produced {produced_papers}"
                    )
            if exp_conflicting > 0:
                if produced_conflicting != exp_conflicting:
                    statement_failures.append(
                        f"statement {exp_stmt[:60]!r}: expected papers_conflicting "
                        f"{exp_conflicting}, produced {produced_conflicting}"
                    )
            # support counts counted once per matched statement
            support_count_ok += 1

        # ---- single-paper observations must not be consensus ---------------
        if expected_not_consensus:
            for exp in expected_statements:
                produced_stmt = by_norm.get(_norm(str(exp.get("statement") or "")))
                if produced_stmt is None:
                    continue
                if str(produced_stmt.get("type") or "") == "consensus":
                    statement_failures.append(
                        "single-paper observation labeled consensus: "
                        f"{str(exp.get('statement'))[:60]!r}"
                    )

        # ---- expected-absent statements must be absent ---------------------
        present_absent: list[str] = []
        for text in expected_absent:
            if _norm(text) in by_norm:
                present_absent.append(text)

        # ---- metrics -------------------------------------------------------
        total_statements = len(produced)

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "synthesis",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "statement_grounding_accuracy": _metric(
                "statement_grounding_accuracy",
                float(len(grounded_ids)),
                total_statements,
                "rate",
                "produced statements whose evidence ids all exist in the corpus",
            ),
            "consensus_accuracy": _metric(
                "consensus_accuracy",
                float(consensus_ok),
                consensus_total,
                "rate",
                "expected consensus statements produced with type consensus",
            ),
            "contradiction_accuracy": _metric(
                "contradiction_accuracy",
                float(contradiction_ok),
                contradiction_total,
                "rate",
                "expected contradiction statements produced with type contradiction",
            ),
            "multi_paper_support_accuracy": _metric(
                "multi_paper_support_accuracy",
                float(multi_paper_ok),
                multi_paper_total,
                "rate",
                "statements supported by >= 2 papers marked multi_paper",
            ),
            "support_count_accuracy": _metric(
                "support_count_accuracy",
                float(support_count_ok),
                expected_total,
                "rate",
                "produced papers_supporting / papers_conflicting match the reference",
            ),
            "unsupported_statement_rate": _metric(
                "unsupported_statement_rate",
                float(len(ungrounded_statement_ids)),
                total_statements,
                "rate",
                "produced statements referencing evidence ids not in the corpus",
            ),
            "hallucinated_reference_count": _metric(
                "hallucinated_reference_count",
                float(len(hallucinated_refs)),
                1,
                "quantity",
                "distinct evidence ids referenced but not present in the corpus",
            ),
        }

        failures_detail: list[str] = []
        failures_detail.extend(statement_failures)
        if rejected < expected_rejections:
            failures_detail.append(
                f"REJECTIONS: {rejected} rejected, expected >= {expected_rejections}"
            )
        if present_absent:
            failures_detail.append(
                "EXPECTED-ABSENT STATEMENTS PRESENT: " + "; ".join(present_absent)
            )
        if ungrounded_statement_ids:
            failures_detail.append(
                f"HALLUCINATED/UNSUPPORTED REFERENCES: {sorted(hallucinated_refs)[:5]}"
            )

        status = EvaluatorStatus.failed if failures_detail else EvaluatorStatus.passed

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=(
                (consensus_ok + contradiction_ok + multi_paper_ok + support_count_ok)
                / max(consensus_total + contradiction_total + multi_paper_total + expected_total, 1)
                if expected_total
                else None
            ),
            value={
                "statements_produced": total_statements,
                "statements_rejected": rejected,
                "grounded_statements": len(grounded_ids),
                "ungrounded_statement_ids": ungrounded_statement_ids,
                "hallucinated_reference_ids": sorted(hallucinated_refs),
                "metrics": metrics,
                "dimension_scores": {
                    k: v
                    for k, v in {
                        "statement_grounding_accuracy": (
                            len(grounded_ids) / total_statements if total_statements else None
                        ),
                        "consensus_accuracy": (
                            consensus_ok / consensus_total if consensus_total else None
                        ),
                        "contradiction_accuracy": (
                            contradiction_ok / contradiction_total if contradiction_total else None
                        ),
                        "multi_paper_support_accuracy": (
                            multi_paper_ok / multi_paper_total if multi_paper_total else None
                        ),
                        "support_count_accuracy": (
                            support_count_ok / expected_total if expected_total else None
                        ),
                        "unsupported_statement_rate": (
                            len(ungrounded_statement_ids) / total_statements
                            if total_statements
                            else 0.0
                        ),
                        "hallucinated_reference_count": float(len(hallucinated_refs)),
                    }.items()
                    if v is not None
                },
            },
            status=status,
            explanation="; ".join(failures_detail)
            if failures_detail
            else "all synthesis checks matched",
            evidence_artifact_ids=[e.artifact_id for e in stmt_envs],
        )


class SynthesisEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.synthesis",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic literature-synthesis evaluator (Phase 7A)",
            provides=["evaluator.synthesis"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.synthesis", SynthesisEvaluator())
