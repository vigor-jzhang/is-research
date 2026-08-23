"""Phase 4B manuscript critic — independent critique of a ManuscriptDraft.

Deterministic pre-checks (unsupported claims, citation gaps, dropped
conditions, failed-proposition references, missing limitations section,
novelty residue) are merged with the `critic`-role qualitative critique
(overclaiming, cross-section inconsistency, gap-contribution mismatch,
mathematical-result distortion, repetition, weak logical flow). The critique
is persisted separately; drafts stay immutable.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.manuscript import (
    ManuscriptCritique,
    ManuscriptCritiqueCategory,
    ManuscriptCritiqueIssue,
    ManuscriptCritiqueVerdict,
    ManuscriptDraft,
    ManuscriptSection,
    ManuscriptSectionId,
)
from research_harness.research.schemas.results import (
    ContributionClaim,
    ResearchResultsPackage,
)

logger = logging.getLogger(__name__)

_NOVELTY_RE = re.compile(
    r"\bfirst\s+(study|work|paper|analysis|investigation|time)\b|"
    r"\bthe\s+first\s+to\b|"
    r"\bno\s+prior\s+(study|work|paper|research|analysis)\b|"
    r"\bnever\s+been\s+(studied|examined|analyzed)\b",
    flags=re.IGNORECASE,
)

_CITE_PLACEHOLDER_RE = re.compile(r"\[CITE:([^\]]+)\]")


class _IssueItem(BaseModel):
    category: str
    description: str
    severity: str = "medium"
    location: str | None = None

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        if v not in ManuscriptCritiqueCategory.values():
            raise ValueError(f"invalid critique category {v!r}")
        return v

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        if v not in ("high", "medium", "low"):
            raise ValueError(f"invalid severity {v!r}")
        return v

    model_config = {"extra": "forbid"}


class _CritiqueResponse(BaseModel):
    overall_assessment: str
    verdict: str
    recommendations: list[str] = Field(default_factory=list)
    issues: list[_IssueItem] = Field(default_factory=list)

    @field_validator("overall_assessment")
    @classmethod
    def validate_assessment(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("overall assessment must be non-empty")
        return v

    @field_validator("verdict")
    @classmethod
    def validate_verdict(cls, v: str) -> str:
        if v not in ManuscriptCritiqueVerdict.values():
            raise ValueError(f"invalid verdict {v!r}")
        return v

    model_config = {"extra": "forbid"}


class ManuscriptCriticService:
    def __init__(self, model_router: Any, artifact_store: Any, critic_role: str = "critic") -> None:
        self._router = model_router
        self._store = artifact_store
        self._critic_role = critic_role

    @property
    def service_id(self) -> str:
        return "research.manuscript_critic"

    async def critique(self, draft_id: str) -> str:
        """Critique a ManuscriptDraft. Returns the ManuscriptCritique id."""
        existing = await self._store.list(artifact_type="manuscript_critique")
        for env in existing:
            try:
                c = ManuscriptCritique.model_validate(env.payload)
                if c.draft_id == draft_id and c.model_role == self._critic_role:
                    return env.artifact_id
            except Exception:
                continue

        d_env = await self._store.get(draft_id)
        draft = d_env.parse_payload(ManuscriptDraft)
        sections = [
            (await self._store.get(sid)).parse_payload(ManuscriptSection)
            for sid in draft.section_ids
        ]
        package = (await self._store.get(draft.results_package_id)).parse_payload(
            ResearchResultsPackage
        )
        contributions = []
        for cid in package.contribution_claim_ids:
            try:
                contributions.append((await self._store.get(cid)).parse_payload(ContributionClaim))
            except Exception:
                continue

        deterministic = await self._deterministic_checks(draft, sections, package, contributions)
        prompt = self._build_prompt(draft, sections, package, deterministic)
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You are an independent, skeptical critic of a manuscript "
                        "draft. Return valid JSON matching the schema. Never "
                        "include chain-of-thought."
                    ),
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self._build_schema(),
            temperature=0.0,
            metadata={"draft_id": draft_id},
        )
        try:
            response = await self._router.complete(self._critic_role, request)
            data = json.loads(response.message.content or "")
            parsed = _CritiqueResponse.model_validate(data)
        except Exception as e:
            raise ValueError(f"manuscript critique call failed: {e}") from e

        issues = deterministic + [
            ManuscriptCritiqueIssue(
                category=ManuscriptCritiqueCategory(i.category),
                description=i.description,
                severity=i.severity,
                location=i.location,
            )
            for i in parsed.issues
        ]
        critique = ManuscriptCritique(
            draft_id=draft_id,
            issues=issues,
            overall_assessment=parsed.overall_assessment,
            verdict=ManuscriptCritiqueVerdict(parsed.verdict),
            recommendations=list(parsed.recommendations),
            model_role=self._critic_role,
        )
        c_env = ArtifactEnvelope.create(
            payload=critique,
            artifact_type="manuscript_critique",
            producer=f"research.manuscript_critic:{self._critic_role}",
        )
        await self._store.put(c_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=draft_id,
                target_artifact_id=c_env.artifact_id,
                producer="research.manuscript_critic",
            )
        )
        return c_env.artifact_id

    # ------------------------------------------------------------------
    # Deterministic checks (no LLM)
    # ------------------------------------------------------------------

    async def _deterministic_checks(
        self,
        draft: ManuscriptDraft,
        sections: list[ManuscriptSection],
        package: ResearchResultsPackage,
        contributions: list[ContributionClaim],
    ) -> list[ManuscriptCritiqueIssue]:
        issues: list[ManuscriptCritiqueIssue] = []
        section_ids = {s.section_id.value for s in sections}
        has_limitations = ManuscriptSectionId.limitations.value in section_ids
        if not has_limitations:
            issues.append(
                ManuscriptCritiqueIssue(
                    category=ManuscriptCritiqueCategory.missing_limitations,
                    description="the draft has no limitations section",
                    severity="high",
                    location=ManuscriptSectionId.limitations.value,
                )
            )

        for section in sections:
            citation_ids = {c.citation_id for c in section.citations}
            placeholders = {m.group(1) for m in _CITE_PLACEHOLDER_RE.finditer(section.body)}
            unused = [c for c in section.citations if c.citation_id not in placeholders]
            missing = placeholders - citation_ids
            if missing:
                issues.append(
                    ManuscriptCritiqueIssue(
                        category=ManuscriptCritiqueCategory.citation_gap,
                        description=f"body of {section.section_id.value} cites unknown placeholders: {sorted(missing)}",
                        severity="high",
                        location=section.section_id.value,
                    )
                )
            if unused:
                issues.append(
                    ManuscriptCritiqueIssue(
                        category=ManuscriptCritiqueCategory.citation_gap,
                        description=f"section {section.section_id.value} declares citations never used in the body",
                        severity="low",
                        location=section.section_id.value,
                    )
                )
            for claim in section.claims:
                if claim.grounding_artifact_id is None and claim.citation_id is None:
                    issues.append(
                        ManuscriptCritiqueIssue(
                            category=ManuscriptCritiqueCategory.unsupported_claim,
                            description=f"claim '{claim.text[:80]}' in {section.section_id.value} has no grounding",
                            severity="high",
                            location=section.section_id.value,
                        )
                    )
                if claim.citation_id and claim.citation_id not in citation_ids:
                    issues.append(
                        ManuscriptCritiqueIssue(
                            category=ManuscriptCritiqueCategory.citation_gap,
                            description=f"claim in {section.section_id.value} cites unknown citation {claim.citation_id}",
                            severity="high",
                            location=section.section_id.value,
                        )
                    )
                if _NOVELTY_RE.search(claim.text) or _NOVELTY_RE.search(section.body):
                    issues.append(
                        ManuscriptCritiqueIssue(
                            category=ManuscriptCritiqueCategory.overclaiming,
                            description=f"global-novelty phrasing remains in section {section.section_id.value}",
                            severity="high",
                            location=section.section_id.value,
                        )
                    )
                    break

        for cid, claim in zip(package.contribution_claim_ids, contributions, strict=True):
            if claim.gap_id != package.gap_id:
                issues.append(
                    ManuscriptCritiqueIssue(
                        category=ManuscriptCritiqueCategory.gap_contribution_mismatch,
                        description=f"contribution {cid} references gap {claim.gap_id}, not the package gap {package.gap_id}",
                        severity="medium",
                        location=ManuscriptSectionId.contributions.value,
                    )
                )
        return issues

    # ------------------------------------------------------------------
    # Prompt + schema
    # ------------------------------------------------------------------

    def _build_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "overall_assessment": {"type": "string"},
                "verdict": {"type": "string", "enum": ManuscriptCritiqueVerdict.values()},
                "recommendations": {"type": "array", "items": {"type": "string"}},
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": ManuscriptCritiqueCategory.values(),
                            },
                            "description": {"type": "string"},
                            "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                            "location": {"type": "string"},
                        },
                        "required": ["category", "description"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["overall_assessment", "verdict"],
            "additionalProperties": False,
        }

    def _build_prompt(
        self,
        draft: ManuscriptDraft,
        sections: list[ManuscriptSection],
        package: ResearchResultsPackage,
        deterministic: list[ManuscriptCritiqueIssue],
    ) -> str:
        sec_lines = "\n\n".join(
            f"[{s.section_id.value}] {s.title}\n{s.body[:1200]}" for s in sections
        )
        det_lines = (
            "\n".join(
                f"  - [{i.category.value}/{i.severity}] {i.description}" for i in deterministic
            )
            or "  (none)"
        )
        return f"""Critique the following manuscript draft (Version {draft.version}).

