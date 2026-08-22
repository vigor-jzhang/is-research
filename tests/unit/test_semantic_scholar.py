import httpx
import pytest
import respx

from research_harness.contracts.literature import LiteratureSearchRequest
from research_harness.plugins.literature.semantic_scholar.client import SemanticScholarClient
from research_harness.plugins.literature.semantic_scholar.mapper import map_semantic_scholar_paper


async def _no_sleep(_):  # type: ignore[no-untyped-def]
    return None


@pytest.fixture
def ss_item():
    return {
        "paperId": "649def34f9f365af6fda3403",
        "corpusId": 123456,
        "externalIds": {"DOI": "10.1234/test", "ArXiv": "1234.5678"},
        "title": "Semantic Test Paper",
        "abstract": "This is an abstract.",
        "year": 2021,
        "venue": "Test Venue",
        "publicationTypes": ["JournalArticle"],
        "publicationDate": "2021-06-01",
        "authors": [
            {"authorId": "123", "name": "Alice Smith"},
            {"authorId": "124", "name": "Bob Jones"},
        ],
        "url": "https://www.semanticscholar.org/paper/123",
        "openAccessPdf": {"url": "https://arxiv.org/pdf/1234.5678.pdf"},
    }


def test_ss_mapper_basic(ss_item):
    paper, _ = map_semantic_scholar_paper(ss_item)
    assert paper.title == "Semantic Test Paper"
    assert paper.year == 2021
    assert paper.venue == "Test Venue"
    assert paper.abstract == "This is an abstract."
    assert paper.doi == "10.1234/test"
    assert paper.open_access_url == "https://arxiv.org/pdf/1234.5678.pdf"
    assert paper.url == "https://www.semanticscholar.org/paper/123"
    assert len(paper.authors) == 2
    assert paper.authors[0].name == "Alice Smith"
    assert paper.publication_type == "JournalArticle"


def test_ss_mapper_paperid_corpusid():
    item = {"paperId": "abc", "corpusId": 999, "title": "T"}
    paper, _ = map_semantic_scholar_paper(item)
    assert any(
        e.scheme == "semantic_scholar" and e.value == "abc" for e in paper.external_identifiers
    )
    assert any(e.scheme == "corpus_id" and e.value == "999" for e in paper.external_identifiers)


def test_ss_mapper_doi_arxiv():
    item = {"title": "T", "externalIds": {"DOI": "10.123/ABC", "ArXiv": "1234.5678"}}
    paper, _ = map_semantic_scholar_paper(item)
    assert paper.doi == "10.123/abc"
    assert any(e.scheme == "arxiv" for e in paper.external_identifiers)


def test_ss_mapper_missing_optional():
    item = {"title": "T"}
    paper, _ = map_semantic_scholar_paper(item)
    assert paper.title == "T"
    assert paper.authors == []
    assert paper.year is None
    assert paper.doi is None


def test_ss_mapper_openaccess():
    item = {"title": "T", "openAccessPdf": {"url": "https://example.com/pdf"}}
    paper, _ = map_semantic_scholar_paper(item)
    assert paper.open_access_url == "https://example.com/pdf"
    item2 = {"title": "T"}
    paper2, _ = map_semantic_scholar_paper(item2)
    assert paper2.open_access_url is None


