"""evaluator.mechanism — deterministic mechanism-development evaluator
(Phase 6D).

Evaluates produced Phase 3A artifacts (GapSelection, MechanismAnalysis,
MechanismAnalysisExecution, MechanismCandidate, MechanismCritique,
SelectedMechanism) against the case reference. Deterministic structural
checks gate pass/fail; quality dimensions (coherence, clarity, relevance,
novelty) are left to advisory model-assisted evaluators.

Structural properties verified:
- all literature_supported elements have valid source ids (in the gap context)
- new_hypothesis is never labeled literature_supported
- modeling assumptions remain explicit
- the selected mechanism traces to the selected gap
- the original candidate remains immutable after revision
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

_KNOWLEDGE_BASES = {
    "literature_supported",
    "research_inference",
    "new_hypothesis",
    "modeling_assumption",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _contained(haystack: str, needle: str) -> bool:
    return bool(needle) and (_norm(needle) in _norm(haystack))


class MechanismEvaluator:
    evaluator_id = "evaluator.mechanism"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        executions = [
            e for e in ctx.produced_artifacts if e.artifact_type == "mechanism_analysis_execution"
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
                explanation="no mechanism_analysis_execution produced for the case",
            )
        exec_env = max(executions, key=lambda e: e.created_at)
        execution = envelope_payload_dict(exec_env)
        candidates_rejected = int(execution.get("candidates_rejected") or 0)

        candidate_envs = [
            e for e in ctx.produced_artifacts if e.artifact_type == "mechanism_candidate"
        ]
        candidates: list[dict[str, Any]] = [envelope_payload_dict(e) for e in candidate_envs]
        critiques = [
            envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "mechanism_critique"
        ]
        selected_envs = [
            e for e in ctx.produced_artifacts if e.artifact_type == "selected_mechanism"
        ]
        selected: list[dict[str, Any]] = [envelope_payload_dict(e) for e in selected_envs]
        selection_envs = [e for e in ctx.produced_artifacts if e.artifact_type == "gap_selection"]

        # context = the gap's grounding universe (produced statements + evidence)
        context_ids = {
            e.artifact_id
            for e in ctx.produced_artifacts
            if e.artifact_type in ("synthesis_statement", "evidence_item")
        }

        # the selected gap: the selection's selected_gap_id (candidates and
        # selected mechanisms trace to the originally selected gap artifact)
        selected_gap_id = (
            envelope_payload_dict(selection_envs[0]).get("selected_gap_id")
            if selection_envs
            else None
        )

        reference = ctx.case.reference or {}
        expected_candidates: dict[str, dict[str, Any]] = dict(
            reference.get("expected_candidates") or {}
        )
        expected_invalid = int(reference.get("expected_invalid_candidates") or 0)
        expected_issues: dict[str, list[dict[str, Any]]] = dict(
            reference.get("expected_critic_issues") or {}
        )
        expected_revision: dict[str, bool] = dict(reference.get("expected_revision") or {})
        expected_unsupported = int(reference.get("expected_unsupported_support") or 0)

        # ---- match candidates to expected (by name) --------------------
        matched_ids: set[str] = set()
        spec_by_cand_id: dict[str, dict[str, Any]] = {}
        for name, spec in expected_candidates.items():
            for env, cand in zip(candidate_envs, candidates, strict=True):
                if env.artifact_id in matched_ids:
                    continue
                if not (
                    _contained(cand.get("name") or "", name)
                    or _contained(name, cand.get("name") or "")
                ):
                    continue
                matched_ids.add(env.artifact_id)
                spec_by_cand_id[env.artifact_id] = spec
                break

        # ---- structural / grounding checks ------------------------------
        basis_mismatches: list[str] = []
        unsupported_elements: list[str] = []
        grounding_mismatches: list[str] = []
        gap_trace_mismatches: list[str] = []
        assumptions_missing: list[str] = []
        for env, cand in zip(candidate_envs, candidates, strict=True):
            cand_id = env.artifact_id
            if selected_gap_id is not None and cand.get("gap_id") != selected_gap_id:
                gap_trace_mismatches.append(
                    f"{cand.get('name')!r}: gap_id {cand.get('gap_id')} != selected gap {selected_gap_id}"
                )
            if not (cand.get("key_assumptions") or []):
                assumptions_missing.append(cand_id)
            for element in cand.get("grounding") or []:
                basis = element.get("basis")
                if basis not in _KNOWLEDGE_BASES:
                    basis_mismatches.append(
                        f"{cand.get('name')!r}: invalid knowledge basis {basis!r}"
                    )
                    continue
                source_ids = list(element.get("source_ids") or [])
                if basis == "literature_supported" and not source_ids:
                    basis_mismatches.append(
                        f"{cand.get('name')!r}: literature_supported element "
                        f"{str(element.get('element'))[:40]!r} has no source_ids"
                    )
                if basis == "new_hypothesis" and source_ids:
                    basis_mismatches.append(
                        f"{cand.get('name')!r}: new_hypothesis element carries source_ids"
                    )
                bad = [sid for sid in source_ids if sid not in context_ids]
                if bad:
                    unsupported_elements.append(
                        f"{cand.get('name')!r}: literature_supported element cites "
                        f"unknown ids {bad[:3]}"
                    )
            bad_support = [
                sid for sid in (cand.get("literature_support_ids") or []) if sid not in context_ids
            ]
            if bad_support:
                grounding_mismatches.append(
                    f"{cand.get('name')!r}: unsupported literature ids {bad_support[:3]}"
                )
            expected_papers = spec_by_cand_id.get(env.artifact_id, {}).get(
                "expected_support_papers"
            )
            if expected_papers is not None and int(
                cand.get("literature_support_papers") or 0
            ) != int(expected_papers):
                grounding_mismatches.append(
                    f"{cand.get('name')!r}: literature_support_papers "
                    f"{cand.get('literature_support_papers')} != expected {expected_papers}"
                )

        # ---- critic issue recall ---------------------------------------
        expected_issue_list = [
            (name, issue) for name, issues in expected_issues.items() for issue in issues
        ]
        found_issues = 0
        for name, issue in expected_issue_list:
            for cand_id, cand in zip(
                [e.artifact_id for e in candidate_envs], candidates, strict=True
            ):
                if not _contained(cand.get("name") or "", name):
                    continue
                cand_critiques = [
                    c for c in critiques if c.get("mechanism_candidate_id") == cand_id
                ]
                if any(
                    any(
                        _contained(i.get("category") or "", issue.get("category") or "")
                        or _contained(issue.get("category") or "", i.get("category") or "")
                        for i in c.get("issues") or []
                    )
                    and _contained(c.get("verdict") or "", issue.get("verdict") or "")
                    for c in cand_critiques
                ):
                    found_issues += 1
                break

        # ---- revision success -------------------------------------------
        revision_correct = 0
        revision_total = len(expected_revision)
        candidate_id_by_name: dict[str, str] = {}
        for env, cand in zip(candidate_envs, candidates, strict=True):
            candidate_id_by_name[_norm(cand.get("name") or "")] = env.artifact_id
        for name, should_revise in expected_revision.items():
            cand_id = candidate_id_by_name.get(_norm(name))
            if cand_id is None:
                continue
            cand = next(c for c in candidates if _contained(c.get("name") or "", name))
            mechanism = next(
                (s for s in selected if s.get("mechanism_candidate_id") == cand_id),
                None,
            )
            if mechanism is None:
                continue
            changed = any(
                mechanism.get(field) != cand.get(field)
                for field in ("name", "description", "causal_logic")
            )
            if changed == should_revise:
                revision_correct += 1

        # ---- selected mechanism validity --------------------------------
        selected_valid = 0
        for sm in selected:
            ok = True
            if selected_gap_id is not None and sm.get("gap_id") != selected_gap_id:
                ok = False
            if not (sm.get("key_assumptions") or []):
                ok = False
            for element in sm.get("grounding") or []:
                basis = element.get("basis")
                source_ids = list(element.get("source_ids") or [])
                if basis == "literature_supported" and (
                    not source_ids or any(sid not in context_ids for sid in source_ids)
                ):
                    ok = False
                if basis == "new_hypothesis" and source_ids:
                    ok = False
            if ok:
                selected_valid += 1
        selected_total = len(selected)

        # ---- metrics ---------------------------------------------------
        total_candidates = len(candidates)
        matched_count = len(matched_ids)
        precision = matched_count / total_candidates if total_candidates else 1.0
        expected_total = len(expected_candidates)
        recall = matched_count / expected_total if expected_total else 1.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        elements_total = sum(len(c.get("grounding") or []) for c in candidates)
        basis_ok = elements_total - len(basis_mismatches)
        grounding_ok = total_candidates - len(grounding_mismatches)
        unsupported_elements_count = len(unsupported_elements)
        trace_ok = total_candidates - len(gap_trace_mismatches)

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "mechanism",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "gap_alignment_accuracy": _metric(
                "gap_alignment_accuracy",
                float(trace_ok),
                total_candidates,
                "rate",
                "candidates tracing to the selected gap",
            ),
            "knowledge_basis_accuracy": _metric(
                "knowledge_basis_accuracy",
                float(basis_ok),
                elements_total,
                "rate",
                "grounding elements with valid bases and structural discipline",
            ),
            "grounding_accuracy": _metric(
                "grounding_accuracy",
                float(grounding_ok),
                total_candidates,
                "rate",
                "candidates whose literature support ids are valid and counts correct",
            ),
            "candidate_validity_rate": _metric(
                "candidate_validity_rate",
                float(matched_count),
                expected_total,
                "rate",
                "expected valid candidates that were produced",
            ),
            "unsupported_support_rate": _metric(
                "unsupported_support_rate",
                float(unsupported_elements_count),
                elements_total,
                "rate",
                "literature_supported elements citing unknown source ids",
            ),
            "critic_issue_recall": _metric(
                "critic_issue_recall",
                float(found_issues),
                len(expected_issue_list),
                "rate",
                "expected critique issues found in produced critiques",
            ),
            "revision_success_rate": _metric(
                "revision_success_rate",
                float(revision_correct),
                revision_total,
                "rate",
                "mechanisms revised exactly when expected",
            ),
            "selected_mechanism_validity": _metric(
                "selected_mechanism_validity",
                float(selected_valid),
                selected_total,
                "rate",
                "selected mechanisms that trace to the gap and keep grounding discipline",
            ),
            "candidate_validity_f1": _metric(
                "candidate_validity_f1",
                f1,
                1,
                "rate",
                "harmonic mean of produced-vs-expected candidate matching",
            ),
            "invalid_candidates_rejected": _metric(
                "invalid_candidates_rejected",
                float(candidates_rejected),
                1,
                "quantity",
                "candidates rejected by the generator's deterministic validation",
            ),
        }

        failures_detail: list[str] = []
        if basis_mismatches:
            failures_detail.append("KNOWLEDGE BASIS MISMATCHES: " + "; ".join(basis_mismatches))
        if unsupported_elements:
            failures_detail.append(
                f"UNSUPPORTED LITERATURE SUPPORT: {len(unsupported_elements)} elements"
            )
        if grounding_mismatches:
            failures_detail.append("GROUNDING MISMATCHES: " + "; ".join(grounding_mismatches))
        if gap_trace_mismatches:
            failures_detail.append("GAP TRACE MISMATCHES: " + "; ".join(gap_trace_mismatches))
        if assumptions_missing:
            failures_detail.append(
                f"MODELING ASSUMPTIONS NOT EXPLICIT: {len(assumptions_missing)} candidates"
            )
        if found_issues != len(expected_issue_list):
            failures_detail.append(
                f"CRITIC ISSUES MISSING: {len(expected_issue_list) - found_issues}"
            )
        if revision_correct != revision_total:
            failures_detail.append(f"REVISION MISMATCHES: {revision_correct}/{revision_total}")
        if candidates_rejected != expected_invalid:
            failures_detail.append(
                f"INVALID CANDIDATE REJECTION: rejected {candidates_rejected}, "
                f"expected {expected_invalid}"
            )
        if unsupported_elements_count > expected_unsupported:
            failures_detail.append(
                f"UNSUPPORTED SUPPORT ABOVE EXPECTED: {unsupported_elements_count} > "
                f"{expected_unsupported}"
            )
        if matched_count != expected_total:
            failures_detail.append(
                f"CANDIDATES MISSING: {expected_total - matched_count} expected candidates"
            )
        if total_candidates > matched_count:
            failures_detail.append(
                f"EXTRA CANDIDATES: {total_candidates - matched_count} produced not expected"
            )
        if selected_valid != selected_total:
            failures_detail.append(f"INVALID SELECTED MECHANISM: {selected_total - selected_valid}")

        status = EvaluatorStatus.failed if failures_detail else EvaluatorStatus.passed

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=recall if expected_total else None,
            value={
                "matched_candidate_ids": sorted(matched_ids),
                "selected_gap_id": selected_gap_id,
                "candidates_rejected": candidates_rejected,
                "basis_mismatches": basis_mismatches,
                "unsupported_elements": unsupported_elements,
                "grounding_mismatches": grounding_mismatches,
                "gap_trace_mismatches": gap_trace_mismatches,
                "assumptions_missing": assumptions_missing,
                "found_critic_issues": found_issues,
                "revision_correct": revision_correct,
                "selected_valid": selected_valid,
                "metrics": metrics,
                "dimension_scores": {
                    k: v
                    for k, v in {
                        "gap_alignment_accuracy": (
                            trace_ok / total_candidates if total_candidates else None
                        ),
                        "knowledge_basis_accuracy": (
                            basis_ok / elements_total if elements_total else None
                        ),
                        "grounding_accuracy": (
                            grounding_ok / total_candidates if total_candidates else None
                        ),
                        "candidate_validity_rate": precision,
                        "unsupported_support_rate": (
                            unsupported_elements_count / elements_total if elements_total else 0.0
                        ),
                        "critic_issue_recall": (
                            found_issues / len(expected_issue_list) if expected_issue_list else None
                        ),
                        "revision_success_rate": (
                            revision_correct / revision_total if revision_total else None
                        ),
                        "selected_mechanism_validity": (
                            selected_valid / selected_total if selected_total else None
                        ),
                        "candidate_validity_f1": f1,
                        "invalid_candidates_rejected": float(candidates_rejected),
                    }.items()
                    if v is not None
                },
            },
            status=status,
            explanation="; ".join(failures_detail)
            if failures_detail
            else "all mechanism checks matched",
            evidence_artifact_ids=[
                exec_env.artifact_id,
                *[e.artifact_id for e in candidate_envs],
                *[e.artifact_id for e in selected_envs],
            ],
        )


class MechanismEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.mechanism",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic mechanism-development evaluator (Phase 6D)",
            provides=["evaluator.mechanism"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.mechanism", MechanismEvaluator())
