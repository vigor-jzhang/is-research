"""Regression tests for literature / documents correctness (round 22).

Batch 4 of the §9 triage: M32, M33, M34, M40, M41, M42, M49. M46 is covered by
a guard test that pins the redirect bound — its premise is refuted in the
round-22 report notes. M38 lives in the screening orchestrator and needs the
autonomy layer, so it is asserted structurally.
"""

from __future__ import annotations

import asyncio
import json
import pathlib

import httpx
import pytest

from research_harness.research.envelope import ArtifactEnvelope

# ---------------------------------------------------------------------------
# M32 — a KeyError must not escape statement validation
# ---------------------------------------------------------------------------


def test_unmapped_conflicting_evidence_raises_value_error():
    """M32: the conflicting loop skipped the paper-mapping check.

    The supporting loop checked it and raised ValueError, which the call site
    catches. The conflicting loop did not, so a KeyError escaped from the
    sorted() two lines later — after the statements had already been persisted,
    orphaning artifacts and forcing a re-billed re-run.
    """
    from research_harness.plugins.literature.synthesis.plugin import (
        LiteratureSynthesizerService,
        _StatementCandidate,
    )

    svc = LiteratureSynthesizerService.__new__(LiteratureSynthesizerService)
    cand = _StatementCandidate(
        statement="Platform pricing converges.",
        type="consensus",
        supporting_evidence_ids=["e1"],
        conflicting_evidence_ids=["e2-unmapped"],
    )
    # Both ids exist as evidence, but only e1 has a paper mapping.
    ev_by_id = {"e1": object(), "e2-unmapped": object()}
    paper_by_evidence = {"e1": "paper-1"}

    with pytest.raises(ValueError, match="no paper mapping"):
        svc._build_statement(cand, ev_by_id, paper_by_evidence)


def test_mapped_ids_still_build_a_statement():
    from research_harness.plugins.literature.synthesis.plugin import (
        LiteratureSynthesizerService,
        _StatementCandidate,
    )

    svc = LiteratureSynthesizerService.__new__(LiteratureSynthesizerService)
    cand = _StatementCandidate(
        statement="Platform pricing converges.",
        type="consensus",
        supporting_evidence_ids=["e1"],
        conflicting_evidence_ids=["e2"],
    )
    stmt = svc._build_statement(cand, {"e1": object(), "e2": object()}, {"e1": "p1", "e2": "p2"})
    assert stmt.conflicting_paper_identity_ids == ["p2"]


# ---------------------------------------------------------------------------
# M33 — identifiers must be encoded before going into a URL path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crossref_get_encodes_the_doi_path():
    """M33: a raw DOI could truncate the path or start a query string."""
    from research_harness.plugins.literature.crossref.client import CrossrefClient

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(404)

    client = CrossrefClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(Exception):
        await client.get("10.1000/abc?x=1#frag")

    assert seen, "no request was made"
    url = httpx.URL(seen[0])
    assert dict(url.params) == {}, f"the DOI leaked into the query string: {url.params}"
    # .path decodes percent-encoding; .raw_path preserves it.
    assert "%3f" in url.raw_path.decode().lower(), (
        f"the DOI was not percent-encoded: {url.raw_path!r}"
    )


@pytest.mark.asyncio
async def test_semantic_scholar_get_normalizes_a_doi_url():
    """M33: a full DOI URL was used verbatim as the paper id."""
    from research_harness.plugins.literature.semantic_scholar.client import (
        SemanticScholarClient,
    )

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(404)

    client = SemanticScholarClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(Exception):
        await client.get("https://doi.org/10.1000/ABC")

    raw_path = httpx.URL(seen[0]).raw_path.decode()
    assert "https://doi.org" not in raw_path, "the whole DOI URL became the paper id"
    # "DOI:" survives (percent-encoded) and "/" stays a path separator.
    assert "DOI%3A10.1000/abc" in raw_path, f"unexpected path: {raw_path}"


