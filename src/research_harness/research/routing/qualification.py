"""Deterministic live-model qualification (Phase 7D.1/7D.2/7D.3).

Pure functions — no network, no LLM. Qualification reuses Phase 7D.0
QualificationCriteria exactly (never loosened to obtain a winner). Produces
structured rejection kinds, and among qualified candidates picks primary +
fallback with quality/reliability first, then latency/cost (policy-driven).
An unqualified model can never be primary or fallback.

Phase 7D.2 adds structured failure attribution, per-task performance,
stability (stable/borderline/unstable), and the ProductionQualificationMatrix.
Phase 7D.3 adds task-specific qualification (same thresholds), the
TaskQualificationMatrix, ModelCapabilityProfile, and evidence-extraction
diagnostics. Task qualification never implies role qualification.
"""

from __future__ import annotations

from statistics import fmean, pvariance
from typing import Any

from research_harness.research.routing.readiness import (
    criteria_for_role,
    effective_critical_grounding,
    qualify_model,
)
from research_harness.research.routing.tasks import (
    TASK_LABELS,
    canonical_task,
    tasks_for_role,
)
from research_harness.research.schemas.live_quality import (
    FailureAttributionKind,
    LiveQualityModelResult,
    LiveQualityTaskPerformance,
    QualificationCriteria,
)
from research_harness.research.schemas.qualification import (
    ModelCapabilityProfile,
    ProductionQualificationMatrix,
    ProductionQualificationMatrixRow,
    QualificationCandidateResult,
    QualificationRejectionKind,
    RoleQualificationSummary,
    TaskQualificationMatrix,
    TaskQualificationResult,
)

_STABILITY_STABLE_MARGIN = 0.05
_STABILITY_MAX_VARIANCE = 0.02

_PROVIDER_ATTRIBUTION_KINDS = {
    FailureAttributionKind.provider_error.value,
    FailureAttributionKind.timeout.value,
    FailureAttributionKind.rate_limit.value,
}

_EVIDENCE_CATEGORIES = {
    "research_question",
    "theory",
    "construct",
    "mechanism",
    "assumption",
    "method",
    "data",
    "variable",
    "finding",
    "result",
    "boundary_condition",
    "limitation",
}


def classify_rejection_kinds(reasons: list[str]) -> list[str]:
    """Map qualify_model reason strings to structured rejection kinds."""
    kinds: list[str] = []
    text = " ".join(reasons).lower()
    if "repetitions" in text or "no live-quality task results" in text or "exercised cases" in text:
        kinds.append(QualificationRejectionKind.insufficient_repetitions.value)
    if "deterministic_pass_rate" in text:
        kinds.append(QualificationRejectionKind.below_quality_threshold.value)
    if "structured_output_success_rate" in text:
        kinds.append(QualificationRejectionKind.structured_output_failure.value)
    if "provider_error" in text:
        kinds.append(QualificationRejectionKind.provider_error_rate.value)
    if "grounding" in text:
        kinds.append(QualificationRejectionKind.critical_grounding_failure.value)
    if "stale" in text:
        kinds.append(QualificationRejectionKind.stale_evidence.value)
    if not kinds and reasons:
        kinds.append(QualificationRejectionKind.capability_mismatch.value)
    return kinds


def qualify_candidate(
    result: LiveQualityModelResult, criteria: QualificationCriteria
) -> tuple[bool, list[str], list[str]]:
    qualified, reasons = qualify_model(result, criteria)
    kinds = classify_rejection_kinds(reasons)
    return qualified, kinds, reasons


