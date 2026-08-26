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
        gaps=("novelty revalidation across changing literature is benchmarked standalone (7A.1)",),
    ),
    CoverageRow(
        capability="Novelty revalidation",
        phase="5A/5B",
        benchmark="novelty-revalidation-v1",
        evaluator="evaluator.novelty_revalidation",
        metrics=(
            "revalidation_trigger_accuracy",
            "stale_reuse_rate",
            "novelty_threat_detection_accuracy",
            "irrelevant_update_accuracy",
            "supersession_accuracy",
            "provenance_version_accuracy",
        ),
        gating="deterministic",
        edge_cases=(
            "unchanged literature reusable",
            "new directly relevant paper re-threatens",
            "new contradictory evidence",
            "mechanism/gap coverage",
            "irrelevant papers do not invalidate",
            "stale artifacts not silently reused",
            "superseding assessment preserves history",
        ),
        gaps=("live provider corpora and publisher endpoints not benchmarked",),
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
        gaps=("packaging beyond citation formatting is benchmarked standalone (7A.1)",),
    ),
    CoverageRow(
        capability="Ingestion + identity resolution",
        phase="2B/2C",
        benchmark="literature-ingestion-identity-v1",
        evaluator="evaluator.identity_resolution",
        metrics=(
            "canonical_mapping_accuracy",
            "duplicate_collapse_accuracy",
            "false_merge_rate",
            "false_split_rate",
            "identifier_normalization_accuracy",
            "supersession_accuracy",
            "partial_ingestion_accuracy",
        ),
        gating="deterministic",
        edge_cases=(
            "same DOI across providers",
            "normalized DOI variants",
            "shared strong identifiers",
            "exact-content duplicates",
            "similar-title separation (no semantic merges)",
            "sparse metadata",
            "provider failure with partial ingestion",
            "identity supersession",
        ),
        gaps=("live provider ingestion pipelines not benchmarked",),
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
        benchmark="literature-synthesis-v1",
        evaluator="evaluator.synthesis",
        metrics=(
            "statement_grounding_accuracy",
            "consensus_accuracy",
            "contradiction_accuracy",
            "multi_paper_support_accuracy",
            "support_count_accuracy",
            "unsupported_statement_rate",
            "hallucinated_reference_count",
        ),
        gating="deterministic",
        edge_cases=(
            "multi-paper consensus",
            "contradiction with both sides",
            "mixed evidence",
            "single-paper observation not consensus",
            "boundary-condition / methodological patterns",
            "hallucinated evidence ids",
            "orphaned evidence with no paper mapping",
        ),
        gaps=("live cross-corpus synthesis corpora not benchmarked",),
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
        gaps=("gap selection heuristics are benchmarked standalone (7A.1)",),
    ),
    CoverageRow(
        capability="Gap selection",
        phase="3A",
        benchmark="gap-selection-v1",
        evaluator="evaluator.gap_selection",
        metrics=(
            "selected_gap_validity",
            "selection_rationale_grounding",
            "alternative_consideration_accuracy",
            "fallback_accuracy",
            "autonomy_decision_accuracy",
            "operator_override_accuracy",
            "reuse_accuracy",
        ),
        gating="deterministic",
        edge_cases=(
            "rank-#1 selection when clearly strongest",
            "valid non-rank-1 selection",
            "operator override",
            "invalid model selection -> deterministic fallback",
            "autonomy approval / rejection",
            "unsupported gap id rejected",
            "deterministic rerun reuse",
        ),
        gaps=("subjective 'best gap' judgment is never scored unless fixture-defined",),
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
        benchmark="analytical-model-specification-v1",
        evaluator="evaluator.model_specification",
        metrics=(
            "symbol_table_accuracy",
            "payoff_completeness",
            "decision_ownership_accuracy",
            "timing_accuracy",
            "information_structure_accuracy",
            "assumption_grounding_accuracy",
            "structural_validity_accuracy",
            "critic_issue_recall",
        ),
        gating="deterministic",
        edge_cases=(
            "valid strategic models",
            "undefined / duplicate symbols",
            "invalid decision ownership",
            "invalid timing",
            "invalid information structure",
            "unsupported literature-backed assumptions",
            "missing payoffs",
            "critic-detected mechanism/model mismatch",
        ),
        gaps=("revision flows beyond critique not benchmarked standalone",),
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
        capability="Document acquisition",
        phase="2E",
        benchmark="document-acquisition-v1",
        evaluator="evaluator.document_acquisition",
        metrics=(
            "acquisition_success_rate",
            "extraction_success_rate",
            "failure_classification_accuracy",
            "fallback_usage_accuracy",
            "duplicate_blob_reuse_accuracy",
            "corpus_availability_accuracy",
        ),
        gating="deterministic",
        edge_cases=(
            "valid OA PDFs",
            "fallback locations",
            "no location",
            "HTML masquerading as PDF",
            "oversized documents",
            "restricted/unavailable documents",
            "duplicate blobs",
            "insufficient extracted text",
        ),
        gaps=("live publisher/academic download endpoints not benchmarked",),
    ),
    CoverageRow(
        capability="Incremental revalidation",
        phase="2C-3C (idempotency)",
        benchmark="incremental-revalidation-v1",
        evaluator="evaluator.revalidation",
        metrics=(
            "stale_reuse_rate",
            "required_recomputation_accuracy",
            "unchanged_reuse_accuracy",
            "provenance_version_accuracy",
        ),
        gating="deterministic",
        edge_cases=(
            "new protocol -> new decisions",
            "superseding identity -> new view",
            "model config change -> new evidence execution",
            "changed corpus -> new synthesis",
            "changed synthesis -> new gap analysis",
            "changed model -> new equilibrium analysis",
            "unchanged inputs -> deterministic reuse",
        ),
        gaps=("cross-run persisted-database revalidation beyond store reopen not benchmarked",),
    ),
    CoverageRow(
        capability="Publication / submission packaging",
        phase="4C",
        benchmark="publication-packaging-v1",
        evaluator="evaluator.publication_packaging",
        metrics=(
            "package_validation_accuracy",
            "export_success_accuracy",
            "bibliography_integrity",
            "placeholder_removal_accuracy",
            "anonymization_accuracy",
            "blob_persistence_accuracy",
            "deterministic_render_accuracy",
        ),
        gating="deterministic",
        edge_cases=(
            "correct citation resolution",
            "unresolved citation blocks readiness",
            "bibliography dedup",
            "missing metadata not invented",
            "anonymous-review mode",
            "leftover internal placeholder",
            "Markdown/LaTeX/DOCX/PDF exports",
            "export BlobStore persistence",
            "deterministic rerender",
            "invalid package not marked publication-ready",
        ),
        gaps=("live journal submission endpoints not benchmarked",),
    ),
    CoverageRow(
        capability="Evidence enrichment / pre-acquisition",
        phase="5C-5D",
        benchmark="evidence-enrichment-v1",
        evaluator="evaluator.evidence_enrichment",
        metrics=(
            "enrichment_grounding_accuracy",
            "enrichment_outcome_accuracy",
            "source_preservation_accuracy",
            "unsupported_rejection_accuracy",
            "stale_reuse_rate",
            "preacquisition_accuracy",
            "provenance_version_accuracy",
        ),
        gating="deterministic",
        edge_cases=(
            "title-only candidate enriched to abstract",
            "indexed-metadata candidate enriched to abstract",
            "unsupported enrichment rejected (never fabricated evidence)",
            "rate-limited source rejected without invention",
            "pre-acquisition selects and upgrades sparse candidates",
            "original sparse source preserved",
            "changed source set does not reuse stale enrichment",
        ),
        gaps=("live publisher/provider get() endpoints not benchmarked",),
    ),
    CoverageRow(
        capability="Policy-constrained model routing (shadow)",
        phase="7C",
        benchmark="model-routing-policy-v1",
        evaluator="evaluator.model_routing",
        metrics=(
            "routing_decision_accuracy",
            "eligibility_filter_accuracy",
            "constraint_satisfaction_accuracy",
            "fallback_accuracy",
            "role_isolation_accuracy",
            "stale_evidence_handling_accuracy",
            "deterministic_tiebreak_accuracy",
            "unsafe_selection_rate",
        ),
        gating="deterministic",
        edge_cases=(
            "quality-first chooses highest eligible correctness",
            "cheaper failing model rejected",
            "cost-constrained chooses cheapest quality-qualified model",
            "latency-constrained chooses fastest eligible model",
            "model without structured-output capability rejected",
            "missing cost handled without invention",
            "stale leaderboard rejected",
            "insufficient repetitions rejected",
            "no eligible candidate",
            "deterministic tie-breaking",
            "fallback selection",
            "role isolation (reasoning evidence never routes a critic task)",
        ),
        gaps=("automatic production model switching not implemented (Phase 7D+)",),
    ),
    CoverageRow(
        capability="Live-quality validation — reasoning (real-model)",
        phase="7D.0",
        benchmark="live-quality-reasoning-v1",
        evaluator="evaluator.live_quality_reasoning",
        metrics=(
            "structured_output_success",
            "grounding_correctness",
            "unsupported_reference_rate",
            "instruction_adherence",
            "required_field_completeness",
            "deterministic_downstream_pass",
            "task_completion_rate",
            "critical_grounding_failures",
        ),
        gating="deterministic",
        edge_cases=(
            "evidence extraction grounding/locators",
            "synthesis references produced evidence",
            "gap types within allowed set",
            "mechanism structure + grounding",
            "model required mathematical structure",
            "proposition deterministic verification",
        ),
        gaps=("runs live only (real provider); not an offline scripted-fixture proxy",),
    ),
    CoverageRow(
        capability="Live-quality validation — critic (real-model)",
        phase="7D.0",
        benchmark="live-quality-critic-v1",
        evaluator="evaluator.live_quality_critic",
        metrics=(
            "defect_recall",
            "false_positive_rate",
            "severity_accuracy",
            "required_category_coverage",
            "actionable_revision_rate",
            "structured_output_success",
        ),
        gating="deterministic",
        edge_cases=(
            "mechanism critique with injected defects",
            "model-specification critique with injected defects",
            "proposition critique with injected defects",
            "results critique with injected defects",
            "manuscript critique with injected defects",
        ),
        gaps=("runs live only (real provider)",),
    ),
    CoverageRow(
        capability="Live-quality validation — fast/screening (real-model)",
        phase="7D.0",
        benchmark="live-quality-fast-v1",
        evaluator="evaluator.live_quality_fast",
        metrics=(
            "decision_accuracy",
            "uncertain_case_handling",
            "false_exclusion_rate",
            "structured_output_success",
        ),
        gating="deterministic",
        edge_cases=(
            "clear relevance decisions",
            "uncertain-case handling without forced exclusion",
        ),
        gaps=("latency/cost aggregated by the live-quality service from call records",),
    ),
    CoverageRow(
        capability="Production-routing readiness gate",
        phase="7D.0",
        benchmark="production-routing-readiness-v1",
        evaluator="evaluator.routing_readiness",
        metrics=(
            "readiness_decision_accuracy",
            "qualification_gate_accuracy",
            "role_isolation_accuracy",
            "unsafe_production_qualification_rate",
        ),
        gating="deterministic",
        edge_cases=(
            "no live evidence -> not qualified",
            "live quality below threshold -> rejected",
            "sufficient repetitions + quality -> qualified",
            "critical grounding failure -> rejected",
            "high provider-error rate -> rejected",
            "stale live evidence -> rejected",
            "role evidence mismatch -> rejected",
            "qualified primary + fallback",
            "no qualified fallback when required -> not ready",
        ),
        gaps=("automatic routing activation not implemented (Phase 7D)",),
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
        gaps=(
            "model tournaments + role leaderboards implemented (Phase 7B); "
            "automatic model routing / live corpora / leaderboard service not implemented (7C+)",
        ),
    ),
)

# Capabilities with no dedicated benchmark row (known evaluation gaps).
KNOWN_COVERAGE_GAPS: tuple[tuple[str, str], ...] = (
    ("Phase 2A provider connectors", "fixture-driven, no benchmark over real provider APIs"),
    ("Phase 3D proposition critique quality", "advisory-only"),
    (
        "Phase 7D automatic production model routing / switching",
        "shadow-mode routing (7C) + live-quality readiness (7D.0) implemented; "
        "controlled activation deliberately not implemented",
    ),
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