# ---------------------------------------------------------------------------
# M34 — one merged identity, one canonical DOI
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merged_identity_emits_one_canonical_doi(tmp_path: pathlib.Path):
    """M34: the same DOI spelled two ways produced two canonical DOIs.

    PaperRecord normalizes its own `doi` field, but not the values inside
    `external_identifiers`, so a merged identity claimed two different DOIs.
    """
    from research_harness.plugins.literature.identity_resolver.plugin import (
        PaperIdentityResolverService,
    )
    from research_harness.plugins.storage.artifacts_sqlite.plugin import (
        SQLiteArtifactStore,
    )
    from research_harness.research.schemas.common import ExternalIdentifier
    from research_harness.research.schemas.identity import PaperIdentity
    from research_harness.research.schemas.paper import PaperRecord

    store = SQLiteArtifactStore(path=tmp_path / "a.db")
    resolver = PaperIdentityResolverService(artifact_store=store)

    p1 = ArtifactEnvelope.create(
        payload=PaperRecord(
            title="A",
            doi="10.123/a",
            external_identifiers=[
                ExternalIdentifier(scheme="doi", value="https://doi.org/10.123/A")
            ],
        ),
        artifact_type="paper_record",
    )
    p2 = ArtifactEnvelope.create(
        payload=PaperRecord(title="B", doi="10.123/a"),
        artifact_type="paper_record",
    )
    await store.put(p1)
    await store.put(p2)

    result = await resolver.resolve([p1.artifact_id, p2.artifact_id])
    assert result.identities_created, "no identity was created"
    identity = (await store.get(result.identities_created[0])).parse_payload(PaperIdentity)
    dois = [c.value for c in identity.canonical_identifiers if c.scheme == "doi"]
    assert dois == ["10.123/a"], f"one paper resolved to {len(dois)} canonical DOIs: {dois}"
    await store.close()


# ---------------------------------------------------------------------------
# M40 + M41 — screener confidence and exclusion enforcement
# ---------------------------------------------------------------------------