def attribute_failure_text(text: str) -> FailureAttributionKind:
    """Deterministically attribute one case-failure string to a kind (Phase 7D.2)."""
    t = text.lower()
    if "timeout" in t:
        return FailureAttributionKind.timeout
    if "rate_limit" in t or "rate limit" in t:
        return FailureAttributionKind.rate_limit
    if "provider" in t or "validation_failure" in t:
        return FailureAttributionKind.provider_error
    if (
        "unsupported reference" in t
        or "locator page" in t
        or "no evidence_item produced" in t
        or "no synthesis_statement produced" in t
        or "no research_gap produced" in t
        or "no mechanism_candidate produced" in t
        or "no formal_analytical_model produced" in t
        or "no proposition produced" in t
        or "deterministic verification did not pass" in t
    ):
        return FailureAttributionKind.grounding_failure
    if "required concepts missing" in t or "instruction_adherence" in t:
        return FailureAttributionKind.instruction_following_failure
    if (
        "empty statement" in t
        or "missing required structure" in t
        or "structured_output" in t
        or "no decision produced" in t
        or "missing overall_assessment" in t
        or "structured_output_success_rate" in t
    ):
        return FailureAttributionKind.structured_output_failure
    if (
        "defect_recall" in t
        or "decision_accuracy" in t
        or "gap_type" in t
        or "false exclusion" in t
        or "false_exclusion" in t
        or "required_field" in t
    ):
        return FailureAttributionKind.model_reasoning_failure
    return FailureAttributionKind.model_reasoning_failure


def attribute_failures(
    failures: list[str],
    *,
    defect_case_ids: set[str],
    case_id: str = "",
) -> tuple[dict[str, int], dict[str, int]]:
    """Attribute a list of case failures into genuine (included) and excluded
    (confirmed benchmark/evaluator defect) buckets.

    For a case confirmed in the calibration ledger as a benchmark or evaluator
    defect, ALL of its failures are excluded from qualification (attributed to
    the defect kind). Otherwise each failure is classified by text.

    Returns (failure_attribution, excluded_failure_attribution)."""
    attribution: dict[str, int] = {}
    excluded: dict[str, int] = {}
    # "*" marks a benchmark-level defect: the whole benchmark is defective, so
    # every case in it is excluded. Matching it here is safe because callers
    # filter defect case ids by benchmark before calling
    # (evaluation_live_quality builds defect_cases with `if bid == benchmark_id`),
    # so a "*" from one benchmark never leaks into another.
    if case_id in defect_case_ids or "*" in defect_case_ids:
        # confirmed benchmark/evaluator defect -> excluded, never a model failure
        for _ in failures:
            excluded[FailureAttributionKind.benchmark_reference_defect.value] = (
                excluded.get(FailureAttributionKind.benchmark_reference_defect.value, 0) + 1
            )
        return attribution, excluded
    for f in failures:
        kind = attribute_failure_text(f).value
        attribution[kind] = attribution.get(kind, 0) + 1
    return attribution, excluded


def stability_status(result: LiveQualityModelResult, criteria: QualificationCriteria) -> str:
    """Evidence-stability status (Phase 7D.2).

    - stable: every repetition meets the deterministic threshold and variance is
      small, with a comfortable margin above the threshold.
    - borderline: qualified evidence with a thin margin (worst within
      `_STABILITY_STABLE_MARGIN` of the threshold) or moderate variance.
    - unstable: evidence is not dependable (a repetition below the threshold,
      critical grounding failures, or provider-error rate above the cap)."""
    worst = result.deterministic_pass_rate_worst
    mean = result.deterministic_pass_rate_mean
    variance = result.deterministic_pass_rate_variance or 0.0
    threshold = criteria.min_deterministic_pass_rate
    provider_error = (
        result.provider_error_frequency if result.provider_error_frequency is not None else 1.0
    )
    if (
        worst is None
        or worst < threshold
        # M17: the same confirmed-defect exclusion qualify_model applies, or a
        # model exonerated by the ledger is still called unstable here.
        or effective_critical_grounding(result)
        or provider_error > criteria.max_provider_error_rate
    ):
        return "unstable"
    if mean is not None and worst >= threshold:
        if variance <= _STABILITY_MAX_VARIANCE and worst >= threshold + _STABILITY_STABLE_MARGIN:
            return "stable"
        return "borderline"
    return "unstable"


