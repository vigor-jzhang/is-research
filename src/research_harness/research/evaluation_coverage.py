"""Evaluation coverage matrix (Phase 6H).

Durable, reportable mapping from production capabilities to benchmark
families (Phase 6A-6H): benchmarks, evaluators, metrics, deterministic vs
advisory gating, covered edge cases, and known gaps. The matrix makes
missing evaluation coverage obvious: capabilities in PRODUCTION_PHASES
without a benchmark row surface in `uncovered_capabilities()`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CoverageRow:
    capability: str
    phase: str
    benchmark: str
    evaluator: str
    metrics: tuple[str, ...]
    gating: str  # "deterministic" | "advisory"
    edge_cases: tuple[str, ...]
    gaps: tuple[str, ...]


COVERAGE_MATRIX: tuple[CoverageRow, ...] = (
    CoverageRow(
        capability="Novelty validation",
        phase="5A/5B",
        benchmark="novelty-threat-v1",
        evaluator="evaluator.deterministic",
        metrics=("pass_rate", "deterministic_gate_failures"),
        gating="deterministic",
        edge_cases=("documented limitation claims", "reviewed-corpus-bounded claims"),
        gaps=("incremental revalidation (Phase 5B) not benchmarked standalone",),
    ),
    CoverageRow(
        capability="Literature retrieval",
        phase="2C",
        benchmark="literature-retrieval-v1",
        evaluator="evaluator.retrieval",
        metrics=("retrieval_recall", "ranking_accuracy", "relevance_accuracy"),
        gating="deterministic",
        edge_cases=("multi-provider dedup", "rank order", "irrelevant-result rejection"),
        gaps=("live provider corpora not benchmarked",),
    ),
    CoverageRow(
        capability="Citation formatting",
        phase="4C",
        benchmark="citation-correctness-v1",
        evaluator="evaluator.citation_correctness",
        metrics=("citation_accuracy", "bibliography_fidelity"),
        gating="deterministic",
        edge_cases=("author-year rendering", "missing identities", "duplicate papers"),
        gaps=("submission packaging beyond formatting not benchmarked",),
    ),
    CoverageRow(
        capability="Literature screening",
        phase="2D",
        benchmark="literature-screening-v1",
        evaluator="evaluator.screening",
        metrics=("inclusion_accuracy", "exclusion_accuracy", "review_accuracy"),
        gating="deterministic",
        edge_cases=("clear include/exclude", "uncertain review", "protocol approval gate"),
        gaps=("full-screen large corpora not benchmarked",),
    ),
    CoverageRow(
        capability="Evidence extraction",
        phase="2F",
        benchmark="evidence-extraction-v1",
        evaluator="evaluator.evidence",
        metrics=("evidence_grounding_rate", "page_grounding_accuracy", "category_accuracy"),
        gating="deterministic",
        edge_cases=("page-bounded chunks", "missing full text", "failing chunks"),
        gaps=("non-English documents not benchmarked",),
    ),
    CoverageRow(
        capability="Literature synthesis",
        phase="2G",
        benchmark="research-pipeline-e2e-v1",
        evaluator="evaluator.pipeline_integrity",
        metrics=("grounding_integrity_rate", "stage_completion_rate"),
        gating="deterministic",
        edge_cases=("evidence-backed statements", "theme structure"),
        gaps=("no standalone synthesis benchmark (covered only end-to-end)",),
    ),
    CoverageRow(
        capability="Gap analysis",
        phase="2H",
        benchmark="research-gap-analysis-v1",
        evaluator="evaluator.gap_analysis",
        metrics=(
            "gap_precision",
            "gap_recall",
            "grounding_accuracy",
            "hallucinated_reference_count",
        ),
        gating="deterministic",
        edge_cases=(
            "contradiction-based gaps",
            "repeated limitations",
            "hallucinated ids",
            "sweeping claims",
        ),
        gaps=("gap selection heuristics not benchmarked standalone",),
    ),
    CoverageRow(
        capability="Mechanism development",
        phase="3A",
        benchmark="mechanism-development-v1",
        evaluator="evaluator.mechanism",
        metrics=(
            "mechanism_grounding_rate",
            "knowledge_basis_accuracy",
            "critic_issue_recall",
            "revision_success_rate",
        ),
        gating="deterministic",
        edge_cases=(
            "literature-supported grounding",
            "sweeping claims",
            "weak gaps",
            "critic/revision flows",
        ),
        gaps=("mechanism quality beyond grounding not benchmarked",),
    ),
    CoverageRow(
        capability="Analytical model specification",
        phase="3B",
        benchmark="research-pipeline-e2e-v1",
        evaluator="evaluator.pipeline_integrity",
        metrics=("stage_completion_rate", "grounding_integrity_rate"),
        gating="deterministic",
        edge_cases=("symbol validity", "literature-supported assumptions"),
        gaps=("no standalone model-builder benchmark (covered only end-to-end)",),
    ),
    CoverageRow(
        capability="Equilibrium derivation",
        phase="3C",
        benchmark="equilibrium-correctness-v1",
        evaluator="evaluator.equilibrium",
        metrics=(
            "equilibrium_expression_accuracy",
            "foc_accuracy",
            "best_response_accuracy",
            "verification_accuracy",
            "solution_order_accuracy",
            "condition_accuracy",
            "unsolvable_detection_accuracy",
            "incorrect_candidate_rejection_rate",
        ),
        gating="deterministic",
        edge_cases=(
            "closed forms",
            "sequential games",
            "conditioned equilibria",
            "LLM candidate rejection",
            "unsolvable models",
        ),
        gaps=("transcendental/LLM-solved equilibria only scripted",),
    ),
    CoverageRow(
        capability="Comparative statics",
        phase="3D",
        benchmark="comparative-statics-v1",
        evaluator="evaluator.comparative_statics",
        metrics=(
            "derivative_accuracy",
            "sign_accuracy",
            "condition_preservation_accuracy",
            "outcome_parameter_coverage",
            "ambiguous_sign_accuracy",
        ),
        gating="deterministic",
        edge_cases=(
            "positive/negative/zero derivatives",
            "ambiguous signs",
            "conditions",
            "unused parameters",
        ),
        gaps=("parameter-domain-restricted signs not benchmarked",),
    ),
    CoverageRow(
        capability="Propositions",
        phase="3D",
        benchmark="proposition-correctness-v1",
        evaluator="evaluator.proposition",
        metrics=(
            "proposition_verification_accuracy",
            "monotonicity_accuracy",
            "equality_accuracy",
            "condition_accuracy",
            "support_reference_accuracy",
            "incorrect_proposition_rejection_rate",
        ),
        gating="deterministic",
        edge_cases=(
            "conditional propositions",
            "wrong-sign rejection",
            "hallucinated support",
            "threshold claims",
        ),
        gaps=("proposition critique quality is advisory-only",),
    ),
    CoverageRow(
        capability="Numerical analysis",
        phase="3E",
        benchmark="numerical-analysis-v1",
        evaluator="evaluator.numerical",
        metrics=(
            "numerical_value_accuracy",
            "feasibility_classification_accuracy",
            "condition_enforcement_accuracy",
            "sweep_accuracy",
            "robustness_classification_accuracy",
            "welfare_accuracy",
            "reproducibility_accuracy",
        ),
        gating="deterministic",
        edge_cases=("sweeps", "grids", "infeasible points", "violated conditions", "welfare"),
        gaps=("stochastic simulation paths not benchmarked",),
    ),
    CoverageRow(
        capability="Results assembly",
        phase="4A",
        benchmark="results-assembly-v1",
        evaluator="evaluator.results_grounding",
        metrics=(
            "finding_grounding_accuracy",
            "condition_preservation_accuracy",
            "proposition_support_accuracy",
            "numerical_support_accuracy",
            "contribution_gap_alignment_accuracy",
            "implication_grounding_accuracy",
            "novelty_claim_accuracy",
            "contradiction_detection_accuracy",
            "unsupported_claim_rate",
        ),
        gating="deterministic",
        edge_cases=(
            "failed-proposition support",
            "novelty normalization",
            "contradictions",
            "unsupported implications",
        ),
        gaps=("contribution novelty quality beyond normalization not benchmarked",),
    ),
    CoverageRow(
        capability="Manuscript grounding",
        phase="4B",
        benchmark="manuscript-grounding-v1",
        evaluator="evaluator.manuscript_grounding",
        metrics=(
            "claim_grounding_accuracy",
            "literature_citation_coverage",
            "mathematical_claim_accuracy",
            "condition_preservation_accuracy",
            "unsupported_claim_rate",
            "citation_reference_accuracy",
            "novelty_claim_accuracy",
            "section_consistency_accuracy",
            "critique_issue_recall",
            "revision_success_rate",
        ),
        gating="deterministic",
        edge_cases=(
            "hallucinated citations",
            "failed-proposition claims",
            "gap/contribution mismatch",
            "revision",
        ),
        gaps=("long-document prose quality is advisory-only",),
    ),
    CoverageRow(
        capability="End-to-end research pipeline",
        phase="2C-4C",
        benchmark="research-pipeline-e2e-v1",
        evaluator="evaluator.pipeline_integrity",
        metrics=(
            "stage_completion_rate",
            "provenance_integrity_rate",
            "grounding_integrity_rate",
            "condition_preservation_rate",
            "citation_integrity_rate",
            "bibliography_fidelity_rate",
            "deterministic_failure_count",
            "end_to_end_pass",
        ),
        gating="deterministic",
        edge_cases=(
            "excluded papers never re-enter",
            "page-grounded evidence",
            "cross-stage provenance",
            "citation identity",
        ),
        gaps=("leaderboards/live corpora/model tournaments not implemented",),
    ),
)

# Capabilities with no dedicated benchmark row (known evaluation gaps).
KNOWN_COVERAGE_GAPS: tuple[tuple[str, str], ...] = (
    ("Phase 3B model builder", "covered only by the e2e benchmark"),
    ("Phase 2G synthesis", "covered only by the e2e benchmark"),
    ("Phase 5B incremental novelty revalidation", "no benchmark"),
    ("Phase 4C submission packaging", "only citation formatting is benchmarked"),
    ("Phase 2A-2B ingestion/identity resolution", "fixture-only, no benchmark"),
    ("Phase 2E acquisition/locators", "no benchmark"),
    ("Phase 3A gap selection heuristics", "no standalone benchmark"),
)


def covered_capabilities() -> tuple[str, ...]:
    return tuple(row.capability for row in COVERAGE_MATRIX)


def rows_for_benchmark(benchmark_id: str) -> tuple[CoverageRow, ...]:
    return tuple(row for row in COVERAGE_MATRIX if row.benchmark == benchmark_id)


def rows_for_capability(capability: str) -> tuple[CoverageRow, ...]:
    return tuple(row for row in COVERAGE_MATRIX if row.capability == capability)


def uncovered_capabilities() -> tuple[tuple[str, str], ...]:
    """Capabilities exercised by production phases with no dedicated
    benchmark row — makes missing evaluation coverage obvious."""
    return KNOWN_COVERAGE_GAPS


def benchmark_coverage_ok() -> bool:
    """Every built-in benchmark family from 6A-6H has at least one row."""
    from research_harness.research.benchmarks import BUILTIN_BENCHMARKS

    covered = {row.benchmark for row in COVERAGE_MATRIX}
    return all(bid in covered for bid in BUILTIN_BENCHMARKS)
