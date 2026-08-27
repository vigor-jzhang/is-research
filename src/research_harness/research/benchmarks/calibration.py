"""Live-quality benchmark calibration audit — Phase 7D.2.

Model-independent, deterministic audit of the live-quality benchmark cases:
valid model-agnostic reference, achievable structured schema, no fixture
leakage, no impossible evidence requirement, deterministic evaluator
correctness, realistic context size, valid grounding ids/pages, and no
provider-specific assumptions. Confirmed defects are excluded from
qualification; a benchmark is never weakened merely because a model fails it.
"""

from __future__ import annotations

from typing import Any

from research_harness.research.benchmarks import BUILTIN_BENCHMARKS
from research_harness.research.schemas.calibration import (
    BenchmarkCalibrationAudit,
    CalibrationCheck,
    CalibrationFinding,
    CalibrationSeverity,
    ConfirmedDefect,
)

_LIVE_QUALITY_BENCHMARKS = {
    "live-quality-reasoning-v1",
    "live-quality-critic-v1",
    "live-quality-fast-v1",
}

_LIVE_QUALITY_EVALUATORS = {
    "evaluator.live_quality_reasoning",
    "evaluator.live_quality_critic",
    "evaluator.live_quality_fast",
}

# Known achievable critic defect categories (from the critic schemas).
_CRITIC_CATEGORIES_BY_TASK = {
    "mechanism_critique": {
        "logical_inconsistency",
        "unsupported_assumption",
        "already_explained_by_reviewed_literature",
        "unclear_causal_direction",
        "unmodelable_concept",
        "missing_actor_or_incentive",
        "alternative_explanation",
    },
    "model_critique": {
        "mechanism_model_mismatch",
        "undefined_concept",
        "inconsistent_timing",
        "impossible_information",
        "redundant_assumption",
        "missing_strategic_actor",
        "payoff_inconsistency",
        "poor_tractability",
        "unjustified_restriction",
    },
    "proposition_critique": {
        "overclaiming",
        "interpretation_beyond_support",
        "missing_conditions",
        "trivial_proposition",
        "contradicts_assumptions_or_mechanism",
        "weak_is_relevance",
    },
    "results_critique": {
        "overclaiming",
        "unsupported_novelty_claim",
        "missing_conditions",
        "symbolic_numerical_contradiction",
        "causal_overstatement",
        "weak_gap_link",
        "weak_is_contribution",
    },
    "manuscript_critique": {
        "unsupported_claim",
        "citation_gap",
        "overclaiming",
        "cross_section_inconsistency",
        "gap_contribution_mismatch",
        "mathematical_result_distortion",
        "repetition",
        "weak_logical_flow",
        "missing_limitations",
    },
}

# Required model-structure fields (analytical_model_specification).
_REQUIRED_MODEL_STRUCTURE = {"actors", "variables", "parameters", "payoffs", "timing"}


def _strings(value: Any) -> list[str]:
    """Collect all string leaves of a nested structure."""
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_strings(v))
    elif isinstance(value, (list, tuple)):
        for v in value:
            out.extend(_strings(v))
    return out


def _concepts_grounded(inputs: dict[str, Any], concepts: list[str]) -> list[str]:
    text = " ".join(_strings(inputs)).lower()
    return [c for c in concepts if c.lower() not in text]


def _placeholder_targets(payload: Any) -> list[str]:
    return [s for s in _strings(payload) if s.startswith("{") and s.endswith("}")]


def _fixture_schema(artifact_type: str) -> Any:
    """Artifact schema used to inject critic fixtures (matches the workflow)."""
    from research_harness.research.schemas.gap import ResearchGap
    from research_harness.research.schemas.manuscript import ManuscriptDraft, ManuscriptSection
    from research_harness.research.schemas.mechanism import (
        MechanismCandidate,
        SelectedMechanism,
    )
    from research_harness.research.schemas.model import FormalAnalyticalModel
    from research_harness.research.schemas.proposition import Proposition
    from research_harness.research.schemas.results import (
        ContributionClaim,
        ResearchFinding,
        ResearchResultsPackage,
    )

    return {
        "research_gap": ResearchGap,
        "mechanism_candidate": MechanismCandidate,
        "selected_mechanism": SelectedMechanism,
        "formal_analytical_model": FormalAnalyticalModel,
        "proposition": Proposition,
        "research_results_package": ResearchResultsPackage,
        "research_finding": ResearchFinding,
        "contribution_claim": ContributionClaim,
        "manuscript_draft": ManuscriptDraft,
        "manuscript_section": ManuscriptSection,
    }.get(artifact_type)


