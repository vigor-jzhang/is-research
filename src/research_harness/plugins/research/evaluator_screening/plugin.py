"""evaluator.screening — deterministic screening evaluator (Phase 6C).

Evaluates the produced screening artifacts (ScreeningExecution,
ScreenedLiteratureSet, ScreeningDecision, ScreeningReview) against the case
reference. Reference keys are stable paper keys (title or DOI) resolved to
produced PaperIdentity ids.

Critical deterministic failures (fail the case):
- expected include incorrectly excluded (false exclusion)
- expected uncertain forced to exclude
- technical failure counted as an academic exclusion
- an identity screened more than once (duplicate screening)
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

_DECISION_ORDER = {"include": 0, "uncertain": 1, "exclude": 2}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


class ScreeningEvaluator:
    evaluator_id = "evaluator.screening"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        executions = [e for e in ctx.produced_artifacts if e.artifact_type == "screening_execution"]
        if not executions:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no screening_execution produced for the case",
            )
        exec_env = max(executions, key=lambda e: e.created_at)
        execution = envelope_payload_dict(exec_env)
        failures: list[dict[str, Any]] = list(execution.get("failures") or [])

        sets = [e for e in ctx.produced_artifacts if e.artifact_type == "screened_literature_set"]
        partitions: dict[str, list[str]] = {
            "included": [],
            "excluded": [],
            "uncertain": [],
        }
        if sets:
            payload = envelope_payload_dict(max(sets, key=lambda e: e.created_at))
            for partition in partitions:
                partitions[partition] = list(payload.get(f"{partition}_identity_ids") or [])

        decisions: list[dict[str, Any]] = [
            envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "screening_decision"
        ]
        decision_by_env_id = {
            e.artifact_id: envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "screening_decision"
        }
        reviews = [e for e in ctx.produced_artifacts if e.artifact_type == "screening_review"]

        produced_decision: dict[str, str] = {}
        for d in decisions:
            produced_decision[d["paper_identity_id"]] = d["decision"]
        reviewed_pi_ids = {
            decision_by_env_id[envelope_payload_dict(r)["screening_decision_id"]][
                "paper_identity_id"
            ]
            for r in reviews
            if envelope_payload_dict(r).get("screening_decision_id") in decision_by_env_id
        }

        # map reference keys (title/doi) -> produced identities
        record_envs = [e for e in ctx.produced_artifacts if e.artifact_type == "paper_record"]
        identities = {
            e.artifact_id: envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "paper_identity"
        }
        paper_to_identity: dict[str, str] = {}
        for iid, identity in identities.items():
            for pid in identity.get("member_paper_artifact_ids") or []:
                paper_to_identity.setdefault(pid, iid)
        key_to_identity: dict[str, str] = {}
        for env in record_envs:
            rec = envelope_payload_dict(env)
            iid = paper_to_identity.get(env.artifact_id)
            if iid is None:
                continue
            doi = (rec.get("doi") or "").strip()
            if doi:
                key_to_identity.setdefault(_norm(doi), iid)
            title = (rec.get("title") or "").strip()
            if title:
                key_to_identity.setdefault(_norm(title), iid)

        reference = ctx.case.reference or {}
        expected: dict[str, str] = {}
        for key, value in (reference.get("expected_decisions") or {}).items():
            iid = key_to_identity.get(_norm(key))
            if iid is not None:
                expected[iid] = str(value)
        expected_reviews: dict[str, bool] = {}
        for key, value in (reference.get("expected_reviews") or {}).items():
            iid = key_to_identity.get(_norm(key))
            if iid is not None:
                expected_reviews[iid] = bool(value)
        expected_failed: set[str] = set()
        for key in reference.get("expected_failed_identities") or []:
            iid = key_to_identity.get(_norm(key))
            if iid is not None:
                expected_failed.add(iid)

        failure_identities: set[str] = {
            f["paper_identity_id"] for f in failures if isinstance(f.get("paper_identity_id"), str)
        }

        # ---- checks ---------------------------------------------------
        decision_mismatches: list[str] = []
        for iid, expected_decision in expected.items():
            _ = expected_decision
            produced = produced_decision.get(iid)
            if produced != expected_decision:
                decision_mismatches.append(
                    f"identity {iid}: expected {expected_decision!r}, produced {produced!r}"
                )

        # expected include -> produced exclude (false exclusion)
        false_exclusions: list[str] = []
        # expected uncertain -> produced exclude (forced exclusion)
        forced_exclusions: list[str] = []
        for iid, expected_decision in expected.items():
            produced = produced_decision.get(iid)
            if expected_decision == "include" and produced == "exclude":
                false_exclusions.append(iid)
            if expected_decision == "uncertain" and produced == "exclude":
                forced_exclusions.append(iid)

        # technical failures counted as exclusions
        failed_but_excluded = sorted(failure_identities & set(partitions["excluded"]))
        # unexpected failures (not expected to fail)
        unexpected_failures = sorted(failure_identities - expected_failed)
        _ = None

        # duplicate screening: an identity decided more than once
        duplicate_screened = [
            iid for iid, count in _identity_counts(decisions).items() if count > 1
        ]

        # review triggers
        review_mismatches: list[str] = []
        for iid, expected_trigger in expected_reviews.items():
            actual = iid in reviewed_pi_ids
            if actual != expected_trigger:
                review_mismatches.append(
                    f"identity {iid}: review triggered={actual}, expected {expected_trigger}"
                )

        # ---- metrics --------------------------------------------------
        expected_items = list(expected.items())
        correct = sum(1 for iid, d in expected_items if produced_decision.get(iid) == d)
        total = len(expected_items)

        inc_expected = {iid for iid, d in expected_items if d == "include"}
        inc_produced = {iid for iid, d in produced_decision.items() if d == "include"}
        tp = len(inc_expected & inc_produced)
        fp = len(inc_produced - inc_expected)
        fn = len(inc_expected - inc_produced)
        include_precision = tp / (tp + fp) if (tp + fp) else 0.0
        include_recall = tp / (tp + fn) if (tp + fn) else 0.0
        include_f1 = (
            2 * include_precision * include_recall / (include_precision + include_recall)
            if (include_precision + include_recall)
            else 0.0
        )

        exc_expected = {iid for iid, d in expected_items if d == "exclude"}
        exc_correct = sum(1 for iid in exc_expected if produced_decision.get(iid) == "exclude")
        unc_expected = {iid for iid, d in expected_items if d == "uncertain"}
        unc_correct = sum(1 for iid in unc_expected if produced_decision.get(iid) == "uncertain")

        not_excludable = inc_expected | unc_expected
        false_exclusion_rate = (
            len([iid for iid in not_excludable if produced_decision.get(iid) == "exclude"])
            / len(not_excludable)
            if not_excludable
            else 0.0
        )
        excludable = exc_expected | unc_expected
        false_inclusion_rate = (
            len([iid for iid in excludable if produced_decision.get(iid) == "include"])
            / len(excludable)
            if excludable
            else 0.0
        )

        review_correct = sum(
            1 for iid, trigger in expected_reviews.items() if (iid in reviewed_pi_ids) == trigger
        )
        review_total = len(expected_reviews)

        failures_count = len(failures)

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "screening",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "screening_accuracy": _metric(
                "screening_accuracy",
                float(correct),
                total,
                "rate",
                "correct decisions over expected decisions",
            ),
            "include_precision": _metric(
                "include_precision",
                float(tp),
                tp + fp,
                "rate",
                "expected includes correctly included over all produced includes",
            ),
            "include_recall": _metric(
                "include_recall",
                float(tp),
                tp + fn,
                "rate",
                "expected includes correctly included over expected includes",
            ),
            "include_f1": _metric(
                "include_f1",
                include_f1,
                1 if inc_expected else 0,
                "rate",
                "harmonic mean of include precision and recall "
                "(mean per-case F1 over cases with include expectations)",
            ),
            "exclude_accuracy": _metric(
                "exclude_accuracy",
                float(exc_correct),
                len(exc_expected),
                "rate",
                "expected excludes correctly excluded",
            ),
            "uncertain_accuracy": _metric(
                "uncertain_accuracy",
                float(unc_correct),
                len(unc_expected),
                "rate",
                "expected uncertain correctly marked uncertain",
            ),
            "false_exclusion_rate": _metric(
                "false_exclusion_rate",
                float(
                    len([iid for iid in not_excludable if produced_decision.get(iid) == "exclude"])
                ),
                len(not_excludable),
                "rate",
                "expected include/uncertain produced as exclude",
            ),
            "false_inclusion_rate": _metric(
                "false_inclusion_rate",
                float(len([iid for iid in excludable if produced_decision.get(iid) == "include"])),
                len(excludable),
                "rate",
                "expected exclude/uncertain produced as include",
            ),
            "review_trigger_accuracy": _metric(
                "review_trigger_accuracy",
                float(review_correct),
                review_total,
                "rate",
                "review triggered exactly when expected",
            ),
            "technical_failure_count": _metric(
                "technical_failure_count",
                float(failures_count),
                1,
                "quantity",
                "candidate screening failures recorded by the orchestrator",
            ),
        }

        failures_detail: list[str] = []
        failures_detail.extend(decision_mismatches)
        if false_exclusions:
            failures_detail.append(f"FALSE EXCLUSION: {false_exclusions}")
        if forced_exclusions:
            failures_detail.append(f"UNCERTAIN FORCED TO EXCLUDE: {forced_exclusions}")
        if failed_but_excluded:
            failures_detail.append(f"TECHNICAL FAILURE COUNTED AS EXCLUSION: {failed_but_excluded}")
        if duplicate_screened:
            failures_detail.append(f"DUPLICATE SCREENING: {duplicate_screened}")
        if unexpected_failures:
            failures_detail.append(f"UNEXPECTED TECHNICAL FAILURES: {unexpected_failures}")
        failures_detail.extend(review_mismatches)

        status = EvaluatorStatus.failed if failures_detail else EvaluatorStatus.passed

        dimension_scores: dict[str, float | None] = {
            "screening_accuracy": correct / total if total else None,
            "include_f1": include_f1,
            "exclude_accuracy": exc_correct / len(exc_expected) if exc_expected else None,
            "uncertain_accuracy": unc_correct / len(unc_expected) if unc_expected else None,
            "false_exclusion_rate": false_exclusion_rate,
            "false_inclusion_rate": false_inclusion_rate,
            "review_trigger_accuracy": (review_correct / review_total if review_total else None),
            "technical_failure_count": float(failures_count),
        }
        dimension_scores = {k: v for k, v in dimension_scores.items() if v is not None}

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=(correct / total) if total else None,
            value={
                "decisions": produced_decision,
                "reviewed_identity_ids": sorted(reviewed_pi_ids),
                "failure_identity_ids": sorted(failure_identities),
                "partitions": partitions,
                "false_exclusions": false_exclusions,
                "forced_exclusions": forced_exclusions,
                "failed_but_excluded": failed_but_excluded,
                "duplicate_screened": duplicate_screened,
                "unexpected_failures": unexpected_failures,
                "metrics": metrics,
                "dimension_scores": dimension_scores,
            },
            status=status,
            explanation="; ".join(failures_detail)
            if failures_detail
            else "all screening checks matched",
            evidence_artifact_ids=[
                exec_env.artifact_id,
                *[
                    e.artifact_id
                    for e in ctx.produced_artifacts
                    if e.artifact_type == "screening_decision"
                ],
            ],
        )


def _identity_counts(decisions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in decisions:
        iid = d.get("paper_identity_id")
        if iid:
            counts[iid] = counts.get(iid, 0) + 1
    return counts


class ScreeningEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.screening",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic screening evaluator (Phase 6C)",
            provides=["evaluator.screening"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.screening", ScreeningEvaluator())
