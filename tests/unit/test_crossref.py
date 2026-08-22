import httpx
import pytest
import respx

from research_harness.contracts.literature import LiteratureSearchRequest
from research_harness.plugins.literature.crossref.client import CrossrefClient
from research_harness.plugins.literature.crossref.mapper import map_crossref_work


@pytest.fixture
def crossref_item():
    return {
        "title": ["Test Paper One"],
        "DOI": "10.1234/test1",
        "author": [
            {"given": "Alice", "family": "Smith", "ORCID": "https://orcid.org/0000-0001-0002-0003"},
            {"given": "Bob", "family": "Jones"},
        ],
        "published-print": {"date-parts": [[2021, 5, 10]]},
        "container-title": ["Journal of Testing"],
        "abstract": "<jats:p>This is <b>abstract</b> text.</jats:p>",
        "URL": "http://dx.doi.org/10.1234/test1",
        "type": "journal-article",
        "publisher": "Test Publisher",
    }


def test_crossref_mapper_title_author_year_venue(crossref_item):
    paper, _ = map_crossref_work(crossref_item)
    assert paper.title == "Test Paper One"
    assert len(paper.authors) == 2
    assert paper.authors[0].name == "Alice Smith"
    assert paper.authors[0].external_ids[0].value == "0000-0001-0002-0003"
    assert paper.year == 2021
    assert paper.venue == "Journal of Testing"
    assert paper.doi == "10.1234/test1"
    assert paper.url == "http://dx.doi.org/10.1234/test1"
    assert paper.publication_type == "journal-article"


def test_crossref_mapper_doi_normalization():
    item = {"title": ["T"], "DOI": "https://doi.org/10.1234/ABC"}
    paper, _ = map_crossref_work(item)
    assert paper.doi == "10.1234/abc"
    assert paper.external_identifiers[0].value == "10.1234/abc"


def test_crossref_mapper_abstract_markup():
    item = {"title": ["T"], "abstract": "<jats:p>Hello <jats:sec>world</jats:sec></jats:p>"}
    paper, _ = map_crossref_work(item)
    assert paper.abstract == "Hello world"


def test_crossref_mapper_missing_optional():
    item = {"title": ["T"]}
    paper, _ = map_crossref_work(item)
    assert paper.title == "T"
    assert paper.authors == []
    assert paper.year is None
    assert paper.venue is None
    assert paper.abstract is None
    assert paper.doi is None


def test_crossref_mapper_external_identifiers():
    item = {"title": ["T"], "DOI": "10.123/abc"}
    paper, _ = map_crossref_work(item)
    assert any(e.scheme == "doi" and e.value == "10.123/abc" for e in paper.external_identifiers)


