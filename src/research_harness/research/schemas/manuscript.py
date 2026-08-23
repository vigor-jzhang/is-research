"""Phase 4B manuscript schemas — outline, sections, citations, draft,
revision, and critique.

Manuscripts are structured and evidence-grounded: every substantive claim
must trace to a verified Phase 2/3/4A artifact, citations are internal
`CitationReference` objects with `[CITE:<id>]` placeholders, and drafts are
immutable with revision via supersedes. No journal-specific formatting or
submission packaging (Phase 4C).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ManuscriptSectionId(str, Enum):
    """Stable section slugs (recommended structure)."""

    introduction = "introduction"
    literature_review = "literature_review"
    research_gap = "research_gap"
    theory_mechanism = "theory_mechanism"
    analytical_model = "analytical_model"
    equilibrium_analysis = "equilibrium_analysis"
    propositions = "propositions"
    numerical_analysis = "numerical_analysis"
    discussion = "discussion"
    contributions = "contributions"
    limitations = "limitations"
    conclusion = "conclusion"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class SectionArtifactType(str, Enum):
    """Artifact types a manuscript claim may ground in."""

    evidence_item = "evidence_item"
    synthesis_statement = "synthesis_statement"
    research_gap = "research_gap"
    selected_mechanism = "selected_mechanism"
    formal_analytical_model = "formal_analytical_model"
    verified_proposition = "verified_proposition"
    numerical_result = "numerical_result"
    research_finding = "research_finding"
    contribution_claim = "contribution_claim"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class SectionSpec(BaseModel):
    section_id: ManuscriptSectionId
    title: str
    description: str
    allowed_artifact_types: list[SectionArtifactType] = Field(default_factory=list)
    artifact_ids: list[str] = Field(
        default_factory=list,
        description="Relevant artifacts for this section (resolved from the chain)",
    )

    model_config = {"extra": "forbid"}


class ManuscriptOutline(BaseModel):
    """Deterministic manuscript structure over a ResearchResultsPackage."""

    results_package_id: str
    title: str
    section_specs: list[SectionSpec] = Field(default_factory=list)
    summary: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class CitationReference(BaseModel):
    """Internal citation: a claim in a section traced to evidence in a paper."""

    citation_id: str = Field(description="Used in the body as [CITE:<citation_id>]")
    paper_identity_id: str
    evidence_item_id: str
    page_locator: str | None = Field(default=None, description="e.g. 'p. 214' or 'pp. 214-217'")
    claim_context: str | None = Field(default=None, description="What is being cited")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("citation_id", "paper_identity_id", "evidence_item_id")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class ManuscriptClaim(BaseModel):
    """One substantive claim of a section with its grounding.

    Either `grounding_artifact_id` (verified Phase 2/3/4A artifact) or
    `citation_id` (literature claim with evidence) must be present.
    """

    text: str
    grounding_type: SectionArtifactType | None = Field(default=None)
    grounding_artifact_id: str | None = Field(default=None)
    citation_id: str | None = Field(default=None)
    conditions: list[str] = Field(
        default_factory=list,
        description="Conditions preserved from the referenced proposition/equilibrium",
    )

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("claim text must be non-empty")
        return v

    model_config = {"extra": "forbid"}


class ManuscriptSection(BaseModel):
    """An immutable manuscript section with structured claims + citations."""

    outline_id: str
    section_id: ManuscriptSectionId
    title: str
    body: str = Field(description="Prose with [CITE:<citation_id>] placeholders")
    claims: list[ManuscriptClaim] = Field(default_factory=list)
    citations: list[CitationReference] = Field(default_factory=list)
    model_role: str = Field(default="reasoning")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("section title must be non-empty")
        return v

    @field_validator("body")
    @classmethod
    def validate_body(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("section body must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class ManuscriptDraftStatus(str, Enum):
    drafted = "drafted"
    critiqued = "critiqued"
    revised = "revised"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class ManuscriptDraft(BaseModel):
    """Immutable draft version; revision creates a superseding ManuscriptDraft."""

    outline_id: str
    results_package_id: str
    title: str
    version: int = Field(default=1)
    section_ids: list[str] = Field(default_factory=list)
    status: ManuscriptDraftStatus = ManuscriptDraftStatus.drafted
    critique_ids: list[str] = Field(default_factory=list)
    supersedes: str | None = Field(
        default=None, description="Draft id this version supersedes (V2 supersedes V1)"
    )
    summary: str | None = Field(default=None)
    model_role: str = Field(default="reasoning")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class ManuscriptDraftExecution(BaseModel):
    """Operational record of a drafting run."""

    outline_id: str
    results_package_id: str
    draft_id: str | None = Field(default=None)
    sections_created: int = 0
    sections_reused: int = 0
    citations_created: int = 0
    claims_created: int = 0
    novelty_claims_normalized: int = 0
    failures: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, Any] = Field(default_factory=dict)
    model_role: str = Field(default="reasoning")
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class ManuscriptCritiqueCategory(str, Enum):
    unsupported_claim = "unsupported_claim"
    citation_gap = "citation_gap"
    overclaiming = "overclaiming"
    cross_section_inconsistency = "cross_section_inconsistency"
    gap_contribution_mismatch = "gap_contribution_mismatch"
    mathematical_result_distortion = "mathematical_result_distortion"
    repetition = "repetition"
    weak_logical_flow = "weak_logical_flow"
    missing_limitations = "missing_limitations"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class ManuscriptCritiqueVerdict(str, Enum):
    approve = "approve"
    revise = "revise"
    reject = "reject"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class ManuscriptCritiqueIssue(BaseModel):
    category: ManuscriptCritiqueCategory
    description: str
    severity: str = Field(default="medium", pattern="^(high|medium|low)$")
    location: str | None = Field(default=None, description="Section id or claim location")

    model_config = {"extra": "forbid"}


class ManuscriptCritique(BaseModel):
    """Independent critique of a ManuscriptDraft, persisted separately."""

    draft_id: str
    issues: list[ManuscriptCritiqueIssue] = Field(default_factory=list)
    overall_assessment: str
    verdict: ManuscriptCritiqueVerdict = ManuscriptCritiqueVerdict.revise
    recommendations: list[str] = Field(default_factory=list)
    model_role: str = Field(default="critic")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("overall_assessment")
    @classmethod
    def validate_assessment(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("overall assessment must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
