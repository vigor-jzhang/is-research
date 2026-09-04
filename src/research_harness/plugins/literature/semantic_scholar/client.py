"""Semantic Scholar HTTP client — httpx, no SDK."""

from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Any

import httpx

from research_harness.contracts.literature import (
    LiteratureAuthenticationError,
    LiteratureNotFoundError,
    LiteratureRateLimitError,
    LiteratureResponseError,
    LiteratureSearchHit,
    LiteratureSearchPage,
    LiteratureSearchRequest,
    LiteratureSourceError,
)
from research_harness.plugins.literature.semantic_scholar.mapper import map_semantic_scholar_paper

logger = logging.getLogger(__name__)

SEMANTIC_SCHOLAR_BASE = "https://api.semanticscholar.org/graph/v1"
DEFAULT_TIMEOUT = 20.0
MAX_RETRIES = 3
DEFAULT_FIELDS = "paperId,corpusId,externalIds,title,abstract,year,venue,publicationTypes,publicationDate,authors,url,openAccessPdf"


class SemanticScholarClient:
    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        api_key: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        sleep: Any = None,
    ) -> None:
        self.timeout = timeout
        self.api_key = api_key if api_key is not None else os.getenv("SEMANTIC_SCHOLAR_API_KEY")
        self._client = http_client
        self._owns_client = http_client is None
        self._sleep = sleep or asyncio.sleep

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    async def search(self, request: LiteratureSearchRequest) -> LiteratureSearchPage:
        # Build params
        offset = 0
        if request.page_token:
            try:
                offset = int(request.page_token)
            except ValueError:
                offset = 0
        params: dict[str, Any] = {
            "query": request.query,
            "offset": str(offset),
            "limit": str(request.limit or 10),
            "fields": DEFAULT_FIELDS,
        }
        # Year filters: Semantic Scholar supports year filter via query? For now handle via post-filtering or via search?
        # The API supports `year` filter? We'll pass as part of query metadata but not rely
        url = f"{SEMANTIC_SCHOLAR_BASE}/paper/search"
        headers = self._headers()

        for attempt in range(MAX_RETRIES + 1):
            try:
                client = self._get_client()
                resp = await client.get(url, params=params, headers=headers, timeout=self.timeout)

                if resp.status_code == 400:
                    raise LiteratureResponseError(
                        f"Semantic Scholar bad request (400): {resp.text}"
                    )
                if resp.status_code == 401:
                    raise LiteratureAuthenticationError(
                        f"Semantic Scholar authentication failed (401): {resp.text}"
                    )
                if resp.status_code == 403:
                    raise LiteratureAuthenticationError(
                        f"Semantic Scholar forbidden (403): {resp.text}"
                    )
                if resp.status_code == 404:
                    # Search 404 means no results? But for search, 404 should be treated as empty? However spec says 404 is not found
                    # For search, we can return empty page if no results, but 404 from search likely means bad query
                    raise LiteratureNotFoundError(
                        f"Semantic Scholar search not found (404): {resp.text}"
                    )
                if resp.status_code == 429:
                    retry_after = None
                    if "Retry-After" in resp.headers:
                        try:
                            retry_after = float(resp.headers["Retry-After"])
                        except ValueError:
                            retry_after = None
                    if attempt < MAX_RETRIES:
                        sleep_for = (
                            retry_after
                            if retry_after is not None
                            else (2**attempt + random.uniform(0, 1))
                        )
                        logger.warning("Semantic Scholar rate limited, retry after %s", sleep_for)
                        await self._sleep(sleep_for)
                        continue
                    raise LiteratureRateLimitError(
                        f"Semantic Scholar rate limited (429): {resp.text}", retry_after=retry_after
                    )
                if resp.status_code >= 500:
                    if attempt < MAX_RETRIES:
                        sleep_for = 2**attempt + random.uniform(0, 1)
                        if "Retry-After" in resp.headers:
                            try:
                                sleep_for = float(resp.headers["Retry-After"])
                            except ValueError:
                                pass
                        logger.warning(
                            "Semantic Scholar 5xx %s retry after %s", resp.status_code, sleep_for
                        )
                        await self._sleep(sleep_for)
                        continue
                    raise LiteratureResponseError(
                        f"Semantic Scholar server error {resp.status_code}: {resp.text}"
                    )
                if resp.status_code >= 400:
                    raise LiteratureResponseError(
                        f"Semantic Scholar error {resp.status_code}: {resp.text}"
                    )

                data = resp.json()
                if not isinstance(data, dict):
                    raise LiteratureResponseError(f"Semantic Scholar malformed: not dict {data}")

                # Handle error envelope
                if "error" in data:
                    raise LiteratureResponseError(f"Semantic Scholar error envelope: {data}")

                total = data.get("total")
                if not isinstance(total, int):
                    total = None

                raw_data = data.get("data")
                if raw_data is None:
                    # Some responses use 'papers' or empty?
                    raw_data = []
                if not isinstance(raw_data, list):
                    raise LiteratureResponseError(
                        f"Semantic Scholar malformed: data not list {data}"
                    )

                # Filter by year if requested (since API may not support year filter directly in search)
                hits: list[LiteratureSearchHit] = []
                for idx, raw in enumerate(raw_data):
                    if not isinstance(raw, dict):
                        continue
                    # Year filtering post-hoc if needed
                    if request.year_from is not None or request.year_to is not None:
                        y = raw.get("year")
                        if isinstance(y, int):
                            if request.year_from is not None and y < request.year_from:
                                continue
                            if request.year_to is not None and y > request.year_to:
                                continue
                    try:
                        paper, _ = map_semantic_scholar_paper(raw)
                        paper_id = (
                            raw.get("paperId")
                            if isinstance(raw.get("paperId"), str)
                            else f"ss:{idx}"
                        )
                        provider_id = (
                            paper_id
                            if isinstance(paper_id, str) and paper_id.strip()
                            else f"ss:{idx}"
                        )
                        hit = LiteratureSearchHit(
                            paper=paper,
                            raw_payload=raw,
                            provider="semantic_scholar",
                            provider_record_id=provider_id,
                            rank=offset + idx,
                            score=None,  # Semantic Scholar search doesn't provide score in basic response
                            metadata={},
                        )
                        hits.append(hit)
                    except Exception as e:
                        logger.warning("Semantic Scholar skipping malformed item: %s %r", e, raw)
                        continue

                # Continuation must key off the RAW page size, not the filtered
                # hit count: the year post-filter and skipped malformed items
                # above routinely leave `len(hits)` below the limit when the
                # provider still has more pages, which silently truncated
                # results to one page (H19).
                next_token = None
                page_size = request.limit if request.limit is not None else len(raw_data)
                if len(raw_data) >= page_size and page_size > 0:
                    # Check if more available
                    if total is None or offset + page_size < total:
                        next_token = str(offset + page_size)

                return LiteratureSearchPage(
                    provider="semantic_scholar",
                    hits=hits,
                    total_estimate=total,
                    next_page_token=next_token,
                    metadata={"query": request.query, "offset": offset},
                )

            except httpx.TimeoutException as e:
                if attempt < MAX_RETRIES:
                    await self._sleep(2**attempt + random.uniform(0, 1))
                    continue
                raise LiteratureSourceError(f"Semantic Scholar timeout: {e}") from e
            except httpx.ConnectError as e:
                if attempt < MAX_RETRIES:
                    await self._sleep(2**attempt + random.uniform(0, 1))
                    continue
                raise LiteratureSourceError(f"Semantic Scholar connect failed: {e}") from e
            except LiteratureSourceError:
                raise
            except Exception as e:
                if isinstance(e, httpx.TransportError) and attempt < MAX_RETRIES:
                    await self._sleep(2**attempt + random.uniform(0, 1))
                    continue
                raise LiteratureSourceError(f"Semantic Scholar unexpected error: {e}") from e

        raise LiteratureSourceError("Semantic Scholar search failed after retries")

    async def get(self, identifier: str) -> LiteratureSearchHit:
        # identifier may be paperId, DOI, or prefixed DOI:10...
        # Semantic Scholar supports GET /paper/{paperId} where paperId can be "DOI:10.123/abc"
        # If identifier looks like DOI, prefix with DOI:
        raw_id = identifier.strip()
        # Check if it's DOI-like (10.xxx/)
        paper_id = raw_id
        if raw_id.lower().startswith("doi:"):
            paper_id = raw_id
        elif raw_id.startswith("10."):
            paper_id = f"DOI:{raw_id}"
        # else assume it's paperId as is (e.g., 649def34...)

        url = f"{SEMANTIC_SCHOLAR_BASE}/paper/{paper_id}"
        params = {"fields": DEFAULT_FIELDS}
        headers = self._headers()

        for attempt in range(MAX_RETRIES + 1):
            try:
                client = self._get_client()
                resp = await client.get(url, params=params, headers=headers, timeout=self.timeout)

                if resp.status_code == 404:
                    raise LiteratureNotFoundError(
                        f"Semantic Scholar paper not found {identifier!r}: {resp.text}"
                    )
                if resp.status_code == 401:
                    raise LiteratureAuthenticationError(
                        f"Semantic Scholar auth failed (401): {resp.text}"
                    )
                if resp.status_code == 403:
                    raise LiteratureAuthenticationError(
                        f"Semantic Scholar forbidden (403): {resp.text}"
                    )
                if resp.status_code == 429:
                    retry_after = None
                    if "Retry-After" in resp.headers:
                        try:
                            retry_after = float(resp.headers["Retry-After"])
                        except ValueError:
                            pass
                    if attempt < MAX_RETRIES:
                        sleep_for = (
                            retry_after
                            if retry_after is not None
                            else (2**attempt + random.uniform(0, 1))
                        )
                        await self._sleep(sleep_for)
                        continue
                    raise LiteratureRateLimitError(
                        f"Semantic Scholar rate limited for {identifier!r}", retry_after=retry_after
                    )
                if resp.status_code >= 500:
                    if attempt < MAX_RETRIES:
                        await self._sleep(2**attempt + random.uniform(0, 1))
                        continue
                    raise LiteratureResponseError(
                        f"Semantic Scholar server error {resp.status_code} for {identifier!r}: {resp.text}"
                    )
                if resp.status_code >= 400:
                    raise LiteratureResponseError(
                        f"Semantic Scholar error {resp.status_code} for {identifier!r}: {resp.text}"
                    )

                data = resp.json()
                if not isinstance(data, dict):
                    raise LiteratureResponseError(
                        f"Semantic Scholar malformed get for {identifier!r}: not dict {data}"
                    )
                if "error" in data:
                    raise LiteratureResponseError(
                        f"Semantic Scholar error envelope for {identifier!r}: {data}"
                    )

                # Single paper dict
                if "paperId" not in data and "title" not in data:
                    raise LiteratureResponseError(
                        f"Semantic Scholar malformed get for {identifier!r}: missing paperId/title {data}"
                    )

                paper, _ = map_semantic_scholar_paper(data)
                provider_id = (
                    data.get("paperId") if isinstance(data.get("paperId"), str) else identifier
                )
                return LiteratureSearchHit(
                    paper=paper,
                    raw_payload=data,
                    provider="semantic_scholar",
                    provider_record_id=provider_id,  # type: ignore[arg-type]
                    rank=None,
                    score=None,
                    metadata={},
                )

            except httpx.TimeoutException as e:
                if attempt < MAX_RETRIES:
                    await self._sleep(2**attempt + random.uniform(0, 1))
                    continue
                raise LiteratureSourceError(
                    f"Semantic Scholar timeout for {identifier!r}: {e}"
                ) from e
            except httpx.ConnectError as e:
                if attempt < MAX_RETRIES:
                    await self._sleep(2**attempt + random.uniform(0, 1))
                    continue
                raise LiteratureSourceError(
                    f"Semantic Scholar connect failed for {identifier!r}: {e}"
                ) from e
            except LiteratureSourceError:
                raise
            except Exception as e:
                if isinstance(e, httpx.TransportError) and attempt < MAX_RETRIES:
                    await self._sleep(2**attempt + random.uniform(0, 1))
                    continue
                raise LiteratureSourceError(
                    f"Semantic Scholar get failed for {identifier!r}: {e}"
                ) from e

        raise LiteratureSourceError(f"Semantic Scholar get failed after retries for {identifier!r}")

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
