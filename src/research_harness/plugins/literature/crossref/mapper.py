"""Pure mapper: Crossref JSON → PaperRecord."""

from __future__ import annotations

import re
from typing import Any

from research_harness.research.schemas.common import ExternalIdentifier, normalize_doi
from research_harness.research.schemas.paper import Author, PaperRecord


def _clean_abstract(raw: str | None) -> str | None:
    if not raw:
        return None
    # Crossref abstract may contain JATS tags like <jats:p>, <jats:sec>
    # Conservative cleaning: strip tags, decode entities minimally
    # Remove tags
    text = re.sub(r"<[^>]+>", "", raw)
    # Replace common entities
    text = (
        text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    )
    text = text.replace("&#39;", "'").replace("&apos;", "'")
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text if text else None


def _extract_year(item: dict[str, Any]) -> int | None:
    # Try published-print, published-online, created, issued
    for key in ("published-print", "published-online", "published", "created", "issued"):
        val = item.get(key)
        if isinstance(val, dict):
            dp = val.get("date-parts")
            if isinstance(dp, list) and dp and isinstance(dp[0], list) and dp[0]:
                try:
                    year = int(dp[0][0])
                    if 1000 <= year <= 3000:
                        return year
                except Exception:
                    continue
    # Try alternative: 'issued' is common
    issued = item.get("issued")
    if isinstance(issued, dict):
        dp = issued.get("date-parts")
        if isinstance(dp, list) and dp and dp[0]:
            try:
                return int(dp[0][0])
            except Exception:
                pass
    # Try 'created' date-time string?
    created = item.get("created")
    if isinstance(created, dict):
        dt = created.get("date-time")
        if isinstance(dt, str) and len(dt) >= 4:
            try:
                return int(dt[:4])
            except Exception:
                pass
    return None


def _extract_authors(item: dict[str, Any]) -> list[Author]:
    authors: list[Author] = []
    raw_authors = item.get("author")
    if not isinstance(raw_authors, list):
        return authors
    for a in raw_authors:
        if not isinstance(a, dict):
            continue
        given = a.get("given", "")
        family = a.get("family", "")
        name = ""
        if given and family:
            name = f"{given} {family}".strip()
        elif family:
            name = family
        elif given:
            name = given
        elif a.get("name"):
            name = a.get("name", "")
        else:
            continue
        # ORCID
        ext_ids: list[ExternalIdentifier] = []
        orcid = a.get("ORCID")
        if isinstance(orcid, str) and orcid.strip():
            # ORCID URL like https://orcid.org/0000-...
            # Extract value
            val = orcid.strip().split("/")[-1]
            ext_ids.append(ExternalIdentifier(scheme="orcid", value=val))
        authors.append(Author(name=name, external_ids=ext_ids))
    return authors


def _extract_venue(item: dict[str, Any]) -> str | None:
    ct = item.get("container-title")
    if isinstance(ct, list) and ct:
        # Take first non-empty
        for v in ct:
            if isinstance(v, str) and v.strip():
                return v.strip()
    # Fallback: short-container-title
    sct = item.get("short-container-title")
    if isinstance(sct, list) and sct:
        for v in sct:
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def map_crossref_work(item: dict[str, Any]) -> tuple[PaperRecord, dict[str, Any]]:
    """Map a single Crossref work item to PaperRecord.

    Returns (paper, raw_item) where raw_item is the original for snapshot.
    Pure function, no HTTP.
    """
    # Title is list
    title_raw = item.get("title")
    title = ""
    if isinstance(title_raw, list) and title_raw:
        # Take first title
        t = title_raw[0]
        if isinstance(t, str):
            title = t.strip()
    elif isinstance(title_raw, str):
        title = title_raw.strip()
    if not title:
        title = "Untitled"

    doi_raw = item.get("DOI")
    doi = None
    if isinstance(doi_raw, str) and doi_raw.strip():
        doi = normalize_doi(doi_raw)

    authors = _extract_authors(item)
    year = _extract_year(item)
    venue = _extract_venue(item)
    abstract = _clean_abstract(
        item.get("abstract") if isinstance(item.get("abstract"), str) else None
    )
    url = item.get("URL") if isinstance(item.get("URL"), str) else None
    pub_type = item.get("type") if isinstance(item.get("type"), str) else None

    # External identifiers
    ext_ids: list[ExternalIdentifier] = []
    if doi:
        ext_ids.append(ExternalIdentifier(scheme="doi", value=doi))
    # Crossref may have other IDs like ISBN, but keep minimal

    # Preserve searchable raw? But raw is stored separately in snapshot; PaperRecord.metadata can keep small provider-specific values if justified
    # For now, keep metadata minimal
    metadata: dict[str, Any] = {}
    # Example: keep Crossref member/publisher if needed? But spec says not to put full raw there
    # We can store publisher if available
    publisher = item.get("publisher")
    if isinstance(publisher, str):
        metadata["crossref_publisher"] = publisher
    # Keep container type
    if pub_type:
        metadata["crossref_type"] = pub_type

    paper = PaperRecord(
        title=title,
        authors=authors,
        year=year,
        venue=venue,
        abstract=abstract,
        doi=doi,
        external_identifiers=ext_ids,
        url=url,
        open_access_url=None,  # Crossref doesn't directly provide OA URL in simple case; could use link
        publication_type=pub_type,
        metadata=metadata,
    )
    return paper, item
