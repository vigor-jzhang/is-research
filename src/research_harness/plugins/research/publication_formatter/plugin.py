"""Phase 4C publication formatter — deterministic formatting, citation
resolution, bibliography, exports, anonymous-review mode, and the immutable
SubmissionPackage.

Formatting never changes research conclusions: sections are re-ordered and
citation placeholders resolved, but proposition/equilibrium conditions and
all verified content stay intact. Citations resolve internally
(CitationReference -> PaperIdentity -> PaperRecord); bibliographic fields are
never invented. Exports render deterministically and are stored via the
BlobStore. No automatic journal submission.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.identity import PaperIdentity
from research_harness.research.schemas.manuscript import (
    ManuscriptClaim,
    ManuscriptDraft,
    ManuscriptSection,
)
from research_harness.research.schemas.paper import PaperRecord
from research_harness.research.schemas.proposition import (
    Proposition,
    PropositionVerification,
    PropositionVerificationStatus,
)
from research_harness.research.schemas.publication import (
    Bibliography,
    BibliographyEntry,
    CitationStyleName,
    CoverLetter,
    ExportRecord,
    FormattedManuscript,
    FormattedManuscriptStatus,
    FormattedSection,
    FrontMatter,
    PublicationExecution,
    PublicationProfile,
    SubmissionPackage,
    SubmissionPackageStatus,
    ValidationIssue,
)
from research_harness.research.schemas.results import (
    ContributionClaim,
    ResearchResultsPackage,
)

logger = logging.getLogger(__name__)

_NOVELTY_RE = re.compile(
    r"\bfirst\s+(study|work|paper|analysis|investigation|time)\b|"
    r"\bthe\s+first\s+to\b|"
    r"\bwe\s+are\s+the\s+first\b|"
    r"\bno\s+prior\s+(study|work|paper|research|analysis)\b|"
    r"\bnever\s+been\s+(studied|examined|analyzed)\b",
    flags=re.IGNORECASE,
)

_CITE_PLACEHOLDER_RE = re.compile(r"\[CITE:([^\]]+)\]")

_REQUIRED_DEFAULT_SECTIONS = [
    "introduction",
    "literature_review",
    "research_gap",
    "theory_mechanism",
    "analytical_model",
    "equilibrium_analysis",
    "propositions",
    "numerical_analysis",
    "discussion",
    "contributions",
    "limitations",
    "conclusion",
]


class _FrontMatterResponse(BaseModel):
    title: str | None = None
    abstract: str = ""
    keywords: list[str] = Field(default_factory=list)

    @field_validator("abstract")
    @classmethod
    def validate_abstract(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("abstract must be non-empty")
        return v

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, v: list[str]) -> list[str]:
        return [k.strip() for k in v if k.strip()]

    model_config = {"extra": "forbid"}


class _CoverLetterResponse(BaseModel):
    opening: str
    contribution_summary: list[str] = Field(default_factory=list)
    journal_fit: str
    closing: str

    @field_validator("opening", "journal_fit", "closing")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must be non-empty")
        return v

    model_config = {"extra": "forbid"}


class PublicationFormatterService:
    def __init__(
        self,
        model_router: Any,
        artifact_store: Any,
        blob_store: Any | None = None,
        formatter_role: str = "reasoning",
        max_llm_calls: int = 10,
    ) -> None:
        self._router = model_router
        self._store = artifact_store
        self._blobs = blob_store
        self._formatter_role = formatter_role
        self._max_llm_calls = max_llm_calls
        self._calls = 0

    @property
    def service_id(self) -> str:
        return "research.publication_formatter"

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    async def create_profile(
        self,
        name: str,
        citation_style: str = CitationStyleName.author_year.value,
        required_sections: list[str] | None = None,
        section_order: list[str] | None = None,
        word_limits: dict[str, int] | None = None,
        total_word_limit: int | None = None,
        abstract_max_words: int = 250,
        keywords_max: int = 6,
        anonymous_review: bool = False,
        abstract_required: bool = True,
        formatting_rules: dict[str, Any] | None = None,
    ) -> str:
        profile = PublicationProfile(
            name=name,
            citation_style=CitationStyleName(citation_style),
            required_sections=(
                list(required_sections)
                if required_sections is not None
                else list(_REQUIRED_DEFAULT_SECTIONS)
            ),
            section_order=list(section_order or []),
            word_limits=dict(word_limits or {}),
            total_word_limit=total_word_limit,
            abstract_max_words=abstract_max_words,
            keywords_max=keywords_max,
            anonymous_review=anonymous_review,
            abstract_required=abstract_required,
            formatting_rules=dict(formatting_rules or {}),
        )
        p_env = ArtifactEnvelope.create(
            payload=profile,
            artifact_type="publication_profile",
            producer="research.publication_formatter",
        )
        await self._store.put(p_env)
        return p_env.artifact_id

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    async def format(self, draft_id: str, profile_id: str) -> str:
        """Format an immutable ManuscriptDraft per a profile. Returns the
        FormattedManuscript artifact id (leaf of its supersedes chain)."""
        existing = await self._store.list(artifact_type="formatted_manuscript")
        for env in existing:
            try:
                fm = FormattedManuscript.model_validate(env.payload)
                if (
                    fm.draft_id == draft_id
                    and fm.profile_id == profile_id
                    and fm.model_role == self._formatter_role
                ):
                    return env.artifact_id
            except Exception:
                continue

        d_env = await self._store.get(draft_id)
        draft = d_env.parse_payload(ManuscriptDraft)
        p_env = await self._store.get(profile_id)
        profile = p_env.parse_payload(PublicationProfile)
        sections = [
            (await self._store.get(sid)).parse_payload(ManuscriptSection)
            for sid in draft.section_ids
        ]
        style = _import_styles().get_style(profile.citation_style)
        section_order = profile.section_order or [s.section_id.value for s in sections]

        ordered = sorted(
            sections,
            key=lambda s: (
                section_order.index(s.section_id.value)
                if s.section_id.value in section_order
                else len(section_order)
            ),
        )

        bibliography, citation_map = await self._build_bibliography(draft_id, sections, style)
        formatted_sections: list[FormattedSection] = []
        for section in ordered:
            body, ok_conditions = await self._render_section(section, style, citation_map)
            formatted_sections.append(
                FormattedSection(
                    section_id=section.section_id.value,
                    title=section.title,
                    body=body,
                    word_count=len(body.split()),
                    conditions_preserved=ok_conditions,
                )
            )

        front_matter = await self._build_front_matter(draft, formatted_sections, profile)
        total_words = sum(s.word_count for s in formatted_sections)

        manuscript = FormattedManuscript(
            draft_id=draft_id,
            results_package_id=draft.results_package_id,
            profile_id=profile_id,
            profile_name=profile.name,
            citation_style=profile.citation_style,
            front_matter=front_matter,
            sections=formatted_sections,
            bibliography_id=None,
            anonymous_review=profile.anonymous_review,
            total_word_count=total_words,
            validation_status=FormattedManuscriptStatus.formatted,
            citation_map=citation_map,
            model_role=self._formatter_role,
        )

        b_env = ArtifactEnvelope.create(
            payload=bibliography,
            artifact_type="bibliography",
            producer="research.publication_formatter",
        )
        await self._store.put(b_env)
        for entry in bibliography.entries:
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=entry.paper_identity_id,
                    target_artifact_id=b_env.artifact_id,
                    producer="research.publication_formatter",
                )
            )
        manuscript.bibliography_id = b_env.artifact_id
        manuscript.bibliography = bibliography

        fm_env = ArtifactEnvelope.create(
            payload=manuscript,
            artifact_type="formatted_manuscript",
            producer=f"research.publication_formatter:{self._formatter_role}",
        )
        await self._store.put(fm_env)
        for target in (draft_id, profile_id, b_env.artifact_id):
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=target,
                    target_artifact_id=fm_env.artifact_id,
                    producer="research.publication_formatter",
                )
            )
        return fm_env.artifact_id

    # ------------------------------------------------------------------
    # Citation resolution + bibliography
    # ------------------------------------------------------------------

    async def _resolve_paper(self, paper_identity_id: str) -> dict[str, Any]:
        id_env = await self._store.get(paper_identity_id)
        identity = id_env.parse_payload(PaperIdentity)
        for pid in identity.member_paper_artifact_ids:
            try:
                rec_env = await self._store.get(pid)
                if rec_env.artifact_type != "paper_record":
                    continue
                rec = rec_env.parse_payload(PaperRecord)
                return {
                    "authors": [a.name for a in rec.authors],
                    "year": rec.year,
                    "title": rec.title,
                    "venue": rec.venue,
                    "doi": rec.doi,
                }
            except Exception:  # noqa: BLE001
                continue
        # identity exists but no readable PaperRecord: never invent fields
        return {"authors": [], "year": None, "title": paper_identity_id, "venue": None, "doi": None}

    async def _build_bibliography(
        self,
        draft_id: str,
        sections: list[ManuscriptSection],
        style: Any,
    ) -> tuple[Bibliography, dict[str, str]]:
        entries: dict[str, BibliographyEntry] = {}
        citation_map: dict[str, str] = {}
        for section in sections:
            for cite in section.citations:
                citation_map[cite.citation_id] = cite.paper_identity_id
        for section in sections:
            for placeholder in _CITE_PLACEHOLDER_RE.findall(section.body):
                if placeholder not in citation_map:
                    continue
                identity_id = citation_map[placeholder]
                if identity_id in entries:
                    if placeholder not in entries[identity_id].citation_ids:
                        entries[identity_id].citation_ids.append(placeholder)
                    continue
                meta = await self._resolve_paper(identity_id)
                entry = BibliographyEntry(
                    paper_identity_id=identity_id,
                    citation_ids=[placeholder],
                    title=meta["title"],
                    authors=list(meta["authors"]),
                    year=meta["year"],
                    venue=meta["venue"],
                    doi=meta["doi"],
                    rendered=style.bibliography_entry(
                        meta["authors"], meta["year"], meta["title"], meta["venue"], meta["doi"]
                    ),
                )
                entries[identity_id] = entry
        bibliography = Bibliography(
            profile_name="",
            citation_style=CitationStyleName.author_year
            if getattr(style, "name", None) is None
            else style.name,
            entries=list(entries.values()),
            rendered_text="\n".join(e.rendered for e in entries.values()),
        )
        return bibliography, citation_map

    async def _render_section(
        self, section: ManuscriptSection, style: Any, citation_map: dict[str, str]
    ) -> tuple[str, bool]:
        # 1. resolve placeholders -> inline citations
        body = section.body
        for placeholder in _CITE_PLACEHOLDER_RE.findall(body):
            cite = next((c for c in section.citations if c.citation_id == placeholder), None)
            if cite is None:
                continue
            meta = await self._resolve_paper(cite.paper_identity_id)
            inline = style.inline(meta["authors"], meta["year"], cite.page_locator, meta["title"])
            body = body.replace(f"[CITE:{placeholder}]", inline)
        # 2. conditions preserved check (claims grounded in verified propositions)
        ok = True
        for claim in section.claims:
            if claim.grounding_type is None or claim.grounding_artifact_id is None:
                continue
            if claim.grounding_type.value == "verified_proposition":
                if not await self._conditions_ok(claim):
                    ok = False
        return body, ok

    async def _conditions_ok(self, claim: ManuscriptClaim) -> bool:
        """Re-validate against the verified proposition (same rule as 4B):
        every proposition condition must appear (substring) in the claim's
        conditions, and the proposition must be verified."""
        try:
            prop = (await self._store.get(claim.grounding_artifact_id)).parse_payload(Proposition)
        except Exception:  # noqa: BLE001
            return False
        verified = False
        for venv in await self._store.list(artifact_type="proposition_verification"):
            try:
                v = venv.parse_payload(PropositionVerification)
            except Exception:  # noqa: BLE001
                continue
            if v.proposition_id == claim.grounding_artifact_id and v.status in (
                PropositionVerificationStatus.verified,
                PropositionVerificationStatus.conditionally_verified,
            ):
                verified = True
        if not verified:
            return False
        for cond in prop.conditions:
            if not any(cond in c for c in claim.conditions):
                return False
        return True

    # ------------------------------------------------------------------
    # Front matter (deterministic default + optional reasoning-role generation)
    # ------------------------------------------------------------------

    async def _build_front_matter(
        self,
        draft: ManuscriptDraft,
        formatted_sections: list[FormattedSection],
        profile: PublicationProfile,
    ) -> FrontMatter:
        fm = FrontMatter(title=draft.title, generated_by="deterministic")
        if profile.anonymous_review:
            fm.authors = []
            fm.affiliations = []
            fm.acknowledgements = ""
        if not profile.abstract_required:
            return fm
        if self._calls >= self._max_llm_calls:
            return fm
        prompt = self._build_front_matter_prompt(draft, formatted_sections, profile)
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You write publication front matter for a finished manuscript. "
                        "Return valid JSON matching the schema. Ground everything in "
                        "the manuscript content below; never add new findings or "
                        "novelty claims. Never include chain-of-thought."
                    ),
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self._build_front_matter_schema(),
            temperature=0.0,
        )
        try:
            response = await self._router.complete(self._formatter_role, request)
            data = json.loads(_extract_json(response.message.content or ""))
            parsed = _FrontMatterResponse.model_validate(data)
        except Exception as e:
            logger.warning("front matter generation failed (%s); using deterministic title", e)
            return fm
        self._calls += 1
        abstract = _NOVELTY_RE.sub("", parsed.abstract).strip()
        keywords = [_NOVELTY_RE.sub("", k).strip() for k in parsed.keywords[: profile.keywords_max]]
        keywords = [k for k in keywords if k]
        return FrontMatter(
            title=(_NOVELTY_RE.sub("", parsed.title).strip() if parsed.title else draft.title),
            abstract=abstract,
            keywords=keywords,
            generated_by="llm",
        )

    def _build_front_matter_prompt(
        self,
        draft: ManuscriptDraft,
        formatted_sections: list[FormattedSection],
        profile: PublicationProfile,
    ) -> str:
        body = "\n\n".join(f"## {s.title}\n{s.body[:900]}" for s in formatted_sections)
        return f"""Write the front matter for the manuscript '{draft.title}'.

