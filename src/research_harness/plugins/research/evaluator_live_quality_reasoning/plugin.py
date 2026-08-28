"""evaluator.live_quality_reasoning — deterministic live-quality evaluator for
the reasoning role (Phase 7D.0).

Validates a real model's output STRUCTURALLY against model-agnostic references
(no exact scripted-output matching): structured-output success, grounding
correctness, unsupported-reference rate, instruction adherence, required-field
completeness, deterministic downstream validation, and task completion.

Phase 7D.3B adds task-specific FAILURE DIAGNOSTICS for the remaining reasoning
tasks (gap analysis, mechanism generation, model specification, proposition
generation). Diagnostics are persisted separately and NEVER change the pass
criteria.
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

_KNOWLEDGE_BASIS = {
    "literature_supported",
    "research_inference",
    "new_hypothesis",
    "modeling_assumption",
}

_SWEEPING_CLAIM_TERMS = (
    "first",
    "first-ever",
    "novel",
    "unprecedented",
    "definitive",
    "the first",
    "no study",
    "never before",
    "for the first time",
    "proves",
)


def _concept_present(text: str, concept: str) -> bool:
    return concept.lower() in text.lower()


def _parens_balanced(expr: str) -> bool:
    depth = 0
    for ch in expr:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _sweeping_claim(text: str) -> bool:
    t = text.lower()
    return any(term in t for term in _SWEEPING_CLAIM_TERMS)


def _refs(payload: dict[str, Any], field: str) -> list[str]:
    value = payload.get(field)
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return [str(value)] if value else []


# ---------------------------------------------------------------------------
# Phase 7D.3B: task-specific diagnostics (pure, deterministic)
# ---------------------------------------------------------------------------


def gap_diagnostics(
    gaps: list[dict[str, Any]],
    *,
    produced_ids: set[str],
    allowed_gap_types: set[str],
) -> dict[str, int]:
    diag: dict[str, int] = {
        "hallucinated_synthesis_evidence_refs": 0,
        "unsupported_gap": 0,
        "incorrect_gap_type": 0,
        "sweeping_novelty_claim": 0,
        "support_count_mismatch": 0,
        "structured_output_failure": 0,
    }
    if not gaps:
        diag["structured_output_failure"] += 1
        return diag
    for gap in gaps:
        unsupported = sum(
            1
            for f in (
                "supporting_synthesis_statement_ids",
                "supporting_evidence_ids",
                "contradiction_statement_ids",
                "relevant_paper_identity_ids",
            )
            for rid in _refs(gap, f)
            if rid not in produced_ids
        )
        diag["hallucinated_synthesis_evidence_refs"] += unsupported
        anchored = any(
            _refs(gap, f)
            for f in (
                "supporting_synthesis_statement_ids",
                "supporting_evidence_ids",
                "contradiction_statement_ids",
                "relevant_paper_identity_ids",
            )
        )
        if not anchored:
            diag["unsupported_gap"] += 1
        gap_type = str(gap.get("gap_type") or "")
        if allowed_gap_types and gap_type not in allowed_gap_types:
            diag["incorrect_gap_type"] += 1
        text = f"{gap.get('title') or ''} {gap.get('description') or ''}"
        if _sweeping_claim(text):
            diag["sweeping_novelty_claim"] += 1
        ev_ids = _refs(gap, "supporting_evidence_ids")
        paper_ids = _refs(gap, "relevant_paper_identity_ids")
        declared_evidence = gap.get("supporting_evidence_items")
        declared_papers = gap.get("supporting_papers")
        if (declared_evidence is not None and int(declared_evidence) != len(ev_ids)) or (
            declared_papers is not None and int(declared_papers) != len(paper_ids)
        ):
            diag["support_count_mismatch"] += 1
    return diag


def mechanism_diagnostics(
    candidates: list[dict[str, Any]],
    *,
    produced_ids: set[str],
    gaps: list[dict[str, Any]],
) -> dict[str, int]:
    diag: dict[str, int] = {
        "invalid_literature_support": 0,
        "knowledge_basis_misclassification": 0,
        "unsupported_source_ids": 0,
        "weak_gap_alignment": 0,
        "missing_actor_incentive": 0,
        "structurally_invalid_candidate": 0,
    }
    if not candidates:
        diag["structurally_invalid_candidate"] += 1
        return diag
    for m in candidates:
        support = _refs(m, "literature_support_ids")
        unsupported = [rid for rid in support if rid not in produced_ids]
        if support and not unsupported:
            pass
        if unsupported:
            diag["invalid_literature_support"] += 1
            diag["unsupported_source_ids"] += len(unsupported)
        for element in m.get("grounding") or []:
            basis = str(element.get("knowledge_basis") or element.get("basis") or "")
            if basis and basis not in _KNOWLEDGE_BASIS:
                diag["knowledge_basis_misclassification"] += 1
        if not _refs(m, "literature_support_ids"):
            diag["weak_gap_alignment"] += 1
        if gaps and not unsupported:
            gap_title = str(gaps[0].get("title") or "")
            terms = [
                w for w in gap_title.lower().replace(":", " ").split() if len(w) > 3 and w.isalpha()
            ]
            mtext = (
                f"{m.get('name') or ''} {m.get('description') or ''} "
                f"{m.get('causal_logic') or ''}".lower()
            )
            if terms and not any(t in mtext for t in terms):
                diag["weak_gap_alignment"] += 1
        if not (m.get("actors") or []) or not (m.get("incentives") or []):
            diag["missing_actor_incentive"] += 1
        for field in ("name", "description", "causal_logic"):
            if not str(m.get(field) or "").strip():
                diag["structurally_invalid_candidate"] += 1
    return diag


def model_specification_diagnostics(
    models: list[dict[str, Any]],
    *,
    produced_ids: set[str],
) -> dict[str, int]:
    diag: dict[str, int] = {
        "undefined_symbols": 0,
        "duplicate_symbols": 0,
        "invalid_ownership": 0,
        "timing_inconsistency": 0,
        "information_structure_inconsistency": 0,
        "unsupported_assumption_grounding": 0,
        "missing_payoff": 0,
        "malformed_mathematical_expression": 0,
    }
    if not models:
        diag["malformed_mathematical_expression"] += 1
        return diag
    for model in models:
        actors = [
            str(a.get("actor_id") or a.get("id") or a.get("name") or "")
            for a in model.get("actors") or []
        ]
        actor_ids = {a for a in actors if a}
        variables = list(model.get("variables") or [])
        parameters = list(model.get("parameters") or [])
        var_symbols = {str(v.get("symbol") or "") for v in variables if v.get("symbol")}
        param_symbols = {str(p.get("symbol") or "") for p in parameters if p.get("symbol")}
        all_symbols = var_symbols | param_symbols
        seen: set[str] = set()
        for v in variables:
            sym = str(v.get("symbol") or "")
            if sym:
                if sym in seen:
                    diag["duplicate_symbols"] += 1
                seen.add(sym)
        for p in parameters:
            sym = str(p.get("symbol") or "")
            if sym:
                if sym in seen:
                    diag["duplicate_symbols"] += 1
                seen.add(sym)
        for v in variables:
            owner = str(v.get("owner_actor_id") or "")
            if owner and owner not in actor_ids:
                diag["invalid_ownership"] += 1
        for stage in model.get("timing") or []:
            for aid in stage.get("actor_ids") or []:
                if aid and aid != "nature" and aid not in actor_ids:
                    diag["timing_inconsistency"] += 1
        info = model.get("information_structure") or {}
        for item in info.get("items") or []:
            for sym in item.get("variable_symbols") or []:
                if sym and sym not in all_symbols:
                    diag["information_structure_inconsistency"] += 1
        for unc in info.get("uncertainty") or []:
            sym = str(unc.get("variable_symbol") or "")
            if sym and sym not in all_symbols:
                diag["information_structure_inconsistency"] += 1
        for assumption in model.get("assumptions") or []:
            basis = str(assumption.get("knowledge_basis") or "")
            sources = _refs(assumption, "source_ids")
            if (basis == "literature_supported" or not basis) and sources:
                unsupported = [rid for rid in sources if rid not in produced_ids]
                diag["unsupported_assumption_grounding"] += len(unsupported)
        payoff_actors = {str(p.get("actor_id") or "") for p in model.get("payoffs") or []}
        for aid in actor_ids:
            if aid not in payoff_actors:
                diag["missing_payoff"] += 1
        for payoff in model.get("payoffs") or []:
            expr = payoff.get("expression") or {}
            expression = str(expr.get("expression") or "")
            symbols_used = _refs(expr, "symbols_used")
            if expression and not _parens_balanced(expression):
                diag["malformed_mathematical_expression"] += 1
            for sym in symbols_used:
                if sym and sym not in all_symbols:
                    diag["undefined_symbols"] += 1
    return diag


def proposition_diagnostics(
    props: list[dict[str, Any]],
    *,
    produced_ids: set[str],
    verifications: list[dict[str, Any]],
) -> dict[str, int]:
    diag: dict[str, int] = {
        "hallucinated_static_id": 0,
        "incorrect_expected_sign": 0,
        "missing_conditions": 0,
        "invalid_equality": 0,
        "unsupported_proposition": 0,
        "structured_output_failure": 0,
    }
    if not props:
        diag["structured_output_failure"] += 1
        return diag
    allowed_signs = {"positive", "negative", "zero"}
    for p in props:
        unsupported = sum(
            1
            for f in ("model_id", "equilibrium_candidate_id", "comparative_statics_analysis_id")
            for rid in _refs(p, f)
            if rid not in produced_ids
        )
        for rid in _refs(p, "supporting_static_ids"):
            if rid not in produced_ids:
                unsupported += 1
        diag["hallucinated_static_id"] += unsupported
        sign = str(p.get("expected_sign") or "")
        if sign and sign not in allowed_signs:
            diag["incorrect_expected_sign"] += 1
        if not (p.get("conditions") or []):
            diag["missing_conditions"] += 1
        math = p.get("mathematical_form") or {}
        expression = str(math.get("expression") or "")
        if expression and not _parens_balanced(expression):
            diag["invalid_equality"] += 1
    if not verifications or not all(str(v.get("status") or "") == "passed" for v in verifications):
        diag["unsupported_proposition"] += 1
    return diag


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
        task_diagnostics: dict[str, int] = {}

        def _check_refs(payload: dict[str, Any], artifact_type: str, label: str) -> None:
            nonlocal total_refs, unsupported_refs, grounding_ok_count, critical_grounding
            for field in _REF_FIELDS.get(artifact_type, []):
                value = payload.get(field)
                # scalar reference fields (source_artifact_id, gap_id, model_id,
                # ...) are single ids; only list fields carry multiple ids
                refs = value if isinstance(value, list) else ([value] if value else [])
                for rid in refs:
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
            task_diagnostics = gap_diagnostics(
                gaps, produced_ids=produced_ids, allowed_gap_types=allowed_gap_types
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
            task_diagnostics = mechanism_diagnostics(
                candidates, produced_ids=produced_ids, gaps=by_type.get("research_gap") or []
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
            task_diagnostics = model_specification_diagnostics(models, produced_ids=produced_ids)

        elif task == "proposition_generation":
            props = by_type.get("proposition") or []
            verifications = by_type.get("proposition_verification") or []
            if not props:
                critical_grounding += 1
                failures.append("proposition_generation: no proposition produced")
            downstream_ok = True
            if verifications:
                # Genuine defect repair (Phase 7D.3B): the production verifier
                # writes PropositionVerification.status in the enum vocabulary
                # (verified/conditionally_verified/failed), never the literal
                # "passed". A verified verification is a pass; requiring the
                # literal "passed" made proposition_generation fail for every
                # correct model.
                downstream_ok = all(
                    str(v.get("status") or "") in ("passed", "verified") for v in verifications
                )
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
            task_diagnostics = proposition_diagnostics(
                props, produced_ids=produced_ids, verifications=verifications
            )
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
                "task_diagnostics": task_diagnostics,
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