@pytest.mark.asyncio
@respx.mock
async def test_ss_search_success():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "total": 1,
                "data": [
                    {
                        "paperId": "p1",
                        "title": "Paper A",
                        "year": 2020,
                        "authors": [{"name": "A"}],
                        "externalIds": {"DOI": "10.123/a"},
                        "url": "https://example.com",
                    }
                ],
            },
        )
    )
    client = SemanticScholarClient(http_client=httpx.AsyncClient(), sleep=_no_sleep)  # type: ignore[arg-type]
    page = await client.search(LiteratureSearchRequest(query="test", limit=10))
    assert len(page.hits) == 1
    assert page.hits[0].paper.title == "Paper A"
    assert page.total_estimate == 1
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_ss_search_empty():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(200, json={"total": 0, "data": []})
    )
    client = SemanticScholarClient(http_client=httpx.AsyncClient(), sleep=_no_sleep)  # type: ignore[arg-type]
    page = await client.search(LiteratureSearchRequest(query="nothing"))
    assert len(page.hits) == 0
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_ss_pagination():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        side_effect=[
            httpx.Response(200, json={"total": 2, "data": [{"paperId": "p1", "title": "P1"}]}),
            httpx.Response(200, json={"total": 2, "data": [{"paperId": "p2", "title": "P2"}]}),
        ]
    )
    client = SemanticScholarClient(http_client=httpx.AsyncClient(), sleep=_no_sleep)  # type: ignore[arg-type]
    page1 = await client.search(LiteratureSearchRequest(query="test", limit=1))
    assert page1.hits[0].paper.title == "P1"
    assert page1.next_page_token == "1"
    page2 = await client.search(
        LiteratureSearchRequest(query="test", limit=1, page_token=page1.next_page_token)
    )
    assert page2.hits[0].paper.title == "P2"
    assert page2.next_page_token is None
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_ss_get_by_paperid():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/p1").mock(
        return_value=httpx.Response(
            200,
            json={"paperId": "p1", "title": "Found", "year": 2022, "authors": [{"name": "X"}]},
        )
    )
    client = SemanticScholarClient(http_client=httpx.AsyncClient(), sleep=_no_sleep)  # type: ignore[arg-type]
    hit = await client.get("p1")
    assert hit.paper.title == "Found"
    assert hit.provider == "semantic_scholar"
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_ss_get_by_doi():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/DOI:10.123/test").mock(
        return_value=httpx.Response(
            200, json={"paperId": "p1", "title": "Found DOI", "externalIds": {"DOI": "10.123/test"}}
        )
    )
    client = SemanticScholarClient(http_client=httpx.AsyncClient(), sleep=_no_sleep)  # type: ignore[arg-type]
    hit = await client.get("10.123/test")
    assert hit.paper.doi == "10.123/test"
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_ss_401():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(401, text="unauth")
    )
    client = SemanticScholarClient(http_client=httpx.AsyncClient(), sleep=_no_sleep)  # type: ignore[arg-type]
    with pytest.raises(Exception, match="401"):
        await client.search(LiteratureSearchRequest(query="test"))
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_ss_404():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(404, text="not found")
    )
    client = SemanticScholarClient(http_client=httpx.AsyncClient(), sleep=_no_sleep)  # type: ignore[arg-type]
    with pytest.raises(Exception, match="404"):
        await client.search(LiteratureSearchRequest(query="test"))
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_ss_429_retry():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0.01"}, text="rate"),
            httpx.Response(200, json={"total": 0, "data": []}),
        ]
    )
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    client = SemanticScholarClient(http_client=httpx.AsyncClient(), sleep=fake_sleep)  # type: ignore[arg-type]
    page = await client.search(LiteratureSearchRequest(query="test"))
    assert page is not None
    assert sleeps[0] == 0.01
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_ss_5xx_retry():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        side_effect=[
            httpx.Response(500, text="err"),
            httpx.Response(200, json={"total": 0, "data": []}),
        ]
    )
    client = SemanticScholarClient(http_client=httpx.AsyncClient(), sleep=_no_sleep)  # type: ignore[arg-type]
    page = await client.search(LiteratureSearchRequest(query="test"))
    assert page is not None
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_ss_timeout():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        side_effect=httpx.TimeoutException("timeout")
    )
    client = SemanticScholarClient(http_client=httpx.AsyncClient(timeout=0.1), sleep=_no_sleep)  # type: ignore[arg-type]
    with pytest.raises(Exception, match="timeout"):
        await client.search(LiteratureSearchRequest(query="test"))
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_ss_api_key_header():
    # When key is set, header should be sent
    captured: dict[str, str] = {}

    def check_request(request):
        captured.update(dict(request.headers))
        return httpx.Response(200, json={"total": 0, "data": []})

    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        side_effect=check_request
    )
    client = SemanticScholarClient(
        api_key="test-key-123", http_client=httpx.AsyncClient(), sleep=_no_sleep
    )  # type: ignore[arg-type]
    await client.search(LiteratureSearchRequest(query="test"))
    assert captured.get("x-api-key") == "test-key-123"
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_ss_no_api_key_header():
    captured: dict[str, str] = {}

    def check_request(request):
        captured.update({k.lower(): v for k, v in request.headers.items()})
        return httpx.Response(200, json={"total": 0, "data": []})

    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        side_effect=check_request
    )
    # Ensure env var not set
    import os

    os.environ.pop("SEMANTIC_SCHOLAR_API_KEY", None)
    client = SemanticScholarClient(api_key=None, http_client=httpx.AsyncClient(), sleep=_no_sleep)  # type: ignore[arg-type]
    await client.search(LiteratureSearchRequest(query="test"))
    assert "x-api-key" not in captured
    await client.close()


@pytest.mark.asyncio
@respx.mock
async def test_ss_malformed_payload():
    respx.get("https://api.semanticscholar.org/graph/v1/paper/search").mock(
        return_value=httpx.Response(200, json={"total": 1, "data": "not a list"})
    )
    client = SemanticScholarClient(http_client=httpx.AsyncClient(), sleep=_no_sleep)  # type: ignore[arg-type]
    with pytest.raises(Exception, match="malformed"):
        await client.search(LiteratureSearchRequest(query="test"))
    await client.close()