def candidate_result(
    result: LiveQualityModelResult,
    criteria: QualificationCriteria,
    *,
    live_quality_run_id: str | None = None,
    leaderboard_id: str | None = None,
) -> QualificationCandidateResult:
    qualified, kinds, reasons = qualify_candidate(result, criteria)
    total = max(result.repetitions, 1)
    critical_frequency = result.critical_grounding_failures / total
    return QualificationCandidateResult(
        candidate_id=result.candidate_id,
        model=result.model,
        resolved_model=result.resolved_model or result.model.get("requested_model"),
        role=result.role,
        benchmark_id=result.benchmark_id,
        qualified=qualified,
        rejection_kinds=kinds,
        rejection_reasons=reasons,
        live_quality_run_id=live_quality_run_id,
        leaderboard_id=leaderboard_id,
        deterministic_pass_rate_mean=result.deterministic_pass_rate_mean,
        deterministic_pass_rate_worst=result.deterministic_pass_rate_worst,
        deterministic_pass_rate_variance=result.deterministic_pass_rate_variance,
        critical_failure_frequency=critical_frequency,
        structured_output_success_rate=result.structured_output_success_rate,
        provider_error_frequency=result.provider_error_frequency,
        latency_ms_p50=result.latency_ms_p50_mean,
        total_tokens=result.total_tokens,
        estimated_cost=result.estimated_cost,
        stability=result.stability or stability_status(result, criteria),
        repetitions=result.repetitions,
        critical_grounding_failures=result.critical_grounding_failures,
        failure_attribution=dict(result.failure_attribution or {}),
        excluded_failure_attribution=dict(result.excluded_failure_attribution or {}),
        task_performance=[tp.model_dump(mode="json") for tp in (result.task_performance or [])],
    )


def _rank_key(c: QualificationCandidateResult) -> tuple[Any, ...]:
    """Quality/reliability first, then latency/cost. None = worst."""
    det = c.deterministic_pass_rate_mean if c.deterministic_pass_rate_mean is not None else -1.0
    structured = (
        c.structured_output_success_rate if c.structured_output_success_rate is not None else -1.0
    )
    error = c.provider_error_frequency if c.provider_error_frequency is not None else float("inf")
    latency = c.latency_ms_p50 if c.latency_ms_p50 is not None else float("inf")
    cost = c.estimated_cost if c.estimated_cost is not None else float("inf")
    return (-det, -structured, error, latency, cost, c.candidate_id)


def build_role_summary(
    candidates: list[QualificationCandidateResult],
    criteria: QualificationCriteria,
    *,
    benchmark_id: str,
    repetitions: int,
) -> RoleQualificationSummary:
    qualified = [c for c in candidates if c.qualified]
    qualified_models = sorted(c.candidate_id for c in qualified)
    ranked = sorted(qualified, key=_rank_key)

    primary = ranked[0].candidate_id if ranked else None
    fallback = ranked[1].candidate_id if len(ranked) >= 2 else None
    if primary and fallback:
        status = "qualified"
    elif primary:
        status = "qualified_without_fallback"
    else:
        status = "no_qualified_model"

    # eligibility (Phase 7D.2): qualified and not unstable; the selected primary
    # is never its own fallback
    for c in candidates:
        stable_ok = (c.stability or "unstable") != "unstable"
        c.primary_eligible = bool(c.qualified and stable_ok)
        c.fallback_eligible = bool(c.qualified and stable_ok and c.candidate_id != primary)

    rejection_counts: dict[str, int] = {}
    for c in candidates:
        for kind in c.rejection_kinds:
            rejection_counts[kind] = rejection_counts.get(kind, 0) + 1

    return RoleQualificationSummary(
        role=criteria.role,
        status=status,
        primary=primary,
        fallback=fallback,
        qualified_models=qualified_models,
        candidates=candidates,
        rejection_counts=rejection_counts,
        criteria=criteria,
        benchmark_id=benchmark_id,
        repetitions=repetitions,
    )


