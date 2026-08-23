"""Phase 4A results critic — independent critique of a ResearchResultsPackage.

Deterministic checks (symbolic/numerical contradiction via violated robustness
checks, missing conditions, remaining novelty claims) are merged with a
qualitative critique from the `critic` role: overclaiming, unsupported novelty,
causal overstatement, weak link to the original gap, and weak IS contribution.
The critique is persisted separately; the package stays immutable.
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
from research_harness.research.schemas.numerical import RobustnessCheck
from research_harness.research.schemas.results import (
    ContributionClaim,
    ResearchFinding,
    ResearchImplication,
    ResearchResultsPackage,
    ResultsCritique,
    ResultsCritiqueCategory,
    ResultsCritiqueIssue,
    ResultsCritiqueVerdict,
)

logger = logging.getLogger(__name__)

_NOVELTY_PATTERNS = [
    r"\bfirst\s+(study|work|paper|analysis|investigation|time)\b",
    r"\bthe\s+first\s+to\b",
    r"\bwe\s+are\s+the\s+first\b",
    r"\bno\s+prior\s+(study|work|paper|research|analysis)\b",
    r"\bnever\s+been\s+(studied|examined|analyzed)\b",
]
_NOVELTY_RE = re.compile("|".join(_NOVELTY_PATTERNS), flags=re.IGNORECASE)


class _IssueItem(BaseModel):
    category: str
    description: str
    severity: str = "medium"
    location: str | None = None

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        if v not in ResultsCritiqueCategory.values():
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
        if v not in ResultsCritiqueVerdict.values():
            raise ValueError(f"invalid verdict {v!r}")
        return v

    model_config = {"extra": "forbid"}


class ResultsCriticService:
    def __init__(self, model_router: Any, artifact_store: Any, critic_role: str = "critic") -> None:
        self._router = model_router
        self._store = artifact_store
        self._critic_role = critic_role

    @property
    def service_id(self) -> str:
        return "research.results_critic"

    async def critique(self, package_id: str) -> str:
        """Critique a ResearchResultsPackage. Returns the ResultsCritique id."""
        existing = await self._store.list(artifact_type="results_critique")
        for env in existing:
            try:
                c = ResultsCritique.model_validate(env.payload)
                if c.package_id == package_id and c.model_role == self._critic_role:
                    return env.artifact_id
            except Exception:
                continue

        p_env = await self._store.get(package_id)
        package = p_env.parse_payload(ResearchResultsPackage)
        findings = [
            (await self._store.get(fid)).parse_payload(ResearchFinding)
            for fid in package.finding_ids
        ]
        contributions = [
            (await self._store.get(cid)).parse_payload(ContributionClaim)
            for cid in package.contribution_claim_ids
        ]
        implications = [
            (await self._store.get(iid)).parse_payload(ResearchImplication)
            for iid in package.implication_ids
        ]

        deterministic = await self._deterministic_checks(package, findings, contributions)

        prompt = self._build_prompt(package, findings, contributions, implications, deterministic)
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You are an independent, skeptical critic of an assembled "
                        "research results package. Return valid JSON matching the "
                        "schema. Never include chain-of-thought."
                    ),
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self._build_schema(),
            temperature=0.0,
            metadata={"package_id": package_id},
        )
        try:
            response = await self._router.complete(self._critic_role, request)
            data = json.loads(response.message.content or "")
            parsed = _CritiqueResponse.model_validate(data)
        except Exception as e:
            raise ValueError(f"results critique call failed: {e}") from e

        issues = deterministic + [
            ResultsCritiqueIssue(
                category=ResultsCritiqueCategory(i.category),
                description=i.description,
                severity=i.severity,
                location=i.location,
            )
            for i in parsed.issues
        ]
        critique = ResultsCritique(
            package_id=package_id,
            issues=issues,
            overall_assessment=parsed.overall_assessment,
            verdict=ResultsCritiqueVerdict(parsed.verdict),
            recommendations=list(parsed.recommendations),
            model_role=self._critic_role,
        )
        c_env = ArtifactEnvelope.create(
            payload=critique,
            artifact_type="results_critique",
            producer=f"research.results_critic:{self._critic_role}",
        )
        await self._store.put(c_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=package_id,
                target_artifact_id=c_env.artifact_id,
                producer="research.results_critic",
            )
        )
        return c_env.artifact_id

    # ------------------------------------------------------------------
    # Deterministic checks (no LLM)
    # ------------------------------------------------------------------

    async def _deterministic_checks(
        self,
        package: ResearchResultsPackage,
        findings: list[ResearchFinding],
        contributions: list[ContributionClaim],
    ) -> list[ResultsCritiqueIssue]:
        issues: list[ResultsCritiqueIssue] = []
        robustness = [
            (await self._store.get(cid)).parse_payload(RobustnessCheck)
            for cid in package.metadata.get("robustness_ids", [])
        ]
        violated_props = {
            r.proposition_id
            for r in robustness
            if r.proposition_id and r.outcome.value == "violated"
        }
        for fid, finding in zip(package.finding_ids, findings, strict=True):
            for pid in finding.supporting_proposition_ids:
                if pid in violated_props:
                    issues.append(
                        ResultsCritiqueIssue(
                            category=ResultsCritiqueCategory.symbolic_numerical_contradiction,
                            description=(
                                f"finding {fid} cites proposition {pid} whose numerical "
                                "robustness check outcome is 'violated'"
                            ),
                            severity="high",
                            location=f"finding {fid}",
                        )
                    )
        for cid, claim in zip(package.contribution_claim_ids, contributions, strict=True):
            if claim.gap_id != package.gap_id:
                issues.append(
                    ResultsCritiqueIssue(
                        category=ResultsCritiqueCategory.weak_gap_link,
                        description=f"contribution {cid} references gap {claim.gap_id}, "
                        f"not the package gap {package.gap_id}",
                        severity="medium",
                        location=f"contribution {cid}",
                    )
                )
            if claim.novelty_claim and _NOVELTY_RE.search(claim.novelty_claim):
                issues.append(
                    ResultsCritiqueIssue(
                        category=ResultsCritiqueCategory.unsupported_novelty_claim,
                        description=f"contribution {cid} still contains a sweeping novelty claim",
                        severity="high",
                        location=f"contribution {cid}",
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
                "verdict": {"type": "string", "enum": ResultsCritiqueVerdict.values()},
                "recommendations": {"type": "array", "items": {"type": "string"}},
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": ResultsCritiqueCategory.values(),
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
        package: ResearchResultsPackage,
        findings: list[ResearchFinding],
        contributions: list[ContributionClaim],
        implications: list[ResearchImplication],
        deterministic: list[ResultsCritiqueIssue],
    ) -> str:
        f_lines = "\n".join(
            f"  [{fid}] ({f.finding_type.value}) {f.statement} | conditions: "
            f"{'; '.join(f.conditions) or '-'} | props: {f.supporting_proposition_ids}"
            for fid, f in zip(package.finding_ids, findings, strict=True)
        )
        c_lines = "\n".join(
            f"  [{cid}] ({c.contribution_type.value}) {c.claim} | findings: {c.finding_ids}"
            + (f" | novelty: {c.novelty_claim}" if c.novelty_claim else "")
            for cid, c in zip(package.contribution_claim_ids, contributions, strict=True)
        )
        i_lines = "\n".join(
            f"  [{iid}] ({i.implication_kind.value}/{i.claim_type.value}) {i.text[:150]}"
            for iid, i in zip(package.implication_ids, implications, strict=True)
        )
        det_lines = (
            "\n".join(
                f"  - [{i.category.value}/{i.severity}] {i.description}" for i in deterministic
            )
            or "  (none)"
        )
        return f"""Critique the following assembled research results package.

