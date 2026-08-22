"""Semantic Scholar literature source plugin."""

from __future__ import annotations

import os
from typing import Any

from research_harness.contracts.literature import LiteratureSource
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.plugins.literature.semantic_scholar.client import SemanticScholarClient


class SemanticScholarSourceAdapter(LiteratureSource):
    def __init__(self, client: SemanticScholarClient) -> None:
        self._client = client

    @property
    def provider_name(self) -> str:
        return "semantic_scholar"

    async def search(self, request):  # type: ignore[no-untyped-def]
        return await self._client.search(request)

    async def get(self, identifier: str):  # type: ignore[no-untyped-def]
        return await self._client.get(identifier)


class SemanticScholarPlugin(Plugin):
    def __init__(self, client: SemanticScholarClient | None = None) -> None:
        self._client_override = client
        self._client: SemanticScholarClient | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="literature.semantic_scholar",
            version="0.1.0",
            plugin_type="literature_source",
            description="Semantic Scholar literature source",
            provides=["literature_source.semantic_scholar"],
            requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        # Handle nested literature.semantic_scholar or flat
        ss_cfg: dict[str, Any] = {}
        if "literature" in cfg and isinstance(cfg["literature"], dict):
            ss_cfg = (
                cfg["literature"].get("semantic_scholar", {})
                if isinstance(cfg["literature"].get("semantic_scholar"), dict)
                else {}
            )
        elif "semantic_scholar" in cfg and isinstance(cfg["semantic_scholar"], dict):
            ss_cfg = cfg["semantic_scholar"]
        else:
            ss_cfg = {
                k: v for k, v in cfg.items() if k in ("timeout_seconds", "timeout", "enabled")
            }

        timeout = ss_cfg.get("timeout_seconds") or ss_cfg.get("timeout") or 20.0
        api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")

        if self._client_override is not None:
            client = self._client_override
        else:
            http_client = cfg.get("http_client")  # type: ignore[union-attr]
            client = SemanticScholarClient(
                timeout=float(timeout), api_key=api_key, http_client=http_client
            )

        self._client = client
        adapter = SemanticScholarSourceAdapter(client)
        ctx.register("literature_source.semantic_scholar", adapter)

    async def stop(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass

    async def teardown(self) -> None:
        self._client = None