def build_qualification_matrix(
    candidates: list[QualificationCandidateResult],
    *,
    role: str,
    benchmark_id: str,
    repetitions: int,
    criteria: QualificationCriteria,
) -> ProductionQualificationMatrix:
    """Build the production-qualification matrix for one role (Phase 7D.2).

    Eligibility is stricter than qualification: a qualified candidate is
    primary/fallback eligible only if its evidence is not unstable. Rows keep
    raw dimensions; unqualified candidates are never ranked above qualified."""
    qualified = [c for c in candidates if c.qualified]
    ranked = sorted(qualified, key=_rank_key)
    primary = ranked[0].candidate_id if ranked else None
    fallback = ranked[1].candidate_id if len(ranked) >= 2 else None
    if primary and fallback:
        status = "qualified"
    elif primary:
        status = "qualified_without_fallback"
    else:
        status = "no_qualified_model"

    rows: list[ProductionQualificationMatrixRow] = []
    for c in candidates:
        rows.append(
            ProductionQualificationMatrixRow(
                role=role,
                candidate=c.candidate_id,
                qualified=c.qualified,
                stability=c.stability,
                primary_eligible=c.primary_eligible,
                fallback_eligible=c.fallback_eligible,
                repetitions=c.repetitions,
                rejection_kinds=list(c.rejection_kinds),
                rejection_reasons=list(c.rejection_reasons),
                live_quality_run_ids=[c.live_quality_run_id] if c.live_quality_run_id else [],
                deterministic_pass_rate_mean=c.deterministic_pass_rate_mean,
                deterministic_pass_rate_worst=c.deterministic_pass_rate_worst,
                deterministic_pass_rate_variance=c.deterministic_pass_rate_variance,
                structured_output_success_rate=c.structured_output_success_rate,
                provider_error_frequency=c.provider_error_frequency,
                critical_grounding_failures=c.critical_grounding_failures,
                latency_ms_p50=c.latency_ms_p50,
                estimated_cost=c.estimated_cost,
                total_tokens=c.total_tokens,
            )
        )
    return ProductionQualificationMatrix(
        role=role,
        status=status,
        primary=primary,
        fallback=fallback,
        rows=rows,
        benchmark_id=benchmark_id,
        repetitions=repetitions,
        criteria=criteria.model_dump(mode="json") if criteria else None,
    )


def summarize_role_live(
    live_results: dict[str, LiveQualityModelResult],
    *,
    role: str,
    benchmark_id: str,
    repetitions: int,
    criteria: QualificationCriteria | None = None,
) -> tuple[RoleQualificationSummary, list[QualificationCandidateResult]]:
    """Build a role summary from a dict of live-quality results (offline use).

    Role isolation: results whose `role` does not match the requested role are
    never considered (a reasoning-qualified model is not qualified for critic)."""
    criteria = criteria or criteria_for_role(role)
    if criteria.role != role:
        criteria = criteria.model_copy(update={"role": role})
    candidates = [
        candidate_result(result, criteria)
        for result in live_results.values()
        if result.role == role
    ]
    return build_role_summary(
        candidates, criteria, benchmark_id=benchmark_id, repetitions=repetitions
    ), candidates


# ---------------------------------------------------------------------------
# Phase 7D.3: task-specific qualification
# ---------------------------------------------------------------------------


