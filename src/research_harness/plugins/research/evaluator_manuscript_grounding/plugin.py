"""evaluator.manuscript_grounding — deterministic manuscript-grounding
evaluator (Phase 6G).

Evaluates produced Phase 4B artifacts (outline, sections, claims,
citations, drafts, critiques, revisions) against known-answer references.
Ground truth is recomputed from the produced artifacts: claim grounding is
re-checked against the produced verified propositions/evidence/results,
citations are re-checked against produced evidence/paper identities, and
novelty phrasing is re-scanned.

Critical deterministic failures:
- hallucinated artifact/citation references
- unsupported literature claim
- failed proposition presented as verified
- missing required proposition conditions
- unsupported novelty claim
- revision that fails to repair a required deterministic issue
"""

from __future__ import annotations

import re
from typing import Any

from research_harness.contracts.evaluator import EvaluatorContext, envelope_payload_dict
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.schemas.evaluation import (
    EvaluatorCategory,
    EvaluatorResult,
    EvaluatorStatus,
)

_NOVELTY_PATTERNS = [
    r"\bfirst\s+(study|work|paper|analysis|investigation|time)\b",
    r"\bthe\s+first\s+to\b",
    r"\bwe\s+are\s+the\s+first\b",
    r"\bno\s+prior\s+(study|work|paper|research|analysis)\b",
    r"\bno\s+(other\s+)?(study|work|paper)\s+has\b",
    r"\bnever\s+been\s+(studied|examined|analyzed)\b",
]
_NOVELTY_RE = re.compile("|".join(_NOVELTY_PATTERNS), flags=re.IGNORECASE)

_CITE_PLACEHOLDER_RE = re.compile(r"\[CITE:([A-Za-z0-9_\-]+)\]")

_LITERATURE_GROUNDING_TYPES = {"evidence_item", "synthesis_statement"}


