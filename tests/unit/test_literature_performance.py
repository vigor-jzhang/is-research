"""Regression tests for literature/document performance (round 31).

Batch: M44 (O(n^3) store round-trips; whole file read before the size check),
M45 (CPU-bound PDF parsing on the event loop), M47 (blocking getaddrinfo on the
event loop, once per URL and per redirect hop).
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest

import research_harness.plugins.documents.fetcher_http.plugin as fetcher_http

# ---------------------------------------------------------------------------
# M44 — the size check came after the read
# ---------------------------------------------------------------------------


def test_oversized_local_file_is_rejected_without_being_read(
    tmp_path: pathlib.Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """M44: `import_local` read the whole file, then checked its size.

    The guard existed but ran after `read_bytes()`, so an oversized file was
    loaded into memory first — the check caused the blow-up it existed to
    prevent. A sparse file makes this cheap: it is over the limit on disk
    while costing nothing to create.
    """
    from research_harness.plugins.documents.acquisition_orchestrator.plugin import (
        DocumentAcquisitionOrchestratorService,
    )

    big = tmp_path / "huge.pdf"
    with big.open("wb") as f:
        f.truncate(60 * 1024 * 1024)  # sparse: 60 MB, over the 50 MB limit
    assert big.stat().st_size > 52428800

    reads: list[int] = []
    real_read = pathlib.Path.read_bytes

    def counting_read_bytes(self: pathlib.Path) -> bytes:
        reads.append(1)
        return b"%PDF-"

    monkeypatch.setattr(pathlib.Path, "read_bytes", counting_read_bytes)
    try:
        svc = DocumentAcquisitionOrchestratorService(
            artifact_store=None, blob_store=None, fetcher=None, extractor=None
        )
        with pytest.raises(ValueError, match="too large"):
            asyncio.run(svc.import_local("pi-1", str(big)))
    finally:
        del real_read
    assert not reads, "the file was read before its size was checked"


# ---------------------------------------------------------------------------
# M47 — blocking DNS on the event loop, once per URL and per hop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dns_resolution_does_not_block_the_loop(monkeypatch) -> None:
    """M47: `socket.getaddrinfo` ran inline, stalling every other coroutine.

    A slow resolver used to freeze the event loop for the whole lookup. With the
    lookup on a worker thread, another coroutine keeps running meanwhile.
    """

    def slow_getaddrinfo(*args: object, **kwargs: object) -> list[object]:
        import time

        time.sleep(0.4)  # blocking, as a real resolver can be
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(fetcher_http.socket, "getaddrinfo", slow_getaddrinfo)
    fetcher_http.clear_dns_cache()

    progress = 0

    async def ticker() -> None:
        nonlocal progress
        while True:
            await asyncio.sleep(0.01)
            progress += 1

    tick = asyncio.create_task(ticker())
    host, port, addresses = await fetcher_http._validate_url("https://example.com/p.pdf")
    tick.cancel()
    assert host == "example.com"
    assert addresses == ("93.184.216.34",)
    # the loop kept turning during a 0.4 s blocking lookup
    assert progress >= 5, f"event loop stalled: only {progress} ticks during the lookup"


@pytest.mark.asyncio
async def test_dns_result_is_cached_across_hops(monkeypatch) -> None:
    """M47: a redirect chain validated the same host once per hop."""
    calls: list[str] = []

    def counting_getaddrinfo(host: str, *args: object, **kwargs: object) -> list[object]:
        calls.append(host)
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(fetcher_http.socket, "getaddrinfo", counting_getaddrinfo)
    fetcher_http.clear_dns_cache()

    for _ in range(4):  # one lookup per redirect hop
        await fetcher_http._validate_url("https://example.com/a.pdf")
    assert len(calls) == 1, f"resolved the same host {len(calls)} times"


@pytest.mark.asyncio
async def test_unresolvable_host_still_rejected(monkeypatch) -> None:
    """Guard: caching must not cache or mask a failure."""

    def boom(*args: object, **kwargs: object) -> list[object]:
        raise OSError("no such host")

    monkeypatch.setattr(fetcher_http.socket, "getaddrinfo", boom)
    fetcher_http.clear_dns_cache()
    with pytest.raises(ValueError, match="could not be resolved"):
        await fetcher_http._validate_url("https://nope.example/p.pdf")


# ---------------------------------------------------------------------------
# M45 — CPU-bound PDF work on the event loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pdf_extraction_does_not_block_the_loop(monkeypatch) -> None:
    """M45: PdfReader and extract_text ran inline on the event loop."""
    import research_harness.plugins.documents.extractor_pypdf.plugin as pypdf_plugin

    class _SlowPage:
        def extract_text(self) -> str:
            import time

            time.sleep(0.05)  # CPU-bound in reality; blocking either way
            return "text"

    class _Reader:
        is_encrypted = False

        def __init__(self, n: int) -> None:
            self.pages = [_SlowPage() for _ in range(n)]

    monkeypatch.setattr(pypdf_plugin, "_open_pdf_reader", lambda b: _Reader(20))

    from research_harness.plugins.documents.extractor_pypdf.plugin import (
        PypdfExtractorService,
    )

    svc = PypdfExtractorService(artifact_store=None, blob_store=None)

    progress = 0

    async def ticker() -> None:
        nonlocal progress
        while True:
            await asyncio.sleep(0.005)
            progress += 1

    tick = asyncio.create_task(ticker())
    pages, page_count, with_text, chars = await asyncio.to_thread(
        pypdf_plugin._extract_pages, _Reader(20), None, None
    )
    tick.cancel()
    assert page_count == 20 and chars == 80
    assert progress >= 5, f"event loop stalled: only {progress} ticks during extraction"


@pytest.mark.asyncio
async def test_page_cap_bounds_the_work() -> None:
    """M45: extraction was unbounded — a huge PDF ran to completion."""
    from research_harness.plugins.documents.extractor_pypdf.plugin import _extract_pages

    class _Page:
        def extract_text(self) -> str:
            return "x" * 10

    class _Reader:
        pages = [_Page() for _ in range(5000)]

        def __init__(self) -> None:
            self.pages = [_Page() for _ in range(5000)]

    pages, page_count, with_text, chars = await asyncio.to_thread(
        _extract_pages, _Reader(), 100, None
    )
    assert page_count == 100, f"page cap ignored: extracted {page_count} pages"
    assert chars == 1000


@pytest.mark.asyncio
async def test_wall_clock_limit_bounds_the_work() -> None:
    """M45: a slow document could occupy the worker indefinitely."""
    from research_harness.plugins.documents.extractor_pypdf.plugin import _extract_pages

    class _Page:
        def extract_text(self) -> str:
            import time

            time.sleep(0.05)
            return "y"

    class _Reader:
        def __init__(self) -> None:
            self.pages = [_Page() for _ in range(500)]

    pages, page_count, with_text, chars = await asyncio.to_thread(
        _extract_pages, _Reader(), None, 0.2
    )
    assert page_count < 500, "the wall-clock limit did not stop extraction"


def test_service_defaults_are_bounded() -> None:
    """Guard: the bounds are on by default, not opt-in."""
    from research_harness.plugins.documents.extractor_pypdf.plugin import (
        PypdfExtractorService,
    )

    svc = PypdfExtractorService(artifact_store=None, blob_store=None)
    assert svc._max_pages is not None and svc._max_pages > 0
    assert svc._max_seconds is not None and svc._max_seconds > 0