def evidence_extraction_diagnostics(
    failures: list[str],
    *,
    produced_evidence: list[dict[str, Any]] | None = None,
    source_text: str = "",
) -> dict[str, int]:
    """Deterministic evidence-extraction failure diagnostics (Phase 7D.3).

    Diagnostic only — never changes the evidence benchmark or pass criteria.
    Buckets: hallucinated evidence IDs (unsupported reference ids), wrong page
    locators, unsupported claims (statement terms absent from the source),
    invalid categories, missing required evidence, malformed structured output.
    """
    diag = {
        "hallucinated_evidence_ids": 0,
        "wrong_page_locators": 0,
        "unsupported_claims": 0,
        "invalid_categories": 0,
        "missing_required_evidence": 0,
        "malformed_structured_output": 0,
    }
    for f in failures:
        if "unsupported reference" in f:
            diag["hallucinated_evidence_ids"] += 1
        if "locator page" in f:
            diag["wrong_page_locators"] += 1
        if "no evidence_item produced" in f:
            diag["missing_required_evidence"] += 1
        if "empty statement" in f:
            diag["malformed_structured_output"] += 1
    if produced_evidence:
        source_lower = (source_text or "").lower()
        for ev in produced_evidence:
            statement = str(ev.get("statement") or "").strip()
            if statement and source_lower:
                terms = [w for w in statement.lower().split() if len(w) > 4 and w.isalpha()]
                if terms and not any(t in source_lower for t in terms):
                    diag["unsupported_claims"] += 1
            category = str(ev.get("category") or "")
            if category and category not in _EVIDENCE_CATEGORIES:
                diag["invalid_categories"] += 1
    return diag


def aggregate_task_performance(
    result: LiveQualityModelResult, task: str
) -> LiveQualityTaskPerformance | None:
    """Combine the model's per-case task results into one canonical-task
    performance (Phase 7D.3). Multiple cases may map to one task (fast
    screening). Returns None when the model has no data for the task."""
    entries = [tp for tp in (result.task_performance or []) if canonical_task(tp.task_id) == task]
    if not entries:
        return None
    rates = [r for tp in entries for r in (tp.pass_rates or [])]
    if not rates:
        rates = [tp.pass_rate_mean or 0.0 for tp in entries if tp.pass_rate_mean is not None]
    structured = [
        tp.structured_output_success_rate
        for tp in entries
        if tp.structured_output_success_rate is not None
    ]
    provider = [
        tp.provider_error_frequency for tp in entries if tp.provider_error_frequency is not None
    ]
    return LiveQualityTaskPerformance(
        task_id=task,
        task_name=TASK_LABELS.get(task, ""),
        repetitions=max((tp.repetitions for tp in entries), default=0),
        pass_rate_mean=fmean(rates) if rates else None,
        pass_rate_worst=min(rates) if rates else None,
        pass_rate_variance=pvariance(rates) if len(rates) > 1 else 0.0,
        pass_rates=list(rates),
        structured_output_success_rate=fmean(structured) if structured else None,
        provider_error_frequency=fmean(provider) if provider else None,
        critical_grounding_failures=sum(tp.critical_grounding_failures for tp in entries),
        failure_attribution=_merge_counts(tp.failure_attribution for tp in entries),
        excluded_failure_attribution=_merge_counts(
            tp.excluded_failure_attribution for tp in entries
        ),
        evidence_diagnostics=_merge_counts(tp.evidence_diagnostics for tp in entries),
        task_diagnostics=_merge_counts(tp.task_diagnostics for tp in entries),
    )


def _merge_counts(count_maps: Any) -> dict[str, int]:
    merged: dict[str, int] = {}
    for m in count_maps:
        for k, v in (m or {}).items():
            merged[str(k)] = merged.get(str(k), 0) + int(v)
    return merged


