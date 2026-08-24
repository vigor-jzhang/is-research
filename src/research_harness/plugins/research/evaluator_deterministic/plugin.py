"""evaluator.deterministic — deterministic comparison evaluator (Phase 6A).

Modes:
- novelty_threat: compares produced novelty artifacts (candidate
  relationships, claim statuses, report status) against the case reference and
  measures false-clear behavior.
- reference_equality: generic single-field equality check for simple cases.

Deterministic evaluators gate case pass/fail; model-assisted evaluators never
override them.
"""

from __future__ import annotations

import re
from typing import Any

from research_harness.contracts.evaluator import EvaluatorContext, EvaluatorError
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import (
    EvaluatorCategory,
    EvaluatorResult,
    EvaluatorStatus,
)
from research_harness.research.schemas.novelty import (
    NoveltyCandidateAssessment,
    NoveltyClaim,
    NoveltyClaimAssessment,
    NoveltyValidationReport,
)

_THREATENED_OR_UNVERIFIED = {"threatened", "unverified"}
_CLEAR_STATUS = "not_threatened_within_search_scope"
_REPORT_NOT_CLEAR = {"blocked", "revise", "unverified"}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _payload_dict(env: ArtifactEnvelope[Any]) -> dict[str, Any]:
    payload = env.payload
    if isinstance(payload, dict):
        return payload
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json")
    return {}


