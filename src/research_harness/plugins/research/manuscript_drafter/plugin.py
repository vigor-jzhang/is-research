"""Phase 4B manuscript drafter — deterministic outline + section-by-section
evidence-grounded drafting.

The `reasoning` role drafts ONE section per model call, receiving only the
artifacts relevant to that section. Every claim must ground in a verified
Phase 2/3/4A artifact (or carry a citation with evidence); failed
propositions are never presented as results; proposition/equilibrium
conditions are preserved exactly; global-novelty phrasing is normalized.
Drafts are immutable; revision creates a superseding ManuscriptDraft.
No journal-specific formatting (Phase 4C).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.gap import ResearchGap
from research_harness.research.schemas.manuscript import (
    CitationReference,
    ManuscriptClaim,
    ManuscriptCritique,
    ManuscriptCritiqueIssue,
    ManuscriptDraft,
    ManuscriptDraftExecution,
    ManuscriptDraftStatus,
    ManuscriptOutline,
    ManuscriptSection,
    ManuscriptSectionId,
    SectionArtifactType,
    SectionSpec,
)
from research_harness.research.schemas.numerical import NumericalExperiment
from research_harness.research.schemas.proposition import (
    Proposition,
    PropositionVerification,
    PropositionVerificationStatus,
)
from research_harness.research.schemas.results import (
    ResearchResultsPackage,
)

logger = logging.getLogger(__name__)

_MAX_VALIDATION_RETRIES = 4

# Sweeping novelty patterns normalized during drafting (same policy as 4A)
_NOVELTY_PATTERNS = [
    r"\bfirst\s+(study|work|paper|analysis|investigation|time)\b",
    r"\bthe\s+first\s+to\b",
    r"\bwe\s+are\s+the\s+first\b",
    r"\bno\s+prior\s+(study|work|paper|research|analysis)\b",
    r"\bnever\s+been\s+(studied|examined|analyzed)\b",
]
_NOVELTY_RE = re.compile("|".join(_NOVELTY_PATTERNS), flags=re.IGNORECASE)

_CITE_PLACEHOLDER_RE = re.compile(r"\[CITE:([^\]]+)\]")

_GROUNDING_ARTIFACT_TYPES: dict[SectionArtifactType, str] = {
    SectionArtifactType.evidence_item: "evidence_item",
    SectionArtifactType.synthesis_statement: "synthesis_statement",
    SectionArtifactType.research_gap: "research_gap",
    SectionArtifactType.selected_mechanism: "selected_mechanism",
    SectionArtifactType.formal_analytical_model: "formal_analytical_model",
    SectionArtifactType.verified_proposition: "proposition",
    SectionArtifactType.numerical_result: "numerical_result",
    SectionArtifactType.research_finding: "research_finding",
    SectionArtifactType.contribution_claim: "contribution_claim",
}


class _ClaimItem(BaseModel):
    text: str
    grounding_type: str | None = None
    grounding_artifact_id: str | None = None
    citation_id: str | None = None
    conditions: list[str] = Field(default_factory=list)

    @field_validator("text")
    @classmethod
    def validate_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("claim text must be non-empty")
        return v

    @field_validator("grounding_type")
    @classmethod
    def validate_grounding_type(cls, v: str | None) -> str | None:
        if v is not None and v not in SectionArtifactType.values():
            raise ValueError(f"invalid grounding type {v!r}")
        return v

    model_config = {"extra": "forbid"}


class _CitationItem(BaseModel):
    citation_id: str
    paper_identity_id: str
    evidence_item_id: str
    page_locator: str | None = None
    claim_context: str | None = None

    @field_validator("citation_id", "paper_identity_id", "evidence_item_id")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must be non-empty")
        return v

    model_config = {"extra": "forbid"}


class _SectionResponse(BaseModel):
    title: str
    body: str
    claims: list[_ClaimItem] = Field(min_length=1)
    citations: list[_CitationItem] = Field(default_factory=list)

    @field_validator("title", "body")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must be non-empty")
        return v

    model_config = {"extra": "forbid"}


_SECTION_TITLES: dict[ManuscriptSectionId, str] = {
    ManuscriptSectionId.introduction: "Introduction",
    ManuscriptSectionId.literature_review: "Literature Review",
    ManuscriptSectionId.research_gap: "Research Gap",
    ManuscriptSectionId.theory_mechanism: "Theory / Mechanism",
    ManuscriptSectionId.analytical_model: "Analytical Model",
    ManuscriptSectionId.equilibrium_analysis: "Equilibrium Analysis",
    ManuscriptSectionId.propositions: "Propositions",
    ManuscriptSectionId.numerical_analysis: "Numerical Analysis",
    ManuscriptSectionId.discussion: "Discussion",
    ManuscriptSectionId.contributions: "Contributions",
    ManuscriptSectionId.limitations: "Limitations",
    ManuscriptSectionId.conclusion: "Conclusion",
}


class ManuscriptDrafterService:
    def __init__(
        self,
        model_router: Any,
        artifact_store: Any,
        drafter_role: str = "reasoning",
        max_llm_calls: int = 100,
    ) -> None:
        self._router = model_router
        self._store = artifact_store
        self._drafter_role = drafter_role
        self._max_llm_calls = max_llm_calls
        self._calls = 0

    @property
    def service_id(self) -> str:
        return "research.manuscript_drafter"

    # ------------------------------------------------------------------
    # Outline (deterministic; no LLM)
    # ------------------------------------------------------------------

    async def outline(self, results_package_id: str) -> str:
        """Build a deterministic ManuscriptOutline from a results package."""
        existing = await self._store.list(artifact_type="manuscript_outline")
        for env in existing:
            try:
                o = ManuscriptOutline.model_validate(env.payload)
                if o.results_package_id == results_package_id:
                    return env.artifact_id
            except Exception:
                continue

        pkg_env = await self._store.get(results_package_id)
        package = pkg_env.parse_payload(ResearchResultsPackage)
        gap = (await self._store.get(package.gap_id)).parse_payload(ResearchGap)
        exp = (
            (await self._store.get(package.numerical_experiment_id)).parse_payload(
                NumericalExperiment
            )
            if package.numerical_experiment_id
            else None
        )

        verified_props = await self._verified_propositions(package.model_id)
        prop_ids = list(verified_props)
        result_ids = list(exp.results) if exp else []
        evidence_ids = list(gap.supporting_evidence_ids)
        synthesis_ids = list(gap.supporting_synthesis_statement_ids)

        def spec(
            sid: ManuscriptSectionId,
            description: str,
            types: list[SectionArtifactType],
            ids: list[str],
        ) -> SectionSpec:
            return SectionSpec(
                section_id=sid,
                title=_SECTION_TITLES[sid],
                description=description,
                allowed_artifact_types=types,
                artifact_ids=ids,
            )

        sections = [
            spec(
                ManuscriptSectionId.introduction,
                "Motivation, research question, and a preview of findings/contributions.",
                [
                    SectionArtifactType.research_finding,
                    SectionArtifactType.contribution_claim,
                    SectionArtifactType.research_gap,
                ],
                package.finding_ids + package.contribution_claim_ids + [package.gap_id],
            ),
            spec(
                ManuscriptSectionId.literature_review,
                "Reviewed literature via evidence items and synthesis statements.",
                [SectionArtifactType.evidence_item, SectionArtifactType.synthesis_statement],
                evidence_ids + synthesis_ids,
            ),
            spec(
                ManuscriptSectionId.research_gap,
                "The gap: what the reviewed corpus leaves unresolved.",
                [SectionArtifactType.research_gap, SectionArtifactType.synthesis_statement],
                [package.gap_id] + synthesis_ids,
            ),
            spec(
                ManuscriptSectionId.theory_mechanism,
                "The proposed mechanism: actors, interactions, incentives.",
                [SectionArtifactType.selected_mechanism, SectionArtifactType.research_gap],
                [package.selected_mechanism_id, package.gap_id],
            ),
            spec(
                ManuscriptSectionId.analytical_model,
                "Model primitives: actors, variables, parameters, timing, assumptions, payoffs.",
                [SectionArtifactType.formal_analytical_model],
                [package.model_id],
            ),
            spec(
                ManuscriptSectionId.equilibrium_analysis,
                "The verified equilibrium and its conditions (via verified propositions).",
                [SectionArtifactType.verified_proposition, SectionArtifactType.numerical_result],
                prop_ids + result_ids[:10],
            ),
            spec(
                ManuscriptSectionId.propositions,
                "Verified propositions and comparative statics with their conditions.",
                [SectionArtifactType.verified_proposition],
                prop_ids,
            ),
            spec(
                ManuscriptSectionId.numerical_analysis,
                "Deterministic numerical experiments: sweeps, robustness, welfare.",
                [SectionArtifactType.numerical_result],
                result_ids[:20],
            ),
            spec(
                ManuscriptSectionId.discussion,
                "What the results mean; interpretation within verified bounds.",
                [
                    SectionArtifactType.research_finding,
                    SectionArtifactType.verified_proposition,
                    SectionArtifactType.numerical_result,
                ],
                package.finding_ids + prop_ids + result_ids[:10],
            ),
            spec(
                ManuscriptSectionId.contributions,
                "Contribution claims relative to the reviewed corpus.",
                [SectionArtifactType.contribution_claim, SectionArtifactType.research_finding],
                package.contribution_claim_ids + package.finding_ids,
            ),
            spec(
                ManuscriptSectionId.limitations,
                "Limitations: model scope, package limitations, robustness gaps.",
                [SectionArtifactType.research_gap, SectionArtifactType.research_finding],
                [package.gap_id] + package.finding_ids,
            ),
            spec(
                ManuscriptSectionId.conclusion,
                "Concise summary of findings, contributions, and implications.",
                [SectionArtifactType.research_finding, SectionArtifactType.contribution_claim],
                package.finding_ids + package.contribution_claim_ids,
            ),
        ]

        outline = ManuscriptOutline(
            results_package_id=results_package_id,
            title=f"Manuscript: {gap.title}",
            section_specs=sections,
            summary=f"{len(sections)} sections over package {results_package_id}",
        )
        o_env = ArtifactEnvelope.create(
            payload=outline,
            artifact_type="manuscript_outline",
            producer="research.manuscript_drafter",
        )
        await self._store.put(o_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=results_package_id,
                target_artifact_id=o_env.artifact_id,
                producer="research.manuscript_drafter",
            )
        )
        return o_env.artifact_id

    async def _add_grounding_edges(self, section_env_id: str, section: ManuscriptSection) -> None:
        seen: set[tuple[str, str]] = set()
        for claim in section.claims:
            if claim.grounding_artifact_id:
                key = (claim.grounding_artifact_id, section_env_id)
                if key not in seen:
                    seen.add(key)
                    await self._store.add_provenance(
                        ProvenanceLink(
                            relation=ProvenanceRelation.derived_from,
                            source_artifact_id=claim.grounding_artifact_id,
                            target_artifact_id=section_env_id,
                            producer="research.manuscript_drafter",
                        )
                    )
        for cite in section.citations:
            for source in (cite.evidence_item_id, cite.paper_identity_id):
                key = (source, section_env_id)
                if key not in seen:
                    seen.add(key)
                    await self._store.add_provenance(
                        ProvenanceLink(
                            relation=ProvenanceRelation.derived_from,
                            source_artifact_id=source,
                            target_artifact_id=section_env_id,
                            producer="research.manuscript_drafter",
                        )
                    )

    # ------------------------------------------------------------------
    # Drafting (section by section)
    # ------------------------------------------------------------------

    async def draft(self, outline_id: str, section_ids: list[str] | None = None) -> str:
        """Draft the manuscript section by section. Returns execution id.

        `section_ids` restricts the sections to draft (all specs when None).
        """
        existing = await self._store.list(artifact_type="manuscript_draft_execution")
        wanted_set = sorted(set(section_ids)) if section_ids else None
        for env in existing:
            try:
                ex = ManuscriptDraftExecution.model_validate(env.payload)
            except Exception:
                continue
            if (
                ex.outline_id == outline_id
                and ex.model_role == self._drafter_role
                and ex.completed_at is not None
                and ex.sections_created > 0
            ):
                done = ex.counts.get("section_ids")
                if wanted_set is None and done == "all":
                    return env.artifact_id
                if wanted_set is not None and done == wanted_set:
                    return env.artifact_id
            continue

        o_env = await self._store.get(outline_id)
        outline = o_env.parse_payload(ManuscriptOutline)
        package = (await self._store.get(outline.results_package_id)).parse_payload(
            ResearchResultsPackage
        )
        verified_props = await self._verified_propositions(package.model_id)
        result_ids = set(
            (await self._store.get(package.numerical_experiment_id))
            .parse_payload(NumericalExperiment)
            .results
            if package.numerical_experiment_id
            else []
        )

        wanted = set(section_ids) if section_ids else None
        started = datetime.now(UTC)
        exec_record = ManuscriptDraftExecution(
            outline_id=outline_id,
            results_package_id=outline.results_package_id,
            model_role=self._drafter_role,
            started_at=started,
        )

        section_envs: list[Any] = []
        for spec_ in outline.section_specs:
            if wanted is not None and spec_.section_id.value not in wanted:
                continue
            if self._calls >= self._max_llm_calls:
                raise ValueError("max LLM calls exceeded while drafting sections")
            section, normalized = await self._draft_one_section(
                o_env.artifact_id,
                outline,
                outline.results_package_id,
                spec_,
                verified_props,
                result_ids,
                None,
            )
            env = ArtifactEnvelope.create(
                payload=section,
                artifact_type="manuscript_section",
                producer=f"research.manuscript_drafter:{self._drafter_role}",
            )
            await self._store.put(env)
            await self._add_grounding_edges(env.artifact_id, section)
            section_envs.append(env)
            exec_record.sections_created += 1
            exec_record.citations_created += len(section.citations)
            exec_record.claims_created += len(section.claims)
            exec_record.novelty_claims_normalized += normalized

        if not section_envs:
            raise ValueError("no sections drafted")

        draft = ManuscriptDraft(
            outline_id=outline_id,
            results_package_id=outline.results_package_id,
            title=outline.title,
            version=1,
            section_ids=[env.artifact_id for env in section_envs],
            status=ManuscriptDraftStatus.drafted,
            summary=(
                f"{len(section_envs)} sections, "
                f"{exec_record.claims_created} claims, "
                f"{exec_record.citations_created} citations"
            ),
            model_role=self._drafter_role,
        )
        d_env = ArtifactEnvelope.create(
            payload=draft,
            artifact_type="manuscript_draft",
            producer=f"research.manuscript_drafter:{self._drafter_role}",
        )
        await self._store.put(d_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=outline_id,
                target_artifact_id=d_env.artifact_id,
                producer="research.manuscript_drafter",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=outline.results_package_id,
                target_artifact_id=d_env.artifact_id,
                producer="research.manuscript_drafter",
            )
        )
        for env in section_envs:
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=env.artifact_id,
                    target_artifact_id=d_env.artifact_id,
                    producer="research.manuscript_drafter",
                )
            )

        exec_record.draft_id = d_env.artifact_id
        exec_record.completed_at = datetime.now(UTC)
        exec_record.counts["section_ids"] = wanted_set if wanted_set is not None else "all"
        exec_id = str(uuid.uuid4())
        exec_env = ArtifactEnvelope.create(
            payload=exec_record,
            artifact_type="manuscript_draft_execution",
            producer="research.manuscript_drafter",
            artifact_id=exec_id,
        )
        await self._store.put(exec_env)
        logger.info("drafted %s sections -> draft %s", len(section_envs), d_env.artifact_id)
        return exec_id

    # ------------------------------------------------------------------
    # Revision (immutable V1 -> critique -> V2)
    # ------------------------------------------------------------------

    async def revise(self, draft_id: str) -> str:
        """Create a superseding ManuscriptDraft from critique feedback.

        Sections flagged by the critique are re-drafted with the critique
        appended to their prompts; un-flagged sections are reused by id.
        """
        d_env = await self._store.get(draft_id)
        draft = d_env.parse_payload(ManuscriptDraft)

        existing = await self._store.list(artifact_type="manuscript_draft")
        for env in existing:
            try:
                d = env.parse_payload(ManuscriptDraft)
                if d.supersedes == draft_id and d.model_role == self._drafter_role:
                    ex_env = next(
                        (
                            e
                            for e in await self._store.list(
                                artifact_type="manuscript_draft_execution"
                            )
                            if e.parse_payload(ManuscriptDraftExecution).draft_id == env.artifact_id
                        ),
                        None,
                    )
                    if ex_env is not None:
                        return ex_env.artifact_id
            except Exception:
                continue

        critique = await self._latest_critique(draft_id)
        flagged: dict[str, list[ManuscriptCritiqueIssue]] = {}
        if critique is not None:
            for issue in critique.issues:
                if issue.location and issue.location in ManuscriptSectionId.values():
                    flagged.setdefault(issue.location, []).append(issue)

        outline = (await self._store.get(draft.outline_id)).parse_payload(ManuscriptOutline)
        package = (await self._store.get(outline.results_package_id)).parse_payload(
            ResearchResultsPackage
        )
        verified_props = await self._verified_propositions(package.model_id)
        result_ids = set(
            (await self._store.get(package.numerical_experiment_id))
            .parse_payload(NumericalExperiment)
            .results
            if package.numerical_experiment_id
            else []
        )

        section_ids: list[str] = []
        reused = 0
        for sid in draft.section_ids:
            section = (await self._store.get(sid)).parse_payload(ManuscriptSection)
            spec_ = next(
                (s for s in outline.section_specs if s.section_id == section.section_id), None
            )
            if spec_ is None:
                section_ids.append(sid)
                continue
            issues = flagged.get(section.section_id.value, [])
            if not issues:
                section_ids.append(sid)
                reused += 1
                continue
            section_v2, _ = await self._draft_one_section(
                draft.outline_id,
                outline,
                outline.results_package_id,
                spec_,
                verified_props,
                result_ids,
                issues,
            )
            env = ArtifactEnvelope.create(
                payload=section_v2,
                artifact_type="manuscript_section",
                producer=f"research.manuscript_drafter:{self._drafter_role}",
            )
            await self._store.put(env)
            await self._add_grounding_edges(env.artifact_id, section_v2)
            section_ids.append(env.artifact_id)

        draft_v2 = ManuscriptDraft(
            outline_id=draft.outline_id,
            results_package_id=draft.results_package_id,
            title=draft.title,
            version=draft.version + 1,
            section_ids=section_ids,
            status=ManuscriptDraftStatus.revised,
            supersedes=draft_id,
            summary=(
                f"revised {draft.version + 1}: {len(section_ids) - reused} section(s) re-drafted, "
                f"{reused} reused"
            ),
            model_role=self._drafter_role,
        )
        d2_env = ArtifactEnvelope.create(
            payload=draft_v2,
            artifact_type="manuscript_draft",
            producer=f"research.manuscript_drafter:{self._drafter_role}",
        )
        await self._store.put(d2_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.supersedes,
                source_artifact_id=draft_id,
                target_artifact_id=d2_env.artifact_id,
                producer="research.manuscript_drafter",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=draft.outline_id,
                target_artifact_id=d2_env.artifact_id,
                producer="research.manuscript_drafter",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=draft.results_package_id,
                target_artifact_id=d2_env.artifact_id,
                producer="research.manuscript_drafter",
            )
        )
        for sid in section_ids:
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=sid,
                    target_artifact_id=d2_env.artifact_id,
                    producer="research.manuscript_drafter",
                )
            )

        exec_record = ManuscriptDraftExecution(
            outline_id=draft.outline_id,
            results_package_id=draft.results_package_id,
            draft_id=d2_env.artifact_id,
            sections_created=len(section_ids) - reused,
            sections_reused=reused,
            citations_created=0,
            claims_created=0,
            counts={"revised_from": draft_id, "version": draft_v2.version},
            model_role=self._drafter_role,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        exec_id = str(uuid.uuid4())
        exec_env = ArtifactEnvelope.create(
            payload=exec_record,
            artifact_type="manuscript_draft_execution",
            producer="research.manuscript_drafter",
            artifact_id=exec_id,
        )
        await self._store.put(exec_env)
        return exec_id

    # ------------------------------------------------------------------
    # Section drafting internals
    # ------------------------------------------------------------------

    async def _draft_one_section(
        self,
        outline_id: str,
        outline: ManuscriptOutline,
        package_id: str,
        spec_: SectionSpec,
        verified_props: dict[str, Proposition],
        result_ids: set[str],
        critique_issues: list[ManuscriptCritiqueIssue] | None,
    ) -> tuple[ManuscriptSection, int]:
        from research_harness.contracts.model import Message, ModelRequest

        errors: list[str] = []
        for attempt in range(1 + _MAX_VALIDATION_RETRIES):
            try:
                prompt = await self._build_section_prompt(
                    outline_id,
                    outline,
                    package_id,
                    spec_,
                    verified_props,
                    result_ids,
                    critique_issues,
                    errors,
                )
                request = ModelRequest(
                    messages=[
                        Message(
                            role="system",
                            content=(
                                "You draft one section of a research manuscript from "
                                "VERIFIED artifacts only. Return valid JSON matching the "
                                "schema. Never invent mathematical results or literature "
                                "claims without citations. Never include chain-of-thought."
                            ),
                        ),
                        Message(role="user", content=prompt),
                    ],
                    response_schema=self._build_section_schema(),
                    temperature=0.0,
                    metadata={"outline_id": outline_id},
                )
                try:
                    response = await self._router.complete(self._drafter_role, request)
                    data = json.loads(_extract_json(response.message.content or ""))
                    parsed = _SectionResponse.model_validate(data)
                except Exception as e:
                    raise ValueError(f"section drafting call failed: {e}") from e
                self._calls += 1

                body, normalized_body = self._normalize_novelty(parsed.body)
                claims: list[ManuscriptClaim] = []
                normalized = 1 if normalized_body else 0
                for c in parsed.claims:
                    text, norm = self._normalize_novelty(c.text)
                    normalized += 1 if norm else 0
                    claims.append(
                        ManuscriptClaim(
                            text=text,
                            grounding_type=(
                                SectionArtifactType(c.grounding_type) if c.grounding_type else None
                            ),
                            grounding_artifact_id=c.grounding_artifact_id,
                            citation_id=c.citation_id,
                            conditions=list(c.conditions),
                        )
                    )
                citations = [
                    CitationReference(
                        citation_id=ci.citation_id,
                        paper_identity_id=ci.paper_identity_id,
                        evidence_item_id=ci.evidence_item_id,
                        page_locator=ci.page_locator,
                        claim_context=ci.claim_context,
                    )
                    for ci in parsed.citations
                ]
                await self._validate_section(
                    spec_, body, claims, citations, verified_props, result_ids
                )
                if not claims:
                    raise ValueError(
                        "section has no claims; every substantive claim must be grounded in "
                        "a verified artifact or citation"
                    )
                section = ManuscriptSection(
                    outline_id=outline_id,
                    section_id=spec_.section_id,
                    title=parsed.title,
                    body=body,
                    claims=claims,
                    citations=citations,
                    model_role=self._drafter_role,
                )
                return section, normalized
            except ValueError as e:
                if attempt >= _MAX_VALIDATION_RETRIES:
                    raise
                logger.info(
                    "section %s validation rejected (%s); retrying with feedback",
                    spec_.section_id.value,
                    e,
                )
                errors.append(str(e))
                await asyncio.sleep(1.0)
        raise ValueError("unreachable")  # pragma: no cover

    async def _validate_section(
        self,
        spec_: SectionSpec,
        body: str,
        claims: list[ManuscriptClaim],
        citations: list[CitationReference],
        verified_props: dict[str, Proposition],
        result_ids: set[str],
    ) -> None:
        allowed = set(spec_.allowed_artifact_types)
        citation_ids: set[str] = set()
        seen_cites: set[str] = set()
        for cite in citations:
            if cite.citation_id in seen_cites:
                raise ValueError(f"duplicate citation id {cite.citation_id}")
            seen_cites.add(cite.citation_id)
            citation_ids.add(cite.citation_id)
            if not await self._store.exists(cite.evidence_item_id):
                raise ValueError(
                    f"citation {cite.citation_id} references unknown evidence {cite.evidence_item_id}"
                )
            if not await self._store.exists(cite.paper_identity_id):
                raise ValueError(
                    f"citation {cite.citation_id} references unknown paper identity {cite.paper_identity_id}"
                )

        placeholders = {m.group(1) for m in _CITE_PLACEHOLDER_RE.finditer(body)}
        missing_placeholders = placeholders - citation_ids
        if missing_placeholders:
            raise ValueError(f"body cites unknown placeholders: {sorted(missing_placeholders)}")

        for i, claim in enumerate(claims):
            if claim.grounding_artifact_id is None and claim.citation_id is None:
                raise ValueError(f"claim {i} has neither grounding artifact nor citation")
            if claim.citation_id is not None and claim.citation_id not in citation_ids:
                raise ValueError(f"claim {i} cites unknown citation id {claim.citation_id}")
            if claim.grounding_artifact_id is None:
                continue
            if claim.grounding_type is None:
                raise ValueError(f"claim {i} has a grounding id but no grounding type")
            if claim.grounding_type not in allowed:
                raise ValueError(
                    f"claim {i} grounds in {claim.grounding_type.value}, not allowed in {spec_.section_id.value}"
                )
            gid = claim.grounding_artifact_id
            if not await self._store.exists(gid):
                raise ValueError(f"claim {i} references unknown artifact {gid}")
            if claim.grounding_type == SectionArtifactType.verified_proposition:
                if gid not in verified_props:
                    raise ValueError(
                        f"claim {i} references proposition {gid} that is not verified/conditionally verified"
                    )
                prop = verified_props[gid]
                missing = [
                    c
                    for c in prop.conditions
                    if not any(c in claim_cond for claim_cond in claim.conditions)
                ]
                if missing:
                    raise ValueError(f"claim {i} drops proposition conditions: {missing}")
            elif claim.grounding_type == SectionArtifactType.numerical_result:
                if gid not in result_ids:
                    raise ValueError(
                        f"claim {i} references numerical result {gid} outside the experiment"
                    )
            elif claim.grounding_type == SectionArtifactType.evidence_item:
                if claim.citation_id is None:
                    raise ValueError(
                        f"claim {i} is an uncited literature claim (evidence requires a citation)"
                    )

    async def _verified_propositions(self, model_id: str) -> dict[str, Proposition]:
        out: dict[str, Proposition] = {}
        for env in await self._store.list(artifact_type="proposition"):
            try:
                p = env.parse_payload(Proposition)
            except Exception:
                continue
            if p.model_id != model_id:
                continue
            latest = None
            for venv in await self._store.list(artifact_type="proposition_verification"):
                try:
                    v = venv.parse_payload(PropositionVerification)
                except Exception:
                    continue
                if v.proposition_id == env.artifact_id:
                    if latest is None or v.created_at >= latest.created_at:
                        latest = v
            if latest is not None and latest.status in (
                PropositionVerificationStatus.verified,
                PropositionVerificationStatus.conditionally_verified,
            ):
                out[env.artifact_id] = p
        return out

    async def _latest_critique(self, draft_id: str) -> ManuscriptCritique | None:
        latest = None
        for env in await self._store.list(artifact_type="manuscript_critique"):
            try:
                c = env.parse_payload(ManuscriptCritique)
            except Exception:
                continue
            if c.draft_id == draft_id:
                if latest is None or c.created_at >= latest.created_at:
                    latest = c
        return latest

    def _normalize_novelty(self, text: str) -> tuple[str, bool]:
        if _NOVELTY_RE.search(text):
            return _NOVELTY_RE.sub("", text).strip(), True
        return text, False

    def _build_section_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "claims": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "grounding_type": {
                                "type": "string",
                                "enum": SectionArtifactType.values(),
                            },
                            "grounding_artifact_id": {"type": "string"},
                            "citation_id": {"type": "string"},
                            "conditions": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["text"],
                        "additionalProperties": False,
                    },
                },
                "citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "citation_id": {"type": "string"},
                            "paper_identity_id": {"type": "string"},
                            "evidence_item_id": {"type": "string"},
                            "page_locator": {"type": "string"},
                            "claim_context": {"type": "string"},
                        },
                        "required": ["citation_id", "paper_identity_id", "evidence_item_id"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["title", "body", "claims"],
            "additionalProperties": False,
        }

    async def _build_section_prompt(
        self,
        outline_id: str,
        outline: ManuscriptOutline,
        package_id: str,
        spec_: SectionSpec,
        verified_props: dict[str, Proposition],
        result_ids: set[str],
        critique_issues: list[ManuscriptCritiqueIssue] | None,
        prior_errors: list[str] | None = None,
    ) -> str:
        blocks: list[str] = []
        for gid in spec_.artifact_ids:
            try:
                env = await self._store.get(gid)
            except Exception:  # noqa: BLE001
                continue
            blocks.append(
                f"Artifact {env.artifact_id} ({env.artifact_type}): {self._summarize(env)}"
            )

        verified_lines = "\n".join(
            f"  [{pid}] {p.statement} | conditions: {'; '.join(p.conditions) or '-'}"
            for pid, p in verified_props.items()
            if pid in spec_.artifact_ids
        )
        crit_text = ""
        if critique_issues:
            lines = "\n".join(f"  - [{i.severity}] {i.description}" for i in critique_issues)
            crit_text = f"\n\nCRITIQUE FEEDBACK for this section (address every point):\n{lines}"
        return f"""Draft the '{spec_.title}' section of a research manuscript.