def qualify_task(
    result: LiveQualityModelResult,
    task: str,
    criteria: QualificationCriteria,
) -> tuple[bool, list[str]]:
    """Deterministic task-level qualification (Phase 7D.3).

    Uses the SAME thresholds as the role criteria (never relaxed). A model may
    be qualified_for_task without being qualified for the entire role."""
    task = canonical_task(task)
    tp = aggregate_task_performance(result, task)
    reasons: list[str] = []
    if tp is None:
        return False, [f"no task-level results for task {task!r}"]
    if criteria.leaderboard_max_age_seconds is not None:
        from datetime import UTC, datetime

        age = (datetime.now(UTC) - result.evidence_timestamp).total_seconds()
        if age > criteria.leaderboard_max_age_seconds:
            reasons.append(
                f"stale live evidence: {age:.0f}s > {criteria.leaderboard_max_age_seconds}s"
            )
    if tp.repetitions < criteria.min_repetitions:
        reasons.append(f"insufficient repetitions: {tp.repetitions} < {criteria.min_repetitions}")
    det = tp.pass_rate_mean if tp.pass_rate_mean is not None else 0.0
    if det < criteria.min_deterministic_pass_rate:
        reasons.append(
            f"task deterministic_pass_rate {det:.3f} < {criteria.min_deterministic_pass_rate}"
        )
    structured = (
        tp.structured_output_success_rate if tp.structured_output_success_rate is not None else 0.0
    )
    if structured < criteria.min_structured_output_success_rate:
        reasons.append(
            f"task structured_output_success_rate {structured:.3f} < "
            f"{criteria.min_structured_output_success_rate}"
        )
    provider = tp.provider_error_frequency if tp.provider_error_frequency is not None else 1.0
    if provider > criteria.max_provider_error_rate:
        reasons.append(
            f"task provider_error_frequency {provider:.3f} > {criteria.max_provider_error_rate}"
        )
    if criteria.require_no_critical_grounding_failures and tp.critical_grounding_failures:
        reasons.append(f"{tp.critical_grounding_failures} critical grounding failure(s)")
    return not reasons, reasons


def task_candidate_result(
    result: LiveQualityModelResult,
    task: str,
    criteria: QualificationCriteria,
    *,
    live_quality_run_id: str | None = None,
) -> TaskQualificationResult | None:
    """Build one (model, task) TaskQualificationResult (Phase 7D.3)."""
    task = canonical_task(task)
    tp = aggregate_task_performance(result, task)
    if tp is None:
        return None
    qualified, reasons = qualify_task(result, task, criteria)
    total = max(tp.repetitions, 1)
    return TaskQualificationResult(
        role=result.role,
        task=task,
        task_label=tp.task_name or TASK_LABELS.get(task, ""),
        candidate_id=result.candidate_id,
        model=result.model,
        resolved_model=result.resolved_model or result.model.get("requested_model"),
        benchmark_id=result.benchmark_id,
        repetitions=tp.repetitions,
        deterministic_pass_rate_mean=tp.pass_rate_mean,
        deterministic_pass_rate_worst=tp.pass_rate_worst,
        deterministic_pass_rate_variance=tp.pass_rate_variance,
        structured_output_success_rate=tp.structured_output_success_rate,
        provider_error_frequency=tp.provider_error_frequency,
        critical_grounding_failures=tp.critical_grounding_failures,
        critical_failure_frequency=tp.critical_grounding_failures / total,
        latency_ms_p50=result.latency_ms_p50_mean,
        total_tokens=result.total_tokens,
        estimated_cost=result.estimated_cost,
        qualified=qualified,
        rejection_reasons=reasons,
        evidence_diagnostics=dict(tp.evidence_diagnostics or {}),
        task_diagnostics=dict(tp.task_diagnostics or {}),
        live_quality_run_id=live_quality_run_id,
    )


def task_rank_key(row: TaskQualificationResult) -> tuple[Any, ...]:
    """Per-task ranking among qualified models: deterministic correctness,
    reliability (worst), structured-output success, latency, cost, tie-break."""
    det = row.deterministic_pass_rate_mean if row.deterministic_pass_rate_mean is not None else -1.0
    worst = (
        row.deterministic_pass_rate_worst if row.deterministic_pass_rate_worst is not None else -1.0
    )
    structured = (
        row.structured_output_success_rate
        if row.structured_output_success_rate is not None
        else -1.0
    )
    latency = row.latency_ms_p50 if row.latency_ms_p50 is not None else float("inf")
    cost = row.estimated_cost if row.estimated_cost is not None else float("inf")
    return (-det, -worst, -structured, latency, cost, row.candidate_id)