class DeterministicEvaluator:
    evaluator_id = "evaluator.deterministic"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        mode = ctx.config.get("mode", "novelty_threat")
        if mode == "novelty_threat":
            return self._novelty_threat(ctx)
        if mode == "reference_equality":
            return self._reference_equality(ctx)
        raise EvaluatorError(f"unknown deterministic mode {mode!r}")

    # ------------------------------------------------------------------
    # novelty_threat mode
    # ------------------------------------------------------------------

    def _novelty_threat(self, ctx: EvaluatorContext) -> EvaluatorResult:
        produced = ctx.produced_artifacts

        reports = [e for e in produced if e.artifact_type == "novelty_validation_report"]
        if not reports:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no novelty_validation_report produced for the case",
            )
        report_env = max(reports, key=lambda e: e.created_at)
        report = report_env.parse_payload(NoveltyValidationReport)

        claims = {
            e.artifact_id: e.parse_payload(NoveltyClaim)
            for e in produced
            if e.artifact_type == "novelty_claim"
        }
        claim_assessments = [
            e.parse_payload(NoveltyClaimAssessment)
            for e in produced
            if e.artifact_type == "novelty_claim_assessment"
        ]
        candidate_assessments = [
            e.parse_payload(NoveltyCandidateAssessment)
            for e in produced
            if e.artifact_type == "novelty_candidate_assessment"
        ]
        identities = {e.artifact_id: e for e in produced if e.artifact_type == "paper_identity"}
        records = {e.artifact_id: e for e in produced if e.artifact_type == "paper_record"}

        def paper_titles(identity_id: str) -> list[str]:
            env = identities.get(identity_id)
            if env is None:
                return []
            member_ids = set(_payload_dict(env).get("member_paper_artifact_ids") or [])
            titles: list[str] = []
            for pid in member_ids:
                rec = records.get(pid)
                if rec is None:
                    continue
                title = (_payload_dict(rec).get("title") or "").strip()
                if title:
                    titles.append(title)
            return titles

        # ---- candidate relationship matching ---------------------------
        relationships: dict[str, str] = {}
        for a in candidate_assessments:
            for title in paper_titles(a.paper_identity_id):
                relationships[_norm(title)] = a.relationship.value

        relationship_matches = 0
        relationship_total = 0
        relationship_failures: list[str] = []
        for prior in ctx.case.reference.get("prior_art") or []:
            relationship_total += 1
            title = _norm(prior.get("title") or "")
            expected = prior.get("relationship")
            produced_rel = relationships.get(title)
            if produced_rel == expected:
                relationship_matches += 1
            else:
                relationship_failures.append(
                    f"{prior.get('title')}: expected {expected!r}, produced {produced_rel!r}"
                )

        # ---- claim status matching -------------------------------------
        produced_status: dict[str, str] = {}
        for a in claim_assessments:
            claim = claims.get(a.claim_id)
            if claim is not None:
                produced_status[_norm(claim.claim_text)] = a.status.value

        claim_matches = 0
        claim_total = 0
        claim_failures: list[str] = []
        false_clear_count = 0
        expected_at_risk = 0
        for text, expected in (ctx.case.reference.get("expected_claim_statuses") or {}).items():
            claim_total += 1
            key = _norm(text)
            actual = produced_status.get(key)
            if actual == expected:
                claim_matches += 1
            else:
                claim_failures.append(
                    f"claim {text[:60]!r}: expected {expected!r}, produced {actual!r}"
                )
            if expected in _THREATENED_OR_UNVERIFIED:
                expected_at_risk += 1
                if actual == _CLEAR_STATUS:
                    false_clear_count += 1

        # ---- report status ---------------------------------------------
        expected_report = ctx.case.reference.get("expected_report_status")
        produced_report = report.overall_status.value
        report_status_match = 1 if expected_report == produced_report else 0
        report_status_total = 1
        false_threat_count = 0
        if expected_report == "clear" and produced_report in _REPORT_NOT_CLEAR:
            false_threat_count = 1

        all_matched = (
            relationship_failures == []
            and claim_failures == []
            and report_status_match == 1
            and false_clear_count == 0
        )
        status = EvaluatorStatus.passed if all_matched else EvaluatorStatus.failed

        explanation_parts: list[str] = []
        if relationship_failures:
            explanation_parts.append("relationship mismatches: " + "; ".join(relationship_failures))
        if claim_failures:
            explanation_parts.append("claim-status mismatches: " + "; ".join(claim_failures))
        if report_status_match != 1:
            explanation_parts.append(
                f"report status: expected {expected_report!r}, produced {produced_report!r}"
            )
        if false_clear_count:
            explanation_parts.append(
                f"FALSE CLEAR: {false_clear_count} claim(s) expected "
                "threatened/unverified were reported clear"
            )
        if not explanation_parts:
            explanation_parts.append("all deterministic checks matched the reference")

        dimension_scores: dict[str, float] = {}
        if relationship_total:
            dimension_scores["candidate_relationship"] = relationship_matches / relationship_total
        if claim_total:
            dimension_scores["claim_status"] = claim_matches / claim_total
        dimension_scores["report_status"] = float(report_status_match)
        dimension_scores["false_clear"] = 0.0 if false_clear_count else 1.0

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=(relationship_matches / relationship_total) if relationship_total else None,
            value={
                "relationship_matches": relationship_matches,
                "relationship_total": relationship_total,
                "claim_status_matches": claim_matches,
                "claim_status_total": claim_total,
                "report_status_match": report_status_match,
                "report_status_total": report_status_total,
                "false_clear_count": false_clear_count,
                "expected_at_risk": expected_at_risk,
                "false_threat_count": false_threat_count,
                "dimension_scores": dimension_scores,
                "report_status": produced_report,
                "candidate_count": len(candidate_assessments),
                "claim_count": len(claim_assessments),
                "metrics": {
                    "candidate_relationship_accuracy": {
                        "value": float(relationship_matches),
                        "count": relationship_total,
                        "kind": "rate",
                        "dimension": "candidate_relationship",
                        "definition": (
                            "fraction of expected prior-art candidates whose "
                            "assessed relationship matches the reference"
                        ),
                    },
                    "claim_status_accuracy": {
                        "value": float(claim_matches),
                        "count": claim_total,
                        "kind": "rate",
                        "dimension": "claim_status",
                        "definition": (
                            "fraction of expected claim statuses that match the reference"
                        ),
                    },
                    "report_status_accuracy": {
                        "value": float(report_status_match),
                        "count": 1,
                        "kind": "rate",
                        "dimension": "report_status",
                        "definition": (
                            "fraction of cases whose report status matches the reference"
                        ),
                    },
                    "false_clear_count": {
                        "value": float(false_clear_count),
                        "count": expected_at_risk,
                        "kind": "quantity",
                        "dimension": "false_clear",
                        "definition": (
                            "claims expected threatened/unverified that were reported "
                            "clear (not_threatened_within_search_scope)"
                        ),
                    },
                    "false_clear_rate": {
                        "value": float(false_clear_count),
                        "count": expected_at_risk,
                        "kind": "rate",
                        "dimension": "false_clear",
                        "definition": (
                            "false-clear count over claims expected threatened/unverified"
                        ),
                    },
                    "false_threat_count": {
                        "value": float(false_threat_count),
                        "count": 1,
                        "kind": "quantity",
                        "dimension": "false_clear",
                        "definition": "cases expected clear whose report was not clear",
                    },
                },
            },
            status=status,
            explanation="; ".join(explanation_parts),
            evidence_artifact_ids=[
                report_env.artifact_id,
                *[
                    e.artifact_id
                    for e in produced
                    if e.artifact_type == "novelty_candidate_assessment"
                ],
                *[e.artifact_id for e in produced if e.artifact_type == "novelty_claim_assessment"],
                *[e.artifact_id for e in produced if e.artifact_type == "novelty_claim"],
            ],
        )

    # ------------------------------------------------------------------
    # reference_equality mode
    # ------------------------------------------------------------------

    def _reference_equality(self, ctx: EvaluatorContext) -> EvaluatorResult:
        artifact_type = ctx.config.get("artifact_type")
        field = ctx.config.get("field")
        expected = ctx.config.get("expected")
        if not artifact_type or not field:
            raise EvaluatorError("reference_equality mode requires config artifact_type and field")
        matches = [e for e in ctx.produced_artifacts if e.artifact_type == artifact_type]
        if not matches:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={"produced": None},
                status=EvaluatorStatus.failed,
                explanation=f"no produced artifact of type {artifact_type!r}",
            )
        env = max(matches, key=lambda e: e.created_at)
        produced = _payload_dict(env).get(field)
        ok = produced == expected
        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=1.0 if ok else 0.0,
            value={"expected": expected, "produced": produced, "field": field},
            status=EvaluatorStatus.passed if ok else EvaluatorStatus.failed,
            explanation=(
                f"field {field!r} matches the reference"
                if ok
                else f"field {field!r}: expected {expected!r}, produced {produced!r}"
            ),
            evidence_artifact_ids=[env.artifact_id],
        )


class DeterministicEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.deterministic",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic comparison evaluator (Phase 6A)",
            provides=["evaluator.deterministic"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.deterministic", DeterministicEvaluator())