Abstract max words: {profile.abstract_max_words}
Keywords max: {profile.keywords_max}

Manuscript content:
{body[:12000]}

Produce: title (only if you must improve it; prefer the given title), abstract,
keywords. No new findings, no novelty claims ('first study', etc.), no
self-references.
"""

    def _build_front_matter_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "abstract": {"type": "string"},
                "keywords": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["abstract"],
            "additionalProperties": False,
        }

    # ------------------------------------------------------------------
    # Validation (deterministic)
    # ------------------------------------------------------------------

    async def validate(self, manuscript_id: str) -> tuple[str, bool]:
        """Validate deterministically; persists a superseding FormattedManuscript
        with validation_status + issues. Returns (leaf id, passed)."""
        m_env = await self._store.get(manuscript_id)
        manuscript = m_env.parse_payload(FormattedManuscript)
        issues: list[ValidationIssue] = []

        # unresolved citations / leftover placeholders
        for section in manuscript.sections:
            placeholders = set(_CITE_PLACEHOLDER_RE.findall(section.body))
            if placeholders:
                issues.append(
                    ValidationIssue(
                        check="leftover_placeholders",
                        detail=f"{section.section_id} still contains {sorted(placeholders)}",
                    )
                )
            missing = placeholders - set(manuscript.citation_map)
            if missing:
                issues.append(
                    ValidationIssue(
                        check="unresolved_citations",
                        detail=f"{section.section_id} cites unresolved ids {sorted(missing)}",
                    )
                )
            if not section.conditions_preserved:
                issues.append(
                    ValidationIssue(
                        check="conditions_changed",
                        detail=f"{section.section_id} drops proposition/equilibrium conditions",
                    )
                )

        # bibliography covers every cited paper
        cited_identities = set(manuscript.citation_map.values())
        bib_identities = (
            {e.paper_identity_id for e in manuscript.bibliography.entries}
            if manuscript.bibliography
            else set()
        )
        uncovered = cited_identities - bib_identities
        if uncovered:
            issues.append(
                ValidationIssue(
                    check="missing_bibliography_entries",
                    detail=f"cited papers without bibliography entries: {sorted(uncovered)}",
                )
            )

        # required sections + ordering
        from research_harness.research.schemas.publication import PublicationProfile

        profile = (await self._store.get(manuscript.profile_id)).parse_payload(PublicationProfile)
        present = {s.section_id for s in manuscript.sections}
        missing_sections = [s for s in profile.required_sections if s not in present]
        if missing_sections:
            issues.append(
                ValidationIssue(
                    check="missing_required_sections",
                    detail=f"missing sections: {missing_sections}",
                )
            )
        if profile.section_order:
            order = [s.section_id for s in manuscript.sections]
            expected = [s for s in profile.section_order if s in present]
            if order != expected:
                issues.append(
                    ValidationIssue(
                        check="section_order",
                        detail=f"expected {expected}, got {order}",
                    )
                )

        # word limits
        for section in manuscript.sections:
            limit = profile.word_limits.get(section.section_id)
            if limit and section.word_count > limit:
                issues.append(
                    ValidationIssue(
                        check="word_count",
                        detail=f"{section.section_id} has {section.word_count} words (limit {limit})",
                    )
                )
        if profile.total_word_limit and manuscript.total_word_count > profile.total_word_limit:
            issues.append(
                ValidationIssue(
                    check="word_count",
                    detail=f"total {manuscript.total_word_count} words (limit {profile.total_word_limit})",
                )
            )
        if len(manuscript.front_matter.abstract.split()) > profile.abstract_max_words:
            issues.append(
                ValidationIssue(
                    check="abstract_word_count",
                    detail=f"abstract has {len(manuscript.front_matter.abstract.split())} words (max {profile.abstract_max_words})",
                )
            )

        # anonymous review violations
        if profile.anonymous_review:
            if manuscript.front_matter.authors or manuscript.front_matter.affiliations:
                issues.append(
                    ValidationIssue(
                        check="anonymous_review",
                        detail="front matter leaks authors or affiliations in anonymous mode",
                    )
                )

        # unsupported novelty language
        for field_name, text in (
            ("abstract", manuscript.front_matter.abstract),
            ("title", manuscript.front_matter.title),
        ):
            if _NOVELTY_RE.search(text):
                issues.append(
                    ValidationIssue(
                        check="novelty_language",
                        detail=f"{field_name} contains global-novelty phrasing",
                    )
                )

        passed = not issues
        new = manuscript.model_copy(
            update={
                "validation_status": (
                    FormattedManuscriptStatus.validated
                    if passed
                    else FormattedManuscriptStatus.failed
                ),
                "validation_issues": issues,
            }
        )
        v_env = ArtifactEnvelope.create(
            payload=new,
            artifact_type="formatted_manuscript",
            producer=f"research.publication_formatter:{self._formatter_role}",
        )
        await self._store.put(v_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.supersedes,
                source_artifact_id=manuscript_id,
                target_artifact_id=v_env.artifact_id,
                producer="research.publication_formatter",
            )
        )
        for target in (
            new.draft_id,
            new.profile_id,
            *([new.bibliography_id] if new.bibliography_id else []),
        ):
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=target,
                    target_artifact_id=v_env.artifact_id,
                    producer="research.publication_formatter",
                )
            )
        return v_env.artifact_id, passed

    async def latest_validated(self, manuscript_id: str) -> str:
        current = manuscript_id
        while True:
            children = await self._store.get_children(current)
            supers = [c.target_artifact_id for c in children if c.relation.value == "supersedes"]
            if not supers:
                break
            current = supers[0]
        return current

    # ------------------------------------------------------------------
    # Export + packaging
    # ------------------------------------------------------------------

    async def export(self, manuscript_id: str, fmt: str) -> str:
        """Deterministic export to the BlobStore. Returns the ExportRecord id."""
        m_env = await self._store.get(manuscript_id)
        manuscript = m_env.parse_payload(FormattedManuscript)

        existing = await self._store.list(artifact_type="export_record")
        for env in existing:
            try:
                er = ExportRecord.model_validate(env.payload)
                if er.source_draft_id == manuscript.draft_id and er.format == fmt:
                    return env.artifact_id
            except Exception:
                continue

        exporter = _import_exporters().get_exporter(fmt)
        profile = (await self._store.get(manuscript.profile_id)).parse_payload(PublicationProfile)
        payload = exporter.render(manuscript, profile)
        if self._blobs is None:
            raise ValueError("blob_store.default is required for exports")
        blob_ref = await self._blobs.put_bytes(payload.data, media_type=payload.media_type)
        record = ExportRecord(
            format=payload.format,
            renderer=payload.renderer,
            renderer_version=payload.renderer_version,
            blob_ref=blob_ref.model_dump(),
            content_hash=payload.content_hash,
            size_bytes=len(payload.data),
            source_draft_id=manuscript.draft_id,
            profile_id=manuscript.profile_id,
        )
        e_env = ArtifactEnvelope.create(
            payload=record, artifact_type="export_record", producer="research.publication_formatter"
        )
        await self._store.put(e_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=manuscript_id,
                target_artifact_id=e_env.artifact_id,
                producer="research.publication_formatter",
            )
        )
        return e_env.artifact_id

    async def package(self, manuscript_id: str, with_cover_letter: bool = False) -> str:
        """Assemble the SubmissionPackage. Status is `ready` only when the
        manuscript is validated and all exports succeed."""
        leaf_id = await self.latest_validated(manuscript_id)
        m_env = await self._store.get(leaf_id)
        manuscript = m_env.parse_payload(FormattedManuscript)

        started = datetime.now(UTC)
        exec_record = PublicationExecution(
            draft_id=manuscript.draft_id,
            profile_id=manuscript.profile_id,
            model_role=self._formatter_role,
            started_at=started,
        )

        export_ids: list[str] = []
        failures: list[dict[str, Any]] = []
        for fmt in _import_exporters().available_formats():
            try:
                export_ids.append(await self.export(leaf_id, fmt))
            except Exception as e:  # noqa: BLE001
                failures.append({"format": fmt, "error": str(e)})

        cover_letter_id = None
        if with_cover_letter:
            try:
                cover_letter_id = await self._cover_letter(leaf_id)
            except Exception as e:  # noqa: BLE001
                failures.append({"cover_letter": str(e)})

        validation_passed = manuscript.validation_status == FormattedManuscriptStatus.validated
        status = (
            SubmissionPackageStatus.ready
            if validation_passed and not failures and len(export_ids) == 4
            else SubmissionPackageStatus.failed
        )
        summary = (
            f"{len(export_ids)} exports, {len(manuscript.citation_map)} citations resolved, "
            f"{len(manuscript.bibliography.entries) if manuscript.bibliography else 0} references, "
            f"validated={validation_passed}"
        )
        pkg = SubmissionPackage(
            formatted_manuscript_id=leaf_id,
            draft_id=manuscript.draft_id,
            profile_id=manuscript.profile_id,
            export_records=[
                (await self._store.get(eid)).parse_payload(ExportRecord) for eid in export_ids
            ],
            cover_letter_id=cover_letter_id,
            status=status,
            summary=summary,
            model_role=self._formatter_role,
        )
        pkg_env = ArtifactEnvelope.create(
            payload=pkg,
            artifact_type="submission_package",
            producer="research.publication_formatter",
        )
        await self._store.put(pkg_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=leaf_id,
                target_artifact_id=pkg_env.artifact_id,
                producer="research.publication_formatter",
            )
        )
        for eid in export_ids:
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=eid,
                    target_artifact_id=pkg_env.artifact_id,
                    producer="research.publication_formatter",
                )
            )
        if cover_letter_id:
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=cover_letter_id,
                    target_artifact_id=pkg_env.artifact_id,
                    producer="research.publication_formatter",
                )
            )

        exec_record.formatted_manuscript_id = leaf_id
        exec_record.submission_package_id = pkg_env.artifact_id
        exec_record.exports_created = len(export_ids)
        exec_record.citations_resolved = len(manuscript.citation_map)
        exec_record.bibliography_entries = (
            len(manuscript.bibliography.entries) if manuscript.bibliography else 0
        )
        exec_record.front_matter_generated = manuscript.front_matter.generated_by == "llm"
        exec_record.cover_letter_generated = cover_letter_id is not None
        exec_record.validation_passed = validation_passed
        exec_record.failures = failures
        exec_record.completed_at = datetime.now(UTC)
        ex_env = ArtifactEnvelope.create(
            payload=exec_record,
            artifact_type="publication_execution",
            producer="research.publication_formatter",
        )
        await self._store.put(ex_env)
        return pkg_env.artifact_id

    # ------------------------------------------------------------------
    # Cover letter
    # ------------------------------------------------------------------

    async def _cover_letter(self, manuscript_id: str) -> str:
        m_env = await self._store.get(manuscript_id)
        manuscript = m_env.parse_payload(FormattedManuscript)
        contribution_ids: list[str] = []
        contributions: list[ContributionClaim] = []
        try:
            package = (await self._store.get(manuscript.results_package_id)).parse_payload(
                ResearchResultsPackage
            )
            contribution_ids = list(package.contribution_claim_ids)
            for cid in contribution_ids:
                try:
                    contributions.append(
                        (await self._store.get(cid)).parse_payload(ContributionClaim)
                    )
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            contribution_ids = []
        profile = (await self._store.get(manuscript.profile_id)).parse_payload(PublicationProfile)

        if self._calls >= self._max_llm_calls:
            raise ValueError("max LLM calls exceeded for cover letter")
        prompt = self._build_cover_letter_prompt(manuscript, contributions, profile)
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You write a structured cover letter for a manuscript "
                        "submission. Return valid JSON matching the schema. "
                        "Summarize contributions only; never overclaim, never "
                        "claim novelty. Never include chain-of-thought."
                    ),
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self._build_cover_letter_schema(),
            temperature=0.0,
            metadata={"manuscript_id": manuscript_id},
        )
        try:
            response = await self._router.complete(self._formatter_role, request)
            data = json.loads(_extract_json(response.message.content or ""))
            parsed = _CoverLetterResponse.model_validate(data)
        except Exception as e:
            raise ValueError(f"cover letter generation failed: {e}") from e
        self._calls += 1

        summary = [_NOVELTY_RE.sub("", s).strip() for s in parsed.contribution_summary]
        summary = [s for s in summary if s]
        letter = CoverLetter(
            formatted_manuscript_id=manuscript_id,
            opening=_NOVELTY_RE.sub("", parsed.opening).strip(),
            contribution_summary=summary,
            journal_fit=_NOVELTY_RE.sub("", parsed.journal_fit).strip(),
            closing=_NOVELTY_RE.sub("", parsed.closing).strip(),
            anonymous=profile.anonymous_review,
            novelty_normalized=0,
            model_role=self._formatter_role,
        )
        l_env = ArtifactEnvelope.create(
            payload=letter,
            artifact_type="cover_letter",
            producer=f"research.publication_formatter:{self._formatter_role}",
        )
        await self._store.put(l_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=manuscript_id,
                target_artifact_id=l_env.artifact_id,
                producer="research.publication_formatter",
            )
        )
        for cid in contribution_ids:
            try:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=cid,
                        target_artifact_id=l_env.artifact_id,
                        producer="research.publication_formatter",
                    )
                )
            except Exception:  # noqa: BLE001
                continue
        return l_env.artifact_id

    def _build_cover_letter_prompt(
        self,
        manuscript: FormattedManuscript,
        contributions: list[ContributionClaim],
        profile: PublicationProfile,
    ) -> str:
        contrib_lines = "\n".join(f"  - {c.claim[:200]}" for c in contributions) or "  (none)"
        return f"""Write a cover letter for the manuscript '{manuscript.front_matter.title}'.