Outline: {outline_id}
Package: {package_id}
Gap: {outline.results_package_id}  Section id: {spec_.section_id.value}
Title of manuscript: {outline.title}

Relevant verified artifacts for this section:
{"\n".join(blocks) or "  (none)"}

Verified propositions available to this section (IDs authoritative; never cite
a failed proposition):
{verified_lines or "  (none)"}

Numerical result ids available: {", ".join(sorted(result_ids)[:10]) if result_ids else "(none)"}
{crit_text}

Rules:
- Write precise prose. Use [CITE:<citation_id>] placeholders in the body ONLY
  for literature claims, and ONLY when the citations array declares that
  citation id. [CITE:...] must NEVER contain an artifact id.
- If the section has no evidence items or paper identities in scope, do NOT
  use any [CITE:...] placeholder and leave citations empty.
- claims[]: one entry per substantive claim with grounding_type +
  grounding_artifact_id (from the verified artifacts above; verified
  propositions ONLY) and/or citation_id.
- For claims grounded in a verified proposition, conditions must include ALL
  of the proposition's conditions verbatim (paraphrase is allowed but the
  original condition text must appear).
- Literature claims (evidence/synthesis grounding) MUST include a citation_id.
- Never claim global novelty ('first study', 'no prior work').
- Return valid JSON only, no chain-of-thought.

