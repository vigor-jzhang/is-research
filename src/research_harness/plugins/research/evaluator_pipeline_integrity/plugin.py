"""evaluator.pipeline_integrity — deterministic end-to-end pipeline evaluator
(Phase 6H).

Evaluates the research-pipeline-e2e-v1 benchmark: one run of the real
production chain (retrieval -> screening -> evidence -> synthesis -> gap ->
mechanism -> model -> equilibrium -> propositions -> numerical -> results ->
manuscript -> citation formatting). Ground truth is recomputed from the
produced artifacts: stage presence, provenance links, grounding references,
condition preservation, citation identity, and bibliography fidelity.

Critical deterministic failures:
- broken provenance chain
- unsupported evidence/claim
- invalid equilibrium accepted
- required condition lost downstream
- wrong citation identity
- invented bibliography metadata
"""

from __future__ import annotations

from typing import Any

import sympy

from research_harness.contracts.evaluator import EvaluatorContext, envelope_payload_dict
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.schemas.evaluation import (
    EvaluatorCategory,
    EvaluatorResult,
    EvaluatorStatus,
)


def _sympy_equivalent(a: str, b: str) -> bool:
    try:
        expr_a = sympy.sympify(a)
        expr_b = sympy.sympify(b)
        diff = sympy.simplify(sympy.cancel(sympy.together(expr_a - expr_b)))  # type: ignore[operator]
        return bool(diff == 0)
    except Exception:  # noqa: BLE001
        return False


