"""Crossref HTTP client — httpx, no SDK."""

from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Any

import httpx

from research_harness.contracts.literature import (
    LiteratureNotFoundError,
    LiteratureRateLimitError,
    LiteratureResponseError,
    LiteratureSearchHit,
    LiteratureSearchPage,
    LiteratureSearchRequest,
    LiteratureSourceError,
)
from research_harness.plugins.literature.crossref.mapper import map_crossref_work

logger = logging.getLogger(__name__)

CROSSREF_BASE = "https://api.crossref.org/works"
DEFAULT_TIMEOUT = 20.0
MAX_RETRIES = 3


class CrossrefClient:
    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT,
        mailto: str | None = None,
        user_agent: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        sleep: Any = None,
    ) -> None:
        self.timeout = timeout
        self.mailto = mailto or os.getenv("CROSSREF_MAILTO")
        self.user_agent = (
            user_agent or f"research-harness/0.1.0 (mailto:{self.mailto})"
            if self.mailto
            else "research-harness/0.1.0"
        )
        self._client = http_client
        self._owns_client = http_client is None
        self._sleep = sleep or asyncio.sleep

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(timeout=self.timeout)

    def _build_headers(self) -> dict[str, str]:
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        return headers

    def _build_search_params(self, request: LiteratureSearchRequest) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if request.query:
            params["query"] = request.query
        # rows = limit
        if request.limit is not None:
            params["rows"] = str(request.limit)
        # offset via page_token (opaque, we encode as offset int string)
        offset = 0
        if request.page_token:
            try:
                offset = int(request.page_token)
            except ValueError:
                offset = 0
        if offset:
            params["offset"] = str(offset)
        # Filters for year
        filters: list[str] = []
        if request.year_from is not None:
            filters.append(f"from-pub-date:{request.year_from}-01-01")
        if request.year_to is not None:
            filters.append(f"until-pub-date:{request.year_to}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        # Polite pool
        if self.mailto:
            params["mailto"] = self.mailto
        return params

    async def search(self, request: LiteratureSearchRequest) -> LiteratureSearchPage:
        params = self._build_search_params(request)
        url = CROSSREF_BASE
        headers = self._build_headers()

        # Retry loop
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                client = self._get_client()
                resp = await client.get(url, params=params, headers=headers, timeout=self.timeout)
                # Handle status codes
                if resp.status_code == 400:
                    raise LiteratureResponseError(f"Crossref bad request (400): {resp.text}")
                if resp.status_code == 404:
                    raise LiteratureNotFoundError(f"Crossref not found (404): {resp.text}")
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
                        logger.warning(
                            "Crossref rate limited, retry after %s (attempt %d)",
                            sleep_for,
                            attempt + 1,
                        )
                        await self._sleep(sleep_for)
                        continue
                    raise LiteratureRateLimitError(
                        f"Crossref rate limited (429): {resp.text}", retry_after=retry_after
                    )
                if resp.status_code >= 500:
                    if attempt < MAX_RETRIES:
                        sleep_for = 2**attempt + random.uniform(0, 1)
                        # Check Retry-After
                        if "Retry-After" in resp.headers:
                            try:
                                sleep_for = float(resp.headers["Retry-After"])
                            except ValueError:
                                pass
                        logger.warning(
                            "Crossref 5xx %s, retry (attempt %d) after %s",
                            resp.status_code,
                            attempt + 1,
                            sleep_for,
                        )
                        await self._sleep(sleep_for)
                        continue
                    raise LiteratureResponseError(
                        f"Crossref server error {resp.status_code}: {resp.text}"
                    )
                if resp.status_code >= 400:
                    raise LiteratureResponseError(f"Crossref error {resp.status_code}: {resp.text}")

                data = resp.json()
                # Validate schema
                if not isinstance(data, dict):
                    raise LiteratureResponseError(f"Crossref malformed response: not dict: {data}")
                message = data.get("message")
                if not isinstance(message, dict):
                    raise LiteratureResponseError(f"Crossref malformed: missing message: {data}")
                items = message.get("items")
                if items is None:
                    # Some responses use message directly as item?
                    items = []
                if not isinstance(items, list):
                    raise LiteratureResponseError(f"Crossref malformed: items not list: {data}")

                total = message.get("total-results")
                if not isinstance(total, int):
                    total = None
                    # Try header?
                    if "total-results" in message:
                        try:
                            total = int(message["total-results"])
                        except Exception:
                            total = None

                # Map items
                hits: list[LiteratureSearchHit] = []
                for idx, raw in enumerate(items):
                    if not isinstance(raw, dict):
                        logger.warning("Crossref skipping non-dict item %r", raw)
                        continue
                    try:
                        paper, _ = map_crossref_work(raw)
                        # Provider record id is DOI
                        doi_val = raw.get("DOI")
                        doi = doi_val if isinstance(doi_val, str) else None
                        # Use DOI or fallback to URL or index
                        provider_id = (
                            doi if isinstance(doi, str) and doi.strip() else f"crossref:{idx}"
                        )
                        hit = LiteratureSearchHit(
                            paper=paper,
                            raw_payload=raw,
                            provider="crossref",
                            provider_record_id=provider_id,
                            rank=idx,
                            score=None,
                            metadata={},
                        )
                        hits.append(hit)
                    except Exception as e:
                        logger.warning("Crossref skipping malformed item: %s %r", e, raw)
                        continue

                # Next page token: offset + rows
                next_token = None
                if request.limit is not None and len(hits) == request.limit:
                    current_offset = int(params.get("offset", "0"))
                    next_token = str(current_offset + request.limit)
                    # If total known and next would exceed, no token
                    if total is not None and current_offset + request.limit >= total:
                        next_token = None

                return LiteratureSearchPage(
                    provider="crossref",
                    hits=hits,
                    total_estimate=total,
                    next_page_token=next_token,
                    metadata={"query": request.query, "params": params},
                )

            except httpx.TimeoutException as e:
                last_exc = e
                if attempt < MAX_RETRIES:
                    sleep_for = 2**attempt + random.uniform(0, 1)
                    logger.warning("Crossref timeout (attempt %d): %s", attempt + 1, e)
                    await self._sleep(sleep_for)
                    continue
                raise LiteratureSourceError(f"Crossref timeout after {self.timeout}s: {e}") from e
            except httpx.ConnectError as e:
                last_exc = e
                if attempt < MAX_RETRIES:
                    sleep_for = 2**attempt + random.uniform(0, 1)
                    logger.warning("Crossref connect error (attempt %d): %s", attempt + 1, e)
                    await self._sleep(sleep_for)
                    continue
                raise LiteratureSourceError(f"Crossref connect failed: {e}") from e
            except LiteratureSourceError:
                raise
            except Exception as e:
                if isinstance(e, httpx.TransportError) and attempt < MAX_RETRIES:
                    sleep_for = 2**attempt + random.uniform(0, 1)
                    logger.warning("Crossref transport error (attempt %d): %s", attempt + 1, e)
                    await self._sleep(sleep_for)
                    continue
                raise LiteratureSourceError(f"Crossref unexpected error: {e}") from e

        raise LiteratureSourceError(f"Crossref failed after retries: {last_exc}")

    async def get(self, identifier: str) -> LiteratureSearchHit:
        # identifier is DOI, may be bare or URL
        from research_harness.research.schemas.common import normalize_doi

        doi = normalize_doi(identifier)
        # Crossref expects DOI as path, not URL-encoded? Use raw DOI
        # Ensure we use original DOI with slash, but encode?
        # httpx will handle path encoding if we pass as URL
        url = f"{CROSSREF_BASE}/{doi}"
        params: dict[str, Any] = {}
        if self.mailto:
            params["mailto"] = self.mailto
        headers = self._build_headers()

        for attempt in range(MAX_RETRIES + 1):
            try:
                client = self._get_client()
                resp = await client.get(url, params=params, headers=headers, timeout=self.timeout)
                if resp.status_code == 404:
                    raise LiteratureNotFoundError(f"Crossref DOI not found {doi!r}: {resp.text}")
                if resp.status_code == 400:
                    raise LiteratureResponseError(
                        f"Crossref bad request for DOI {doi!r}: {resp.text}"
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
                        f"Crossref rate limited for DOI {doi!r}", retry_after=retry_after
                    )
                if resp.status_code >= 500:
                    if attempt < MAX_RETRIES:
                        sleep_for = 2**attempt + random.uniform(0, 1)
                        await self._sleep(sleep_for)
                        continue
                    raise LiteratureResponseError(
                        f"Crossref server error {resp.status_code} for DOI {doi!r}: {resp.text}"
                    )
                if resp.status_code >= 400:
                    raise LiteratureResponseError(
                        f"Crossref error {resp.status_code} for DOI {doi!r}: {resp.text}"
                    )

                data = resp.json()
                # Crossref get returns message as single work
                message = data.get("message")
                if not isinstance(message, dict):
                    raise LiteratureResponseError(
                        f"Crossref malformed get response for {doi!r}: {data}"
                    )
                # It may be that message itself is the work, or message contains work?
                # For works/{doi}, message is the work dict
                work = message
                # Heuristic: if message has "items", it's list; else it's work
                if "items" in work and isinstance(work["items"], list):
                    # Unexpected, but take first
                    if not work["items"]:
                        raise LiteratureNotFoundError(f"Crossref DOI not found {doi!r}")
                    work = work["items"][0]

                paper, _ = map_crossref_work(work)
                return LiteratureSearchHit(
                    paper=paper,
                    raw_payload=work,
                    provider="crossref",
                    provider_record_id=doi,
                    rank=None,
                    score=None,
                    metadata={},
                )

            except httpx.TimeoutException as e:
                if attempt < MAX_RETRIES:
                    await self._sleep(2**attempt + random.uniform(0, 1))
                    continue
                raise LiteratureSourceError(f"Crossref timeout for DOI {doi!r}: {e}") from e
            except httpx.ConnectError as e:
                if attempt < MAX_RETRIES:
                    await self._sleep(2**attempt + random.uniform(0, 1))
                    continue
                raise LiteratureSourceError(f"Crossref connect failed for DOI {doi!r}: {e}") from e
            except LiteratureSourceError:
                raise
            except Exception as e:
                if isinstance(e, httpx.TransportError) and attempt < MAX_RETRIES:
                    await self._sleep(2**attempt + random.uniform(0, 1))
                    continue
                raise LiteratureSourceError(f"Crossref get failed for {doi!r}: {e}") from e

        raise LiteratureSourceError(f"Crossref get failed after retries for {doi!r}")

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
