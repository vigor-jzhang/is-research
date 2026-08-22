"""Phase 2F evidence extractor — page-bounded structured extraction from FullTextDocument text.

Bounded deterministic chunks preserving page boundaries; strict Pydantic output;
grounding validation (pages exist, pages supplied, category valid, non-empty).
No chain-of-thought, no cross-paper synthesis.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata

logger = logging.getLogger(__name__)


class EvidenceExtractionChunk(BaseModel):
    """Page-bounded chunk of extracted full-text pages."""

    document_id: str = Field(description="FullTextDocument artifact id")
    chunk_index: int = Field(ge=0)
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)
    page_texts: list[dict[str, Any]] = Field(
        description="List of {'page': int, 'text': str} within the chunk"
    )

    model_config = {"extra": "forbid"}

    @property
    def pages(self) -> list[int]:
        return [p["page"] for p in self.page_texts]

    @property
    def text(self) -> str:
        parts = []
        for p in self.page_texts:
            parts.append(f"[Page {p['page']}]\n{p.get('text', '')}")
        return "\n\n".join(parts)

    def contains_page(self, page: int) -> bool:
        return self.start_page <= page <= self.end_page


class EvidenceCandidate(BaseModel):
    """Strict structured model output for a single evidence item."""

    category: str
    statement: str
    page_numbers: list[int] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    excerpt: str | None = Field(default=None, description="Short source-support excerpt")

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        from research_harness.research.schemas.evidence import EvidenceCategory

        if v not in EvidenceCategory.values():
            raise ValueError(f"invalid evidence category {v!r}")
        return v

    @field_validator("statement")
    @classmethod
    def validate_statement(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("statement must be non-empty")
        return v

    model_config = {"extra": "forbid"}


def chunk_pages(
    document_id: str, pages: list[dict[str, Any]], pages_per_chunk: int
) -> list[EvidenceExtractionChunk]:
    """Deterministic page-bounded chunking preserving page boundaries.

    pages: sorted list of {'page': int(1-based), 'text': str} from FullTextDocument text blob.
    Returns chunks of consecutive pages, each within [start_page, end_page].
    """
    if pages_per_chunk < 1:
        raise ValueError("pages_per_chunk must be >= 1")
    if not pages:
        return []
    ordered = sorted(pages, key=lambda p: p["page"])
    chunks: list[EvidenceExtractionChunk] = []
    for idx, i in enumerate(range(0, len(ordered), pages_per_chunk)):
        group = ordered[i : i + pages_per_chunk]
        start = group[0]["page"]
        end = group[-1]["page"]
        chunks.append(
            EvidenceExtractionChunk(
                document_id=document_id,
                chunk_index=idx,
                start_page=start,
                end_page=end,
                page_texts=group,
            )
        )
    return chunks


class EvidenceExtractorService:
    def __init__(
        self,
        model_router: Any,
        artifact_store: Any,
        blob_store: Any,
        model_role: str = "reasoning",
    ) -> None:
        self._router = model_router
        self._store = artifact_store
        self._blobs = blob_store
        self._model_role = model_role

    @property
    def extractor_id(self) -> str:
        return "literature.evidence_extractor"

    def _build_schema(self) -> dict[str, Any]:
        from research_harness.research.schemas.evidence import EvidenceCategory

        return {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": EvidenceCategory.values(),
                            },
                            "statement": {"type": "string"},
                            "page_numbers": {
                                "type": "array",
                                "items": {"type": "integer", "minimum": 1},
                            },
                            "confidence": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 1,
                            },
                            "excerpt": {"type": "string"},
                        },
                        "required": [
                            "category",
                            "statement",
                            "page_numbers",
                            "confidence",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        }

    async def extract_chunk(self, chunk: EvidenceExtractionChunk) -> list[EvidenceCandidate]:
        """Run structured extraction for one page-bounded chunk.

        Validates every candidate against the chunk (grounding). Raises
        ValueError on malformed/ungrounded output; returns [] for empty.
        """
        prompt = f"""You are a research reader extracting structured evidence from a scholarly paper.

You are given pages {chunk.start_page}-{chunk.end_page} of FullTextDocument {chunk.document_id}.

Task: Extract only evidence that is directly supported by the provided text.
- Cite exact page numbers that are within {chunk.start_page}-{chunk.end_page}.
- Do NOT cite pages you were not given.
- Do NOT invent, summarize beyond the text, or speculate.
- Categories: research_question, theory, construct, mechanism, assumption, method, data, variable, finding, result, boundary_condition, limitation, future_research.
- Provide a short one-line excerpt (optional) supporting each item; the durable representation is the statement, not the excerpt.
- Do not reason step by step. Return JSON only, matching the schema.

Text:
{chunk.text}
"""
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content="You are a careful evidence extractor. Return valid JSON matching the requested schema. Never include chain-of-thought.",
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self._build_schema(),
            temperature=0.0,
            metadata={
                "document_id": chunk.document_id,
                "start_page": chunk.start_page,
                "end_page": chunk.end_page,
            },
        )

        try:
            response = await self._router.complete(self._model_role, request)
        except Exception as e:
            raise RuntimeError(
                f"evidence model call failed (role {self._model_role!r}): {e}"
            ) from e

        content = response.message.content or ""
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise ValueError(f"evidence model returned invalid JSON: {content[:500]!r}: {e}") from e

        items = data.get("items", [])
        candidates: list[EvidenceCandidate] = []
        for raw in items:
            try:
                cand = EvidenceCandidate.model_validate(raw)
            except Exception as e:
                raise ValueError(f"invalid evidence candidate {raw!r}: {e}") from e
            # Grounding: every cited page must be within the supplied chunk
            for p in cand.page_numbers:
                if not chunk.contains_page(p):
                    raise ValueError(
                        f"evidence page {p} outside supplied chunk {chunk.start_page}-{chunk.end_page}"
                    )
            candidates.append(cand)
        return candidates


class EvidenceExtractorPlugin(Plugin):
    def __init__(self, model_role: str | None = None) -> None:
        self._model_role_override = model_role
        self._service: EvidenceExtractorService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="literature.evidence_extractor",
            version="0.1.0",
            plugin_type="literature",
            description="Page-bounded structured evidence extractor (Phase 2F)",
            provides=["evidence_extractor.default"],
            requires=["model_router.default", "artifact_store.default", "blob_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        lit_cfg: dict[str, Any] = {}
        if "literature" in cfg and isinstance(cfg["literature"], dict):
            lit_cfg = (
                cfg["literature"].get("evidence", {})
                if isinstance(cfg["literature"].get("evidence"), dict)
                else {}
            )
        model_role = self._model_role_override or lit_cfg.get("model_role") or "reasoning"
        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        blobs = ctx.require("blob_store.default")
        self._service = EvidenceExtractorService(
            model_router=router,
            artifact_store=store,
            blob_store=blobs,
            model_role=str(model_role),
        )
        ctx.register("evidence_extractor.default", self._service)