class ManuscriptGroundingEvaluator:
    evaluator_id = "evaluator.manuscript_grounding"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        section_envs = [
            e for e in ctx.produced_artifacts if e.artifact_type == "manuscript_section"
        ]
        if not section_envs:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no manuscript_section produced for the case",
            )
        sections = sorted(
            ((e.artifact_id, envelope_payload_dict(e)) for e in section_envs),
            key=lambda item: item[1].get("created_at") or "",
        )
        critiques = [
            envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "manuscript_critique"
        ]
        drafts = [
            (e.artifact_id, envelope_payload_dict(e))
            for e in ctx.produced_artifacts
            if e.artifact_type == "manuscript_draft"
        ]

        produced_ids = {e.artifact_id for e in ctx.produced_artifacts}
        verification_status_by_prop: dict[str, str] = {}
        for env in ctx.produced_artifacts:
            if env.artifact_type != "proposition_verification":
                continue
            verification_status_by_prop[envelope_payload_dict(env).get("proposition_id", "")] = (
                envelope_payload_dict(env).get("status", "")
            )
        verified_prop_ids = {
            e.artifact_id
            for e in ctx.produced_artifacts
            if e.artifact_type == "proposition"
            and verification_status_by_prop.get(e.artifact_id)
            in ("verified", "conditionally_verified")
        }
        failed_prop_ids = {
            e.artifact_id
            for e in ctx.produced_artifacts
            if e.artifact_type == "proposition"
            and verification_status_by_prop.get(e.artifact_id) == "failed"
        }
        result_ids = {
            e.artifact_id for e in ctx.produced_artifacts if e.artifact_type == "numerical_result"
        }
        evidence_ids = {
            e.artifact_id for e in ctx.produced_artifacts if e.artifact_type == "evidence_item"
        }
        paper_ids = {
            e.artifact_id for e in ctx.produced_artifacts if e.artifact_type == "paper_identity"
        }
        props_by_id = {
            e.artifact_id: envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "proposition"
        }

        outline_envs = [
            e for e in ctx.produced_artifacts if e.artifact_type == "manuscript_outline"
        ]
        allowed_by_section: dict[str, set[str]] = {}
        if outline_envs:
            outline_env = max(outline_envs, key=lambda e: e.created_at)
            outline = envelope_payload_dict(outline_env)
            for spec in outline.get("section_specs") or []:
                allowed_by_section[spec.get("section_id")] = set(
                    spec.get("allowed_artifact_types") or []
                )

        reference = ctx.case.reference or {}
        expected_critique_categories = set(reference.get("expected_critique_categories") or [])
        expected_sections = set(reference.get("expected_sections") or [])
        expected_revision = bool(reference.get("expected_revision") or False)

        failures_detail: list[str] = []

        # ---- per-section checks ------------------------------------------
        claims_grounded = 0
        claims_total = 0
        literature_ok = 0
        literature_total = 0
        conditions_ok = 0
        conditions_total = 0
        citations_valid = 0
        citations_total = 0
        sections_consistent = 0
        novelty_sections_ok = 0
        novelty_sections_total = 0
        unsupported_claims = 0

        math_claims_ok = 0
        math_claims_total = 0
        for _env_id, section in sections:
            section_id = str(section.get("section_id") or "")
            allowed_types = allowed_by_section.get(section_id, set())
            claims = list(section.get("claims") or [])
            citations = list(section.get("citations") or [])
            body = str(section.get("body") or "")

            section_consistent = True
            novelty_found = _NOVELTY_RE.search(body) or any(
                _NOVELTY_RE.search(str(c.get("text") or "")) for c in claims
            )
            if novelty_found:
                failures_detail.append(
                    f"NOVELTY OVERCLAIM in section {section_id}: sweeping novelty "
                    "phrasing persisted"
                )
            else:
                novelty_sections_ok += 1
            novelty_sections_total += 1

            citation_ids = {c.get("citation_id") for c in citations}
            for claim in claims:
                claims_total += 1
                grounding_id = claim.get("grounding_artifact_id")
                grounding_type = claim.get("grounding_type")
                citation_id = claim.get("citation_id")

                grounded = False
                if grounding_id is not None:
                    if (
                        grounding_type is not None
                        and allowed_types
                        and grounding_type not in allowed_types
                    ):
                        failures_detail.append(
                            f"CLAIM grounds in {grounding_type}, not allowed in {section_id}"
                        )
                        section_consistent = False
                    if grounding_id not in produced_ids:
                        failures_detail.append(f"CLAIM references unknown artifact {grounding_id}")
                    elif grounding_type == "verified_proposition":
                        if grounding_id in failed_prop_ids:
                            failures_detail.append(
                                f"FAILED PROPOSITION presented as verified: {grounding_id}"
                            )
                            section_consistent = False
                        elif grounding_id in verified_prop_ids:
                            grounded = True
                        else:
                            failures_detail.append(
                                f"CLAIM grounds in non-verified proposition {grounding_id}"
                            )
                    elif grounding_type in ("evidence_item", "research_gap", "contribution_claim"):
                        if grounding_id in evidence_ids or grounding_id in produced_ids:
                            grounded = True
                    elif grounding_type == "numerical_result":
                        if grounding_id in result_ids:
                            grounded = True
                        else:
                            failures_detail.append(
                                f"CLAIM references numerical result {grounding_id} outside the experiment"
                            )
                    else:
                        grounded = True
                if citation_id is not None:
                    if citation_id not in citation_ids:
                        failures_detail.append(f"CLAIM cites unknown citation {citation_id}")
                    elif grounding_id is None:
                        grounded = True
                if grounding_id is None and citation_id is None:
                    failures_detail.append(
                        f"UNSUPPORTED LITERATURE CLAIM: {str(claim.get('text'))[:80]}"
                    )
                if grounding_type in ("verified_proposition", "numerical_result"):
                    math_claims_total += 1
                    if grounding_id is not None and (
                        (
                            grounding_type == "verified_proposition"
                            and grounding_id in verified_prop_ids
                        )
                        or (grounding_type == "numerical_result" and grounding_id in result_ids)
                    ):
                        math_claims_ok += 1
                if grounded:
                    claims_grounded += 1
                else:
                    unsupported_claims += 1

                # literature claims must carry a valid citation
                if grounding_type in _LITERATURE_GROUNDING_TYPES or (
                    grounding_id is None and citation_id is not None
                ):
                    literature_total += 1
                    if (
                        (
                            citation_id is not None
                            and citation_id in citation_ids
                            and grounding_id is not None
                        )
                        or grounding_id is None
                        and citation_id is not None
                    ):
                        literature_ok += 1

                # condition preservation for proposition-grounded claims
                if grounding_type == "verified_proposition" and grounding_id in props_by_id:
                    conditions_total += 1
                    required = list(props_by_id[grounding_id].get("conditions") or [])
                    missing = [
                        c
                        for c in required
                        if not any(c in cond for cond in claim.get("conditions") or [])
                    ]
                    if missing:
                        failures_detail.append(f"CLAIM drops proposition conditions: {missing}")
                    else:
                        conditions_ok += 1

            for citation in citations:
                citations_total += 1
                if (
                    citation.get("evidence_item_id") in evidence_ids
                    and citation.get("paper_identity_id") in paper_ids
                ):
                    citations_valid += 1
                else:
                    failures_detail.append(
                        f"HALLUCINATED CITATION {citation.get('citation_id')}: "
                        f"evidence {citation.get('evidence_item_id')} / paper "
                        f"{citation.get('paper_identity_id')} not produced"
                    )

            # placeholders must resolve to declared citations
            placeholders = {m.group(1) for m in _CITE_PLACEHOLDER_RE.finditer(body)}
            if placeholders - citation_ids:
                failures_detail.append(
                    f"BODY cites unknown placeholders in {section_id}: "
                    f"{sorted(placeholders - citation_ids)}"
                )

            if section_consistent:
                sections_consistent += 1
            else:
                failures_detail.append(f"INCONSISTENT SECTION {section_id}")

        # ---- critique coverage -------------------------------------------
        critique_categories = {
            issue.get("category")
            for critique in critiques
            for issue in critique.get("issues") or []
        }
        missing_categories = expected_critique_categories - critique_categories
        for category in sorted(missing_categories):
            failures_detail.append(f"CRITIQUE MISSING expected issue {category}")

        # ---- sections drafted --------------------------------------------
        drafted_sections = {section.get("section_id") for _, section in sections}
        if expected_sections and not expected_sections.issubset(drafted_sections):
            failures_detail.append(
                f"MISSING SECTIONS: expected {sorted(expected_sections - drafted_sections)}"
            )

        # ---- revision ----------------------------------------------------
        revision_ok = 0
        revision_total = 0
        if expected_revision:
            v1 = next((d for _, d in drafts if not d.get("supersedes")), None)
            v2 = next((d for _, d in drafts if d.get("supersedes")), None)
            if v1 is None or v2 is None:
                failures_detail.append("REVISION FAILED: no superseding draft found")
            else:
                v1_id = next((did for did, d in drafts if d is v1), None)
                v2_id = next((did for did, d in drafts if d is v2), None)
                if v2_id is None or v2.get("supersedes") != v1_id:
                    failures_detail.append(f"REVISION FAILED: {v2_id} does not supersede {v1_id}")
                flagged_locations = {
                    issue.get("location")
                    for critique in critiques
                    for issue in critique.get("issues") or []
                    if issue.get("location")
                }
                v2_section_ids = set((v2 or {}).get("section_ids") or [])
                v1_section_ids = set((v1 or {}).get("section_ids") or [])
                issues_by_location: dict[str, list[str]] = {}
                for critique in critiques:
                    for issue in critique.get("issues") or []:
                        if issue.get("location"):
                            issues_by_location.setdefault(str(issue.get("location")), []).append(
                                str(issue.get("category"))
                            )
                for location in flagged_locations:
                    revision_total += 1
                    redrafted = [
                        section
                        for sid, section in sections
                        if sid in v2_section_ids and sid not in v1_section_ids
                    ]
                    new_section = next(
                        (s for s in redrafted if s.get("section_id") == location), None
                    )
                    repaired = new_section is not None
                    if repaired and "citation_gap" in issues_by_location.get(location, []):
                        # a declared citation that is never used must be gone
                        citation_ids = {
                            c.get("citation_id") for c in (new_section or {}).get("citations") or []
                        }
                        placeholders = {
                            m.group(1)
                            for m in _CITE_PLACEHOLDER_RE.finditer(
                                str((new_section or {}).get("body") or "")
                            )
                        }
                        if citation_ids - placeholders:
                            repaired = False
                    if not repaired:
                        failures_detail.append(f"REVISION FAILED TO REPAIR section {location}")
                    else:
                        revision_ok += 1
                # un-flagged sections must be reused by id
                reused = v1_section_ids & v2_section_ids
                if v1_section_ids and not reused and flagged_locations:
                    failures_detail.append("REVISION did not reuse any un-flagged section")

        status = EvaluatorStatus.failed if failures_detail else EvaluatorStatus.passed
        section_total = len(sections)

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "manuscript",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "claim_grounding_accuracy": _metric(
                "claim_grounding_accuracy",
                float(claims_grounded),
                claims_total,
                "rate",
                "claims grounded in produced verified artifacts or citations",
            ),
            "literature_citation_coverage": _metric(
                "literature_citation_coverage",
                float(literature_ok),
                literature_total,
                "rate",
                "literature claims carrying valid citations",
            ),
            "mathematical_claim_accuracy": _metric(
                "mathematical_claim_accuracy",
                float(math_claims_ok),
                math_claims_total,
                "rate",
                "claims grounded in verified propositions and experiment numerical results",
            ),
            "condition_preservation_accuracy": _metric(
                "condition_preservation_accuracy",
                float(conditions_ok),
                conditions_total,
                "rate",
                "claims preserving all conditions of cited propositions",
            ),
            "unsupported_claim_rate": _metric(
                "unsupported_claim_rate",
                float(unsupported_claims),
                claims_total,
                "rate",
                "claims without valid grounding or citation",
            ),
            "citation_reference_accuracy": _metric(
                "citation_reference_accuracy",
                float(citations_valid),
                citations_total,
                "rate",
                "citations referencing produced evidence and paper identities",
            ),
            "novelty_claim_accuracy": _metric(
                "novelty_claim_accuracy",
                float(novelty_sections_ok),
                novelty_sections_total,
                "rate",
                "sections free of sweeping novelty phrasing",
            ),
            "section_consistency_accuracy": _metric(
                "section_consistency_accuracy",
                float(sections_consistent),
                section_total,
                "rate",
                "sections with no failed-proposition or hallucinated grounding",
            ),
            "critique_issue_recall": _metric(
                "critique_issue_recall",
                float(len(expected_critique_categories - missing_categories)),
                len(expected_critique_categories),
                "rate",
                "expected deterministic critique issues surfaced",
            ),
            "revision_success_rate": _metric(
                "revision_success_rate",
                float(revision_ok),
                revision_total,
                "rate",
                "flagged sections re-drafted and repaired by the revision",
            ),
        }

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=(claims_grounded / claims_total) if claims_total else None,
            value={
                "sections": [
                    {
                        "id": sid,
                        "section_id": s.get("section_id"),
                        "claims": len(s.get("claims") or []),
                        "citations": len(s.get("citations") or []),
                    }
                    for sid, s in sections
                ],
                "critique_categories": sorted(critique_categories),
                "draft_versions": [
                    {"id": did, "version": d.get("version"), "supersedes": d.get("supersedes")}
                    for did, d in drafts
                ],
                "metrics": metrics,
                "dimension_scores": {
                    k: v
                    for k, v in {
                        "claim_grounding_accuracy": (
                            claims_grounded / claims_total if claims_total else None
                        ),
                        "literature_citation_coverage": (
                            literature_ok / literature_total if literature_total else None
                        ),
                        "mathematical_claim_accuracy": (
                            math_claims_ok / math_claims_total if math_claims_total else None
                        ),
                        "condition_preservation_accuracy": (
                            conditions_ok / conditions_total if conditions_total else None
                        ),
                        "unsupported_claim_rate": (
                            unsupported_claims / claims_total if claims_total else None
                        ),
                        "citation_reference_accuracy": (
                            citations_valid / citations_total if citations_total else None
                        ),
                        "novelty_claim_accuracy": (
                            novelty_sections_ok / novelty_sections_total
                            if novelty_sections_total
                            else None
                        ),
                        "section_consistency_accuracy": (
                            sections_consistent / section_total if section_total else None
                        ),
                        "critique_issue_recall": (
                            (len(expected_critique_categories - missing_categories))
                            / len(expected_critique_categories)
                            if expected_critique_categories
                            else None
                        ),
                        "revision_success_rate": (
                            revision_ok / revision_total if revision_total else None
                        ),
                    }.items()
                    if v is not None
                },
            },
            status=status,
            explanation="; ".join(failures_detail)
            if failures_detail
            else "all manuscript-grounding checks matched",
            evidence_artifact_ids=[e.artifact_id for e in section_envs],
        )


class ManuscriptGroundingEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.manuscript_grounding",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic manuscript-grounding evaluator (Phase 6G)",
            provides=["evaluator.manuscript_grounding"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.manuscript_grounding", ManuscriptGroundingEvaluator())
