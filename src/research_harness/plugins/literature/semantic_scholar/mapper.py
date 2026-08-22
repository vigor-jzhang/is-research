"""Pure mapper: Semantic Scholar JSON → PaperRecord."""

from __future__ import annotations

from typing import Any

from research_harness.research.schemas.common import ExternalIdentifier, normalize_doi
from research_harness.research.schemas.paper import Author, PaperRecord


def _extract_doi(external_ids: dict[str, Any] | None, doi_field: str | None = None) -> str | None:
    doi = None
    if isinstance(doi_field, str) and doi_field.strip():
        doi = doi_field.strip()
    elif isinstance(external_ids, dict):
        # externalIds may contain DOI
        doi = external_ids.get("DOI")
        if isinstance(doi, str) and doi.strip():
            doi = doi.strip()
        else:
            # Try lowercase
            for k, v in external_ids.items():
                if k.lower() == "doi" and isinstance(v, str) and v.strip():
                    doi = v.strip()
                    break
    if doi:
        return normalize_doi(doi)
    return None


def map_semantic_scholar_paper(item: dict[str, Any]) -> tuple[PaperRecord, dict[str, Any]]:
    """Map a single Semantic Scholar paper dict to PaperRecord.

    Pure function, no HTTP.
    """
    paper_id = item.get("paperId") if isinstance(item.get("paperId"), str) else ""
    corpus_id = item.get("corpusId")
    title = item.get("title")
    if not isinstance(title, str) or not title.strip():
        title = "Untitled"
    else:
        title = title.strip()

    abstract = item.get("abstract")
    if not isinstance(abstract, str) or not abstract.strip():
        abstract = None
    else:
        abstract = abstract.strip()

    year = item.get("year")
    if not isinstance(year, int):
        # Try publicationDate
        pub_date = item.get("publicationDate")
        if isinstance(pub_date, str) and len(pub_date) >= 4:
            try:
                year = int(pub_date[:4])
            except Exception:
                year = None
        else:
            year = None
    if isinstance(year, int) and not (1000 <= year <= 3000):
        year = None

    venue = item.get("venue")
    if not isinstance(venue, str) or not venue.strip():
        venue = None
    else:
        venue = venue.strip()

    # Authors: list of dicts with name
    authors: list[Author] = []
    raw_authors = item.get("authors")
    if isinstance(raw_authors, list):
        for a in raw_authors:
            if not isinstance(a, dict):
                continue
            name = a.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            name = name.strip()
            ext_ids: list[ExternalIdentifier] = []
            author_id = a.get("authorId")
            if isinstance(author_id, str) and author_id.strip():
                ext_ids.append(
                    ExternalIdentifier(scheme="semantic_scholar", value=author_id.strip())
                )
            authors.append(Author(name=name, external_ids=ext_ids))

    external_ids_raw = item.get("externalIds")
    if not isinstance(external_ids_raw, dict):
        external_ids_raw = None
    doi = _extract_doi(external_ids_raw, None)

    url = item.get("url") if isinstance(item.get("url"), str) else None
    open_access_url = None
    oap = item.get("openAccessPdf")
    if isinstance(oap, dict):
        u = oap.get("url")
        if isinstance(u, str) and u.strip():
            open_access_url = u.strip()

    pub_types = item.get("publicationTypes")
    pub_type = None
    if isinstance(pub_types, list) and pub_types:
        # Take first
        first = pub_types[0]
        if isinstance(first, str):
            pub_type = first
    elif isinstance(item.get("publicationType"), str):
        pub_type = item.get("publicationType")  # type: ignore[assignment]

    # Build external identifiers
    ext_ids: list[ExternalIdentifier] = []
    if doi:
        ext_ids.append(ExternalIdentifier(scheme="doi", value=doi))
    # Semantic Scholar paperId
    if isinstance(paper_id, str) and paper_id.strip():
        ext_ids.append(ExternalIdentifier(scheme="semantic_scholar", value=paper_id.strip()))
    if isinstance(corpus_id, int):
        ext_ids.append(ExternalIdentifier(scheme="corpus_id", value=str(corpus_id)))
    # arXiv
    if isinstance(external_ids_raw, dict):
        arxiv = (
            external_ids_raw.get("ArXiv")
            or external_ids_raw.get("arxiv")
            or external_ids_raw.get("ARXIV")
        )
        if isinstance(arxiv, str) and arxiv.strip():
            ext_ids.append(ExternalIdentifier(scheme="arxiv", value=arxiv.strip()))
        # Also handle other ids like PubMed, etc., but keep minimal
        for k, v in external_ids_raw.items():
            lk = k.lower()
            if lk in ("doi", "arxiv", "corpusid"):
                continue
            if isinstance(v, str) and v.strip():
                # Normalize scheme lower
                ext_ids.append(ExternalIdentifier(scheme=k.lower(), value=v.strip()))

    metadata: dict[str, Any] = {}
    if isinstance(paper_id, str) and paper_id.strip():
        metadata["semantic_scholar_paperId"] = paper_id.strip()
    if isinstance(corpus_id, int):
        metadata["semantic_scholar_corpusId"] = corpus_id
    if pub_type:
        metadata["semantic_scholar_publicationType"] = pub_type

    paper = PaperRecord(
        title=title,
        authors=authors,
        year=year,
        venue=venue,
        abstract=abstract,
        doi=doi,
        external_identifiers=ext_ids,
        url=url,
        open_access_url=open_access_url,
        publication_type=pub_type,
        metadata=metadata,
    )
    return paper, item
