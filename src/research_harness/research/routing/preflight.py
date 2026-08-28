"""Provider/model capability preflight (Phase 7D.3B).

Lightweight probes run BEFORE a candidate enters an expensive qualification
campaign so provider/gateway availability failures stay distinct from
model-quality failures:

- reachability: the requested model resolves and completes a trivial request
  (with the configured timeout/retry path).
- structured output: a JSON-schema completion returns parseable JSON with the
  required field.
- context size: the model completes a prompt at least as large as the role's
  required context (computed from the live-quality benchmark case inputs).

Classification (never a qualification verdict):
- available: all probes pass; the exact requested/resolved model id is recorded.
- temporarily_unavailable: retryable transient failures (timeout/rate limit).
- capability_mismatch: structured-output or context probes fail — the model
  cannot satisfy the role's interface requirements.
- provider_error: hard provider/gateway failures (validation, 5xx, auth).

A provider-unavailable model is NEVER interpreted as academically incapable
and is NEVER qualified from preflight evidence. This module is pure policy —
the probes call the real provider via the CandidateModelRouter so call
accounting and failure classification stay consistent with live quality runs.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from research_harness.contracts.model import Message, ModelRequest, ModelResponse
from research_harness.research.schemas.qualification import (
    ModelPreflight,
    ModelPreflightCheck,
    PreflightCheckKind,
    PreflightStatus,
)
from research_harness.research.schemas.tournament import TournamentModelConfig

_RETRYABLE_MARKERS = ("timeout", "rate limit", "429")

_STRUCTURED_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string"},
        "ok": {"type": "boolean"},
    },
    "required": ["status", "ok"],
}


def _classify_error(text: str) -> PreflightStatus:
    """Classify a probe failure into an availability vs capability bucket."""
    t = text.lower()
    if any(m in t for m in _RETRYABLE_MARKERS):
        return PreflightStatus.temporarily_unavailable
    if "context" in t and ("length" in t or "exceed" in t or "token" in t):
        return PreflightStatus.capability_mismatch
    return PreflightStatus.provider_error


def _build_probe_request(
    *,
    role: str,
    prompt: str,
    requested_model: str,
    response_schema: dict[str, Any] | None = None,
    max_tokens: int | None = None,
) -> ModelRequest:
    return ModelRequest(
        messages=[
            Message(
                role="system",
                content=(
                    "You are a capability probe. Reply concisely. "
                    "When a JSON schema is requested, respond with a single JSON "
                    "object matching it exactly."
                ),
            ),
            Message(role="user", content=prompt),
        ],
        response_schema=response_schema,
        max_tokens=max_tokens,
        # OpenRouter routes by metadata['model']; it must be the candidate id.
        metadata={"model": requested_model, "purpose": "capability-preflight", "role": role},
    )


async def _complete_with_retry(
    provider: Any,
    request: ModelRequest,
    *,
    timeout_seconds: float,
    retries: int,
) -> tuple[ModelResponse | None, str]:
    """One probe call exercising the same timeout/retry path as live runs.

    Preserves the RAW provider error text so capability mismatches (e.g.
    context-length) are classified correctly instead of being flattened to a
    generic provider error."""
    attempts = 0
    while True:
        attempts += 1
        try:
            response = await asyncio.wait_for(provider.complete(request), timeout=timeout_seconds)
            return response, ""
        except TimeoutError:
            error = "timeout"
        except Exception as e:  # noqa: BLE001
            error = str(e)
        if attempts <= retries and any(m in error.lower() for m in _RETRYABLE_MARKERS):
            continue
        return None, error


def _structured_ok(payload: dict[str, Any] | None) -> bool:
    """Structured-output probe check that tolerates one level of nesting.

    Some models wrap the whole object as the value of a key (e.g.
    {"status": {"status": "ready", "ok": true}}); the probe asks for
    {"status": ..., "ok": true} so a valid nested object is still evidence that
    structured JSON output works. Never interprets a non-JSON/empty response
    as a success."""
    if not isinstance(payload, dict):
        return False
    if isinstance(payload.get("ok"), bool) and isinstance(payload.get("status"), str):
        return payload["ok"] is True
    for value in payload.values():
        if isinstance(value, dict):
            if isinstance(value.get("ok"), bool) and isinstance(value.get("status"), str):
                return value["ok"] is True
    return False


def _parse_json_content(content: str | None) -> dict[str, Any] | None:
    if not content:
        return None
    text = content.strip()
    # tolerate a code-fence wrapper that some providers add
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
    except (ValueError, TypeError):
        return None


async def run_candidate_preflight(
    *,
    role: str,
    candidate: TournamentModelConfig,
    service_lookup: Any,
    base_router: Any = None,
    timeout_seconds: float = 120.0,
    retries: int = 2,
    required_context_chars: int = 0,
    probe_max_tokens: int | None = None,
) -> ModelPreflight:
    """Run the lightweight capability probes for one candidate model.

    Uses the real provider with the same timeout/retry path as live-quality
    runs. Never a qualification verdict; the exact requested/resolved model id
    is recorded."""
    try:
        provider = service_lookup(f"model_provider.{candidate.provider}")
    except Exception as e:  # noqa: BLE001
        return ModelPreflight(
            role=role,
            candidate_id=candidate.candidate_id,
            provider=candidate.provider,
            requested_model=candidate.requested_model,
            resolved_model=None,
            status=PreflightStatus.provider_error,
            checks=[
                ModelPreflightCheck(
                    kind=PreflightCheckKind.reachability.value,
                    passed=False,
                    detail=f"no provider registered: {e}",
                )
            ],
            required_context_chars=required_context_chars,
            timeout_seconds=timeout_seconds,
            retries=retries,
            error=str(e),
        )
    checks: list[ModelPreflightCheck] = []
    resolved: str | None = None
    status = PreflightStatus.available
    error_text = ""

    # ---- 1. reachability probe ----------------------------------------------
    reach_req = _build_probe_request(
        role=role,
        prompt="Reply with exactly: ok",
        requested_model=candidate.requested_model,
    )
    reach_start = time.monotonic()
    response, err = await _complete_with_retry(
        provider, reach_req, timeout_seconds=timeout_seconds, retries=retries
    )
    reach_latency = (time.monotonic() - reach_start) * 1000
    if response is not None:
        resolved = response.model or candidate.requested_model
        checks.append(
            ModelPreflightCheck(
                kind=PreflightCheckKind.reachability.value,
                passed=True,
                detail=f"model reachable; resolved {resolved!r}",
                latency_ms=reach_latency,
                resolved_model=resolved,
            )
        )
    else:
        status = _classify_error(err)
        error_text = err
        checks.append(
            ModelPreflightCheck(
                kind=PreflightCheckKind.reachability.value,
                passed=False,
                detail=err,
                latency_ms=reach_latency,
            )
        )
        return ModelPreflight(
            role=role,
            candidate_id=candidate.candidate_id,
            provider=candidate.provider,
            requested_model=candidate.requested_model,
            resolved_model=None,
            status=status,
            checks=checks,
            required_context_chars=required_context_chars,
            timeout_seconds=timeout_seconds,
            retries=retries,
            error=error_text,
        )

    # ---- 2. structured-output probe -----------------------------------------
    so_req = _build_probe_request(
        role=role,
        prompt='Respond with a single JSON object: {"status": "ready", "ok": true}',
        requested_model=candidate.requested_model,
        response_schema=_STRUCTURED_SCHEMA,
        max_tokens=probe_max_tokens or 200,
    )
    so_start = time.monotonic()
    parsed: dict[str, Any] | None = None
    err = ""
    content: str | None = None
    so_latency = (time.monotonic() - so_start) * 1000
    for _attempt in range(retries + 1):
        response, err = await _complete_with_retry(
            provider, so_req, timeout_seconds=timeout_seconds, retries=retries
        )
        content = response.message.content if response is not None else None
        candidate_parsed = _parse_json_content(content)
        if _structured_ok(candidate_parsed):
            parsed = candidate_parsed
            break
    so_latency = (time.monotonic() - so_start) * 1000
    so_ok = _structured_ok(parsed)
    last_content = content if content is not None else "no response"
    checks.append(
        ModelPreflightCheck(
            kind=PreflightCheckKind.structured_output.value,
            passed=so_ok,
            detail=(
                "structured JSON output matches the requested schema"
                if so_ok
                else f"structured output not parseable/matching: {err or last_content}"
            ),
            latency_ms=so_latency,
            resolved_model=resolved,
        )
    )
    if not so_ok:
        return ModelPreflight(
            role=role,
            candidate_id=candidate.candidate_id,
            provider=candidate.provider,
            requested_model=candidate.requested_model,
            resolved_model=resolved,
            status=PreflightStatus.capability_mismatch,
            checks=checks,
            required_context_chars=required_context_chars,
            timeout_seconds=timeout_seconds,
            retries=retries,
            error=err or "structured-output probe failed",
        )

    # ---- 3. context-size probe ----------------------------------------------
    ctx_ok = True
    ctx_latency: float | None = None
    ctx_detail = f"required context ~{required_context_chars} chars; no context probe requested"
    if required_context_chars > 0:
        pad = "The role's benchmark inputs require sustained context. "
        filler = ("context " * (required_context_chars // 7 + 1))[:required_context_chars]
        prompt = pad + "Repeat the following tokens exactly once: " + filler
        ctx_req = _build_probe_request(
            role=role,
            prompt=prompt,
            requested_model=candidate.requested_model,
            max_tokens=probe_max_tokens or 200,
        )
        ctx_start = time.monotonic()
        response, err = await _complete_with_retry(
            provider, ctx_req, timeout_seconds=timeout_seconds, retries=retries
        )
        ctx_latency = (time.monotonic() - ctx_start) * 1000
        ctx_ok = response is not None
        ctx_detail = (
            "context probe completed at the required size"
            if ctx_ok
            else f"context probe failed: {err}"
        )
    checks.append(
        ModelPreflightCheck(
            kind=PreflightCheckKind.context_size.value,
            passed=ctx_ok,
            detail=ctx_detail,
            latency_ms=ctx_latency,
            resolved_model=resolved,
        )
    )
    if not ctx_ok:
        # Classify by the actual error: a context-length rejection is a
        # capability mismatch, but an upstream/gateway error (502, overloaded)
        # is an availability failure that must never be read as incapability.
        ctx_status = _classify_error(err)
        if ctx_status in (PreflightStatus.available, PreflightStatus.capability_mismatch):
            ctx_status = (
                PreflightStatus.capability_mismatch
                if "context" in err.lower()
                else PreflightStatus.provider_error
            )
        return ModelPreflight(
            role=role,
            candidate_id=candidate.candidate_id,
            provider=candidate.provider,
            requested_model=candidate.requested_model,
            resolved_model=resolved,
            status=ctx_status,
            checks=checks,
            required_context_chars=required_context_chars,
            timeout_seconds=timeout_seconds,
            retries=retries,
            error="context-size probe failed",
        )

    return ModelPreflight(
        role=role,
        candidate_id=candidate.candidate_id,
        provider=candidate.provider,
        requested_model=candidate.requested_model,
        resolved_model=resolved,
        status=PreflightStatus.available,
        checks=checks,
        required_context_chars=required_context_chars,
        timeout_seconds=timeout_seconds,
        retries=retries,
        error="",
    )


async def preflight_required_context_chars(
    *, role: str, benchmark_cases: list[dict[str, Any]]
) -> int:
    """Estimate the required context size (characters) for a role's live-quality
    benchmark from the case inputs (documents, statements, papers, fixtures).
    Cheap and deterministic; used to size the context probe."""
    total = 0
    for case in benchmark_cases:
        inp = case.get("input") or {}
        for key in ("documents", "papers", "evidence", "statements", "profiles", "themes"):
            for item in inp.get(key) or []:
                total += len(
                    str(item.get("text") or item.get("abstract") or item.get("statement") or "")
                )
                for page in item.get("pages") or []:
                    total += len(str(page.get("text") or ""))
        for key in ("gap", "mechanism", "model", "candidate"):
            item = inp.get(key)
            if isinstance(item, dict):
                total += len(str(item.get("description") or "")) + len(str(item.get("title") or ""))
        for item in (inp.get("fixtures") or {}).values():
            for payload in item or []:
                total += len(str(payload.get("description") or "")) + len(
                    str(payload.get("statement") or "") or ""
                )
    return max(total, 0)
