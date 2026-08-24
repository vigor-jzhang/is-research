"""evaluator.gap_analysis — deterministic gap-analysis evaluator (Phase 6D).

Evaluates produced gap artifacts (GapAnalysis, GapAnalysisExecution,
ResearchGap) against the case reference. Reference gaps are keyed by title;
deterministic grounding checks gate pass/fail.

Critical deterministic failures:
- hallucinated references (gaps rejected for nonexistent statement/evidence ids)
- unsupported gap persisted (a gap with no grounding)
- global novelty claim presented as established fact (sweeping phrasing)
- deterministic support counts incorrect
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

_SWEEPING = re.compile(
    r"no (research|studies|study|paper|papers|literature|work|evidence) "
    r"(has|have|has been|has ever|has never|exists|exist)"
    r"|nothing is known|no one has|has never been studied|never been studied"
    r"|no evidence exists|nothing exists",
    re.IGNORECASE,
)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _contained(haystack: str, needle: str) -> bool:
    return bool(needle) and (_norm(needle) in _norm(haystack))


class GapAnalysisEvaluator:
    evaluator_id = "evaluator.gap_analysis"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        executions = [
            e for e in ctx.produced_artifacts if e.artifact_type == "gap_analysis_execution"
        ]
        if not executions:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no gap_analysis_execution produced for the case",
            )
        exec_env = max(executions, key=lambda e: e.created_at)
        execution = envelope_payload_dict(exec_env)
        gaps_rejected = int(execution.get("gaps_rejected") or 0)
        failures = list(execution.get("failures") or [])
        hallucinated_count = sum(1 for f in failures if "hallucinated" in str(f.get("error") or ""))

        gap_envs = [e for e in ctx.produced_artifacts if e.artifact_type == "research_gap"]
        gaps: list[dict[str, Any]] = [envelope_payload_dict(e) for e in gap_envs]
        analyses = [e for e in ctx.produced_artifacts if e.artifact_type == "gap_analysis"]
        ranked_ids: list[str] = []
        if analyses:
            ranked_ids = list(
                envelope_payload_dict(max(analyses, key=lambda e: e.created_at)).get(
                    "ranked_gap_ids"
                )
                or []
            )

        # valid grounding universe (fixture statements + evidence)
        valid_statement_ids = {
            e.artifact_id
            for e in ctx.produced_artifacts
            if e.artifact_type == "synthesis_statement"
        }
        valid_evidence_ids = {
            e.artifact_id for e in ctx.produced_artifacts if e.artifact_type == "evidence_item"
        }

        reference = ctx.case.reference or {}
        expected_gaps: dict[str, dict[str, Any]] = dict(reference.get("expected_gaps") or {})
        expected_rank = list(reference.get("expected_rank_order") or [])
        expected_hallucinated = int(reference.get("expected_hallucinated") or 0)
        expected_unsupported = int(reference.get("expected_unsupported") or 0)
        expected_sweeping = bool(reference.get("expected_sweeping") or False)
        expected_tentative = set(reference.get("expected_tentative") or [])

        # ---- match produced gaps to expected (by title) ----------------
        matched_ids: set[str] = set()
        matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for title, spec in expected_gaps.items():
            for env, gap in zip(gap_envs, gaps, strict=True):
                if env.artifact_id in matched_ids:
                    continue
                if not (
                    _contained(gap.get("title") or "", title)
                    or _contained(title, gap.get("title") or "")
                ):
                    continue
                matched_ids.add(env.artifact_id)
                matched.append((gap, spec))
                break

        # ---- per-gap checks --------------------------------------------
        type_mismatches: list[str] = []
        support_mismatches: list[str] = []
        tentative_mismatches: list[str] = []
        for gap, spec in matched:
            produced_type = (gap.get("gap_type") or "").lower()
            expected_type = str(spec.get("gap_type") or "").lower()
            if produced_type != expected_type:
                type_mismatches.append(
                    f"{gap.get('title')!r}: expected type {expected_type!r}, "
                    f"produced {produced_type!r}"
                )
            for count_key, expected_value in (
                ("supporting_papers", spec.get("supporting_papers")),
                ("supporting_evidence_items", spec.get("supporting_evidence_items")),
                ("contradicting_papers", spec.get("contradicting_papers")),
            ):
                if expected_value is None:
                    continue
                if int(gap.get(count_key) or 0) != int(expected_value):
                    support_mismatches.append(
                        f"{gap.get('title')!r}: {count_key} expected {expected_value}, "
                        f"produced {gap.get(count_key)}"
                    )
            tentative = (gap.get("strength") or "") == "tentative"
            expected_tent = _norm(gap.get("title") or "") in {_norm(t) for t in expected_tentative}
            if tentative != expected_tent:
                tentative_mismatches.append(
                    f"{gap.get('title')!r}: tentative={tentative}, expected {expected_tent}"
                )

        # ---- unsupported / sweeping / grounding across ALL produced ----
        unsupported_ids: list[str] = []
        sweeping_ids: list[str] = []
        ungrounded_ids: list[str] = []
        for env, gap in zip(gap_envs, gaps, strict=True):
            statement_ids = gap.get("supporting_synthesis_statement_ids") or []
            contradiction_ids = gap.get("contradiction_statement_ids") or []
            evidence_ids = gap.get("supporting_evidence_ids") or []
            if not statement_ids and not evidence_ids:
                unsupported_ids.append(env.artifact_id)
                ungrounded_ids.append(env.artifact_id)
            bad_stmt = [
                sid for sid in statement_ids + contradiction_ids if sid not in valid_statement_ids
            ]
            bad_ev = [eid for eid in evidence_ids if eid not in valid_evidence_ids]
            if bad_stmt or bad_ev:
                ungrounded_ids.append(env.artifact_id)
            text = f"{gap.get('title') or ''} {gap.get('description') or ''}"
            if _SWEEPING.search(text):
                sweeping_ids.append(env.artifact_id)

        # ---- ranking check ----------------------------------------------
        ranked_index = {rid: i for i, rid in enumerate(ranked_ids)}
        rank_mismatches: list[str] = []
        expected_ranked_ids = [
            next(
                (
                    env.artifact_id
                    for env, gap in zip(gap_envs, gaps, strict=True)
                    if _contained(gap.get("title") or "", t)
                ),
                None,
            )
            for t in expected_rank
        ]
        expected_ranked_ids = [rid for rid in expected_ranked_ids if rid is not None]
        for i in range(len(expected_ranked_ids) - 1):
            a, b = expected_ranked_ids[i], expected_ranked_ids[i + 1]
            if a in ranked_index and b in ranked_index and ranked_index[a] >= ranked_index[b]:
                rank_mismatches.append(
                    f"expected {expected_rank[i]!r} before {expected_rank[i + 1]!r}"
                )

        # ---- metrics ---------------------------------------------------
        total_gaps = len(gaps)
        matched_count = len(matched_ids)
        precision = matched_count / total_gaps if total_gaps else 1.0
        expected_total = len(expected_gaps)
        recall = matched_count / expected_total if expected_total else 1.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        grounded = total_gaps - len(ungrounded_ids)
        grounded_rate = grounded / total_gaps if total_gaps else 1.0
        bounded = total_gaps - len(sweeping_ids)
        bounded_rate = bounded / total_gaps if total_gaps else 1.0
        support_entries = sum(
            1
            for spec in expected_gaps.values()
            if any(
                spec.get(k) is not None
                for k in ("supporting_papers", "supporting_evidence_items", "contradicting_papers")
            )
        )
        support_ok = support_entries - len(support_mismatches)

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "gap_analysis",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "gap_type_accuracy": _metric(
                "gap_type_accuracy",
                float(matched_count - len(type_mismatches)),
                matched_count,
                "rate",
                "matched gaps with the expected gap type",
            ),
            "gap_precision": _metric(
                "gap_precision",
                float(matched_count),
                total_gaps,
                "rate",
                "produced gaps matching an expected gap",
            ),
            "gap_recall": _metric(
                "gap_recall",
                float(matched_count),
                expected_total,
                "rate",
                "expected gaps that were produced",
            ),
            "gap_f1": _metric(
                "gap_f1",
                f1,
                1,
                "rate",
                "harmonic mean of gap precision and recall",
            ),
            "grounding_accuracy": _metric(
                "grounding_accuracy",
                float(grounded),
                total_gaps,
                "rate",
                "produced gaps whose cited ids exist in the fixture sets",
            ),
            "corpus_bounded_claim_accuracy": _metric(
                "corpus_bounded_claim_accuracy",
                float(bounded),
                total_gaps,
                "rate",
                "produced gaps free of sweeping/global-novelty phrasing",
            ),
            "support_count_accuracy": _metric(
                "support_count_accuracy",
                float(support_ok),
                support_entries,
                "rate",
                "expected deterministic support counts that match",
            ),
            "ranking_accuracy": _metric(
                "ranking_accuracy",
                float(len(expected_ranked_ids) - len(rank_mismatches)),
                len(expected_ranked_ids),
                "rate",
                "expected gap rank order preserved in ranked_gap_ids",
            ),
            "unsupported_gap_rate": _metric(
                "unsupported_gap_rate",
                float(len(unsupported_ids)),
                total_gaps,
                "rate",
                "produced gaps with no supporting statements or evidence",
            ),
            "hallucinated_reference_count": _metric(
                "hallucinated_reference_count",
                float(hallucinated_count),
                1,
                "quantity",
                "gaps rejected for hallucinated statement/evidence ids",
            ),
        }

        failures_detail: list[str] = []
        if type_mismatches:
            failures_detail.append("TYPE MISMATCHES: " + "; ".join(type_mismatches))
        if support_mismatches:
            failures_detail.append("SUPPORT COUNT MISMATCHES: " + "; ".join(support_mismatches))
        if tentative_mismatches:
            failures_detail.append("TENTATIVE MISMATCHES: " + "; ".join(tentative_mismatches))
        if rank_mismatches:
            failures_detail.append("RANK MISMATCHES: " + "; ".join(rank_mismatches))
        if hallucinated_count != expected_hallucinated:
            failures_detail.append(
                f"HALLUCINATED REFERENCES: {hallucinated_count}, expected {expected_hallucinated}"
            )
        if len(unsupported_ids) > expected_unsupported:
            failures_detail.append(
                f"UNSUPPORTED GAP PERSISTED: {len(unsupported_ids)} > {expected_unsupported}"
            )
        if sweeping_ids and not expected_sweeping:
            failures_detail.append(
                f"GLOBAL NOVELTY CLAIM AS FACT: {sweeping_ids} (sweeping phrasing)"
            )
        if matched_count != expected_total:
            failures_detail.append(
                f"GAPS MISSING: {expected_total - matched_count} expected gaps not produced"
            )
        if total_gaps > matched_count:
            failures_detail.append(
                f"EXTRA GAPS: {total_gaps - matched_count} produced gaps not expected"
            )

        status = EvaluatorStatus.failed if failures_detail else EvaluatorStatus.passed

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=recall if expected_total else None,
            value={
                "matched_gap_ids": sorted(matched_ids),
                "unsupported_gap_ids": unsupported_ids,
                "ungrounded_gap_ids": ungrounded_ids,
                "sweeping_gap_ids": sweeping_ids,
                "gaps_rejected": gaps_rejected,
                "hallucinated_reference_count": hallucinated_count,
                "ranked_gap_ids": ranked_ids,
                "metrics": metrics,
                "dimension_scores": {
                    k: v
                    for k, v in {
                        "gap_type_accuracy": (
                            (matched_count - len(type_mismatches)) / matched_count
                            if matched_count
                            else None
                        ),
                        "gap_precision": precision,
                        "gap_recall": recall,
                        "gap_f1": f1,
                        "grounding_accuracy": grounded_rate,
                        "corpus_bounded_claim_accuracy": bounded_rate,
                        "support_count_accuracy": (
                            support_ok / support_entries if support_entries else None
                        ),
                        "ranking_accuracy": (
                            (len(expected_ranked_ids) - len(rank_mismatches))
                            / len(expected_ranked_ids)
                            if expected_ranked_ids
                            else None
                        ),
                        "unsupported_gap_rate": (
                            len(unsupported_ids) / total_gaps if total_gaps else 0.0
                        ),
                        "hallucinated_reference_count": float(hallucinated_count),
                    }.items()
                    if v is not None
                },
            },
            status=status,
            explanation="; ".join(failures_detail) if failures_detail else "all gap checks matched",
            evidence_artifact_ids=[
                exec_env.artifact_id,
                *[e.artifact_id for e in gap_envs],
            ],
        )


class GapAnalysisEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.gap_analysis",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic gap-analysis evaluator (Phase 6D)",
            provides=["evaluator.gap_analysis"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.gap_analysis", GapAnalysisEvaluator())
