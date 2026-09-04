"""OpenRouter model provider plugin."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from typing import Any

import httpx

from research_harness.config.dotenv import load_dotenv
from research_harness.contracts.common import Usage
from research_harness.contracts.model import (
    Message,
    ModelCapabilities,
    ModelRequest,
    ModelResponse,
    ToolCall,
)
from research_harness.kernel.errors import ModelError
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata

# Ensure .env is loaded for standalone usage
load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 2

# Statuses worth retrying: rate limits and server-side failures are transient,
# whereas 401/403/400/404/422 will fail identically on every attempt (H20).
RETRYABLE_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
NON_RETRYABLE_STATUSES = frozenset({400, 401, 403, 404, 422})
MAX_BACKOFF_SECONDS = 30.0


def _retry_after_seconds(resp: Any) -> float | None:
    """Read Retry-After as seconds; the HTTP-date form is ignored."""
    raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _backoff_delay(attempt: int, retry_after: float | None = None) -> float:
    """Exponential backoff with jitter, capped; honours Retry-After."""
    if retry_after is not None:
        return min(max(retry_after, 0.0), MAX_BACKOFF_SECONDS)
    return min(2**attempt + random.uniform(0.0, 1.0), MAX_BACKOFF_SECONDS)


class _RetryableHTTPError(Exception):
    """Internal: an HTTP status worth another attempt."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class OpenRouterProvider:
    """Adapter for OpenRouter chat completions."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        default_headers: dict[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
        sleep_fn: Any = None,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.default_headers = default_headers or {}
        self._client = http_client
        self._owns_client = http_client is None
        # Injectable so tests can skip the real backoff delay (H20).
        self._sleep = sleep_fn or asyncio.sleep
        self.capabilities = ModelCapabilities(
            tool_calling=True,
            structured_output=True,
            streaming=False,
            context_length=None,
        )

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        # Keep one owned client for the provider lifetime. Creating a client
        # per request leaks connection pools because ``close`` cannot reach it.
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if not self.api_key:
            raise ModelError(
                "OPENROUTER_API_KEY is not set. Set it in environment or plugin config 'api_key'."
            )

        payload = self._build_payload(request)
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            **self.default_headers,
        }

        # Retry for transient transport failures
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            start = time.monotonic()
            try:
                client = self._get_client()
                # Use client.request to allow injected mock client
                resp = await client.post(url, headers=headers, json=payload, timeout=self.timeout)
                latency_ms = (time.monotonic() - start) * 1000

                if resp.status_code == 401:
                    raise ModelError(f"OpenRouter authentication failed (401): {resp.text}")
                if resp.status_code in NON_RETRYABLE_STATUSES:
                    raise ModelError(f"OpenRouter error {resp.status_code}: {resp.text}")
                if resp.status_code in RETRYABLE_STATUSES:
                    raise _RetryableHTTPError(
                        f"OpenRouter error {resp.status_code}: {resp.text}",
                        _retry_after_seconds(resp),
                    )
                if resp.status_code >= 400:
                    raise ModelError(f"OpenRouter error {resp.status_code}: {resp.text}")

                data = resp.json()
                return self._parse_response(data, latency_ms, request)

            except httpx.TimeoutException as e:
                last_exc = e
                logger.warning(
                    "OpenRouter timeout (attempt %d/%d): %s", attempt + 1, MAX_RETRIES + 1, e
                )
                if attempt == MAX_RETRIES:
                    raise ModelError(f"OpenRouter request timed out after {self.timeout}s") from e
                await self._sleep(_backoff_delay(attempt))
                continue
            except httpx.ConnectError as e:
                last_exc = e
                logger.warning(
                    "OpenRouter connect error (attempt %d/%d): %s", attempt + 1, MAX_RETRIES + 1, e
                )
                if attempt == MAX_RETRIES:
                    raise ModelError(f"OpenRouter connection failed: {e}") from e
                await self._sleep(_backoff_delay(attempt))
                continue
            except _RetryableHTTPError as e:
                last_exc = e
                if attempt == MAX_RETRIES:
                    raise ModelError(str(e)) from e
                delay = _backoff_delay(attempt, e.retry_after)
                logger.warning(
                    "OpenRouter retryable error (attempt %d/%d, retrying in %.2fs): %s",
                    attempt + 1,
                    MAX_RETRIES + 1,
                    delay,
                    e,
                )
                await self._sleep(delay)
                continue
            except ModelError:
                raise
            except Exception as e:
                last_exc = e
                # Check if it's a transport-level httpx error that is retryable
                if isinstance(e, httpx.TransportError):
                    if attempt == MAX_RETRIES:
                        raise ModelError(f"OpenRouter transport error: {e}") from e
                    await self._sleep(_backoff_delay(attempt))
                    continue
                raise ModelError(f"OpenRouter unexpected error: {e}") from e

        raise ModelError(f"OpenRouter failed after retries: {last_exc}")

    def _build_payload(self, request: ModelRequest) -> dict[str, Any]:
        # Extract model from metadata if provided; otherwise provider will error
        model = request.metadata.get("model")
        if not model:
            raise ModelError("ModelRequest.metadata must contain 'model' for OpenRouter")

        messages: list[dict[str, Any]] = []
        for m in request.messages:
            msg: dict[str, Any] = {"role": m.role}
            if m.content is not None:
                msg["content"] = m.content
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                            if isinstance(tc.arguments, dict)
                            else tc.arguments,
                        },
                    }
                    for tc in m.tool_calls
                ]
            if m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            # Only include content if present; for tool messages content is required
            if m.content is None and m.role != "assistant":
                msg["content"] = ""
            messages.append(msg)

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in request.tools
            ]
            # Ensure tool_choice auto if tools present
            payload["tool_choice"] = "auto"
        if request.response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "response",
                    "schema": request.response_schema,
                    "strict": True,
                },
            }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens

        # Pass provider routing params if present in metadata
        if "provider" in request.metadata:
            payload["provider"] = request.metadata["provider"]

        return payload

    def _parse_response(
        self, data: dict[str, Any], latency_ms: float, request: ModelRequest
    ) -> ModelResponse:
        try:
            # Handle OpenRouter error envelope (even on 200)
            if "error" in data:
                err = data["error"]
                # err may be dict with message/code or string
                if isinstance(err, dict):
                    msg = err.get("message", str(err))
                    code = err.get("code", "")
                    raise ModelError(f"OpenRouter upstream error {code}: {msg} | raw={data}")
                raise ModelError(f"OpenRouter error: {err} | raw={data}")

            choices = data.get("choices")
            if not choices or not isinstance(choices, list):
                raise ModelError(f"OpenRouter malformed response: missing choices: {data}")

            choice = choices[0]
            msg_data = choice.get("message", {})
            content = msg_data.get("content")
            raw_tool_calls = msg_data.get("tool_calls") or []
            finish_reason = choice.get("finish_reason")

            tool_calls: list[ToolCall] = []
            for tc in raw_tool_calls:
                func = tc.get("function", {})
                name = func.get("name", "")
                args_raw = func.get("arguments", "{}")
                # Try to parse arguments JSON string to dict
                if isinstance(args_raw, str):
                    try:
                        args_parsed: Any = json.loads(args_raw) if args_raw.strip() else {}
                    except json.JSONDecodeError:
                        args_parsed = args_raw
                else:
                    args_parsed = args_raw
                tool_calls.append(
                    ToolCall(
                        id=tc.get("id", ""),
                        name=name,
                        arguments=args_parsed if isinstance(args_parsed, dict) else args_raw,
                    )
                )

            # Usage extraction
            usage_data = data.get("usage") or {}
            usage = None
            if usage_data:
                usage = Usage(
                    prompt_tokens=usage_data.get("prompt_tokens"),
                    completion_tokens=usage_data.get("completion_tokens"),
                    total_tokens=usage_data.get("total_tokens"),
                    cost=usage_data.get("cost"),
                )

            model_name: str | None = data.get("model") or request.metadata.get("model")
            provider_name: str | None = data.get("provider")

            message = Message(
                role=msg_data.get("role", "assistant"),
                content=content,
                tool_calls=tool_calls if tool_calls else None,
            )

            return ModelResponse(
                message=message,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                usage=usage,
                model=model_name,
                provider=provider_name,
                latency_ms=latency_ms,
                metadata={"raw": data},
            )
        except ModelError:
            raise
        except Exception as e:
            raise ModelError(f"failed to parse OpenRouter response: {e}; raw={data}") from e

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()


class OpenRouterPlugin(Plugin):
    """Plugin that provides OpenRouter model provider."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        # Allow injection for testing
        self._api_key_override = api_key
        self._base_url_override = base_url
        self._timeout_override = timeout
        self._http_client = http_client
        self._provider: OpenRouterProvider | None = None
        self._ctx: PluginContext | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="model.openrouter",
            version="0.1.0",
            plugin_type="model",
            description="OpenRouter chat completions provider",
            provides=["model_provider.openrouter"],
            requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        cfg = ctx.config or {}
        api_key = self._api_key_override or cfg.get("api_key") or os.getenv("OPENROUTER_API_KEY")
        base_url = self._base_url_override or cfg.get("base_url") or DEFAULT_BASE_URL
        timeout = self._timeout_override or cfg.get("timeout") or DEFAULT_TIMEOUT
        # Allow http_client from config for testing (not serialized)
        http_client = self._http_client or cfg.get("http_client")  # type: ignore[assignment]

        provider = OpenRouterProvider(
            api_key=api_key,
            base_url=base_url,
            timeout=float(timeout),
            http_client=http_client,  # type: ignore[arg-type]
        )
        self._provider = provider
        ctx.register("model_provider.openrouter", provider)

    async def stop(self) -> None:
        if self._provider is not None:
            await self._provider.close()

    async def teardown(self) -> None:
        self._provider = None
