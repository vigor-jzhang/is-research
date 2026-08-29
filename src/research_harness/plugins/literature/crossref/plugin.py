"""Crossref literature source plugin."""

from __future__ import annotations

import os
from typing import Any

from research_harness.contracts.literature import LiteratureSource
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.plugins.literature.crossref.client import CrossrefClient


class CrossrefSourceAdapter(LiteratureSource):
    """Adapter that exposes CrossrefClient as LiteratureSource."""

    def __init__(self, client: CrossrefClient) -> None:
        self._client = client

    @property
    def provider_name(self) -> str:
        return "crossref"

    async def search(self, request):  # type: ignore[no-untyped-def]
        return await self._client.search(request)

    async def get(self, identifier: str):  # type: ignore[no-untyped-def]
        return await self._client.get(identifier)


class CrossrefPlugin(Plugin):
    def __init__(self, client: CrossrefClient | None = None) -> None:
        self._client_override = client
        self._client: CrossrefClient | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="literature.crossref",
            version="0.1.0",
            plugin_type="literature_source",
            description="Crossref literature source",
            provides=["literature_source.crossref"],
            requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        # cfg may be {"literature": {"crossref": {...}}} or {"crossref": {...}} or flat
        crossref_cfg: dict[str, Any] = {}
        if "literature" in cfg and isinstance(cfg["literature"], dict):
            crossref_cfg = (
                cfg["literature"].get("crossref", {})
                if isinstance(cfg["literature"].get("crossref"), dict)
                else {}
            )
        elif "crossref" in cfg and isinstance(cfg["crossref"], dict):
            crossref_cfg = cfg["crossref"]
        else:
            crossref_cfg = {
                k: v
                for k, v in cfg.items()
                if k in ("timeout_seconds", "timeout", "mailto", "enabled")
            }

        if crossref_cfg.get("enabled") is False:
            return

        timeout = crossref_cfg.get("timeout_seconds") or crossref_cfg.get("timeout") or 20.0
        mailto = crossref_cfg.get("mailto") or os.getenv("CROSSREF_MAILTO")

        if self._client_override is not None:
            client = self._client_override
        else:
            # Allow http_client injection for tests via config
            http_client = cfg.get("http_client")  # type: ignore[union-attr]
            client = CrossrefClient(timeout=float(timeout), mailto=mailto, http_client=http_client)

        self._client = client
        adapter = CrossrefSourceAdapter(client)
        ctx.register("literature_source.crossref", adapter)

    async def stop(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass

    async def teardown(self) -> None:
        self._client = None
