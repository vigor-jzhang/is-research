"""evaluator.llm_judge — generic model-assisted judge (Phase 6A).

Scores a produced case against the reference using a rubric carried by the
benchmark/evaluation config. Requires structured output; results are advisory
and never override deterministic evaluators.
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

_JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "status": {"type": "string", "enum": ["pass", "fail"]},
        "explanation": {"type": "string"},
    },
    "required": ["score", "status", "explanation"],
    "additionalProperties": False,
}


class LlmJudgeEvaluator:
    evaluator_id = "evaluator.llm_judge"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.model_assisted.value

    def __init__(self, model_router: Any, role: str = "critic") -> None:
        self._router = model_router
        self._role = role

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        router = ctx.model_router or self._router
        role = ctx.config.get("judge_role") or self._role
        rubric = ctx.config.get("llm_judge") or {}

        produced_summary = f"{len(ctx.produced_artifacts)} artifact(s): " + ", ".join(
            sorted({e.artifact_type for e in ctx.produced_artifacts})
        )
        reference_summary = json.dumps(ctx.case.reference, sort_keys=True)[:2000]
        template = rubric.get(
            "prompt_template",
            "Case: {case_name}\nProduced: {produced_summary}\n"
            "Reference: {reference_summary}\n"
            "Judge whether the produced research output satisfies the reference.",
        )
        prompt = template.format(
            case_name=ctx.case.name,
            produced_summary=produced_summary,
            reference_summary=reference_summary,
        )
        system = rubric.get("system", "You are an evaluation judge for a research system.")

        started = time.monotonic()
        response = await router.complete(
            role,
            ModelRequest(
                messages=[
                    Message(
                        role="system",
                        content=(f"{system} Return valid JSON matching the schema exactly."),
                    ),
                    Message(role="user", content=prompt),
                ],
                response_schema=_JUDGE_SCHEMA,
                temperature=0.0,
            ),
        )
        latency_ms = int((time.monotonic() - started) * 1000)

        data = _parse_json(response.message.content or "")
        try:
            score = max(0.0, min(1.0, float(data.get("score", 0.0))))
        except (TypeError, ValueError):
            score = 0.0
        verdict = data.get("status", "fail")
        status = EvaluatorStatus.passed if verdict == "pass" else EvaluatorStatus.failed
        usage = response.usage

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.model_assisted,
            score=score,
            value={
                "verdict": verdict,
                "produced_artifact_types": sorted(
                    {e.artifact_type for e in ctx.produced_artifacts}
                ),
            },
            status=status,
            explanation=data.get("explanation") or "",
            evidence_artifact_ids=[e.artifact_id for e in ctx.produced_artifacts],
            model_metadata={
                "role": role,
                "model": response.model,
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
                "latency_ms": latency_ms,
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
        raise ValueError("judge response is not an object")
    return data


class LlmJudgeEvaluatorPlugin(Plugin):
    def __init__(self, role_override: str | None = None) -> None:
        self._role_override = role_override

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.llm_judge",
            version="0.1.0",
            plugin_type="evaluator",
            description="Generic model-assisted judge evaluator (Phase 6A)",
            provides=["evaluator.llm_judge"],
            requires=["model_router.default"],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        evaluation_cfg: dict[str, Any] = {}
        if isinstance(ctx.config.get("evaluation"), dict):
            evaluation_cfg = ctx.config["evaluation"]
        role = self._role_override or evaluation_cfg.get("judge_role") or "critic"
        router = ctx.require("model_router.default")
        ctx.register("evaluator.llm_judge", LlmJudgeEvaluator(model_router=router, role=role))