def audit_live_quality_benchmark(benchmark_id: str) -> BenchmarkCalibrationAudit:
    """Audit one live-quality benchmark against the calibration checklist."""
    definition = BUILTIN_BENCHMARKS.get(benchmark_id)
    checks: list[CalibrationCheck] = []
    findings: list[CalibrationFinding] = []
    confirmed: list[ConfirmedDefect] = []

    def _fail(
        check: str,
        message: str,
        *,
        case_id: str = "",
        kind: str | None = None,
    ) -> None:
        findings.append(
            CalibrationFinding(
                severity=CalibrationSeverity.defect,
                message=message,
                benchmark_id=benchmark_id,
                case_id=case_id,
                check=check,
                attributed_kind=kind,
            )
        )
        if kind is not None:
            confirmed.append(
                ConfirmedDefect(
                    benchmark_id=benchmark_id,
                    case_id=case_id,
                    kind=kind,
                    message=message,
                )
            )

    if definition is None:
        return BenchmarkCalibrationAudit(
            benchmark_id=benchmark_id,
            verdict="repair_needed",
            checks=[CalibrationCheck(name="benchmark_exists", passed=False, details="not found")],
            findings=[
                CalibrationFinding(
                    severity=CalibrationSeverity.defect,
                    message=f"unknown benchmark {benchmark_id!r}",
                    benchmark_id=benchmark_id,
                    check="benchmark_exists",
                    attributed_kind="benchmark_reference_defect",
                )
            ],
            confirmed_defects=[
                ConfirmedDefect(
                    benchmark_id=benchmark_id, case_id="*", kind="benchmark_reference_defect"
                )
            ],
        )

    cases = definition.cases
    n = len(cases)

    # 1. valid model-independent reference
    missing_ref = [
        (c.id, "reference or reference.task missing")
        for c in cases
        if not (c.reference or {}).get("task")
    ]
    checks.append(
        CalibrationCheck(
            name="valid_reference",
            passed=not missing_ref,
            details=f"{n} cases; {len(missing_ref)} missing reference/task: {missing_ref}",
        )
    )

    # 2. no impossible evidence requirement (required concepts grounded in input)
    for c in cases:
        concepts = list((c.reference or {}).get("required_concepts") or [])
        if not concepts:
            continue
        missing = _concepts_grounded(c.input, concepts)
        if missing:
            _fail(
                "reference_grounded",
                f"required concepts not present in the case input (impossible evidence): {missing}",
                case_id=c.id,
                kind="benchmark_reference_defect",
            )
    checks.append(
        CalibrationCheck(
            name="reference_grounded",
            passed=all(f.check != "reference_grounded" for f in findings),
            details="required concepts are grounded in the case inputs (no impossible evidence)",
        )
    )

    # 3. achievable structured schema (critic defect categories + model structure)
    for c in cases:
        task = str((c.reference or {}).get("task") or "")
        injected = (c.reference or {}).get("injected_defects") or []
        if injected:
            achievable = _CRITIC_CATEGORIES_BY_TASK.get(task)
            bad = [
                str(d.get("category"))
                for d in injected
                if achievable is not None and str(d.get("category")) not in achievable
            ]
            if bad:
                _fail(
                    "schema_achievable",
                    f"injected defect categories not in the critic vocabulary: {bad}",
                    case_id=c.id,
                    kind="benchmark_reference_defect",
                )
        if task == "analytical_model_specification":
            req = set(
                (c.reference or {}).get("required_model_structure") or _REQUIRED_MODEL_STRUCTURE
            )
            unmodelable = req - _REQUIRED_MODEL_STRUCTURE
            if unmodelable:
                _fail(
                    "schema_achievable",
                    f"required model structure fields do not exist in the model schema: {unmodelable}",
                    case_id=c.id,
                    kind="benchmark_reference_defect",
                )
    # fixtures must actually validate against their artifact schemas (Phase 7D.3:
    # an invalid fixture silently errors the case every run and corrupts the
    # deterministic pass rate)
    for c in cases:
        fixtures = c.input.get("fixtures") or {}
        for atype, payloads in fixtures.items():
            schema = _fixture_schema(atype)
            if schema is None:
                _fail(
                    "schema_achievable",
                    f"no schema for fixture type {atype!r}",
                    case_id=c.id,
                    kind="benchmark_reference_defect",
                )
                continue
            for i, payload in enumerate(payloads or []):
                try:
                    schema.model_validate(payload)
                except Exception as e:  # noqa: BLE001
                    _fail(
                        "schema_achievable",
                        f"fixture {atype}[{i}] fails schema validation: {e}",
                        case_id=c.id,
                        kind="benchmark_reference_defect",
                    )
    checks.append(
        CalibrationCheck(
            name="schema_achievable",
            passed=all(f.check != "schema_achievable" for f in findings),
            details="injected defect categories and required structures are achievable; "
            "fixtures validate against their schemas",
        )
    )

    # 4. no fixture leakage (reference answer never literally present in fixtures)
    leaked: list[str] = []
    for c in cases:
        if not c.input.get("fixtures"):
            continue
        injected_categories = [
            str(d.get("category")) for d in (c.reference or {}).get("injected_defects") or []
        ]
        fixture_text = " ".join(_strings(c.input.get("fixtures"))).lower()
        for cat in injected_categories:
            if cat.lower() in fixture_text:
                leaked.append(f"{c.id}: defect category {cat!r} leaked into fixtures")
    checks.append(
        CalibrationCheck(
            name="no_fixture_leakage",
            passed=not leaked,
            details="; ".join(leaked) if leaked else "fixtures never contain the reference answers",
        )
    )

    # 5. valid grounding ids/pages
    grounding_bad: list[str] = []
    for c in cases:
        fixtures = c.input.get("fixtures") or {}
        plan_keys = {
            f"{atype}#{i}"
            for atype, payloads in fixtures.items()
            for i in range(len(payloads or []))
        }
        for _atype, payloads in fixtures.items():
            for payload in payloads or []:
                for target in _placeholder_targets(payload):
                    key = target[1:-1]
                    if key not in plan_keys:
                        grounding_bad.append(f"{c.id}: unresolved fixture placeholder {target!r}")
        if c.id == "lq-evidence-extraction":
            doc_pages = c.input.get("documents", [{}])[0].get("pages") or []
            page_count = len(doc_pages)
            for p in doc_pages:
                if int(p.get("page") or 0) < 1 or int(p.get("page") or 0) > max(page_count, 1):
                    grounding_bad.append(
                        "lq-evidence-extraction: locator page out of document range"
                    )
    checks.append(
        CalibrationCheck(
            name="grounding_ids_valid",
            passed=not grounding_bad,
            details="; ".join(grounding_bad) if grounding_bad else "grounding ids/pages resolve",
        )
    )

    # 6. deterministic evaluator correctness (evaluator registered + config match)
    eval_bad: list[str] = []
    config_evals = list(definition.config.get("evaluators") or [])
    if not config_evals:
        eval_bad.append(f"{benchmark_id}: benchmark declares no evaluators")
    for eid in config_evals:
        if eid not in _LIVE_QUALITY_EVALUATORS:
            eval_bad.append(f"{benchmark_id}: unknown evaluator {eid!r}")
    expected_evaluator = {
        "live-quality-reasoning-v1": "evaluator.live_quality_reasoning",
        "live-quality-critic-v1": "evaluator.live_quality_critic",
        "live-quality-fast-v1": "evaluator.live_quality_fast",
    }.get(benchmark_id)
    if expected_evaluator and expected_evaluator not in config_evals:
        eval_bad.append(f"{benchmark_id}: expected evaluator {expected_evaluator!r} not configured")
    for c in cases:
        ref_eval = (c.reference or {}).get("evaluator")
        if ref_eval and ref_eval not in _LIVE_QUALITY_EVALUATORS:
            eval_bad.append(f"{c.id}: reference evaluator {ref_eval!r} unknown")
    checks.append(
        CalibrationCheck(
            name="evaluator_correctness",
            passed=not eval_bad,
            details="; ".join(eval_bad)
            if eval_bad
            else "evaluators registered and dimension-aligned",
        )
    )

    # 7. realistic context size
    size_bad: list[str] = []
    for c in cases:
        text = " ".join(_strings(c.input))
        words = len(text.split())
        if words > 40_000:
            size_bad.append(f"{c.id}: {words} words exceeds a realistic context budget")
    checks.append(
        CalibrationCheck(
            name="realistic_context_size",
            passed=not size_bad,
            details="; ".join(size_bad) if size_bad else "context sizes are realistic",
        )
    )

    # 8. no provider-specific assumptions
    provider_bad: list[str] = []
    for c in cases:
        text = " ".join(_strings(c.input) + _strings(c.reference or {})).lower()
        for marker in ("openrouter", ":free", "gpt-", "claude", "deepseek-", "nemotron"):
            if marker in text:
                provider_bad.append(f"{c.id}: provider-specific assumption {marker!r}")
    checks.append(
        CalibrationCheck(
            name="no_provider_assumptions",
            passed=not provider_bad,
            details="; ".join(provider_bad)
            if provider_bad
            else "inputs/references are provider-agnostic",
        )
    )

    verdict = "ok"
    if any(f.severity == CalibrationSeverity.defect for f in findings):
        verdict = "repair_needed"

    return BenchmarkCalibrationAudit(
        benchmark_id=benchmark_id,
        benchmark_version=definition.version,
        verdict=verdict,
        checks=checks,
        findings=findings,
        confirmed_defects=confirmed,
    )


def audit_all_live_quality_benchmarks() -> list[BenchmarkCalibrationAudit]:
    return [
        audit_live_quality_benchmark(benchmark_id)
        for benchmark_id in sorted(_LIVE_QUALITY_BENCHMARKS)
    ]


def confirmed_defect_map() -> dict[tuple[str, str], str]:
    """(benchmark_id, case_id) -> attribution kind for confirmed defects (Phase 7D.2).

    Consumed by the live-quality service so confirmed benchmark/evaluator
    defects are excluded from qualification."""
    mapping: dict[tuple[str, str], str] = {}
    for audit in audit_all_live_quality_benchmarks():
        for d in audit.confirmed_defects:
            mapping[(d.benchmark_id, d.case_id)] = d.kind
    return mapping
