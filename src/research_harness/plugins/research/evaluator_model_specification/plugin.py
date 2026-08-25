"""evaluator.model_specification — deterministic analytical-model evaluator
(Phase 7A).

Evaluates produced Phase 3B artifacts (ModelSpecificationExecution,
FormalAnalyticalModel, ModelSpecificationCritique) against known-answer
references:

- structural validity: produced model_created / rejected match the reference,
  and rejected specs carry the expected failure substring
- symbol-table accuracy: created model's variable+parameter symbols match
- payoff completeness: every strategic actor has a payoff function
- decision-ownership accuracy: every decision variable has an owner actor
- timing accuracy: sequential stages 0..N with valid actor references
- information-structure accuracy: observed symbols are defined and actors/stages valid
- assumption-grounding accuracy: literature_supported assumptions cite
  resolvable synthesis/evidence artifacts
- critic issue recall: expected critique issue categories are detected
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


class ModelSpecificationEvaluator:
    evaluator_id = "evaluator.model_specification"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        executions = [
            e for e in ctx.produced_artifacts if e.artifact_type == "model_specification_execution"
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
                explanation="no model_specification_execution produced for the case",
            )
        execution = envelope_payload_dict(max(executions, key=lambda e: e.created_at))
        rejected = bool(execution.get("rejected"))
        model_created = bool(execution.get("model_created"))
        failures = list(execution.get("failures") or [])

        model_envs = [
            e for e in ctx.produced_artifacts if e.artifact_type == "formal_analytical_model"
        ]
        model = (
            envelope_payload_dict(max(model_envs, key=lambda e: e.created_at))
            if model_envs
            else None
        )
        critiques = [
            envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "model_specification_critique"
        ]

        reference = ctx.case.reference or {}
        expected_created = bool(reference.get("expected_model_created", True))
        expected_rejected = bool(reference.get("expected_rejected") or False)
        expected_failure = reference.get("expected_failure_substring")
        expected_symbols = [str(s) for s in (reference.get("expected_symbols") or [])]
        expected_payoff_actors = [str(a) for a in (reference.get("expected_payoff_actors") or [])]
        expected_decision_owners = dict(reference.get("expected_decision_owners") or {})
        expected_critique_issues = [
            str(c) for c in (reference.get("expected_critique_issues") or [])
        ]

        valid_sources: set[str] = {
            e.artifact_id
            for e in ctx.produced_artifacts
            if e.artifact_type in ("synthesis_statement", "evidence_item")
        }

        failures_detail: list[str] = []

        # ---- structural validity -------------------------------------------
        if expected_created and not model_created:
            failures_detail.append(
                f"MODEL NOT CREATED: expected created but execution rejected (rejected={rejected})"
            )
        if expected_rejected and not rejected:
            failures_detail.append("MODEL NOT REJECTED: expected the builder to reject the spec")
        if not expected_rejected and rejected:
            failures_detail.append("MODEL REJECTED: expected the spec to build a model")
        if expected_failure and rejected:
            if not any(str(expected_failure) in str(f.get("error") or "") for f in failures):
                failures_detail.append(
                    f"REJECTION REASON MISMATCH: expected {expected_failure!r} "
                    f"in failures {failures[:2]}"
                )

        # ---- created-model structural metrics ------------------------------
        symbol_ok = 0
        symbol_total = 0
        payoff_ok = 0
        payoff_total = 0
        ownership_ok = 0
        ownership_total = 0
        timing_ok = 0
        timing_total = 0
        info_ok = 0
        info_total = 0
        assumption_ok = 0
        assumption_total = 0
        symbol_table: set[str] = set()

        if model is not None and model_created and expected_created:
            actors = list(model.get("actors") or [])
            actor_ids = {a.get("actor_id") for a in actors}
            strategic_actors = [a.get("actor_id") for a in actors if a.get("strategic")]
            variables = list(model.get("variables") or [])
            parameters = list(model.get("parameters") or [])
            symbol_table = {v.get("symbol") for v in variables} | {
                p.get("symbol") for p in parameters
            }

            # symbol table
            if expected_symbols:
                symbol_total = len(expected_symbols)
                if symbol_table == set(expected_symbols):
                    symbol_ok = symbol_total
                else:
                    failures_detail.append(
                        f"SYMBOL TABLE MISMATCH: expected {sorted(expected_symbols)}, "
                        f"produced {sorted(symbol_table)}"
                    )

            # payoff completeness
            payoff_actors = {p.get("actor_id") for p in (model.get("payoffs") or [])}
            payoff_total = len(strategic_actors)
            missing_payoffs = [a for a in strategic_actors if a not in payoff_actors]
            if missing_payoffs:
                failures_detail.append(f"MISSING PAYOFF: strategic actors {missing_payoffs}")
            payoff_ok = payoff_total - len(missing_payoffs)
            if expected_payoff_actors:
                missing_expected = [a for a in expected_payoff_actors if a not in payoff_actors]
                if missing_expected:
                    failures_detail.append(
                        f"EXPECTED PAYOFF MISSING: {missing_expected} "
                        f"(produced for {sorted(payoff_actors)})"
                    )

            # decision ownership
            ownership_total = sum(1 for v in variables if v.get("kind") == "decision_variable")
            for v in variables:
                if v.get("kind") != "decision_variable":
                    continue
                owner = v.get("owner_actor_id")
                if not owner or owner not in actor_ids:
                    failures_detail.append(
                        f"DECISION OWNERSHIP: {v.get('symbol')!r} has invalid owner {owner!r}"
                    )
                elif str(v.get("symbol")) in expected_decision_owners and (
                    owner != expected_decision_owners[str(v.get("symbol"))]
                ):
                    failures_detail.append(
                        f"DECISION OWNERSHIP: {v.get('symbol')!r} owned by {owner!r}, "
                        f"expected {expected_decision_owners[str(v.get('symbol'))]!r}"
                    )
                else:
                    ownership_ok += 1

            # timing
            timing = list(model.get("timing") or [])
            timing_total = len(timing)
            stage_nums = [t.get("stage_number") for t in timing]
            if not timing:
                failures_detail.append("TIMING: no timing stages defined")
            elif len(set(stage_nums)) != len(stage_nums) or stage_nums != list(range(len(timing))):
                failures_detail.append(f"TIMING: stages must be sequential 0..N, got {stage_nums}")
            else:
                bad_actor_refs = [
                    aid
                    for t in timing
                    for aid in (t.get("actor_ids") or [])
                    if aid not in actor_ids
                ]
                if bad_actor_refs:
                    failures_detail.append(
                        f"TIMING: unknown actor references {sorted(set(bad_actor_refs))}"
                    )
                else:
                    timing_ok = timing_total

            # information structure
            info_items = list((model.get("information_structure") or {}).get("items") or [])
            info_total = len(info_items)
            for item in info_items:
                item_actor = item.get("actor_id")
                observed = list(item.get("variable_symbols") or [])
                bad_actor = item_actor not in actor_ids
                bad_symbols = [s for s in observed if s not in symbol_table]
                if bad_actor or bad_symbols:
                    failures_detail.append(
                        f"INFORMATION STRUCTURE: actor {item_actor!r} observes "
                        f"{observed} with bad_actor={bad_actor}, undefined "
                        f"symbols={bad_symbols}"
                    )
                else:
                    info_ok += 1
            uncertainty = list((model.get("information_structure") or {}).get("uncertainty") or [])
            info_total += len(uncertainty)
            for u in uncertainty:
                if u.get("variable_symbol") not in symbol_table:
                    failures_detail.append(
                        f"INFORMATION STRUCTURE: uncertainty references undefined "
                        f"symbol {u.get('variable_symbol')!r}"
                    )
                else:
                    info_ok += 1

            # assumption grounding
            assumption_total = len(model.get("assumptions") or [])
            for a in model.get("assumptions") or []:
                if a.get("knowledge_basis") != "literature_supported":
                    assumption_ok += 1
                    continue
                sources = list(a.get("source_ids") or [])
                bad_sources = [s for s in sources if s not in valid_sources]
                if not sources:
                    failures_detail.append(
                        f"ASSUMPTION GROUNDING: literature_supported assumption "
                        f"{str(a.get('statement'))[:60]!r} has no source_ids"
                    )
                elif bad_sources:
                    failures_detail.append(
                        f"ASSUMPTION GROUNDING: literature_supported assumption "
                        f"{str(a.get('statement'))[:60]!r} cites unresolvable sources "
                        f"{bad_sources[:3]}"
                    )
                else:
                    assumption_ok += 1

        # ---- critic issue recall -------------------------------------------
        issue_categories: set[str] = set()
        for crit in critiques:
            issue_categories.update(i.get("category") for i in (crit.get("issues") or []))
        critic_missing: list[str] = []
        if expected_critique_issues:
            if not critiques:
                failures_detail.append("CRITIC: no model_specification_critique produced")
                critic_missing = list(expected_critique_issues)
            else:
                critic_missing = [c for c in expected_critique_issues if c not in issue_categories]
                if critic_missing:
                    failures_detail.append(
                        f"CRITIC ISSUES MISSING: {critic_missing} "
                        f"(produced {sorted(issue_categories)})"
                    )
        critic_total = len(expected_critique_issues)
        critic_ok = critic_total - len(critic_missing)

        # ---- metrics -------------------------------------------------------

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "model_specification",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "symbol_table_accuracy": _metric(
                "symbol_table_accuracy",
                float(symbol_ok),
                symbol_total,
                "rate",
                "produced variable+parameter symbol set matches the reference",
            ),
            "payoff_completeness": _metric(
                "payoff_completeness",
                float(payoff_ok),
                payoff_total,
                "rate",
                "strategic actors with a payoff function",
            ),
            "decision_ownership_accuracy": _metric(
                "decision_ownership_accuracy",
                float(ownership_ok),
                ownership_total,
                "rate",
                "decision variables with a valid owner actor",
            ),
            "timing_accuracy": _metric(
                "timing_accuracy",
                float(timing_ok),
                timing_total,
                "rate",
                "sequential timing stages with valid actor references",
            ),
            "information_structure_accuracy": _metric(
                "information_structure_accuracy",
                float(info_ok),
                info_total,
                "rate",
                "information items observing defined symbols with valid actors/stages",
            ),
            "assumption_grounding_accuracy": _metric(
                "assumption_grounding_accuracy",
                float(assumption_ok),
                assumption_total,
                "rate",
                "literature_supported assumptions citing resolvable sources",
            ),
            "structural_validity_accuracy": _metric(
                "structural_validity_accuracy",
                1.0
                if not (expected_created != model_created or expected_rejected != rejected)
                else 0.0,
                1,
                "rate",
                "produced model_created/rejected status matches the reference",
            ),
            "critic_issue_recall": _metric(
                "critic_issue_recall",
                float(critic_ok),
                critic_total,
                "rate",
                "expected critique issue categories detected by the critic",
            ),
        }

        status = EvaluatorStatus.failed if failures_detail else EvaluatorStatus.passed

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=1.0 if not failures_detail else 0.0,
            value={
                "model_created": model_created,
                "rejected": rejected,
                "failures": failures,
                "symbol_table": sorted(symbol_table) if model else None,
                "payoff_actors": sorted(
                    {p.get("actor_id") for p in (model or {}).get("payoffs") or []}
                ),
                "critique_issue_categories": sorted(issue_categories),
                "metrics": metrics,
                "dimension_scores": {
                    k: v
                    for k, v in {
                        "symbol_table_accuracy": (
                            symbol_ok / symbol_total if symbol_total else None
                        ),
                        "payoff_completeness": (payoff_ok / payoff_total if payoff_total else None),
                        "decision_ownership_accuracy": (
                            ownership_ok / ownership_total if ownership_total else None
                        ),
                        "timing_accuracy": timing_ok / timing_total if timing_total else None,
                        "information_structure_accuracy": (
                            info_ok / info_total if info_total else None
                        ),
                        "assumption_grounding_accuracy": (
                            assumption_ok / assumption_total if assumption_total else None
                        ),
                        "structural_validity_accuracy": (
                            1.0
                            if not (
                                expected_created != model_created or expected_rejected != rejected
                            )
                            else 0.0
                        ),
                        "critic_issue_recall": (critic_ok / critic_total if critic_total else None),
                    }.items()
                    if v is not None
                },
            },
            status=status,
            explanation="; ".join(failures_detail)
            if failures_detail
            else "all model-specification checks matched",
            evidence_artifact_ids=[e.artifact_id for e in model_envs],
        )


class ModelSpecificationEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.model_specification",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic analytical-model-specification evaluator (Phase 7A)",
            provides=["evaluator.model_specification"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.model_specification", ModelSpecificationEvaluator())
