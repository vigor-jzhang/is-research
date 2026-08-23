"""Manuscript exporter contract — deterministic rendering of a formatted
manuscript into a file format, stored through the BlobStore.

New exporters (APA-style journals, other formats) implement this protocol and
register a `manuscript_exporter.<format>` service.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from research_harness.research.schemas.publication import FormattedManuscript


class ExportPayload(BaseModel):
    """Result of a deterministic export."""

    format: str
    renderer: str
    renderer_version: str
    data: bytes
    media_type: str
    content_hash: str = Field(description="sha256 hex of `data`")

    model_config = {"extra": "forbid"}


class ManuscriptExporter(Protocol):
    """Protocol for format exporters."""

    @property
    def format(self) -> str: ...

    @property
    def renderer(self) -> str: ...

    @property
    def renderer_version(self) -> str: ...

    def render(self, manuscript: FormattedManuscript, profile: Any) -> ExportPayload:
        """Render deterministically. Must not modify the manuscript."""
        ...


class ExportPayloadBuilder:
    """Shared helpers for exporters (content hashing)."""

    @staticmethod
    def build(
        fmt: str, renderer: str, renderer_version: str, data: bytes, media_type: str
    ) -> ExportPayload:
        import hashlib

        return ExportPayload(
            format=fmt,
            renderer=renderer,
            renderer_version=renderer_version,
            data=data,
            media_type=media_type,
            content_hash=hashlib.sha256(data).hexdigest(),
        )
