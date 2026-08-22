"""Mapper contract: same paper via both providers yields compatible PaperRecord."""

from research_harness.plugins.literature.crossref.mapper import map_crossref_work
from research_harness.plugins.literature.semantic_scholar.mapper import map_semantic_scholar_paper


def test_same_paper_both_providers():
    # Fake paper that exists in both providers with same DOI
    crossref_raw = {
        "title": ["Deep Learning for IS Research"],
        "DOI": "10.1234/dl-is-2021",
        "author": [{"given": "Jane", "family": "Doe"}, {"given": "John", "family": "Smith"}],
        "published-print": {"date-parts": [[2021]]},
        "container-title": ["Journal of IS"],
        "abstract": "<jats:p>Abstract about DL.</jats:p>",
        "URL": "https://doi.org/10.1234/dl-is-2021",
        "type": "journal-article",
    }
    ss_raw = {
        "paperId": "abc123",
        "corpusId": 999,
        "externalIds": {"DOI": "10.1234/dl-is-2021", "ArXiv": "2101.00001"},
        "title": "Deep Learning for IS Research",
        "abstract": "Abstract about DL.",
        "year": 2021,
        "venue": "Journal of IS",
        "authors": [{"name": "Jane Doe"}, {"name": "John Smith"}],
        "url": "https://www.semanticscholar.org/paper/abc123",
        "publicationTypes": ["JournalArticle"],
    }

    paper_crossref, _ = map_crossref_work(crossref_raw)
    paper_ss, _ = map_semantic_scholar_paper(ss_raw)

    # Normalized DOI must match (lower-case bare)
    assert paper_crossref.doi == "10.1234/dl-is-2021"
    assert paper_ss.doi == "10.1234/dl-is-2021"
    assert paper_crossref.doi == paper_ss.doi

    # Title should be same
    assert paper_crossref.title == paper_ss.title

    # Year same
    assert paper_crossref.year == paper_ss.year == 2021

    # Author names (order may differ but we check set)
    crossref_names = {a.name for a in paper_crossref.authors}
    ss_names = {a.name for a in paper_ss.authors}
    assert crossref_names == ss_names == {"Jane Doe", "John Smith"}

    # Venue similar (allow exact)
    assert paper_crossref.venue == paper_ss.venue == "Journal of IS"

    # Do not require full equality — providers expose different metadata
    # But ensure DOI external identifier present in both
    assert any(e.scheme == "doi" for e in paper_crossref.external_identifiers)
    assert any(e.scheme == "doi" for e in paper_ss.external_identifiers)


def test_mapper_doi_normalization_shared():
    # Both mappers should use same normalize_doi function
    crossref_raw = {"title": ["T"], "DOI": "https://doi.org/10.1234/ABC"}
    ss_raw = {"title": "T", "externalIds": {"DOI": "https://doi.org/10.1234/ABC"}}

    paper_c, _ = map_crossref_work(crossref_raw)
    paper_s, _ = map_semantic_scholar_paper(ss_raw)

    assert paper_c.doi == paper_s.doi == "10.1234/abc"