@pytest.mark.asyncio
@respx.mock
async def test_crossref_search_success():
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "message-type": "work-list",
                "message": {
                    "items": [
                        {
                            "title": ["Paper A"],
                            "DOI": "10.123/a",
                            "author": [{"given": "A", "family": "B"}],
                            "published-print": {"date-parts": [[2020]]},
                            "container-title": ["V"],
                            "URL": "http://example.com/a",
                            "type": "journal-article",
                        }
                    ],
                    "total-results": 1,
                },
            },
        )
    )
    client = CrossrefClient(http_client=httpx.AsyncClient(), sleep=lambda x: None)  # type: ignore[arg-type]
    page = await client.search(LiteratureSearchRequest(query="test", limit=10))
    assert len(page.hits) == 1
    assert page.hits[0].paper.title == "Paper A"
    assert page.total_estimate == 1
    assert page.next_page_token is None  # only 1 hit, limit 10
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_crossref_search_empty():
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "message-type": "work-list",
                "message": {"items": [], "total-results": 0},
            },
        )
    )
    client = CrossrefClient(http_client=httpx.AsyncClient(), sleep=lambda x: None)  # type: ignore[arg-type]
    page = await client.search(LiteratureSearchRequest(query="nothing", limit=10))
    assert len(page.hits) == 0
    assert page.total_estimate == 0
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_crossref_pagination():
    # First page
    respx.get("https://api.crossref.org/works").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "status": "ok",
                    "message-type": "work-list",
                    "message": {
                        "items": [{"title": ["P1"], "DOI": "10.123/p1"}],
                        "total-results": 2,
                    },
                },
            ),
            httpx.Response(
                200,
                json={
                    "status": "ok",
                    "message-type": "work-list",
                    "message": {
                        "items": [{"title": ["P2"], "DOI": "10.123/p2"}],
                        "total-results": 2,
                    },
                },
            ),
        ]
    )
    client = CrossrefClient(http_client=httpx.AsyncClient(), sleep=lambda x: None)  # type: ignore[arg-type]
    page1 = await client.search(LiteratureSearchRequest(query="test", limit=1))
    assert len(page1.hits) == 1
    assert page1.next_page_token == "1"
    assert page1.hits[0].paper.title == "P1"
    page2 = await client.search(
        LiteratureSearchRequest(query="test", limit=1, page_token=page1.next_page_token)
    )
    assert len(page2.hits) == 1
    assert page2.hits[0].paper.title == "P2"
    assert page2.next_page_token is None  # no more
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_crossref_get_by_doi():
    doi = "10.123/test"
    respx.get(f"https://api.crossref.org/works/{doi}").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "ok",
                "message-type": "work",
                "message": {
                    "title": ["Found Paper"],
                    "DOI": doi,
                    "author": [{"given": "X", "family": "Y"}],
                    "published-print": {"date-parts": [[2022]]},
                    "container-title": ["V"],
                    "URL": f"https://doi.org/{doi}",
                    "type": "journal-article",
                },
            },
        )
    )
    client = CrossrefClient(http_client=httpx.AsyncClient(), sleep=lambda x: None)  # type: ignore[arg-type]
    hit = await client.get(doi)
    assert hit.paper.title == "Found Paper"
    assert hit.paper.doi == doi
    assert hit.provider == "crossref"
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_crossref_400():
    respx.get("https://api.crossref.org/works").mock(return_value=httpx.Response(400, text="bad"))
    client = CrossrefClient(http_client=httpx.AsyncClient(), sleep=lambda x: None)  # type: ignore[arg-type]
    with pytest.raises(Exception, match="400"):
        await client.search(LiteratureSearchRequest(query="test"))
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_crossref_404():
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(404, text="not found")
    )
    client = CrossrefClient(http_client=httpx.AsyncClient(), sleep=lambda x: None)  # type: ignore[arg-type]
    with pytest.raises(Exception, match="404"):
        await client.search(LiteratureSearchRequest(query="test"))
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_crossref_429_retry():
    # First 429 with Retry-After, then success
    respx.get("https://api.crossref.org/works").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0.01"}, text="rate limited"),
            httpx.Response(
                200,
                json={
                    "status": "ok",
                    "message-type": "work-list",
                    "message": {"items": [], "total-results": 0},
                },
            ),
        ]
    )
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    client = CrossrefClient(http_client=httpx.AsyncClient(), sleep=fake_sleep)  # type: ignore[arg-type]
    page = await client.search(LiteratureSearchRequest(query="test"))
    assert page is not None
    assert len(sleeps) == 1
    assert sleeps[0] == 0.01
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_crossref_5xx_retry():
    respx.get("https://api.crossref.org/works").mock(
        side_effect=[
            httpx.Response(500, text="server error"),
            httpx.Response(
                200,
                json={
                    "status": "ok",
                    "message-type": "work-list",
                    "message": {"items": [], "total-results": 0},
                },
            ),
        ]
    )

    async def no_sleep(_):  # type: ignore[no-untyped-def]
        return None

    client = CrossrefClient(http_client=httpx.AsyncClient(), sleep=no_sleep)  # type: ignore[arg-type]
    page = await client.search(LiteratureSearchRequest(query="test"))
    assert page is not None
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_crossref_timeout():
    respx.get("https://api.crossref.org/works").mock(side_effect=httpx.TimeoutException("timeout"))
    # Use async sleep that does nothing

    async def no_sleep(_):  # type: ignore[no-untyped-def]
        return None

    client = CrossrefClient(http_client=httpx.AsyncClient(timeout=0.1), sleep=no_sleep)  # type: ignore[arg-type]
    with pytest.raises(Exception, match="timeout"):
        await client.search(LiteratureSearchRequest(query="test"))
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_crossref_malformed_payload():
    respx.get("https://api.crossref.org/works").mock(
        return_value=httpx.Response(200, json={"status": "ok", "message": "not a dict"})
    )
    client = CrossrefClient(http_client=httpx.AsyncClient(), sleep=lambda x: None)  # type: ignore[arg-type]
    with pytest.raises(Exception, match="malformed"):
        await client.search(LiteratureSearchRequest(query="test"))
    await client.close()


def test_crossref_mapper_missing_doi_still_valid():
    item = {"title": ["T"], "author": [{"given": "A", "family": "B"}]}
    paper, _ = map_crossref_work(item)
    assert paper.doi is None
    assert paper.title == "T"