def build_task_matrix(
    live_results: dict[str, LiveQualityModelResult],
    *,
    role: str,
    benchmark_id: str,
    repetitions: int,
    criteria: QualificationCriteria | None = None,
    live_quality_run_ids: dict[str, str] | None = None,
) -> tuple[TaskQualificationMatrix, list[TaskQualificationResult]]:
    """Build the role's task-qualification matrix from live-quality results
    (Phase 7D.3). Role isolation enforced; role qualification is computed
    separately and recorded so task coverage never implies role qualification."""
    criteria = criteria or criteria_for_role(role)
    if criteria.role != role:
        criteria = criteria.model_copy(update={"role": role})
    tasks = tasks_for_role(role)
    rows: list[TaskQualificationResult] = []
    qualified_models_by_task: dict[str, list[str]] = {t: [] for t in tasks}
    ranked_models_by_task: dict[str, list[str]] = {}
    qualified_tasks_by_model: dict[str, list[str]] = {}
    for result in live_results.values():
        if result.role != role:
            continue
        qualified_tasks_by_model[result.candidate_id] = []
        for task in tasks:
            row = task_candidate_result(
                result,
                task,
                criteria,
                # M26: provenance. Hardcoding None meant every task row lost the
                # run it came from, so consumers like the task-aware router
                # collected an empty qualification_result_ids list.
                live_quality_run_id=(live_quality_run_ids or {}).get(result.candidate_id),
            )
            if row is None:
                continue
            rows.append(row)
            if row.qualified:
                qualified_models_by_task[task].append(result.candidate_id)
                qualified_tasks_by_model[result.candidate_id].append(task)

    # per-task ranking among qualified models only
    for task in tasks:
        task_rows = [r for r in rows if r.task == task and r.qualified]
        ranked_models_by_task[task] = [r.candidate_id for r in sorted(task_rows, key=task_rank_key)]

    _role_summary, _candidates = summarize_role_live(
        live_results,
        role=role,
        benchmark_id=benchmark_id,
        repetitions=repetitions,
        criteria=criteria,
    )
    matrix = TaskQualificationMatrix(
        role=role,
        benchmark_id=benchmark_id,
        tasks=tasks,
        rows=rows,
        qualified_models_by_task=qualified_models_by_task,
        ranked_models_by_task=ranked_models_by_task,
        qualified_tasks_by_model=qualified_tasks_by_model,
        role_qualified_models=list(_role_summary.qualified_models),
        criteria=criteria.model_dump(mode="json"),
        repetitions=repetitions,
    )
    return matrix, rows


def build_model_capability_profiles(
    matrix: TaskQualificationMatrix,
    *,
    stability_by_model: dict[str, str] | None = None,
    latency_by_model: dict[str, float | None] | None = None,
    cost_by_model: dict[str, float | None] | None = None,
    tokens_by_model: dict[str, int | None] | None = None,
) -> list[ModelCapabilityProfile]:
    """Build per-model capability profiles from a task matrix (Phase 7D.3)."""
    profiles: list[ModelCapabilityProfile] = []
    by_model: dict[str, list[TaskQualificationResult]] = {}
    for row in matrix.rows:
        by_model.setdefault(row.candidate_id, []).append(row)
    for candidate_id, rows in by_model.items():
        first = rows[0]
        task_qualifications = {
            row.task: ("qualified_for_task" if row.qualified else "not_qualified_for_task")
            for row in rows
        }
        profiles.append(
            ModelCapabilityProfile(
                model=candidate_id,
                resolved_model=first.resolved_model,
                role=matrix.role,
                benchmark_id=matrix.benchmark_id,
                task_qualifications=task_qualifications,
                role_qualified=candidate_id in matrix.role_qualified_models,
                stability=(stability_by_model or {}).get(candidate_id),
                repetitions=max((r.repetitions for r in rows), default=0),
                latency_ms_p50=(latency_by_model or {}).get(candidate_id),
                total_tokens=(tokens_by_model or {}).get(candidate_id),
                estimated_cost=(cost_by_model or {}).get(candidate_id),
            )
        )
    return profiles


