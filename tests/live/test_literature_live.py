"""Optional live literature smoke tests — requires network.

Run with:

    uv run --env-file .env pytest -m live_literature -v
    # or
    uv run --env-file .env pytest -m live -v  (includes this via live marker? we use live_literature)

Normal `uv run pytest` skips.
"""

import os

import pytest

pytestmark = pytest.mark.live_literature


@pytest.mark.live_literature
@pytest.mark.asyncio
async def test_crossref_live_lookup(tmp_path):
    # Crossref does not require key, but respects mailto
    # Use known DOI
    doi = "10.1038/nature12373"  # known paper
    mailto = os.getenv("CROSSREF_MAILTO")
    # Build client
    from research_harness.plugins.literature.crossref.client import CrossrefClient

    client = CrossrefClient(mailto=mailto, timeout=20.0)
    try:
        hit = await client.get(doi)
    except Exception as e:
        pytest.skip(f"Crossref live lookup skipped due to error (network/provider): {e}")
    assert hit.paper.title
    assert hit.paper.doi is not None
    assert hit.provider == "crossref"
    await client.close()


@pytest.mark.live_literature
@pytest.mark.asyncio
async def test_crossref_live_search(tmp_path):
    from research_harness.contracts.literature import LiteratureSearchRequest
    from research_harness.plugins.literature.crossref.client import CrossrefClient

    client = CrossrefClient(mailto=os.getenv("CROSSREF_MAILTO"), timeout=20.0)
    try:
        page = await client.search(LiteratureSearchRequest(query="information systems", limit=2))
    except Exception as e:
        pytest.skip(f"Crossref live search skipped: {e}")
    assert len(page.hits) >= 1
    assert page.hits[0].paper.title
    await client.close()


@pytest.mark.live_literature
@pytest.mark.asyncio
async def test_semantic_scholar_live_search(tmp_path):
    # Semantic Scholar may require key for higher quotas, but search may work without
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    # Indicate whether authenticated
    from research_harness.contracts.literature import LiteratureSearchRequest
    from research_harness.plugins.literature.semantic_scholar.client import SemanticScholarClient

    client = SemanticScholarClient(api_key=api_key, timeout=20.0)
    try:
        page = await client.search(LiteratureSearchRequest(query="information systems", limit=2))
    except Exception as e:
        # If 401/403 without key, skip
        if "401" in str(e) or "403" in str(e):
            pytest.skip(f"Semantic Scholar live search requires key, skipped: {e}")
        pytest.skip(f"Semantic Scholar live search skipped: {e}")
    assert len(page.hits) >= 1
    assert page.hits[0].paper.title
    await client.close()


@pytest.mark.live_literature
@pytest.mark.asyncio
async def test_semantic_scholar_live_get(tmp_path):
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY")
    from research_harness.plugins.literature.semantic_scholar.client import SemanticScholarClient

    client = SemanticScholarClient(api_key=api_key, timeout=20.0)
    # Use a known paperId or DOI
    try:
        hit = await client.get("DOI:10.1038/nature12373")
    except Exception as e:
        if "401" in str(e) or "403" in str(e):
            pytest.skip(f"Semantic Scholar get requires key, skipped: {e}")
        pytest.skip(f"Semantic Scholar live get skipped: {e}")
    assert hit.paper.title
    await client.close()
