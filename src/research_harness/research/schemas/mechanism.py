"""Phase 3A mechanism-development schemas — gap selection, mechanism candidates,
critique, and the selected mechanism.

Epistemic rule: mechanisms are candidate hypotheses. Every element of a
mechanism is explicitly labeled with its knowledge basis so that
literature-supported facts are never conflated with novel hypotheses or
modeling assumptions. No mathematical model is constructed here (Phase 3B).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.research.schemas.gap import AnalyticalModelOpportunity


class SelectionStatus(str, Enum):
    """Lifecycle of a gap selection."""

    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"


class KnowledgeBasis(str, Enum):
    """Epistemic status of a mechanism element.

    - literature_supported: backed by existing synthesis/evidence artifacts
    - research_inference: inferred from the reviewed corpus (not directly stated)
    - new_hypothesis: novel hypothesis proposed by the researcher/model
    - modeling_assumption: assumption adopted for future modeling convenience
    """

    literature_supported = "literature_supported"
    research_inference = "research_inference"
    new_hypothesis = "new_hypothesis"
    modeling_assumption = "modeling_assumption"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class MechanismEvaluation(BaseModel):
    """Raw dimension scores for a mechanism candidate (kept separate).

    The composite is a simple mean for transparency; raw dimensions are
    authoritative for interpretation.
    """

    gap_alignment: float = Field(ge=0.0, le=1.0)
    theoretical_coherence: float = Field(ge=0.0, le=1.0)
    novelty_within_reviewed_corpus: float = Field(ge=0.0, le=1.0)
    analytical_tractability: float = Field(ge=0.0, le=1.0)
    managerial_economic_relevance: float = Field(ge=0.0, le=1.0)
    is_relevance: float = Field(ge=0.0, le=1.0)

    @property
    def composite(self) -> float:
        return round(
            (
                self.gap_alignment
                + self.theoretical_coherence
                + self.novelty_within_reviewed_corpus
                + self.analytical_tractability
                + self.managerial_economic_relevance
                + self.is_relevance
            )
            / 6.0,
            3,
        )

    model_config = {"extra": "forbid"}


class GroundingElement(BaseModel):
    """One element of a mechanism with its explicit knowledge basis.

    `source_ids` are required for `literature_supported` elements and must
    resolve to existing synthesis/evidence artifacts; they are optional for
    other bases.
    """

    element: str = Field(description="The mechanism element, e.g. a causal claim")
    basis: KnowledgeBasis
    source_ids: list[str] = Field(default_factory=list)

    @field_validator("element")
    @classmethod
    def validate_element(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("grounding element must be non-empty")
        return v

    model_config = {"extra": "forbid"}


class GapSelection(BaseModel):
    """Durable record of which gap was selected (and why) for Phase 3A."""

    gap_analysis_id: str
    selected_gap_id: str
    alternative_gap_ids: list[str] = Field(default_factory=list)
    evidence_synthesis_basis: str | None = Field(
        default=None, description="Summary of the evidence/synthesis basis of the selection"
    )
    research_importance: float | None = Field(default=None, ge=0.0, le=1.0)
    theoretical_relevance: float | None = Field(default=None, ge=0.0, le=1.0)
    analytical_model_suitability: float | None = Field(default=None, ge=0.0, le=1.0)
    tractability: float | None = Field(default=None, ge=0.0, le=1.0)
    selection_rationale: str
    status: SelectionStatus = SelectionStatus.pending_approval
    autonomy_mode: str = Field(default="high", description="high or interactive")
    approval_required: bool = False
    approval_decided_by: str | None = Field(default=None)
    approval_reason: str | None = Field(default=None)
    selected_by: str = Field(
        default="model", description="model, operator, or fallback (deterministic)"
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("selection_rationale")
    @classmethod
    def validate_rationale(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("selection rationale must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class MechanismStatus(str, Enum):
    candidate = "candidate"
    critiqued = "critiqued"
    selected = "selected"
    rejected = "rejected"


class MechanismCandidate(BaseModel):
    """A structured candidate mechanism for the selected gap.

    No equations/propositions here — that is Phase 3B.
    """

    gap_id: str
    gap_selection_id: str | None = Field(default=None)
    name: str
    description: str
    actors: list[str] = Field(default_factory=list)
    strategic_interactions: list[str] = Field(default_factory=list)
    information_structure: str | None = Field(default=None)
    incentives: list[str] = Field(default_factory=list)
    causal_logic: str
    key_assumptions: list[str] = Field(default_factory=list)
    expected_outcomes: list[str] = Field(default_factory=list)
    boundary_conditions: list[str] = Field(default_factory=list)
    literature_support_ids: list[str] = Field(
        default_factory=list,
        description="Referenced synthesis_statement / evidence_item artifact ids",
    )
    grounding: list[GroundingElement] = Field(
        default_factory=list,
        description="Element-level knowledge basis (literature vs inference vs hypothesis)",
    )
    literature_support_papers: int = Field(
        default=0, description="Deterministic count of distinct supporting papers"
    )
    literature_support_evidence_items: int = Field(
        default=0, description="Deterministic count of supporting evidence items"
    )
    analytical_model_potential: AnalyticalModelOpportunity | None = Field(default=None)
    evaluation: MechanismEvaluation | None = Field(default=None)
    status: MechanismStatus = MechanismStatus.candidate
    model_role: str = Field(default="reasoning")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "description", "causal_logic")
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


class CritiqueCategory(str, Enum):
    """Categories of critique issues (extensible)."""

    logical_inconsistency = "logical_inconsistency"
    unsupported_assumption = "unsupported_assumption"
    already_explained_by_reviewed_literature = "already_explained_by_reviewed_literature"
    unclear_causal_direction = "unclear_causal_direction"
    unmodelable_concept = "unmodelable_concept"
    missing_actor_or_incentive = "missing_actor_or_incentive"
    alternative_explanation = "alternative_explanation"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class CritiqueSeverity(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class CritiqueIssue(BaseModel):
    category: CritiqueCategory
    description: str
    severity: CritiqueSeverity = CritiqueSeverity.medium
    location: str | None = Field(default=None)

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("critique issue description must be non-empty")
        return v

    model_config = {"extra": "forbid"}


class CritiqueVerdict(str, Enum):
    keep = "keep"
    revise = "revise"
    reject = "reject"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class MechanismCritique(BaseModel):
    """Independent critique of a mechanism candidate (durable)."""

    mechanism_candidate_id: str
    gap_id: str | None = Field(default=None)
    issues: list[CritiqueIssue] = Field(default_factory=list)
    overall_assessment: str
    verdict: CritiqueVerdict = CritiqueVerdict.revise
    revision_recommendations: list[str] = Field(default_factory=list)
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


class MechanismAnalysisStatus(str, Enum):
    generated = "generated"
    critiqued = "critiqued"
    selected = "selected"


class MechanismAnalysis(BaseModel):
    """Durable aggregate of a mechanism-development run over a gap selection.

    Immutable; updated by superseding artifacts with the same artifact type
    (generated -> critiqued -> selected), preserving the full lineage.
    """

    gap_selection_id: str
    gap_id: str
    candidate_ids: list[str] = Field(default_factory=list)
    critique_ids: list[str] = Field(default_factory=list)
    selected_mechanism_id: str | None = Field(default=None)
    status: MechanismAnalysisStatus = MechanismAnalysisStatus.generated
    generator_role: str = Field(default="reasoning")
    critic_role: str = Field(default="critic")
    revision_role: str = Field(default="reasoning")
    summary: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class MechanismAnalysisExecution(BaseModel):
    """Operational record of a mechanism generation run."""

    gap_selection_id: str
    gap_id: str
    candidates_created: int = 0
    candidates_rejected: int = 0
    generator_role: str = "reasoning"
    failures: list[dict[str, Any]] = Field(default_factory=list)
    counts: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class SelectedMechanism(BaseModel):
    """The final mechanism after generation + critique + revision.

    The original candidates are never mutated; this artifact records the
    candidate, the critiques it incorporated, and the revision notes.
    """

    gap_id: str
    gap_selection_id: str
    mechanism_candidate_id: str
    critique_ids: list[str] = Field(default_factory=list)
    name: str
    description: str
    actors: list[str] = Field(default_factory=list)
    strategic_interactions: list[str] = Field(default_factory=list)
    information_structure: str | None = Field(default=None)
    incentives: list[str] = Field(default_factory=list)
    causal_logic: str
    key_assumptions: list[str] = Field(default_factory=list)
    expected_outcomes: list[str] = Field(default_factory=list)
    boundary_conditions: list[str] = Field(default_factory=list)
    grounding: list[GroundingElement] = Field(default_factory=list)
    revision_notes: list[str] = Field(
        default_factory=list, description="What changed relative to the candidate"
    )
    analytical_model_potential: AnalyticalModelOpportunity | None = Field(default=None)
    evaluation: MechanismEvaluation | None = Field(default=None)
    model_role: str = Field(default="reasoning")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "description", "causal_logic")
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
