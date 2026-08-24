"""evaluator.results_grounding — deterministic results-assembly evaluator
(Phase 6G).

Evaluates produced Phase 4A artifacts (ResearchResultsPackage, findings,
contribution claims, implications, critique) against known-answer
references. Ground truth is recomputed from the produced artifacts: support
ids are re-checked against the produced verified propositions/statics/
results, required conditions are re-derived from the cited supports, and
novelty phrasing is re-scanned with the same deterministic patterns.

Critical deterministic failures:
- failed proposition used as valid support
- unsupported finding persisted
- required mathematical conditions dropped
- symbolic/numerical contradiction hidden
- global novelty claim persisted as fact
- contribution without valid gap/finding support
- unsupported implication persisted
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

_GROUNDED_IMPLICATION_TYPES = {
    "mathematically_established",
    "interpretation",
    "managerial_implication",
    "policy",
}


class ResultsGroundingEvaluator:
    evaluator_id = "evaluator.results_grounding"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        package_envs = [e for e in ctx.produced_artifacts if e.artifact_type == "results_package"]
        if not package_envs:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no results_package produced for the case",
            )
        package_env = max(package_envs, key=lambda e: e.created_at)
        package = envelope_payload_dict(package_env)
        package_id = package_env.artifact_id

        produced: dict[str, dict[str, Any]] = {}
        for env in ctx.produced_artifacts:
            produced[env.artifact_id] = envelope_payload_dict(env)

        findings = [produced.get(fid, {}) for fid in package.get("finding_ids") or []]
        contributions = [
            produced.get(cid, {}) for cid in package.get("contribution_claim_ids") or []
        ]
        implications = [produced.get(iid, {}) for iid in package.get("implication_ids") or []]
        critiques = [
            envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "results_critique"
        ]

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
        static_ids = {
            e.artifact_id for e in ctx.produced_artifacts if e.artifact_type == "comparative_static"
        }
        result_ids = {
            e.artifact_id for e in ctx.produced_artifacts if e.artifact_type == "numerical_result"
        }

        reference = ctx.case.reference or {}
        expected_critique_categories = set(reference.get("expected_critique_categories") or [])
        expected_novelty_normalized = int(reference.get("expected_novelty_normalized") or 0)
        expected_unsupported = int(reference.get("expected_unsupported") or 0)

        failures_detail: list[str] = []

        # ---- findings ----------------------------------------------------
        grounded = 0
        conditions_preserved = 0
        prop_support_ok = 0
        prop_support_total = 0
        num_support_ok = 0
        num_support_total = 0
        unsupported_claims = 0
        for finding in findings:
            support_ids = (
                list(finding.get("supporting_proposition_ids") or [])
                + list(finding.get("supporting_comparative_static_ids") or [])
                + list(finding.get("supporting_numerical_result_ids") or [])
            )
            valid = bool(support_ids)
            for pid in finding.get("supporting_proposition_ids") or []:
                if pid in failed_prop_ids:
                    failures_detail.append(
                        f"FINDING uses failed proposition {pid} as valid support"
                    )
                    valid = False
                elif pid not in verified_prop_ids:
                    failures_detail.append(f"FINDING cites unknown proposition {pid}")
                    valid = False
            for sid in finding.get("supporting_comparative_static_ids") or []:
                if sid not in static_ids:
                    failures_detail.append(f"FINDING cites unknown comparative static {sid}")
                    valid = False
            for rid in finding.get("supporting_numerical_result_ids") or []:
                if rid not in result_ids:
                    failures_detail.append(f"FINDING cites unknown numerical result {rid}")
                    valid = False
            if not valid:
                failures_detail.append(
                    f"UNSUPPORTED FINDING persisted: {finding.get('statement', '')[:80]}"
                )
                unsupported_claims += 1
            else:
                grounded += 1

            # condition preservation (re-derive from cited supports)
            required: list[str] = []
            for pid in finding.get("supporting_proposition_ids") or []:
                required.extend(produced.get(pid, {}).get("conditions") or [])
            for sid in finding.get("supporting_comparative_static_ids") or []:
                required.extend(produced.get(sid, {}).get("conditions") or [])
            missing = [
                c
                for c in required
                if not any(c in cond for cond in finding.get("conditions") or [])
            ]
            if missing:
                failures_detail.append(f"CONDITIONS DROPPED in finding: missing {missing}")
            else:
                conditions_preserved += 1

            if finding.get("supporting_proposition_ids"):
                prop_support_total += 1
                if all(
                    pid in verified_prop_ids
                    for pid in finding.get("supporting_proposition_ids") or []
                ):
                    prop_support_ok += 1
            if finding.get("supporting_numerical_result_ids"):
                num_support_total += 1
                if all(
                    rid in result_ids
                    for rid in finding.get("supporting_numerical_result_ids") or []
                ):
                    num_support_ok += 1

        # ---- contributions ------------------------------------------------
        gap_aligned = 0
        novelty_ok = 0
        for contribution in contributions:
            claim = str(contribution.get("claim") or "")
            novelty = str(contribution.get("novelty_claim") or "")
            finding_refs = list(contribution.get("finding_ids") or [])
            normalized = bool(contribution.get("novelty_normalized"))
            aligned = (
                contribution.get("gap_id") == package.get("gap_id")
                and bool(finding_refs)
                and all(fid in set(package.get("finding_ids") or []) for fid in finding_refs)
            )
            if not aligned:
                failures_detail.append(
                    f"CONTRIBUTION without valid gap/finding support: {claim[:80]}"
                )
            else:
                gap_aligned += 1
            novelty_clean = not _NOVELTY_RE.search(claim) and not _NOVELTY_RE.search(novelty)
            if not novelty_clean:
                failures_detail.append(
                    f"NOVELTY OVERCLAIM persisted as fact in contribution: {claim[:80]}"
                )
            elif normalized != bool(expected_novelty_normalized) and expected_novelty_normalized:
                failures_detail.append(
                    "NOVELTY not normalized although the reference requires normalization"
                )
            else:
                novelty_ok += 1

        # ---- implications -------------------------------------------------
        implication_grounded = 0
        for implication in implications:
            claim_type = implication.get("claim_type")
            finding_refs = list(implication.get("grounded_in_finding_ids") or [])
            package_finding_ids = set(package.get("finding_ids") or [])
            requires_grounding = claim_type in _GROUNDED_IMPLICATION_TYPES or claim_type is None
            if requires_grounding and (
                not finding_refs or any(fid not in package_finding_ids for fid in finding_refs)
            ):
                failures_detail.append(
                    f"UNSUPPORTED IMPLICATION persisted ({claim_type}): {implication.get('text', '')[:80]}"
                )
                unsupported_claims += 1
            else:
                implication_grounded += 1

        # ---- critique: contradiction detection ---------------------------
        critique_categories = {
            issue.get("category")
            for critique in critiques
            for issue in critique.get("issues") or []
        }
        contradiction_found = "symbolic_numerical_contradiction" in critique_categories
        if "symbolic_numerical_contradiction" in expected_critique_categories:
            if not contradiction_found:
                failures_detail.append(
                    "SYMBOLIC/NUMERICAL CONTRADICTION hidden: critique did not surface it"
                )

        # ---- critique: other expected categories -------------------------
        missing_categories = expected_critique_categories - critique_categories
        for category in sorted(missing_categories):
            failures_detail.append(f"CRITIQUE MISSING expected issue {category}")
        if unsupported_claims != expected_unsupported:
            failures_detail.append(
                f"UNSUPPORTED CLAIM COUNT: expected {expected_unsupported}, "
                f"found {unsupported_claims}"
            )

        status = EvaluatorStatus.failed if failures_detail else EvaluatorStatus.passed
        finding_total = len(findings)
        contribution_total = len(contributions)
        implication_total = len(implications)

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "results",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "finding_grounding_accuracy": _metric(
                "finding_grounding_accuracy",
                float(grounded),
                finding_total,
                "rate",
                "findings grounded in verified produced supports",
            ),
            "condition_preservation_accuracy": _metric(
                "condition_preservation_accuracy",
                float(conditions_preserved),
                finding_total,
                "rate",
                "findings preserving all required conditions of cited supports",
            ),
            "proposition_support_accuracy": _metric(
                "proposition_support_accuracy",
                float(prop_support_ok),
                prop_support_total,
                "rate",
                "findings citing only verified/conditionally-verified propositions",
            ),
            "numerical_support_accuracy": _metric(
                "numerical_support_accuracy",
                float(num_support_ok),
                num_support_total,
                "rate",
                "findings citing only experiment numerical results",
            ),
            "contribution_gap_alignment_accuracy": _metric(
                "contribution_gap_alignment_accuracy",
                float(gap_aligned),
                contribution_total,
                "rate",
                "contributions aligned to the package gap with valid finding support",
            ),
            "implication_grounding_accuracy": _metric(
                "implication_grounding_accuracy",
                float(implication_grounded),
                implication_total,
                "rate",
                "implications grounded in package findings",
            ),
            "novelty_claim_accuracy": _metric(
                "novelty_claim_accuracy",
                float(novelty_ok),
                contribution_total,
                "rate",
                "contributions free of sweeping novelty claims",
            ),
            "contradiction_detection_accuracy": _metric(
                "contradiction_detection_accuracy",
                1.0 if contradiction_found else 0.0,
                1,
                "rate",
                "symbolic/numerical contradictions surfaced by the critique",
            ),
            "unsupported_claim_rate": _metric(
                "unsupported_claim_rate",
                float(unsupported_claims),
                finding_total + implication_total,
                "rate",
                "unsupported findings and implications persisted",
            ),
        }

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=(grounded / finding_total) if finding_total else None,
            value={
                "package_id": package_id,
                "finding_ids": package.get("finding_ids") or [],
                "contribution_claim_ids": package.get("contribution_claim_ids") or [],
                "implication_ids": package.get("implication_ids") or [],
                "critique_categories": sorted(critique_categories),
                "metrics": metrics,
                "dimension_scores": {
                    k: v
                    for k, v in {
                        "finding_grounding_accuracy": (
                            grounded / finding_total if finding_total else None
                        ),
                        "condition_preservation_accuracy": (
                            conditions_preserved / finding_total if finding_total else None
                        ),
                        "proposition_support_accuracy": (
                            prop_support_ok / prop_support_total if prop_support_total else None
                        ),
                        "numerical_support_accuracy": (
                            num_support_ok / num_support_total if num_support_total else None
                        ),
                        "contribution_gap_alignment_accuracy": (
                            gap_aligned / contribution_total if contribution_total else None
                        ),
                        "implication_grounding_accuracy": (
                            implication_grounded / implication_total if implication_total else None
                        ),
                        "novelty_claim_accuracy": (
                            novelty_ok / contribution_total if contribution_total else None
                        ),
                        "contradiction_detection_accuracy": 1.0 if contradiction_found else 0.0,
                        "unsupported_claim_rate": (
                            unsupported_claims / (finding_total + implication_total)
                            if (finding_total + implication_total)
                            else None
                        ),
                    }.items()
                    if v is not None
                },
            },
            status=status,
            explanation="; ".join(failures_detail)
            if failures_detail
            else "all results-assembly checks matched",
            evidence_artifact_ids=[package_env.artifact_id],
        )


class ResultsGroundingEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.results_grounding",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic results-assembly evaluator (Phase 6G)",
            provides=["evaluator.results_grounding"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.results_grounding", ResultsGroundingEvaluator())