# ---------------------------------------------------------------------------
# Phase 7D.3B: remaining-task coverage
# ---------------------------------------------------------------------------


def build_remaining_task_coverage(
    matrices: list[TaskQualificationMatrix],
    *,
    preflights: list[Any],
    campaigns: list[Any],
) -> Any:
    """Build the RemainingTaskCoverage for the tasks that were unqualified at
    the start of Phase 7D.3B.

    For each remaining task:
    - qualified_primary/qualified_fallback: the top two qualified models ranked
      by the task matrix (ranked_models_by_task).
    - qualified_model_count: number of qualified models.
    - tested_model_count: candidates exercised on the role's benchmark.
    - provider_unavailable_count: candidates whose preflight classified them
      temporarily_unavailable/provider_error (never interpreted as incapable).
    - dominant_failure_reason: the most common structured rejection kind among
      the task's unqualified rows, or 'provider_unavailable' when the task was
      not meaningfully exercised.
    """
    from research_harness.research.routing.tasks import remaining_tasks_for_role
    from research_harness.research.schemas.qualification import (
        RemainingTaskCoverage,
        RemainingTaskCoverageRow,
    )

    by_role = {m.role: m for m in matrices}
    preflight_by_model: dict[tuple[str, str], str] = {}
    latest_preflight: dict[tuple[str, str], Any] = {}
    for p in preflights:
        # M22: key by (candidate, role). A preflight is a role-specific probe —
        # reasoning, critic and fast ask for different things — so keying on the
        # candidate alone applied whichever role happened to be newest to all
        # three, reporting critic availability as reasoning availability.
        key = (p.candidate_id, p.role)
        if (
            key not in latest_preflight
            or p.created_at > latest_preflight[key].created_at
        ):
            latest_preflight[key] = p
    for key, p in latest_preflight.items():
        preflight_by_model[key] = p.status.value

    tested_by_role: dict[str, set[str]] = {}
    for c in campaigns:
        tested_by_role.setdefault(c.role, set()).update(cc.candidate_id for cc in c.candidates)

    rows: list[RemainingTaskCoverageRow] = []
    for role in ("reasoning", "critic", "fast"):
        matrix = by_role.get(role)
        tasks = remaining_tasks_for_role(role)
        for task in tasks:
            tested = sorted(tested_by_role.get(role, set()))
            ranked = (matrix.ranked_models_by_task.get(task) or []) if matrix else []
            qualified_models = (
                list(matrix.qualified_models_by_task.get(task) or []) if matrix else []
            )
            unqualified = [m for m in tested if m not in qualified_models]
            provider_unavailable = sum(
                1
                for m in tested
                if preflight_by_model.get((m, role))
                in {
                    "temporarily_unavailable",
                    "provider_error",
                }
            )
            dominant = ""
            if qualified_models:
                dominant = ""
            elif unqualified:
                reason_counts: dict[str, int] = {}
                if matrix:
                    for row in matrix.rows:
                        if row.task != task or row.candidate_id not in unqualified:
                            continue
                        for kind in classify_rejection_kinds(list(row.rejection_reasons)):
                            reason_counts[kind] = reason_counts.get(kind, 0) + 1
                if provider_unavailable and not reason_counts:
                    dominant = "provider_unavailable"
                elif reason_counts:
                    dominant = max(reason_counts, key=lambda k: reason_counts[k])
                else:
                    dominant = (
                        "provider_unavailable"
                        if provider_unavailable
                        else "below_quality_threshold"
                    )
            rows.append(
                RemainingTaskCoverageRow(
                    task=task,
                    qualified_primary=ranked[0] if ranked else None,
                    qualified_fallback=ranked[1] if len(ranked) >= 2 else None,
                    qualified_model_count=len(qualified_models),
                    tested_model_count=len(tested),
                    provider_unavailable_count=provider_unavailable,
                    dominant_failure_reason=dominant,
                )
            )
    return RemainingTaskCoverage(rows=rows)
