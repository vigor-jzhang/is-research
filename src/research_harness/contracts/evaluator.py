"""Evaluator contract — Phase 6A.

Evaluators are the only place evaluation logic lives. Production research
plugins never import or depend on evaluator implementations; the evaluation
harness composes evaluators by service id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import (
    BenchmarkCase,
    EvaluatorResult,
)


def envelope_payload_dict(env: ArtifactEnvelope[Any]) -> dict[str, Any]:
    """Payload as a plain dict, whether the envelope holds a Pydantic model
    (unit tests) or a store-round-tripped dict."""
    payload = env.payload
    if isinstance(payload, dict):
        return payload
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json")
    return {}


class EvaluatorError(Exception):
    """Raised by an evaluator when it cannot produce a result."""


@dataclass(frozen=True)
class EvaluatorContext:
    """Everything one evaluator invocation receives."""

    case: BenchmarkCase
    case_envelope: ArtifactEnvelope[Any]
    produced_artifacts: list[ArtifactEnvelope[Any]] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    model_router: Any | None = field(default=None)
    blob_store: Any | None = field(default=None)
    provenance: dict[str, list[Any]] = field(default_factory=dict)


@runtime_checkable
class Evaluator(Protocol):
    """Generic evaluator contract implemented by evaluator plugin services."""

    @property
    def evaluator_id(self) -> str: ...

    @property
    def evaluator_version(self) -> str: ...

    @property
    def category(self) -> str: ...

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult: ...
