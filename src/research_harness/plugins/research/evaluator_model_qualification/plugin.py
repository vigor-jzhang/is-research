"""evaluator.model_qualification — deterministic live-model-qualification
evaluator (Phase 7D.1/7D.2).

Checks qualification summaries and the production-qualification matrix against
known-answer expectations: status/primary/fallback selection, qualified-model
sets, structured rejection kinds, stability classification, primary/fallback
eligibility, role isolation, deterministic tie-breaks, and the critical metric
`unsafe_model_qualification_rate` which must be 0 (a primary or fallback
selected from an unqualified candidate is a critical failure).
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


class ModelQualificationEvaluator:
    evaluator_id = "evaluator.model_qualification"
    evaluator_version = "0.2.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        summaries = [
            e for e in ctx.produced_artifacts if e.artifact_type == "qualification_summary"
        ]
        matrices = [
            e
            for e in ctx.produced_artifacts
            if e.artifact_type == "production_qualification_matrix"
        ]
        reference = ctx.case.reference or {}

        if not summaries and not matrices:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no qualification_summary or production_qualification_matrix produced",
            )

        failures: list[str] = []
        unsafe = 0
        decision_ok = True
        rejection_ok = True
        role_ok = True
        tiebreak_ok = True
        stability_ok = True
        eligibility_ok = True
        # Tracked so metrics can distinguish "checked and passed" from "never
        # checked": the matrix path does not evaluate several of the checks.
        is_matrix = bool(matrices)

        if matrices:
            (
                decision_ok,
                stability_ok,
                eligibility_ok,
                rejection_ok,
                role_ok,
                tiebreak_ok,
                unsafe,
                failures,
            ) = self._check_matrix(matrices, reference, failures)
        else:
            (
                decision_ok,
                stability_ok,
                eligibility_ok,
                rejection_ok,
                role_ok,
                tiebreak_ok,
                unsafe,
                failures,
            ) = self._check_summary(summaries, reference, failures)

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "model_qualification",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "qualification_decision_accuracy": _metric(
                "qualification_decision_accuracy",
                1.0 if decision_ok else 0.0,
                1,
                "rate",
                "role status and primary/fallback selection match expectations",
            ),
            # The matrix path does not evaluate rejection kinds or role
            # isolation -- _check_matrix hard-codes both to True -- so they are
            # reported as not measured (value 0, count 0) rather than as a
            # perfect 1.0. Both halves are required: the harness sums values and
            # counts across cases, so leaving the value at 1.0 while zeroing the
            # count inflates the aggregate above 1.0. This mirrors how
            # expected_tiebreak / expected_stability already gate their metrics.
            "rejection_classification_accuracy": _metric(
                "rejection_classification_accuracy",
                0.0 if is_matrix else (1.0 if rejection_ok else 0.0),
                0 if is_matrix else 1,
                "rate",
                "structured rejection kinds match expectations",
            ),
            "role_isolation_accuracy": _metric(
                "role_isolation_accuracy",
                0.0 if is_matrix else (1.0 if role_ok else 0.0),
                0 if is_matrix else 1,
                "rate",
                "qualification considers only the requested role's evidence",
            ),
            "deterministic_tiebreak_accuracy": _metric(
                "deterministic_tiebreak_accuracy",
                1.0 if reference.get("expected_tiebreak") is not None and tiebreak_ok else 0.0,
                1 if reference.get("expected_tiebreak") is not None else 0,
                "rate",
                "ties between qualified models break deterministically",
            ),
            "stability_classification_accuracy": _metric(
                "stability_classification_accuracy",
                1.0 if reference.get("expected_stability") is not None and stability_ok else 0.0,
                1 if reference.get("expected_stability") is not None else 0,
                "rate",
                "stability (stable/borderline/unstable) matches expectations",
            ),
            "eligibility_accuracy": _metric(
                "eligibility_accuracy",
                1.0
                if (
                    (
                        reference.get("expected_primary_eligible") is not None
                        or reference.get("expected_fallback_eligible") is not None
                        or reference.get("expected_matrix_rows") is not None
                    )
                    and eligibility_ok
                )
                else 0.0,
                1
                if (
                    reference.get("expected_primary_eligible") is not None
                    or reference.get("expected_fallback_eligible") is not None
                    or reference.get("expected_matrix_rows") is not None
                )
                else 0,
                "rate",
                "primary/fallback eligibility matches expectations (unstable never eligible)",
            ),
            "unsafe_model_qualification_rate": _metric(
                "unsafe_model_qualification_rate",
                float(unsafe),
                max(unsafe, 1),
                "rate",
                "any unqualified model selected as primary or fallback (critical)",
            ),
        }

        result_status = EvaluatorStatus.failed if failures else EvaluatorStatus.passed
        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=1.0 if not failures else 0.0,
            value={
                "unsafe_model_qualification_count": unsafe,
                "metrics": metrics,
                "dimension_scores": {
                    "qualification_decision_accuracy": float(decision_ok),
                    "rejection_classification_accuracy": float(rejection_ok),
                    "role_isolation_accuracy": float(role_ok),
                    "deterministic_tiebreak_accuracy": float(tiebreak_ok),
                    "stability_classification_accuracy": float(stability_ok),
                    "eligibility_accuracy": float(eligibility_ok),
                    "unsafe_model_qualification_rate": float(unsafe),
                },
            },
            status=result_status,
            explanation="; ".join(failures) if failures else "all qualification checks matched",
            evidence_artifact_ids=[e.artifact_id for e in summaries + matrices],
        )

    # ------------------------------------------------------------------
    # Summary-based checks (per-role qualification)
    # ------------------------------------------------------------------

    def _check_summary(
        self,
        summaries: list[Any],
        reference: dict[str, Any],
        failures: list[str],
    ) -> tuple[bool, bool, bool, bool, bool, bool, int, list[str]]:
        s = envelope_payload_dict(max(summaries, key=lambda e: e.created_at))
        status = str(s.get("status") or "")
        primary = s.get("primary")
        fallback = s.get("fallback")
        qualified_models = {str(x) for x in (s.get("qualified_models") or [])}
        candidates = list(s.get("candidates") or [])
        summary_role = str(s.get("role") or "")

        qualified_candidate_ids = {
            str(c.get("candidate_id")) for c in candidates if c.get("qualified")
        }
        by_candidate = {str(c.get("candidate_id")): c for c in candidates}

        expected_status = reference.get("expected_status")
        expected_primary = reference.get("expected_primary")
        expected_fallback = reference.get("expected_fallback")
        expected_qualified_models_raw = reference.get("expected_qualified_models")
        expected_qualified_models = (
            {str(x) for x in expected_qualified_models_raw}
            if expected_qualified_models_raw is not None
            else None
        )
        expected_rejection_kinds = reference.get("expected_rejection_kinds") or {}
        expected_role = reference.get("expected_role")

        decision_ok = True
        if expected_status is not None and status != expected_status:
            failures.append(f"STATUS: got {status!r}, expected {expected_status!r}")
            decision_ok = False
        if expected_primary is not None and primary != expected_primary:
            failures.append(f"PRIMARY: got {primary!r}, expected {expected_primary!r}")
            decision_ok = False
        if expected_fallback is not None and fallback != expected_fallback:
            failures.append(f"FALLBACK: got {fallback!r}, expected {expected_fallback!r}")
            decision_ok = False
        if expected_role is not None and summary_role != expected_role:
            failures.append(f"ROLE: got {summary_role!r}, expected {expected_role!r}")

        if expected_qualified_models is not None:
            missing = expected_qualified_models - qualified_models
            extra = qualified_models - expected_qualified_models
            if missing or extra:
                failures.append(
                    f"QUALIFIED: expected {sorted(expected_qualified_models)}, "
                    f"got {sorted(qualified_models)}"
                )

        rejection_ok = True
        for candidate_id, expected_kinds in expected_rejection_kinds.items():
            c = by_candidate.get(str(candidate_id))
            if c is None:
                failures.append(f"REJECTION: candidate {candidate_id!r} not evaluated")
                rejection_ok = False
                continue
            actual_kinds = {str(k) for k in (c.get("rejection_kinds") or [])}
            expected_set = {str(k) for k in expected_kinds}
            if actual_kinds != expected_set:
                failures.append(
                    f"REJECTION {candidate_id}: expected {sorted(expected_set)}, "
                    f"got {sorted(actual_kinds)}"
                )
                rejection_ok = False

        unsafe = 0
        for selected in (primary, fallback):
            if selected is None:
                continue
            if selected not in qualified_models:
                unsafe += 1
                failures.append(f"UNSAFE: selected model {selected!r} is not qualified")
            if selected not in qualified_candidate_ids:
                unsafe += 1
                failures.append(f"UNSAFE: selected candidate {selected!r} was rejected")
        if status == "no_qualified_model" and (primary is not None or fallback is not None):
            unsafe += 1
            failures.append("UNSAFE: no_qualified_model but a model was selected")
        if status == "qualified_without_fallback" and fallback is not None:
            unsafe += 1
            failures.append("UNSAFE: qualified_without_fallback but a fallback was selected")

        role_ok = all(str(c.get("role") or "") == summary_role for c in candidates)

        tiebreak_ok = True
        if reference.get("expected_tiebreak") is not None:
            if primary != reference.get("expected_tiebreak"):
                failures.append(
                    f"TIE-BREAK: expected {reference.get('expected_tiebreak')!r}, got {primary!r}"
                )
                tiebreak_ok = False

        stability_ok, eligibility_ok = self._check_stability_eligibility(
            by_candidate, primary, fallback, reference, failures
        )
        return (
            decision_ok,
            stability_ok,
            eligibility_ok,
            rejection_ok,
            role_ok,
            tiebreak_ok,
            unsafe,
            failures,
        )

    def _check_stability_eligibility(
        self,
        by_candidate: dict[str, dict[str, Any]],
        primary: Any,
        fallback: Any,
        reference: dict[str, Any],
        failures: list[str],
    ) -> tuple[bool, bool]:
        stability_ok = True
        stability_map: dict[str, Any] = {}
        expected_stability = reference.get("expected_stability")
        if isinstance(expected_stability, str) and primary is not None:
            stability_map[str(primary)] = expected_stability
        elif isinstance(expected_stability, dict):
            stability_map = {str(k): v for k, v in expected_stability.items()}
        for cid, exp in stability_map.items():
            got = (by_candidate.get(str(cid)) or {}).get("stability")
            if got != exp:
                failures.append(f"STABILITY {cid}: got {got!r}, expected {exp!r}")
                stability_ok = False

        eligibility_ok = True
        primary_map: dict[str, Any] = {}
        exp_primary = reference.get("expected_primary_eligible")
        if isinstance(exp_primary, bool) and primary is not None:
            primary_map[str(primary)] = exp_primary
        elif isinstance(exp_primary, dict):
            primary_map = {str(k): v for k, v in exp_primary.items()}
        for cid, exp in primary_map.items():
            got = (by_candidate.get(str(cid)) or {}).get("primary_eligible")
            if bool(got) != bool(exp):
                failures.append(f"ELIGIBILITY primary {cid}: got {got!r}, expected {exp!r}")
                eligibility_ok = False
        fallback_map: dict[str, Any] = {}
        exp_fallback = reference.get("expected_fallback_eligible")
        if isinstance(exp_fallback, bool) and fallback is not None:
            fallback_map[str(fallback)] = exp_fallback
        elif isinstance(exp_fallback, dict):
            fallback_map = {str(k): v for k, v in exp_fallback.items()}
        for cid, exp in fallback_map.items():
            got = (by_candidate.get(str(cid)) or {}).get("fallback_eligible")
            if bool(got) != bool(exp):
                failures.append(f"ELIGIBILITY fallback {cid}: got {got!r}, expected {exp!r}")
                eligibility_ok = False
        rep_map: dict[str, Any] = {}
        exp_reps = reference.get("expected_repetitions")
        if isinstance(exp_reps, int) and primary is not None:
            rep_map[str(primary)] = exp_reps
        elif isinstance(exp_reps, dict):
            rep_map = {str(k): v for k, v in exp_reps.items()}
        for cid, exp in rep_map.items():
            got = (by_candidate.get(str(cid)) or {}).get("repetitions")
            if int(got or 0) != int(exp):
                failures.append(f"REPETITIONS {cid}: got {got!r}, expected {exp!r}")
        return stability_ok, eligibility_ok

    # ------------------------------------------------------------------
    # Matrix-based checks (cross-role production-qualification matrix)
    # ------------------------------------------------------------------

    def _check_matrix(
        self,
        matrices: list[Any],
        reference: dict[str, Any],
        failures: list[str],
    ) -> tuple[bool, bool, bool, bool, bool, bool, int, list[str]]:
        expected_rows = reference.get("expected_matrix_rows") or []
        expected_status = reference.get("expected_matrix_status") or {}
        matrices_by_role = {
            str((envelope_payload_dict(m)).get("role")): envelope_payload_dict(m) for m in matrices
        }

        decision_ok = True
        eligibility_ok = True
        unsafe = 0
        for expected in expected_rows:
            role = str(expected.get("role") or "")
            candidate = str(expected.get("candidate") or "")
            m = matrices_by_role.get(role)
            if m is None:
                failures.append(f"MATRIX: missing role {role!r}")
                decision_ok = False
                continue
            row = next(
                (r for r in m.get("rows") or [] if str(r.get("candidate")) == candidate), None
            )
            if row is None:
                failures.append(f"MATRIX: missing row {role!r}/{candidate!r}")
                decision_ok = False
                continue
            for field in ("qualified", "stability", "primary_eligible", "fallback_eligible"):
                exp = expected.get(field)
                if exp is None:
                    continue
                got = row.get(field)
                if field == "stability":
                    if got != exp:
                        failures.append(
                            f"MATRIX {role}/{candidate}: stability got {got!r}, expected {exp!r}"
                        )
                        decision_ok = False
                elif bool(got) != bool(exp):
                    failures.append(
                        f"MATRIX {role}/{candidate}: {field} got {got!r}, expected {exp!r}"
                    )
                    decision_ok = False
            if row.get("primary_eligible") and not row.get("qualified"):
                unsafe += 1
                failures.append(
                    f"UNSAFE: matrix row {role}/{candidate} primary_eligible but not qualified"
                )
            if row.get("fallback_eligible") and not row.get("qualified"):
                unsafe += 1
                failures.append(
                    f"UNSAFE: matrix row {role}/{candidate} fallback_eligible but not qualified"
                )

        for role, exp_status in expected_status.items():
            got = (matrices_by_role.get(str(role)) or {}).get("status")
            if got != exp_status:
                failures.append(f"MATRIX STATUS {role}: got {got!r}, expected {exp_status!r}")
                decision_ok = False

        # eligibility consistency: eligible implies qualified and not unstable
        for role, m in matrices_by_role.items():
            for row in m.get("rows") or []:
                if row.get("primary_eligible") or row.get("fallback_eligible"):
                    if not row.get("qualified"):
                        failures.append(
                            f"MATRIX {role}/{row.get('candidate')}: eligible but unqualified"
                        )
                        eligibility_ok = False
                    if row.get("stability") == "unstable":
                        failures.append(
                            f"MATRIX {role}/{row.get('candidate')}: eligible but unstable"
                        )
                        eligibility_ok = False
        return decision_ok, True, eligibility_ok, True, True, True, unsafe, failures


class ModelQualificationEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.model_qualification",
            version="0.2.0",
            plugin_type="evaluator",
            description="Deterministic live-model-qualification evaluator (Phase 7D.1/7D.2)",
            provides=["evaluator.model_qualification"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.model_qualification", ModelQualificationEvaluator())