{self._prior_errors_text(prior_errors)}
"""

    def _prior_errors_text(self, prior_errors: list[str] | None) -> str:
        if not prior_errors:
            return ""
        lines = "\n".join(f"  - {err}" for err in prior_errors)
        return (
            "Your previous attempt was REJECTED by deterministic validation:\n"
            f"{lines}\n"
            "Fix ALL occurrences and re-issue the full corrected response for "
            "this section."
        )

    def _summarize(self, env: Any) -> str:
        try:
            payload = env.payload
            if isinstance(payload, dict):
                keys = ["statement", "title", "description", "claim", "summary"]
                for k in keys:
                    if k in payload and isinstance(payload[k], str):
                        return payload[k][:300]
            return json.dumps(payload, ensure_ascii=False)[:300]
        except Exception:  # noqa: BLE001
            return ""


def _extract_json(text: str) -> str:
    """Recover a JSON object from model output (fences, prose, trailing text)."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    return text[start : end + 1]


class ManuscriptDrafterPlugin(Plugin):
    def __init__(self, drafter_role: str | None = None) -> None:
        self._drafter_role_override = drafter_role
        self._service: ManuscriptDrafterService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.manuscript_drafter",
            version="0.1.0",
            plugin_type="research",
            description="Manuscript outline + section-by-section evidence-grounded drafting (Phase 4B)",
            provides=["manuscript_drafter.default"],
            requires=["model_router.default", "artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        research_cfg: dict[str, Any] = {}
        if "research" in cfg and isinstance(cfg["research"], dict):
            research_cfg = (
                cfg["research"].get("manuscript", {})
                if isinstance(cfg["research"].get("manuscript"), dict)
                else {}
            )
        drafter_role = (
            self._drafter_role_override or research_cfg.get("drafter_role") or "reasoning"
        )
        max_llm_calls = int(research_cfg.get("max_llm_calls", 100))
        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        self._service = ManuscriptDrafterService(
            model_router=router,
            artifact_store=store,
            drafter_role=str(drafter_role),
            max_llm_calls=max_llm_calls,
        )
        ctx.register("manuscript_drafter.default", self._service)