Gap: {package.gap_id}
Mechanism: {package.selected_mechanism_id}
Model: {package.model_id}  Equilibrium: {package.equilibrium_analysis_id}
Limitations: {"; ".join(package.limitations) or "-"}

Findings:
{f_lines}

Contribution claims:
{c_lines}

Implications:
{i_lines}

Deterministic pre-checks already found:
{det_lines}

Critique dimensions:
- overclaiming (claims beyond verified results)
- unsupported novelty claims (global-novelty phrasing)
- missing equilibrium/proposition conditions
- contradiction between symbolic and numerical results
- causal overstatement
- weak link to the original research gap
- weak IS contribution

Return: overall_assessment, verdict (approve|revise|reject), recommendations,
and issues with category (one of {", ".join(ResultsCritiqueCategory.values())}),
severity, location. Valid JSON only, no chain-of-thought.
"""


class ResultsCriticPlugin(Plugin):
    def __init__(self, critic_role: str | None = None) -> None:
        self._critic_role_override = critic_role
        self._service: ResultsCriticService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.results_critic",
            version="0.1.0",
            plugin_type="research",
            description="Results package critique: overclaiming, novelty, conditions, conflicts (Phase 4A)",
            provides=["results_critic.default"],
            requires=["model_router.default", "artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        research_cfg: dict[str, Any] = {}
        if "research" in cfg and isinstance(cfg["research"], dict):
            research_cfg = (
                cfg["research"].get("results", {})
                if isinstance(cfg["research"].get("results"), dict)
                else {}
            )
        critic_role = self._critic_role_override or research_cfg.get("critic_role") or "critic"
        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        self._service = ResultsCriticService(
            model_router=router, artifact_store=store, critic_role=str(critic_role)
        )
        ctx.register("results_critic.default", self._service)
