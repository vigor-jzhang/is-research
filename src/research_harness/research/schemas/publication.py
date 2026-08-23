"""Phase 4C publication schemas — profiles, bibliography, formatted
manuscript, exports, and the submission package.

Formatting is deterministic and never changes research conclusions.
Citations resolve internally via CitationReference -> PaperIdentity ->
PaperRecord; bibliographic fields are never invented. Exported files live in
the BlobStore (binary DOCX/PDF never inside SQLite JSON). No automatic
journal submission (out of scope).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class CitationStyleName(str, Enum):
    author_year = "author_year"
    apa = "apa"
    journal_specific = "journal_specific"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class PublicationProfile(BaseModel):
    """Configurable target-publication profile (never hard-coded to one journal)."""

    name: str = Field(description="Journal / style name, e.g. 'MIS Quarterly (generic)'")
    citation_style: CitationStyleName = CitationStyleName.author_year
    required_sections: list[str] = Field(
        default_factory=list, description="Section ids that must be present"
    )
    section_order: list[str] = Field(
        default_factory=list,
        description="Desired section id order (reorders the draft's sections)",
    )
    word_limits: dict[str, int] = Field(
        default_factory=dict, description="Section id -> max words ('' for total)"
    )
    total_word_limit: int | None = Field(default=None, ge=1)
    abstract_max_words: int = Field(default=250, ge=10, le=2000)
    abstract_required: bool = Field(default=True)
    keywords_max: int = Field(default=6, ge=0, le=20)
    anonymous_review: bool = Field(default=False)
    formatting_rules: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form rules consumed by exporters (e.g. latex_documentclass)",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("profile name must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class BibliographyEntry(BaseModel):
    """One canonical reference, deduplicated by PaperIdentity."""

    paper_identity_id: str
    citation_ids: list[str] = Field(default_factory=list)
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = Field(default=None)
    venue: str | None = Field(default=None)
    doi: str | None = Field(default=None)
    rendered: str = Field(description="Rendered per the citation style")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class Bibliography(BaseModel):
    """Resolved reference list for a formatted manuscript."""

    profile_name: str
    citation_style: CitationStyleName = CitationStyleName.author_year
    entries: list[BibliographyEntry] = Field(default_factory=list)
    rendered_text: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class FormattedSection(BaseModel):
    section_id: str
    title: str
    body: str = Field(description="Rendered prose; [CITE:...] resolved to inline citations")
    word_count: int = Field(default=0)
    conditions_preserved: bool = Field(
        default=True, description="Section claims' conditions unchanged vs the draft"
    )

    model_config = {"extra": "forbid"}


class FrontMatter(BaseModel):
    title: str
    abstract: str = Field(default="")
    keywords: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    affiliations: list[str] = Field(default_factory=list)
    acknowledgements: str = Field(default="")
    generated_by: str = Field(default="deterministic", description="deterministic | llm")

    model_config = {"extra": "forbid"}


class FormattedManuscriptStatus(str, Enum):
    formatted = "formatted"
    validated = "validated"
    failed = "failed"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class ValidationIssue(BaseModel):
    check: str
    severity: str = Field(default="error", pattern="^(error|warning)$")
    detail: str

    model_config = {"extra": "forbid"}


class FormattedManuscript(BaseModel):
    """Deterministic rendering of an immutable ManuscriptDraft per a profile."""

    draft_id: str
    results_package_id: str
    profile_id: str
    profile_name: str
    citation_style: CitationStyleName = CitationStyleName.author_year
    front_matter: FrontMatter = Field(default_factory=lambda: FrontMatter(title=""))
    sections: list[FormattedSection] = Field(default_factory=list)
    bibliography_id: str | None = Field(default=None)
    bibliography: Bibliography | None = Field(default=None)
    anonymous_review: bool = Field(default=False)
    total_word_count: int = Field(default=0)
    validation_status: FormattedManuscriptStatus = FormattedManuscriptStatus.formatted
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    citation_map: dict[str, str] = Field(
        default_factory=dict,
        description="rendered inline key -> paper_identity_id (provenance kept after rendering)",
    )
    model_role: str = Field(default="reasoning")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class ExportRecord(BaseModel):
    """Metadata of one deterministic export; content lives in the BlobStore."""

    format: str = Field(description="markdown | latex | docx | pdf")
    renderer: str
    renderer_version: str
    blob_ref: dict[str, Any] = Field(description="BlobReference (content-addressed)")
    content_hash: str = Field(description="sha256 of the rendered bytes")
    size_bytes: int = Field(ge=0)
    source_draft_id: str
    profile_id: str
    exported_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class SubmissionPackageStatus(str, Enum):
    assembled = "assembled"
    ready = "ready"
    failed = "failed"

    @classmethod
    def values(cls) -> list[str]:
        return [c.value for c in cls]


class SubmissionPackage(BaseModel):
    """Immutable submission package: formatted manuscript + exports (+ optional cover letter)."""

    formatted_manuscript_id: str
    draft_id: str
    profile_id: str
    export_records: list[ExportRecord] = Field(default_factory=list)
    cover_letter_id: str | None = Field(default=None)
    status: SubmissionPackageStatus = SubmissionPackageStatus.assembled
    summary: str | None = Field(default=None)
    model_role: str = Field(default="reasoning")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class CoverLetter(BaseModel):
    """Optional structured cover letter, subject to the same anti-overclaiming rules."""

    formatted_manuscript_id: str
    recipient: str = Field(default="Dear Editor")
    opening: str = Field(default="")
    contribution_summary: list[str] = Field(default_factory=list)
    journal_fit: str = Field(default="")
    closing: str = Field(default="")
    authors: list[str] = Field(default_factory=list)
    affiliations: list[str] = Field(default_factory=list)
    anonymous: bool = Field(default=False)
    novelty_normalized: int = Field(default=0)
    model_role: str = Field(default="reasoning")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("opening")
    @classmethod
    def validate_opening(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("cover letter opening must be non-empty")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}


class PublicationExecution(BaseModel):
    """Operational record of a publication pipeline run."""

    draft_id: str
    profile_id: str
    formatted_manuscript_id: str | None = Field(default=None)
    submission_package_id: str | None = Field(default=None)
    exports_created: int = 0
    citations_resolved: int = 0
    bibliography_entries: int = 0
    front_matter_generated: bool = Field(default=False)
    cover_letter_generated: bool = Field(default=False)
    validation_passed: bool = Field(default=False)
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
