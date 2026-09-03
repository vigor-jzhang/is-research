"""evaluator.evidence_enrichment — deterministic evidence-enrichment evaluator
(Phase 7C, closes the Phase 5C-5D coverage gap).

Checks the real enrichment/pre-acquisition runs against known-answer
expectations:
- enrichment grounding: executions are grounded (plan -> identity/claim,
  attempts -> retrieved records/items, before/after basis consistent)
- source preservation: original fixture records are preserved, not overwritten
- unsupported enrichment rejection: failed/not_found enrichment never
  fabricates evidence
- stale enrichment reuse: enrichment from a previous run is never reused for
  a changed source set
- provenance/version correctness: execution -> plan -> identity/claim and
  attempt -> execution links hold
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

_BASIS_RANK = {"title_only": 0, "indexed_metadata": 1, "abstract": 2, "full_text": 3}
_STRATEGY_ABSTRACT = "provider_get_abstract"


class EvidenceEnrichmentEvaluator:
    evaluator_id = "evaluator.evidence_enrichment"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        reports = [
            e for e in ctx.produced_artifacts if e.artifact_type == "evidence_enrichment_report"
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
                explanation="no evidence_enrichment_report produced for the case",
            )
        report = envelope_payload_dict(max(reports, key=lambda e: e.created_at))
        runs = list(report.get("runs") or [])
        by_id = {e.artifact_id: e for e in ctx.produced_artifacts}
        provenance = ctx.provenance or {}
        # Counted separately from grounding: provenance_version_accuracy used to
        # be a copy of grounding_hits/grounding_total, so the declared check
        # ("execution -> plan -> identity links hold") was never performed.
        provenance_total = 0
        provenance_hits = 0

        def _parents(aid: str) -> set[str]:
            return {str(getattr(p, "source_artifact_id", "")) for p in provenance.get(aid, [])}

        reference = ctx.case.reference or {}
        expected_run_count = reference.get("expected_run_count")
        expected_outcomes = list(reference.get("expected_outcomes") or [])
        expected_attempt_statuses = list(reference.get("expected_attempt_statuses") or [])
        expected_before_basis = list(reference.get("expected_before_basis") or [])
        expected_after_basis = list(reference.get("expected_after_basis") or [])
        expected_no_invented = bool(reference.get("expected_no_invented_evidence") or False)
        expected_grounded = bool(reference.get("expected_grounded") or False)
        expected_source_preserved = bool(reference.get("expected_source_preserved") or False)
        expected_preacquisition = bool(reference.get("expected_preacquisition") or False)
        expected_executions_differ = bool(reference.get("expected_executions_differ") or False)

        failures: list[str] = []

        # ---- run count -----------------------------------------------------
        if expected_run_count is not None and len(runs) != expected_run_count:
            failures.append(f"RUN COUNT: expected {expected_run_count} run(s), got {len(runs)}")

        all_execution_ids: list[str] = []
        grounding_total = 0
        grounding_hits = 0
        outcome_total = 0
        outcome_hits = 0
        invented_items = 0

        for idx, run in enumerate(runs):
            label = str(run.get("label") or f"run-{idx}")
            exec_ids = [str(x) for x in (run.get("enrichment_execution_ids") or [])]
            pre_ids = [str(x) for x in (run.get("preacquisition_execution_ids") or [])]
            all_execution_ids.extend(exec_ids)
            run_attempt_ids: list[str] = []

            expected_outcome = expected_outcomes[idx] if idx < len(expected_outcomes) else None
            expected_attempt = (
                expected_attempt_statuses[idx] if idx < len(expected_attempt_statuses) else None
            )
            expected_before = (
                expected_before_basis[idx] if idx < len(expected_before_basis) else None
            )
            expected_after = expected_after_basis[idx] if idx < len(expected_after_basis) else None

            for eid in exec_ids:
                env = by_id.get(eid)
                if env is None:
                    failures.append(f"{label}: enrichment execution {eid} not produced")
                    continue
                execution = envelope_payload_dict(env)
                outcome = str(execution.get("outcome") or "")
                before = str(execution.get("before_evidence_basis") or "")
                after = str(execution.get("after_evidence_basis") or "")
                plan_id = str(execution.get("plan_id") or "")
                attempt_ids = [str(x) for x in (execution.get("attempt_ids") or [])]
                result_ids = [str(x) for x in (execution.get("resulting_evidence_ids") or [])]
                run_attempt_ids.extend(attempt_ids)

                # provenance chain: execution -> plan -> paper identity. Only
                # measured when provenance links were supplied for this
                # execution, so a context without provenance reports "not
                # measured" rather than a false zero.
                if plan_id and plan_id in by_id and _parents(eid):
                    provenance_total += 1
                    linked = plan_id in _parents(eid)
                    plan_identity_id = str(
                        envelope_payload_dict(by_id[plan_id]).get("paper_identity_id") or ""
                    )
                    if (
                        linked
                        and plan_identity_id
                        and plan_identity_id in by_id
                        and _parents(plan_id)
                    ):
                        linked = plan_identity_id in _parents(plan_id)
                    if linked:
                        provenance_hits += 1

                outcome_total += 1
                if expected_outcome is None or outcome == expected_outcome:
                    outcome_hits += 1
                else:
                    failures.append(
                        f"{label}: execution outcome {outcome!r}, expected {expected_outcome!r}"
                    )
                if expected_before is not None and before != expected_before:
                    failures.append(
                        f"{label}: before basis {before!r}, expected {expected_before!r}"
                    )
                if expected_after is not None and after != expected_after:
                    failures.append(f"{label}: after basis {after!r}, expected {expected_after!r}")

                # grounding: enriched outcome requires strict basis upgrade
                grounded = True
                if expected_grounded:
                    if outcome == "enriched" and _BASIS_RANK.get(after, -1) <= _BASIS_RANK.get(
                        before, -1
                    ):
                        grounded = False
                        failures.append(
                            f"{label}: {eid} claims outcome {outcome} without a basis upgrade "
                            f"({before} -> {after})"
                        )
                    plan_env = by_id.get(plan_id)
                    if plan_env is None:
                        grounded = False
                        failures.append(f"{label}: execution {eid} plan {plan_id} not produced")
                    else:
                        plan = envelope_payload_dict(plan_env)
                        identity_id = str(plan.get("paper_identity_id") or "")
                        identity_produced = identity_id in by_id
                        if not identity_produced:
                            grounded = False
                            failures.append(
                                f"{label}: enrichment plan {plan_id} not grounded to a produced "
                                f"paper identity"
                            )
                        if _parents(plan_id) and identity_id not in _parents(plan_id):
                            failures.append(
                                f"{label}: enrichment plan {plan_id} not linked to its identity"
                            )
                    succeeded_attempt = False
                    for aid in attempt_ids:
                        a_env = by_id.get(aid)
                        if a_env is None:
                            continue
                        attempt = envelope_payload_dict(a_env)
                        if attempt.get("status") == "success":
                            succeeded_attempt = True
                            if (
                                expected_attempt is not None
                                and attempt.get("strategy") != "provider_get_abstract"
                            ):
                                failures.append(
                                    f"{label}: attempt {aid} strategy {attempt.get('strategy')!r}, "
                                    "expected provider_get_abstract"
                                )
                            if not result_ids:
                                grounded = False
                                failures.append(
                                    f"{label}: success attempt {aid} retrieved no evidence ids"
                                )
                    if outcome == "enriched" and not succeeded_attempt:
                        grounded = False
                        failures.append(
                            f"{label}: outcome enriched but no attempt succeeded for {eid}"
                        )
                grounding_total += 1
                if grounded:
                    grounding_hits += 1

            # ---- attempt statuses -------------------------------------------
            for aid in run_attempt_ids:
                a_env = by_id.get(aid)
                if a_env is None:
                    continue
                attempt = envelope_payload_dict(a_env)
                if attempt.get("status") in ("success", "partial"):
                    # acquired abstract must become a novelty-marked EvidenceItem
                    for rid in attempt.get("retrieved_artifact_ids") or []:
                        item_env = by_id.get(str(rid))
                        if item_env is None or item_env.artifact_type != "evidence_item":
                            continue
                        item = envelope_payload_dict(item_env)
                        meta = item.get("metadata") or {}
                        if (
                            str(attempt.get("status")) == "success"
                            and meta.get("novelty_enrichment") is not True
                        ):
                            failures.append(
                                f"{label}: acquired evidence item {rid} missing novelty_enrichment marker"
                            )

            # ---- unsupported enrichment rejection ---------------------------
            if expected_no_invented:
                for env in ctx.produced_artifacts:
                    if env.artifact_type != "evidence_item":
                        continue
                    item = envelope_payload_dict(env)
                    meta = item.get("metadata") or {}
                    if meta.get("novelty_enrichment") is True:
                        invented_items += 1
                        failures.append(
                            f"{label}: unsupported enrichment fabricated an evidence item"
                        )

            # ---- pre-acquisition --------------------------------------------
            if expected_preacquisition and not pre_ids:
                failures.append(f"{label}: expected pre-acquisition but none ran")
            if pre_ids:
                pre_env = by_id.get(pre_ids[0])
                if pre_env is not None:
                    pre = envelope_payload_dict(pre_env)
                    if not pre.get("selected_candidate_ids"):
                        failures.append(f"{label}: pre-acquisition selected no candidates")

        # ---- source preservation -------------------------------------------
        source_preserved = True
        if expected_source_preserved:
            paper_records = [
                envelope_payload_dict(e)
                for e in ctx.produced_artifacts
                if e.artifact_type == "paper_record"
            ]
            if not paper_records:
                source_preserved = False
                failures.append("SOURCE PRESERVATION: no paper records produced")

        # ---- stale reuse across runs ----------------------------------------
        # ``stale`` counts executions reused across runs; ``reuse_checked``
        # counts how many executions were *eligible* to be stale, so the rate
        # is an actual proportion. Using ``max(stale, 1)`` as the denominator
        # made the value always 0.0 or 1.0 -- a boolean dressed as a rate.
        stale = 0
        reuse_checked = 0
        if len(runs) > 1:
            seen: set[str] = set()
            for idx, run in enumerate(runs):
                ids = {str(x) for x in (run.get("enrichment_execution_ids") or [])}
                if idx > 0:
                    # Only executions compared against an earlier run can be stale.
                    reuse_checked += len(ids)
                overlap = ids & seen
                if overlap:
                    stale += len(overlap)
                    failures.append(
                        f"STALE REUSE: enrichment executions {sorted(overlap)} reused across runs"
                    )
                seen |= ids
        if expected_executions_differ and len(runs) > 1:
            a = {str(x) for x in (runs[0].get("enrichment_execution_ids") or [])}
            b = {str(x) for x in (runs[1].get("enrichment_execution_ids") or [])}
            if a & b:
                # Deliberately does not increment ``stale``: the loop above has
                # already counted this overlap, and counting it twice inflated
                # the metric.
                failures.append(
                    f"STALE REUSE: changed source set reused {len(a & b)} enrichment execution(s)"
                )

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "evidence_enrichment",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "enrichment_grounding_accuracy": _metric(
                "enrichment_grounding_accuracy",
                float(grounding_hits),
                grounding_total,
                "rate",
                "enrichment executions are grounded (basis upgrade, plan->identity, "
                "succeeded attempt with retrieved evidence)",
            ),
            "enrichment_outcome_accuracy": _metric(
                "enrichment_outcome_accuracy",
                float(outcome_hits),
                outcome_total,
                "rate",
                "enrichment outcomes match the expected fixture outcome",
            ),
            "source_preservation_accuracy": _metric(
                "source_preservation_accuracy",
                1.0 if (not expected_source_preserved or source_preserved) else 0.0,
                1,
                "rate",
                "original source records are preserved, not overwritten",
            ),
            "unsupported_rejection_accuracy": _metric(
                "unsupported_rejection_accuracy",
                1.0 if (not expected_no_invented or invented_items == 0) else 0.0,
                1,
                "rate",
                "failed/not_found enrichment never fabricates evidence",
            ),
            "stale_reuse_rate": _metric(
                "stale_reuse_rate",
                float(stale),
                reuse_checked,
                "rate",
                "share of enrichments eligible for cross-run comparison that were "
                "stale (reused from an earlier run)",
            ),
            "preacquisition_accuracy": _metric(
                "preacquisition_accuracy",
                1.0
                if (not expected_preacquisition)
                else (
                    0.0 if not runs or not (runs[0].get("preacquisition_execution_ids")) else 1.0
                ),
                1,
                "rate",
                "pre-acquisition runs when expected and selects candidates",
            ),
            "provenance_version_accuracy": _metric(
                "provenance_version_accuracy",
                float(provenance_hits),
                provenance_total,
                "rate",
                "enrichment execution -> plan -> paper identity provenance links hold",
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
                "run_count": len(runs),
                "enrichment_execution_ids": sorted(all_execution_ids),
                "metrics": metrics,
                "dimension_scores": {
                    "enrichment_grounding_accuracy": (
                        (grounding_hits / grounding_total) if grounding_total else 0.0
                    ),
                    "enrichment_outcome_accuracy": (
                        (outcome_hits / outcome_total) if outcome_total else 0.0
                    ),
                    "source_preservation_accuracy": (
                        1.0 if (not expected_source_preserved or source_preserved) else 0.0
                    ),
                    "unsupported_rejection_accuracy": (
                        1.0 if (not expected_no_invented or invented_items == 0) else 0.0
                    ),
                    "stale_reuse_rate": (stale / reuse_checked) if reuse_checked else 0.0,
                    "preacquisition_accuracy": (
                        1.0
                        if (not expected_preacquisition)
                        else (
                            0.0
                            if not runs or not (runs[0].get("preacquisition_execution_ids"))
                            else 1.0
                        )
                    ),
                    "provenance_version_accuracy": (
                        (provenance_hits / provenance_total) if provenance_total else 0.0
                    ),
                },
            },
            status=status,
            explanation="; ".join(failures) if failures else "all enrichment checks matched",
            evidence_artifact_ids=[e.artifact_id for e in reports],
        )


class EvidenceEnrichmentEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.evidence_enrichment",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic evidence-enrichment evaluator (Phase 7C)",
            provides=["evaluator.evidence_enrichment"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.evidence_enrichment", EvidenceEnrichmentEvaluator())