Target: {profile.name}  (anonymous review: {profile.anonymous_review})

Abstract: {manuscript.front_matter.abstract[:1000]}

Contribution claims (summarize faithfully, no embellishment):
{contrib_lines}

Produce: opening, contribution_summary (list), journal_fit, closing.
Rules: no novelty claims ('first study'), no new findings, no self-references.
"""

    def _build_cover_letter_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "opening": {"type": "string"},
                "contribution_summary": {"type": "array", "items": {"type": "string"}},
                "journal_fit": {"type": "string"},
                "closing": {"type": "string"},
            },
            "required": ["opening", "contribution_summary", "journal_fit", "closing"],
            "additionalProperties": False,
        }


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    return text[start : end + 1]


def _import_styles() -> Any:
    from research_harness.plugins.research.publication_formatter import styles

    return styles


def _import_exporters() -> Any:
    from research_harness.plugins.research.publication_formatter import exporters

    return exporters


class PublicationFormatterPlugin(Plugin):
    def __init__(self, formatter_role: str | None = None) -> None:
        self._formatter_role_override = formatter_role
        self._service: PublicationFormatterService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.publication_formatter",
            version="0.1.0",
            plugin_type="research",
            description="Publication formatting, bibliography, exports, submission package (Phase 4C)",
            provides=["publication_formatter.default"],
            requires=["model_router.default", "artifact_store.default", "blob_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        research_cfg: dict[str, Any] = {}
        if "research" in cfg and isinstance(cfg["research"], dict):
            research_cfg = (
                cfg["research"].get("publication", {})
                if isinstance(cfg["research"].get("publication"), dict)
                else {}
            )
        formatter_role = (
            self._formatter_role_override or research_cfg.get("formatter_role") or "reasoning"
        )
        max_llm_calls = int(research_cfg.get("max_llm_calls", 10))
        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        blobs = ctx.require("blob_store.default")
        self._service = PublicationFormatterService(
            model_router=router,
            artifact_store=store,
            blob_store=blobs,
            formatter_role=str(formatter_role),
            max_llm_calls=max_llm_calls,
        )
        ctx.register("publication_formatter.default", self._service)
        # register the deterministic exporters as services
        from research_harness.plugins.research.publication_formatter import exporters

        for exporter in exporters.EXPORTERS:
            ctx.register(f"manuscript_exporter.{exporter.format}", exporter)