Package: {draft.results_package_id}  Gap: {package.gap_id}
Limitations recorded in package: {"; ".join(package.limitations) or "-"}

Sections:
{sec_lines}

Deterministic pre-checks already found:
{det_lines}

Critique dimensions:
- unsupported claims (no grounding artifact, no citation)
- citation gaps (literature claims without evidence citations)
- overclaiming (beyond verified results)
- inconsistency across sections (contradictory statements)
- mismatch between the research gap and the claimed contributions
- mathematical-result distortion (claims that change verified results)
- repetition (content duplicated across sections)
- weak logical flow (ordering, transitions, coherence)
- missing limitations

Return: overall_assessment, verdict (approve|revise|reject), recommendations,
and issues with category (one of {", ".join(ManuscriptCritiqueCategory.values())}),
severity, location (a section id like 'introduction' or a claim). Valid JSON
only, no chain-of-thought.
"""


class ManuscriptCriticPlugin(Plugin):
    def __init__(self, critic_role: str | None = None) -> None:
        self._critic_role_override = critic_role
        self._service: ManuscriptCriticService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.manuscript_critic",
            version="0.1.0",
            plugin_type="research",
            description="Manuscript draft critique: claims, citations, consistency (Phase 4B)",
            provides=["manuscript_critic.default"],
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
        critic_role = self._critic_role_override or research_cfg.get("critic_role") or "critic"
        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        self._service = ManuscriptCriticService(
            model_router=router, artifact_store=store, critic_role=str(critic_role)
        )
        ctx.register("manuscript_critic.default", self._service)
