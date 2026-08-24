"""evaluator.claim_grounding — model-assisted grounding check (Phase 6A).

Checks whether novelty claim assessments are grounded in cited evidence.
Deterministic guard: claims with no evidence artifacts are ungrounded without
any model call. The verdict is advisory (never overrides deterministic
evaluators).
"""

from __future__ import annotations

import json
import time
from typing import Any

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.contracts.model import Message, ModelRequest
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.schemas.evaluation import (
    EvaluatorCategory,
    EvaluatorResult,
    EvaluatorStatus,
)
from research_harness.research.schemas.novelty import (
    NoveltyCandidateAssessment,
    NoveltyClaim,
)

_GROUNDING_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["grounded", "partially_grounded", "ungrounded"],
        },
        "explanation": {"type": "string"},
    },
    "required": ["verdict", "explanation"],
    "additionalProperties": False,
}

_SCORE = {"grounded": 1.0, "partially_grounded": 0.5, "ungrounded": 0.0}


class ClaimGroundingEvaluator:
    evaluator_id = "evaluator.claim_grounding"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.model_assisted.value

    def __init__(self, model_router: Any, role: str = "critic") -> None:
        self._router = model_router
        self._role = role

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        router = ctx.model_router or self._router
        role = ctx.config.get("judge_role") or self._role

        claim_envs = [e for e in ctx.produced_artifacts if e.artifact_type == "novelty_claim"]
        claims = {e.artifact_id: e.parse_payload(NoveltyClaim) for e in claim_envs}
        assessments = [
            e.parse_payload(NoveltyCandidateAssessment)
            for e in ctx.produced_artifacts
            if e.artifact_type == "novelty_candidate_assessment"
        ]
        by_claim: dict[str, list[NoveltyCandidateAssessment]] = {}
        for a in assessments:
            by_claim.setdefault(a.claim_id, []).append(a)

        grounded = 0
        partially = 0
        ungrounded = 0
        verdicts: dict[str, str] = {}
        evidence_ids: list[str] = []
        total_meta: dict[str, Any] = {
            "role": role,
            "model": "none",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "latency_ms": 0,
        }

        for claim_id, claim in claims.items():
            claim_ev = [a for a in by_claim.get(claim_id, []) if a.evidence_artifact_ids]
            if not claim_ev:
                verdicts[claim_id] = "ungrounded"
                ungrounded += 1
                continue
            prompt_lines = [f"Claim: {claim.claim_text}"]
            for a in claim_ev:
                prompt_lines.append(
                    f"- candidate {a.paper_identity_id}: relationship={a.relationship.value}, "
                    f"evidence_basis={a.evidence_basis.value}, "
                    f"evidence_artifacts={len(a.evidence_artifact_ids)}"
                )
            started = time.monotonic()
            response = await router.complete(
                role,
                ModelRequest(
                    messages=[
                        Message(
                            role="system",
                            content=(
                                "You judge whether a novelty candidate assessment is "
                                "grounded in the cited evidence. "
                                "Return valid JSON matching the schema exactly."
                            ),
                        ),
                        Message(
                            role="user",
                            content=(
                                "Assess whether each candidate assessment is grounded in "
                                "the cited evidence.\n\n" + "\n".join(prompt_lines)
                            ),
                        ),
                    ],
                    response_schema=_GROUNDING_SCHEMA,
                    temperature=0.0,
                ),
            )
            latency = int((time.monotonic() - started) * 1000)
            data = _parse_json(response.message.content or "")
            verdict = data.get("verdict", "ungrounded")
            if verdict not in _SCORE:
                verdict = "ungrounded"
            verdicts[claim_id] = verdict
            if verdict == "grounded":
                grounded += 1
            elif verdict == "partially_grounded":
                partially += 1
            else:
                ungrounded += 1
            usage = response.usage
            total_meta["model"] = response.model or total_meta["model"]
            total_meta["prompt_tokens"] += int(usage.prompt_tokens) if usage else 0
            total_meta["completion_tokens"] += int(usage.completion_tokens) if usage else 0
            total_meta["latency_ms"] += latency
            evidence_ids.extend(
                aid for a in claim_ev for aid in a.evidence_artifact_ids if aid not in evidence_ids
            )

        n = len(claims)
        score = (grounded * 1.0 + partially * 0.5) / n if n else None
        status = EvaluatorStatus.failed if n and ungrounded > 0 else EvaluatorStatus.passed
        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.model_assisted,
            score=score,
            value={
                "claims": n,
                "grounded": grounded,
                "partially_grounded": partially,
                "ungrounded": ungrounded,
                "verdicts": verdicts,
            },
            status=status,
            explanation=(
                f"{grounded} grounded, {partially} partially grounded, "
                f"{ungrounded} ungrounded of {n} claims"
            ),
            evidence_artifact_ids=evidence_ids,
            model_metadata={
                **total_meta,
                "model": total_meta["model"],
            },
        )


def _parse_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("grounding response is not an object")
    return data


class ClaimGroundingEvaluatorPlugin(Plugin):
    def __init__(self, role_override: str | None = None) -> None:
        self._role_override = role_override

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.claim_grounding",
            version="0.1.0",
            plugin_type="evaluator",
            description="Model-assisted claim-grounding evaluator (Phase 6A)",
            provides=["evaluator.claim_grounding"],
            requires=["model_router.default"],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        evaluation_cfg: dict[str, Any] = {}
        if isinstance(ctx.config.get("evaluation"), dict):
            evaluation_cfg = ctx.config["evaluation"]
        role = self._role_override or evaluation_cfg.get("judge_role") or "critic"
        router = ctx.require("model_router.default")
        ctx.register(
            "evaluator.claim_grounding",
            ClaimGroundingEvaluator(model_router=router, role=role),
        )