class PipelineIntegrityEvaluator:
    evaluator_id = "evaluator.pipeline_integrity"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        produced_ids = {e.artifact_id for e in ctx.produced_artifacts}
        types_present: dict[str, list[str]] = {}
        payload_by_id: dict[str, dict[str, Any]] = {}
        for env in ctx.produced_artifacts:
            types_present.setdefault(env.artifact_type, []).append(env.artifact_id)
            payload_by_id[env.artifact_id] = envelope_payload_dict(env)

        reference = ctx.case.reference or {}
        expected_stages = dict(reference.get("expected_stages") or {})
        expected_provenance = [
            (str(a), str(b)) for a, b in reference.get("expected_provenance") or []
        ]
        expected_equilibrium = dict(reference.get("expected_equilibrium") or {})
        expected_conditions = [str(c) for c in reference.get("expected_conditions") or []]
        expected_baseline = dict(reference.get("expected_baseline") or {})
        expected_citation_identity = dict(reference.get("expected_citation_identity") or {})

        failures_detail: list[str] = []
        deterministic_failures = 0

        # ---- stage completion --------------------------------------------
        stages_ok = 0
        for stage, artifact_type in expected_stages.items():
            if types_present.get(artifact_type):
                stages_ok += 1
            else:
                failures_detail.append(f"STAGE MISSING {stage}: no {artifact_type} produced")
        stage_total = len(expected_stages)

        # ---- provenance chain --------------------------------------------
        provenance_ok = 0
        for source_type, target_type in expected_provenance:
            found = False
            for env in ctx.produced_artifacts:
                if env.artifact_type != target_type:
                    continue
                parent_ids = {
                    getattr(link, "source_artifact_id", None)
                    for link in ctx.provenance.get(env.artifact_id, [])
                }
                if any(
                    e.artifact_id in parent_ids and e.artifact_type == source_type
                    for e in ctx.produced_artifacts
                ):
                    found = True
                    break
            if found:
                provenance_ok += 1
            else:
                failures_detail.append(
                    f"BROKEN PROVENANCE CHAIN: no {source_type} -> {target_type} link"
                )
        provenance_total = len(expected_provenance)

        # ---- grounding integrity -----------------------------------------
        grounding_ok = 0
        grounding_total = 0
        for env in ctx.produced_artifacts:
            payload = envelope_payload_dict(env)
            if env.artifact_type == "synthesis_statement":
                for eid in payload.get("supporting_evidence_ids") or []:
                    grounding_total += 1
                    if eid in produced_ids:
                        grounding_ok += 1
                    else:
                        failures_detail.append(
                            f"UNSUPPORTED EVIDENCE: synthesis cites unknown {eid}"
                        )
            elif env.artifact_type == "research_gap":
                for sid in (payload.get("supporting_synthesis_statement_ids") or []) + (
                    payload.get("contradiction_statement_ids") or []
                ):
                    grounding_total += 1
                    if sid in produced_ids:
                        grounding_ok += 1
                    else:
                        failures_detail.append(
                            f"UNSUPPORTED SYNTHESIS REF: gap cites unknown {sid}"
                        )
                for eid in payload.get("supporting_evidence_ids") or []:
                    grounding_total += 1
                    if eid in produced_ids:
                        grounding_ok += 1
                    else:
                        failures_detail.append(f"UNSUPPORTED EVIDENCE REF: gap cites unknown {eid}")
            elif env.artifact_type == "research_finding":
                for pid in (
                    (payload.get("supporting_proposition_ids") or [])
                    + (payload.get("supporting_comparative_static_ids") or [])
                    + (payload.get("supporting_numerical_result_ids") or [])
                ):
                    grounding_total += 1
                    if pid in produced_ids:
                        grounding_ok += 1
                    else:
                        failures_detail.append(
                            f"UNSUPPORTED FINDING SUPPORT: finding cites unknown {pid}"
                        )
            elif env.artifact_type == "manuscript_section":
                for claim in payload.get("claims") or []:
                    grounding_total += 1
                    gid = claim.get("grounding_artifact_id")
                    if gid is None or gid in produced_ids:
                        grounding_ok += 1
                    else:
                        failures_detail.append(
                            f"UNSUPPORTED CLAIM: manuscript claim references unknown {gid}"
                        )

        # ---- equilibrium validity ----------------------------------------
        candidate_exprs: dict[str, str] = {}
        candidate_envs = types_present.get("equilibrium_candidate") or []
        if candidate_envs:
            candidate_env = max(
                (e for e in ctx.produced_artifacts if e.artifact_id in candidate_envs),
                key=lambda e: e.created_at,
            )
            candidate = envelope_payload_dict(candidate_env)
            candidate_exprs = {
                e.get("variable"): e.get("expression", {}).get("expression", "")
                for e in candidate.get("expressions") or []
            }
        for variable, expected_expr in expected_equilibrium.items():
            produced_expr = candidate_exprs.get(variable)
            if produced_expr is None or not _sympy_equivalent(produced_expr, expected_expr):
                failures_detail.append(
                    f"INVALID EQUILIBRIUM ACCEPTED: {variable} produced "
                    f"{produced_expr!r}, expected {expected_expr!r}"
                )

        # ---- condition preservation downstream ---------------------------
        condition_ok = 0
        condition_total = len(expected_conditions)
        produced_text = ""
        for env in ctx.produced_artifacts:
            payload = envelope_payload_dict(env)
            if env.artifact_type == "equilibrium_candidate":
                for expression in payload.get("expressions") or []:
                    produced_text += " | ".join(expression.get("conditions") or []) + " "
            elif env.artifact_type == "proposition" or env.artifact_type == "research_finding":
                produced_text += " | ".join(payload.get("conditions") or []) + " "
            elif env.artifact_type == "manuscript_section":
                for claim in payload.get("claims") or []:
                    produced_text += " | ".join(claim.get("conditions") or []) + " "
        for cond in expected_conditions:
            if cond in produced_text:
                condition_ok += 1
            else:
                failures_detail.append(f"CONDITION LOST DOWNSTREAM: {cond!r}")

        # ---- numerical agreement -----------------------------------------
        if expected_baseline:
            baseline_found = False
            for env in ctx.produced_artifacts:
                if env.artifact_type != "numerical_result":
                    continue
                payload = envelope_payload_dict(env)
                if payload.get("scenario") != "baseline":
                    continue
                baseline_found = True
                for variable, expected_value in expected_baseline.items():
                    produced_value = (payload.get("outcomes") or {}).get(variable)
                    if (
                        produced_value is None
                        or abs(float(produced_value) - float(expected_value)) > 1e-6
                    ):
                        failures_detail.append(
                            f"NUMERICAL MISMATCH: baseline {variable} produced "
                            f"{produced_value!r}, expected {expected_value!r}"
                        )
            if not baseline_found:
                failures_detail.append("NUMERICAL MISMATCH: no baseline result produced")

        # ---- citation integrity ------------------------------------------
        citation_ok = 0
        citation_total = 0
        for env in ctx.produced_artifacts:
            if env.artifact_type != "manuscript_section":
                continue
            for citation in envelope_payload_dict(env).get("citations") or []:
                citation_total += 1
                citation_id = citation.get("citation_id")
                identity_id = citation.get("paper_identity_id")
                evidence_id = citation.get("evidence_item_id")
                expected_title = expected_citation_identity.get(citation_id)
                if identity_id not in produced_ids or evidence_id not in produced_ids:
                    failures_detail.append(
                        f"WRONG CITATION IDENTITY: {citation_id} references "
                        f"unknown {identity_id}/{evidence_id}"
                    )
                    continue
                if expected_title:
                    identity = payload_by_id.get(identity_id, {})
                    member_ids = identity.get("member_paper_artifact_ids") or []
                    member_titles = []
                    for mid in member_ids:
                        record = payload_by_id.get(mid, {})
                        if record.get("title"):
                            member_titles.append(str(record.get("title")))
                    if expected_title not in member_titles:
                        failures_detail.append(
                            f"WRONG CITATION IDENTITY: {citation_id} resolves to "
                            f"{member_titles}, expected {expected_title!r}"
                        )
                        continue
                citation_ok += 1

        # ---- bibliography fidelity (no invented metadata) ----------------
        bibliography_ok = 0
        bibliography_total = 0
        for env in ctx.produced_artifacts:
            if env.artifact_type != "bibliography":
                continue
            for entry in envelope_payload_dict(env).get("entries") or []:
                bibliography_total += 1
                identity_id = entry.get("paper_identity_id")
                identity = payload_by_id.get(identity_id, {})
                member_ids = identity.get("member_paper_artifact_ids") or []
                records = [payload_by_id.get(mid, {}) for mid in member_ids]
                if not records:
                    failures_detail.append(
                        f"INVENTED BIBLIOGRAPHY: entry {entry.get('title')} has no "
                        "member paper records"
                    )
                    continue
                source = records[0]
                invented = [
                    field
                    for field in ("title", "authors", "year", "venue", "doi")
                    if entry.get(field) is not None and entry.get(field) != source.get(field)
                ]
                if invented:
                    failures_detail.append(
                        f"INVENTED BIBLIOGRAPHY METADATA: entry {entry.get('title')} "
                        f"fields {invented} contradict the paper record"
                    )
                    continue
                bibliography_ok += 1

        deterministic_failures = len(failures_detail)
        status = EvaluatorStatus.failed if failures_detail else EvaluatorStatus.passed
        end_to_end_ok = (
            status == EvaluatorStatus.passed
            and stages_ok == stage_total
            and provenance_ok == provenance_total
            and grounding_ok == grounding_total
            and condition_ok == condition_total
            and citation_ok == citation_total
            and bibliography_ok == bibliography_total
        )

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "pipeline",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "stage_completion_rate": _metric(
                "stage_completion_rate",
                float(stages_ok),
                stage_total,
                "rate",
                "production stages that produced their artifacts",
            ),
            "provenance_integrity_rate": _metric(
                "provenance_integrity_rate",
                float(provenance_ok),
                provenance_total,
                "rate",
                "expected derived_from provenance links present",
            ),
            "grounding_integrity_rate": _metric(
                "grounding_integrity_rate",
                float(grounding_ok),
                grounding_total,
                "rate",
                "grounding references resolve to produced artifacts",
            ),
            "condition_preservation_rate": _metric(
                "condition_preservation_rate",
                float(condition_ok),
                condition_total,
                "rate",
                "required conditions preserved downstream",
            ),
            "citation_integrity_rate": _metric(
                "citation_integrity_rate",
                float(citation_ok),
                citation_total,
                "rate",
                "citations resolve to the correct paper identity",
            ),
            "bibliography_fidelity_rate": _metric(
                "bibliography_fidelity_rate",
                float(bibliography_ok),
                bibliography_total,
                "rate",
                "bibliography entries match their paper records",
            ),
            "deterministic_failure_count": _metric(
                "deterministic_failure_count",
                float(deterministic_failures),
                1,
                "quantity",
                "deterministic gates that fired during the e2e run",
            ),
            "end_to_end_pass": _metric(
                "end_to_end_pass",
                1.0 if end_to_end_ok else 0.0,
                1,
                "rate",
                "the full pipeline passed every deterministic gate",
            ),
        }

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=(stages_ok / stage_total) if stage_total else None,
            value={
                "stages_present": {
                    stage: artifact_type in types_present
                    for stage, artifact_type in expected_stages.items()
                },
                "candidate_expressions": candidate_exprs,
                "deterministic_failures": failures_detail,
                "metrics": metrics,
                "dimension_scores": {
                    k: v
                    for k, v in {
                        "stage_completion_rate": (stages_ok / stage_total if stage_total else None),
                        "provenance_integrity_rate": (
                            provenance_ok / provenance_total if provenance_total else None
                        ),
                        "grounding_integrity_rate": (
                            grounding_ok / grounding_total if grounding_total else None
                        ),
                        "condition_preservation_rate": (
                            condition_ok / condition_total if condition_total else None
                        ),
                        "citation_integrity_rate": (
                            citation_ok / citation_total if citation_total else None
                        ),
                        "bibliography_fidelity_rate": (
                            bibliography_ok / bibliography_total if bibliography_total else None
                        ),
                        "end_to_end_pass": 1.0 if end_to_end_ok else 0.0,
                    }.items()
                    if v is not None
                },
            },
            status=status,
            explanation="; ".join(failures_detail)
            if failures_detail
            else "the full research pipeline passed every deterministic gate",
            evidence_artifact_ids=[
                e.artifact_id for e in ctx.produced_artifacts if e.artifact_type in types_present
            ],
        )


class PipelineIntegrityEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.pipeline_integrity",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic end-to-end pipeline integrity evaluator (Phase 6H)",
            provides=["evaluator.pipeline_integrity"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.pipeline_integrity", PipelineIntegrityEvaluator())
