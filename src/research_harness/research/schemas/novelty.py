"""Phase 5A schemas — external novelty validation and submission-risk gate.

Every artifact here is immutable. Novelty validation never proves global
novelty; it produces evidence-backed, scope-declared assessments of whether
prior literature could materially challenge the manuscript's novelty claims,
plus a submission-readiness decision that never overstates what literature
search can prove.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class NoveltyClaimType(str, Enum):
    absolute_priority = "absolute_priority"
    scoped_priority = "scoped_priority"
    literature_absence = "literature_absence"
    mechanism_novelty = "mechanism_novelty"
    model_novelty = "model_novelty"
    result_novelty = "result_novelty"
    empirical_or_contextual_novelty = "empirical_or_contextual_novelty"
    contribution_difference = "contribution_difference"


class ClaimRiskLevel(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class ClaimImportance(str, Enum):
    major = "major"
    minor = "minor"


class NoveltyClaimLocation(BaseModel):
    section_id: str
    quote: str
    paragraph: int | None = None

    model_config = {"extra": "forbid"}


class NoveltyClaim(BaseModel):
    """One manuscript claim requiring external validation."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    manuscript_id: str
    section_id: str = Field(description="Primary manuscript section (e.g. introduction)")
    claim_text: str
    claim_type: NoveltyClaimType
    risk: ClaimRiskLevel
    scope: str | None = Field(
        default=None, description="Declared scope of the claim, e.g. 'in two-sided markets'"
    )
    importance: ClaimImportance = ClaimImportance.major
    extraction_method: str = Field(
        description="deterministic | model_assisted | hybrid (deterministic + model)"
    )
    source_quote: str = Field(description="Exact span in the manuscript that grounds the claim")
    locations: list[NoveltyClaimLocation] = Field(
        default_factory=list, description="All manuscript locations asserting the same claim"
    )
    source_artifact_ids: list[str] = Field(default_factory=list)
    equivalent_claim_id: str | None = Field(
        default=None,
        description=(
            "Previous-manuscript claim id this claim is exactly equivalent to "
            "(stable identity across supersessions)"
        ),
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class NoveltyQueryType(str, Enum):
    exact = "exact"
    mechanism = "mechanism"
    relationship = "relationship"
    setting = "setting"
    theory = "theory"
    synonym = "synonym"


class NoveltyPlanQuery(BaseModel):
    literature_query_id: str | None = None
    query: str
    query_type: NoveltyQueryType
    synonyms: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class NoveltyPlanGeneration(BaseModel):
    method: str = Field(
        default="deterministic", description="deterministic | model_assisted | hybrid"
    )
    model_role: str | None = None

    model_config = {"extra": "forbid"}


class NoveltySearchPlan(BaseModel):
    """Persisted BEFORE external search executes. Bounded, scope-declared."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    claim_id: str
    manuscript_id: str
    queries: list[NoveltyPlanQuery] = Field(default_factory=list)
    query_artifact_ids: list[str] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)
    date_cutoff: date | None = None
    year_from: int | None = None
    year_to: int | None = None
    maximum_results: int = Field(default=10, ge=1, le=200)
    search_scope: str = Field(default="", description="Declared search scope statement")
    generation: NoveltyPlanGeneration = Field(default_factory=NoveltyPlanGeneration)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class NoveltySearchExecution(BaseModel):
    """Durable record of every external search executed for a claim."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    claim_id: str
    search_plan_id: str
    search_record_artifact_ids: list[str] = Field(default_factory=list)
    as_of_date: date | None = Field(default=None, description="Explicit assessment date")
    planned_searches: int = 0
    executed_searches: int = 0
    successful_searches: int = 0
    provider_failures: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class NoveltyCandidateRef(BaseModel):
    paper_identity_id: str
    found_by_query_ids: list[str] = Field(default_factory=list)
    found_by_providers: list[str] = Field(default_factory=list)
    rank: int | None = None
    score: float | None = None
    earliest_year: int | None = Field(default=None, description="None = date unavailable")

    model_config = {"extra": "forbid"}


class NoveltyExclusion(BaseModel):
    paper_identity_id: str | None = None
    paper_record_id: str | None = None
    reason: str

    model_config = {"extra": "forbid"}


class NoveltyCandidateSet(BaseModel):
    """Union of candidate papers for a claim, deduplicated via PaperIdentity."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    claim_id: str
    search_plan_id: str
    search_execution_id: str
    candidates: list[NoveltyCandidateRef] = Field(default_factory=list)
    excluded: list[NoveltyExclusion] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class MatchLevel(str, Enum):
    match = "match"
    partial_match = "partial_match"
    different = "different"
    unknown = "unknown"


class NoveltyDimension(str, Enum):
    focal_phenomenon = "focal_phenomenon"
    actors = "actors"
    setting = "setting"
    mechanism = "mechanism"
    key_assumptions = "key_assumptions"
    strategic_decision = "strategic_decision"
    causal_equilibrium_relationship = "causal_equilibrium_relationship"
    theoretical_result = "theoretical_result"
    claimed_contribution = "claimed_contribution"


class NoveltyDimensionScore(BaseModel):
    dimension: NoveltyDimension
    value: MatchLevel

    model_config = {"extra": "forbid"}


class EvidenceBasis(str, Enum):
    full_text = "full_text"
    abstract = "abstract"
    indexed_metadata = "indexed_metadata"
    title_only = "title_only"


class CandidateRelationship(str, Enum):
    direct_prior_art = "direct_prior_art"
    strong_overlap = "strong_overlap"
    partial_overlap = "partial_overlap"
    adjacent = "adjacent"
    distinct = "distinct"
    insufficient_evidence = "insufficient_evidence"


class NoveltyCandidateAssessment(BaseModel):
    """Evidence-backed comparison of one prior-art candidate against the claim."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    claim_id: str
    candidate_set_id: str
    paper_identity_id: str
    dimensions: list[NoveltyDimensionScore] = Field(default_factory=list)
    relationship: CandidateRelationship
    evidence_basis: EvidenceBasis
    evidence_artifact_ids: list[str] = Field(
        default_factory=list,
        description="EvidenceItem / FullTextDocument / PaperRecord artifact ids used",
    )
    assessment_text: str = Field(default="")
    critic_assessment_ids: list[str] = Field(default_factory=list)
    model_role: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class CriticVerdict(str, Enum):
    concurs = "concurs"
    disputes = "disputes"
    uncertain = "uncertain"


class NoveltyCriticAssessment(BaseModel):
    """Independent critic pass over a candidate assessment. Persisted
    separately so disagreement is preserved, never erased."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    claim_id: str
    candidate_assessment_id: str
    paper_identity_id: str
    verdict: CriticVerdict
    reasoning: str = Field(default="")
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    model_role: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class NoveltyCoverage(BaseModel):
    planned_query_count: int = 0
    executed_query_count: int = 0
    successful_query_count: int = 0
    provider_count: int = 0
    provider_failures: list[dict[str, Any]] = Field(default_factory=list)
    candidate_count: int = 0
    candidates_with_evidence_count: int = 0
    date_coverage_limitations: list[str] = Field(default_factory=list)
    coverage_sufficient: bool = False

    model_config = {"extra": "forbid"}


class NoveltyClaimStatus(str, Enum):
    threatened = "threatened"
    weakened = "weakened"
    not_threatened_within_search_scope = "not_threatened_within_search_scope"
    unverified = "unverified"


class NoveltyClaimAssessment(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    claim_id: str
    manuscript_id: str
    search_plan_id: str
    search_execution_id: str
    candidate_set_id: str
    candidate_assessment_ids: list[str] = Field(default_factory=list)
    critic_assessment_ids: list[str] = Field(default_factory=list)
    status: NoveltyClaimStatus
    coverage: NoveltyCoverage = Field(default_factory=NoveltyCoverage)
    reasoning: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class NoveltyRevisionRecommendation(BaseModel):
    """Structured recommendation only — the manuscript is never modified."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    claim_id: str
    original_text: str
    risk: ClaimRiskLevel
    reason: str
    supporting_candidate_ids: list[str] = Field(default_factory=list)
    suggested_scope_change: str | None = None
    suggested_wording: str | None = None
    model_role: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class NoveltyReportStatus(str, Enum):
    clear = "clear"
    revise = "revise"
    blocked = "blocked"
    unverified = "unverified"


class NoveltyValidationReport(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    submission_package_id: str
    manuscript_id: str = Field(description="FormattedManuscript artifact id assessed")
    draft_id: str
    manuscript_content_hash: str = Field(
        description="Content hash of the assessed manuscript (version safety)"
    )
    as_of_date: date = Field(description="Explicit assessment date")
    claim_ids: list[str] = Field(default_factory=list)
    claim_assessment_ids: list[str] = Field(default_factory=list)
    search_execution_ids: list[str] = Field(default_factory=list)
    candidate_set_ids: list[str] = Field(default_factory=list)
    candidate_assessment_ids: list[str] = Field(default_factory=list)
    critic_assessment_ids: list[str] = Field(default_factory=list)
    revision_recommendation_ids: list[str] = Field(default_factory=list)
    critical_threats: list[str] = Field(default_factory=list)
    weakened_claims: list[str] = Field(default_factory=list)
    unverified_claims: list[str] = Field(default_factory=list)
    safe_within_scope_claims: list[str] = Field(default_factory=list)
    coverage_summary: dict[str, Any] = Field(default_factory=dict)
    overall_status: NoveltyReportStatus
    aggregation_policy: str = Field(
        default=(
            "any critical-risk claim threatened -> blocked; "
            "else any threatened or weakened claim -> revise; "
            "else any assessed claim unverified -> unverified; "
            "else -> clear"
        )
    )
    supersedes: str | None = Field(default=None)
    model_role: str = "reasoning"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class ReadinessStatus(str, Enum):
    ready = "ready"
    needs_revision = "needs_revision"
    blocked = "blocked"
    unverified = "unverified"


class SubmissionReadinessGate(BaseModel):
    """External-readiness layer on top of the unchanged SubmissionPackage.
    SubmissionPackage.status == ready keeps its Phase 4C meaning; this gate
    adds 'external pre-submission validation has been performed'."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    submission_package_id: str
    novelty_report_id: str
    manuscript_id: str
    draft_id: str
    package_status: str = Field(description="SubmissionPackageStatus value at gate time")
    novelty_status: NoveltyReportStatus
    status: ReadinessStatus
    blocking_claim_ids: list[str] = Field(default_factory=list)
    revision_claim_ids: list[str] = Field(default_factory=list)
    unverified_claim_ids: list[str] = Field(default_factory=list)
    decision_policy: str = Field(
        default=(
            "package not ready -> blocked; "
            "novelty clear -> ready; "
            "novelty revise -> needs_revision; "
            "novelty blocked -> blocked; "
            "novelty unverified -> unverified"
        )
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class NoveltyValidationExecution(BaseModel):
    """Operational record of one novelty-validate run."""

    submission_package_id: str
    report_id: str | None = None
    gate_id: str | None = None
    claims_extracted: int = 0
    search_executions: int = 0
    candidate_assessments: int = 0
    critic_assessments: int = 0
    revision_recommendations: int = 0
    unverified_claims: int = 0
    blocked_claims: int = 0
    model_role: str = "reasoning"
    failures: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class ManuscriptSectionChangeType(str, Enum):
    unchanged = "unchanged"
    changed = "changed"
    added = "added"
    removed = "removed"


class ManuscriptSectionChange(BaseModel):
    section_id: str
    change_type: ManuscriptSectionChangeType
    old_body_hash: str | None = None
    new_body_hash: str | None = None

    model_config = {"extra": "forbid"}


class ManuscriptChangeSet(BaseModel):
    """Deterministic record of what changed between two manuscript states."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    old_manuscript_id: str
    new_manuscript_id: str
    old_content_hash: str
    new_content_hash: str
    changed_sections: list[ManuscriptSectionChange] = Field(default_factory=list)
    added_claim_ids: list[str] = Field(default_factory=list)
    removed_claim_ids: list[str] = Field(default_factory=list)
    modified_claim_ids: list[str] = Field(default_factory=list)
    unchanged_claim_ids: list[str] = Field(default_factory=list)
    claim_identity_map: dict[str, str] = Field(
        default_factory=dict,
        description="new claim id -> previous claim id for exactly equivalent claims",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class NoveltyRevalidationPlan(BaseModel):
    """Persisted BEFORE any Phase 5B search: which claims to reuse, which to
    revalidate, and why."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    previous_report_id: str
    manuscript_change_set_id: str
    new_manuscript_id: str
    affected_claim_ids: list[str] = Field(
        default_factory=list, description="New-manuscript claims to revalidate"
    )
    reusable_claim_assessment_ids: list[str] = Field(
        default_factory=list, description="Previous assessments safely reused"
    )
    reuse_reasons: dict[str, str] = Field(default_factory=dict)
    revalidation_reasons: dict[str, str] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class NoveltyRevalidationExecution(BaseModel):
    """Operational record of one incremental revalidation run."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    plan_id: str
    previous_report_id: str
    reused_assessment_ids: list[str] = Field(default_factory=list)
    newly_validated_claim_ids: list[str] = Field(default_factory=list)
    newly_assessment_ids: list[str] = Field(default_factory=list)
    resulting_report_id: str | None = None
    resulting_gate_id: str | None = None
    failures: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class StalenessStatus(str, Enum):
    current = "current"
    stale = "stale"


class EnrichmentAttemptStatus(str, Enum):
    success = "success"
    not_found = "not_found"
    restricted = "restricted"
    rate_limited = "rate_limited"
    failed = "failed"
    skipped = "skipped"


class EvidenceEnrichmentPlan(BaseModel):
    """Persisted BEFORE any acquisition: what evidence is requested for a
    sparse candidate, which strategies to try, and why."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    candidate_assessment_id: str | None = Field(
        default=None, description="Existing assessment id when reassessing"
    )
    paper_identity_id: str
    claim_id: str
    requested_evidence_types: list[str] = Field(
        default_factory=list, description="e.g. ['abstract', 'full_text']"
    )
    acquisition_strategies: list[str] = Field(
        default_factory=list,
        description="Ordered strategies: provider_get_abstract, document_full_text",
    )
    reason: str = Field(default="")
    provider_service_policy: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class EvidenceEnrichmentAttempt(BaseModel):
    """One acquisition attempt: strategy, provider, status, artifacts."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    plan_id: str
    strategy: str = Field(description="e.g. provider_get_abstract | document_full_text")
    provider: str | None = None
    status: EnrichmentAttemptStatus
    retrieved_artifact_ids: list[str] = Field(default_factory=list)
    failure_reason: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class EnrichmentOutcome(str, Enum):
    enriched = "enriched"
    partially_enriched = "partially_enriched"
    no_improvement = "no_improvement"
    failed = "failed"


class PreAcquisitionExecution(BaseModel):
    """Phase 5D — bounded evidence pre-acquisition before novelty assessment.
    Records considered/selected/skipped candidates, deterministic selection
    reasons, budgets, and deterministic metrics."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    claim_id: str
    candidate_set_id: str
    considered_candidate_ids: list[str] = Field(default_factory=list)
    selected_candidate_ids: list[str] = Field(default_factory=list)
    skipped_candidate_ids: list[str] = Field(default_factory=list)
    selection_reasons: dict[str, str] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    metrics: dict[str, int] = Field(default_factory=dict)
    enrichment_execution_ids: list[str] = Field(
        default_factory=list, description="Phase 5C EvidenceEnrichmentExecution ids"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class EvidenceEnrichmentExecution(BaseModel):
    """Operational record: before/after evidence basis and outcome."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    plan_id: str
    attempt_ids: list[str] = Field(default_factory=list)
    resulting_evidence_ids: list[str] = Field(
        default_factory=list,
        description="PaperRecord / FullTextDocument / EvidenceItem artifact ids gained",
    )
    before_evidence_basis: EvidenceBasis
    after_evidence_basis: EvidenceBasis
    outcome: EnrichmentOutcome
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