class _FakeRouter:
    def __init__(self, content: str) -> None:
        self.content = content

    async def complete(self, role, request):  # noqa: ANN201
        from research_harness.contracts.model import Message, ModelResponse

        return ModelResponse(
            message=Message(role="assistant", content=self.content),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


async def _screen(tmp_path: pathlib.Path, payload: dict) -> object:
    from research_harness.plugins.literature.title_abstract_screener.plugin import (
        TitleAbstractScreenerService,
    )
    from research_harness.plugins.storage.artifacts_sqlite.plugin import (
        SQLiteArtifactStore,
    )
    from research_harness.research.schemas.identity import (
        PaperIdentity,
        ResolutionMethod,
    )
    from research_harness.research.schemas.screening_decision import ScreeningDecision
    from research_harness.research.schemas.screening_protocol import (
        ProtocolStatus,
        ScreeningCriterion,
        ScreeningProtocol,
    )
    from research_harness.research.schemas.screening_view import PaperScreeningView

    store = SQLiteArtifactStore(path=tmp_path / f"{id(payload)}.db")
    pi = ArtifactEnvelope.create(
        payload=PaperIdentity(
            member_paper_artifact_ids=["p1"],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.exact_identifier,
            resolution_evidence=[],
        ),
        artifact_type="paper_identity",
        producer="test",
    )
    await store.put(pi)
    proto = ArtifactEnvelope.create(
        payload=ScreeningProtocol(
            research_question_id="rq1",
            objective="obj",
            inclusion_criteria=[
                ScreeningCriterion(criterion_id="I1", kind="inclusion", description="pricing")
            ],
            exclusion_criteria=[
                ScreeningCriterion(criterion_id="E1", kind="exclusion", description="non-scholarly")
            ],
            status=ProtocolStatus.approved,
        ),
        artifact_type="screening_protocol",
        producer="test",
    )
    await store.put(proto)
    view = ArtifactEnvelope.create(
        payload=PaperScreeningView(
            paper_identity_id=pi.artifact_id,
            title="Pricing in platforms",
            abstract="Study on algorithmic pricing",
            authors=["A One"],
            year=2020,
            venue="J",
            field_sources={},
            member_paper_artifact_ids=[],
        ),
        artifact_type="paper_screening_view",
        producer="test",
    )
    await store.put(view)

    svc = TitleAbstractScreenerService(
        model_router=_FakeRouter(json.dumps(payload)),  # type: ignore[arg-type]
        artifact_store=store,
        model_role="fast",
    )
    dec_id = await svc.screen(view.artifact_id, proto.artifact_id)
    dec = (await store.get(dec_id)).parse_payload(ScreeningDecision)
    await store.close()
    return dec


@pytest.mark.asyncio
async def test_confidence_out_of_range_is_clamped(tmp_path: pathlib.Path):
    """M40: the schema declares 0..1 but the value was used raw."""
    dec = await _screen(
        tmp_path,
        {
            "decision": "include",
            "matched_inclusion_criteria": ["I1"],
            "matched_exclusion_criteria": [],
            "reason_codes": ["R1"],
            "rationale_summary": "ok",
            "confidence": 4.2,
            "information_sufficiency": "sufficient",
        },
    )
    assert dec.confidence == 1.0, f"confidence {dec.confidence} escaped the declared range"

    low = await _screen(
        tmp_path,
        {
            "decision": "include",
            "matched_inclusion_criteria": ["I1"],
            "matched_exclusion_criteria": [],
            "reason_codes": ["R1"],
            "rationale_summary": "ok",
            "confidence": -3.0,
            "information_sufficiency": "sufficient",
        },
    )
    assert low.confidence == 0.0


@pytest.mark.asyncio
async def test_matched_exclusion_forces_exclude(tmp_path: pathlib.Path):
    """M41: the code logged "forcing exclude" and then did nothing."""
    dec = await _screen(
        tmp_path,
        {
            "decision": "include",
            "matched_inclusion_criteria": ["I1"],
            "matched_exclusion_criteria": ["E1"],
            "reason_codes": ["R1"],
            "rationale_summary": "model wants include",
            "confidence": 0.9,
            "information_sufficiency": "sufficient",
        },
    )
    assert dec.decision.value == "exclude", "an exclusion criterion was matched but not enforced"


@pytest.mark.asyncio
async def test_no_exclusion_leaves_the_decision_alone(tmp_path: pathlib.Path):
    dec = await _screen(
        tmp_path,
        {
            "decision": "include",
            "matched_inclusion_criteria": ["I1"],
            "matched_exclusion_criteria": [],
            "reason_codes": ["R1"],
            "rationale_summary": "ok",
            "confidence": 0.9,
            "information_sufficiency": "sufficient",
        },
    )
    assert dec.decision.value == "include"


# ---------------------------------------------------------------------------
# M42 — locator must preserve provider priority order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_locator_reuse_preserves_priority_order(tmp_path: pathlib.Path):
    """M42: sorting by artifact UUID destroyed the priority order.

    Locations are created in provider priority order and `store.list` returns
    them in that order (created_at ASC). Sorting by UUID reordered them, so the
    acquisition orchestrator could prefer a repository copy over the publisher
    PDF. The two ids below sort opposite to their creation order.
    """
    from research_harness.plugins.documents.locator_unpaywall.plugin import (
        UnpaywallLocatorService,
    )
    from research_harness.plugins.storage.artifacts_sqlite.plugin import (
        SQLiteArtifactStore,
    )
    from research_harness.research.schemas.common import ExternalIdentifier
    from research_harness.research.schemas.document_location import (
        AccessType,
        DocumentLocation,
        HostType,
    )
    from research_harness.research.schemas.identity import (
        PaperIdentity,
        ResolutionMethod,
    )
    from research_harness.research.schemas.provider_snapshot import (
        ProviderRecordSnapshot,
    )

    store = SQLiteArtifactStore(path=tmp_path / "a.db")
    pi = ArtifactEnvelope.create(
        payload=PaperIdentity(
            member_paper_artifact_ids=["p1"],
            canonical_identifiers=[ExternalIdentifier(scheme="doi", value="10.123/a")],
            resolution_method=ResolutionMethod.exact_identifier,
            resolution_evidence=[],
        ),
        artifact_type="paper_identity",
        producer="test",
        artifact_id="identity-1",
    )
    await store.put(pi)
    snap = ArtifactEnvelope.create(
        payload=ProviderRecordSnapshot(
                provider="unpaywall",
                provider_record_id="10.123/a",
                request_kind="get",
                raw_payload={},
        ),
        artifact_type="provider_record_snapshot",
        producer="test",
        artifact_id="snapshot-1",
    )
    await store.put(snap)

    def _loc(artifact_id: str, host: HostType) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=DocumentLocation(
                paper_identity_id=pi.artifact_id,
                provider_snapshot_id=snap.artifact_id,
                url=f"https://example.com/{artifact_id}.pdf",
                host_type=host,
                access_type=AccessType.open_access,
                resolver="unpaywall",
            ),
            artifact_type="document_location",
            producer="test",
            artifact_id=artifact_id,
        )

    # Created publisher-first (the priority order); zzz < aaa would sort wrong.
    await store.put(_loc("zzz-publisher", HostType.publisher))
    await asyncio.sleep(0.002)
    await store.put(_loc("aaa-repository", HostType.repository))

    svc = UnpaywallLocatorService(artifact_store=store, email="test@example.org")
    got = await svc.resolve(pi.artifact_id)
    assert got == ["zzz-publisher", "aaa-repository"], (
        f"expected creation (priority) order, got {got}"
    )
    await store.close()


