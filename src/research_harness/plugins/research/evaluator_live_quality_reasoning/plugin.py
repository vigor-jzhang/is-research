"""evaluator.live_quality_reasoning — deterministic live-quality evaluator for
the reasoning role (Phase 7D.0).

Validates a real model's output STRUCTURALLY against model-agnostic references
(no exact scripted-output matching): structured-output success, grounding
correctness, unsupported-reference rate, instruction adherence, required-field
completeness, deterministic downstream validation, and task completion.
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

_REF_FIELDS = {
    "evidence_item": ["source_artifact_id"],
    "synthesis_statement": [
        "supporting_evidence_ids",
        "conflicting_evidence_ids",
        "supporting_paper_identity_ids",
        "conflicting_paper_identity_ids",
    ],
    "research_gap": [
        "supporting_synthesis_statement_ids",
        "supporting_evidence_ids",
        "contradiction_statement_ids",
        "relevant_paper_identity_ids",
    ],
    "mechanism_candidate": ["gap_id", "literature_support_ids"],
    "formal_analytical_model": ["selected_mechanism_id", "gap_id"],
    "proposition": [
        "model_id",
        "equilibrium_candidate_id",
        "comparative_statics_analysis_id",
        "supporting_static_ids",
    ],
}


def _concept_present(text: str, concept: str) -> bool:
    return concept.lower() in text.lower()


class LiveQualityReasoningEvaluator:
    evaluator_id = "evaluator.live_quality_reasoning"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        reference = ctx.case.reference or {}
        task = str(reference.get("task") or ctx.case.input.get("task") or "")
        required_concepts = list(reference.get("required_concepts") or [])
        allowed_gap_types = {str(t) for t in (reference.get("allowed_gap_types") or [])}
        required_model_structure = list(
            reference.get("required_model_structure")
            or ["actors", "variables", "parameters", "payoffs", "timing"]
        )

        by_id = {e.artifact_id: e for e in ctx.produced_artifacts}
        produced_ids = set(by_id)
        by_type: dict[str, list[dict[str, Any]]] = {}
        for env in ctx.produced_artifacts:
            by_type.setdefault(env.artifact_type, []).append(envelope_payload_dict(env))

        failures: list[str] = []
        total_refs = 0
        unsupported_refs = 0
        grounding_ok_count = 0
        critical_grounding = 0
        verifications: list[dict[str, Any]] = []
        downstream_ok = True

        def _check_refs(payload: dict[str, Any], artifact_type: str, label: str) -> None:
            nonlocal total_refs, unsupported_refs, grounding_ok_count, critical_grounding
            for field in _REF_FIELDS.get(artifact_type, []):
                for rid in payload.get(field) or []:
                    total_refs += 1
                    if str(rid) in produced_ids:
                        grounding_ok_count += 1
                    else:
                        unsupported_refs += 1
                        critical_grounding += 1
                        failures.append(
                            f"{label}: unsupported reference {rid!r} in {field} "
                            "(not produced by this run)"
                        )

        # ---- task-specific structural checks --------------------------------
        if task == "evidence_extraction":
            items = by_type.get("evidence_item") or []
            if not items:
                critical_grounding += 1
                failures.append("evidence_extraction: no evidence_item produced")
            for idx, item in enumerate(items):
                _check_refs(item, "evidence_item", f"evidence[{idx}]")
                locator = item.get("locator") or {}
                raw_pages = locator.get("pages") or (
                    [locator.get("page")] if locator.get("page") else []
                )
                pages: list[int] = []
                for p in raw_pages:
                    try:
                        pages.append(int(str(p)))
                    except (TypeError, ValueError):
                        pages.append(0)
                doc_env = by_id.get(str(item.get("source_artifact_id") or ""))
                doc = envelope_payload_dict(doc_env) if doc_env else {}
                page_count = 0
                try:
                    page_count = int(doc.get("page_count") or 0)
                except (TypeError, ValueError):
                    page_count = 0
                for p in pages:
                    if page_count and (p < 1 or p > page_count):
                        critical_grounding += 1
                        failures.append(
                            f"evidence[{idx}]: locator page {p} outside document pages 1..{page_count}"
                        )
                if not str(item.get("statement") or "").strip():
                    failures.append(f"evidence[{idx}]: empty statement (required field)")
            all_text = " ".join(str(i.get("statement") or "") for i in items)

        elif task == "literature_synthesis":
            statements = by_type.get("synthesis_statement") or []
            if not statements:
                critical_grounding += 1
                failures.append("literature_synthesis: no synthesis_statement produced")
            for idx, st in enumerate(statements):
                _check_refs(st, "synthesis_statement", f"statement[{idx}]")
                if not str(st.get("statement") or "").strip():
                    failures.append(f"statement[{idx}]: empty statement (required field)")
            all_text = " ".join(str(s.get("statement") or "") for s in statements)

        elif task == "gap_analysis":
            gaps = by_type.get("research_gap") or []
            if not gaps:
                critical_grounding += 1
                failures.append("gap_analysis: no research_gap produced")
            for idx, gap in enumerate(gaps):
                _check_refs(gap, "research_gap", f"gap[{idx}]")
                gap_type = str(gap.get("gap_type") or "")
                if allowed_gap_types and gap_type not in allowed_gap_types:
                    failures.append(
                        f"gap[{idx}]: gap_type {gap_type!r} not in allowed {sorted(allowed_gap_types)}"
                    )
                if not str(gap.get("description") or "").strip():
                    failures.append(f"gap[{idx}]: empty description (required field)")
            all_text = " ".join(
                f"{g.get('title') or ''} {g.get('description') or ''}" for g in gaps
            )

        elif task == "mechanism_development":
            candidates = by_type.get("mechanism_candidate") or []
            if not candidates:
                critical_grounding += 1
                failures.append("mechanism_development: no mechanism_candidate produced")
            for idx, m in enumerate(candidates):
                _check_refs(m, "mechanism_candidate", f"mechanism[{idx}]")
                for field in ("name", "description", "causal_logic"):
                    if not str(m.get(field) or "").strip():
                        failures.append(f"mechanism[{idx}]: empty {field} (required field)")
            all_text = " ".join(
                f"{m.get('name') or ''} {m.get('description') or ''} {m.get('causal_logic') or ''}"
                for m in candidates
            )

        elif task == "analytical_model_specification":
            models = by_type.get("formal_analytical_model") or []
            if not models:
                critical_grounding += 1
                failures.append("model_specification: no formal_analytical_model produced")
            for idx, m in enumerate(models):
                _check_refs(m, "formal_analytical_model", f"model[{idx}]")
                for field in required_model_structure:
                    value = m.get(field)
                    if not value or (isinstance(value, list) and not value):
                        failures.append(f"model[{idx}]: missing required structure {field!r}")
                if not str(m.get("description") or "").strip():
                    failures.append(f"model[{idx}]: empty description (required field)")
            all_text = " ".join(str(m.get("description") or "") for m in models)

        elif task == "proposition_generation":
            props = by_type.get("proposition") or []
            verifications = by_type.get("proposition_verification") or []
            if not props:
                critical_grounding += 1
                failures.append("proposition_generation: no proposition produced")
            downstream_ok = True
            if verifications:
                downstream_ok = all(str(v.get("status") or "") == "passed" for v in verifications)
                if not downstream_ok:
                    critical_grounding += 1
                    failures.append(
                        "proposition_generation: deterministic verification did not pass"
                    )
            else:
                failures.append("proposition_generation: no proposition_verification produced")
            for idx, p in enumerate(props):
                _check_refs(p, "proposition", f"proposition[{idx}]")
                if not str(p.get("statement") or "").strip():
                    failures.append(f"proposition[{idx}]: empty statement (required field)")
            all_text = " ".join(str(p.get("statement") or "") for p in props)
        else:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation=f"live-quality reasoning: unknown task {task!r}",
            )

        # ---- instruction adherence (required concepts) ----------------------
        missing_concepts = [c for c in required_concepts if not _concept_present(all_text, c)]
        if missing_concepts:
            failures.append(f"instruction_adherence: required concepts missing: {missing_concepts}")

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "live_quality_reasoning",
                "definition": definition,
            }

        grounding_correctness = (
            grounding_ok_count / total_refs if total_refs else (1.0 if not failures else 0.0)
        )
        unsupported_rate = unsupported_refs / total_refs if total_refs else 0.0
        instruction_ok = not missing_concepts
        fields_ok = not any("required field" in f for f in failures)
        completed = ctx.case.input.get("_completed", True)

        metrics: dict[str, dict[str, Any]] = {
            "structured_output_success": _metric(
                "structured_output_success",
                float(1.0 if not any("no " in f and "produced" in f for f in failures) else 0.0),
                1,
                "rate",
                "the task's main artifact was produced and parsed",
            ),
            "grounding_correctness": _metric(
                "grounding_correctness",
                grounding_correctness,
                max(total_refs, 1),
                "rate",
                "produced artifacts reference only artifacts produced by the run",
            ),
            "unsupported_reference_rate": _metric(
                "unsupported_reference_rate",
                unsupported_rate,
                max(total_refs, 1),
                "rate",
                "references to ids not produced by the run (hallucination)",
            ),
            "instruction_adherence": _metric(
                "instruction_adherence",
                1.0 if instruction_ok else 0.0,
                1 if required_concepts else 0,
                "rate",
                "required concepts appear in the produced content",
            ),
            "required_field_completeness": _metric(
                "required_field_completeness",
                1.0 if fields_ok else 0.0,
                1,
                "rate",
                "all required fields are non-empty",
            ),
            "deterministic_downstream_pass": _metric(
                "deterministic_downstream_pass",
                1.0
                if task != "proposition_generation" or (verifications and downstream_ok)
                else 0.0,
                1,
                "rate",
                "deterministic downstream validation passed (e.g. proposition verification)",
            ),
            "task_completion_rate": _metric(
                "task_completion_rate",
                1.0 if completed else 0.0,
                1,
                "rate",
                "the task ran to completion without errors",
            ),
            "critical_grounding_failures": _metric(
                "critical_grounding_failures",
                float(critical_grounding),
                max(critical_grounding, 1),
                "quantity",
                "unsupported references, out-of-range locators, or failed downstream validation",
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
                "task": task,
                "unsupported_references": unsupported_refs,
                "critical_grounding_failures": critical_grounding,
                "metrics": metrics,
                "dimension_scores": {
                    "structured_output_success": (
                        1.0 if not any("no " in f and "produced" in f for f in failures) else 0.0
                    ),
                    "grounding_correctness": grounding_correctness,
                    "unsupported_reference_rate": unsupported_rate,
                    "instruction_adherence": float(instruction_ok),
                    "required_field_completeness": float(fields_ok),
                    "deterministic_downstream_pass": (
                        1.0
                        if task != "proposition_generation" or (verifications and downstream_ok)
                        else 0.0
                    ),
                    "task_completion_rate": float(completed),
                },
            },
            status=status,
            explanation="; ".join(failures)
            if failures
            else "live-quality reasoning checks matched",
        )


class LiveQualityReasoningEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.live_quality_reasoning",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic live-quality reasoning evaluator (Phase 7D.0)",
            provides=["evaluator.live_quality_reasoning"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.live_quality_reasoning", LiveQualityReasoningEvaluator())