# ---------------------------------------------------------------------------
# M46 — guard: the redirect bound is standard, not off by one
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_redirects_bounds_redirects_not_requests(tmp_path: pathlib.Path):
    """M46 (refuted): max_redirects=5 follows 5 redirects over 6 requests.

    The finding counted the initial request as a hop. This is the standard
    httpx/requests semantic, and the loop's `<=` is load-bearing: with `<` the
    loop would exit before the final request and the too_many_redirects error
    would never be raised, so a 302 would be streamed as a success.
    """
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService
    from research_harness.plugins.storage.artifacts_sqlite.plugin import (
        SQLiteArtifactStore,
    )
    from research_harness.plugins.storage.blobs_filesystem.plugin import (
        FilesystemBlobStore,
    )
    from research_harness.research.schemas.document_location import DocumentLocation
    from research_harness.research.schemas.identity import (
        PaperIdentity,
        ResolutionMethod,
    )

    store = SQLiteArtifactStore(path=tmp_path / "a.db")
    blobs = FilesystemBlobStore(root=tmp_path / "blobs")
    pi = ArtifactEnvelope.create(
        payload=PaperIdentity(
            member_paper_artifact_ids=["p1"],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.exact_identifier,
            resolution_evidence=[],
        ),
        artifact_type="paper_identity",
        producer="test",
    )
    await store.put(pi)
    loc = ArtifactEnvelope.create(
        payload=DocumentLocation(
            paper_identity_id=pi.artifact_id, resolver="test", url="https://example.com/hop0"
        ),
        artifact_type="document_location",
        producer="test",
    )
    await store.put(loc)

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": f"https://example.com/hop{len(seen)}"})

    svc = HttpFetcherService(
        artifact_store=store,
        blob_store=blobs,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_redirects=5,
    )
    acq_id = await svc.fetch(loc.artifact_id)
    from research_harness.research.schemas.document_acquisition import DocumentAcquisition

    acq = (await store.get(acq_id)).parse_payload(DocumentAcquisition)
    assert len(seen) == 6, f"expected 1 initial + 5 redirects, made {len(seen)} requests"
    assert acq.failure_code == "too_many_redirects"
    await store.close()


# ---------------------------------------------------------------------------
# M49 — a server-supplied Retry-After must be bounded
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module", ["crossref", "semantic_scholar"])
def test_retry_after_is_clamped(module: str):
    """M49: `Retry-After: 86400` meant a 24-hour sleep per attempt."""
    import importlib

    mod = importlib.import_module(
        f"research_harness.plugins.literature.{module}.client"
    )
    clamp = mod._clamp_retry_after
    assert clamp(86400) <= 30.0, "a day-long Retry-After was honoured verbatim"
    assert clamp(2.5) == 2.5, "a sane value must pass through"
    assert clamp(-5) == 0.0, "a negative Retry-After must not go negative"
    assert clamp(0) == 0.0
