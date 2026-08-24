"""Benchmark workflow drivers — Phase 6A/6B/6C.

Benchmark execution runs PRODUCTION workflows (the real NoveltyValidationService,
the real LiteratureSearchOrchestratorService, the real Phase 4C
PublicationFormatterService, the real screening and evidence pipelines) composed
with deterministic fixtures, so benchmarks exercise the production code path
with no network and no paid models.

This module lives outside the plugin tree on purpose: the architecture rules
forbid cross-category plugin imports, and benchmark drivers legitimately
compose services from every category. The evaluation harness plugin delegates
case execution here.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from research_harness.contracts.literature import LiteratureSearchHit, LiteratureSearchPage
from research_harness.contracts.model import Message, ModelResponse
from research_harness.kernel.services import ServiceError
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase
from research_harness.research.schemas.paper import Author, PaperRecord
from research_harness.research.schemas.publication import (
    FormattedManuscript,
    FormattedSection,
    FrontMatter,
    SubmissionPackage,
)

_DEFAULT_PRODUCER = "research.evaluation_harness"


class BenchmarkError(Exception):
    """Benchmark workflow error (raised by fixture composition, never by
    production code)."""


class FixtureModelRouter:
    """Scripted model router serving keyed JSON responses; used by benchmark
    cases so production workflows run deterministically without a network."""

    def __init__(self, fixtures: list[dict[str, Any]]) -> None:
        self._fixtures = list(fixtures)
        self.calls = 0

    async def complete(self, role: str, request: Any) -> ModelResponse:
        self.calls += 1
        text = " ".join(m.content or "" for m in request.messages)
        for fixture in self._fixtures:
            if fixture.get("match") and fixture["match"] in text:
                return ModelResponse(
                    message=Message(role="assistant", content=json.dumps(fixture["response"])),
                    tool_calls=[],
                    finish_reason="stop",
                    model="fixture",
                    provider="fixture",
                    usage=None,
                    latency_ms=1,
                )
        raise BenchmarkError(f"no llm fixture for prompt: {text[:200]}")

    def resolve(self, role: str) -> dict[str, str]:
        return {"provider": "fixture", "model": "fixture"}


class FixtureLiteratureSource:
    """Literature source backed by benchmark fixture paper records. Hits are
    returned in fixture order, which doubles as the provider ranking."""

    def __init__(self, provider_name: str, papers: list[dict[str, Any]] | str) -> None:
        self.provider_name = provider_name
        self.fail_all = papers == "fail_all"
        self.papers = (
            [] if self.fail_all else [PaperRecord(**p) for p in papers]  # type: ignore[arg-type]
        )

    async def search(self, request: Any) -> LiteratureSearchPage:
        if self.fail_all:
            raise RuntimeError(f"fixture provider {self.provider_name} failed")
        return LiteratureSearchPage(
            provider=self.provider_name,
            hits=[
                LiteratureSearchHit(
                    paper=p,
                    raw_payload={"title": p.title},
                    provider=self.provider_name,
                    provider_record_id=p.doi or p.title,
                )
                for p in self.papers
            ],
            total_estimate=len(self.papers),
        )

    async def get(self, identifier: str) -> Any:
        raise NotImplementedError


def make_source_lookup(
    sources: dict[str, FixtureLiteratureSource],
) -> Callable[[str], Any]:
    def lookup(name: str) -> Any:
        if name.startswith("literature_source."):
            provider = name[len("literature_source.") :]
            source = sources.get(provider)
            if source is None:
                raise ServiceError(f"no benchmark fixture source for provider {provider!r}")
            return source
        raise ServiceError(f"benchmark runtime does not provide {name!r}")

    return lookup


def make_retrieval_lookup(
    sources: dict[str, FixtureLiteratureSource], resolver: Any
) -> Callable[[str], Any]:
    """Lookup for the real search orchestrator: fixture literature sources
    plus the production paper-identity resolver."""

    def lookup(name: str) -> Any:
        if name.startswith("literature_source."):
            provider = name[len("literature_source.") :]
            source = sources.get(provider)
            if source is None:
                raise ServiceError(f"no benchmark fixture source for provider {provider!r}")
            return source
        if name == "paper_identity_resolver.default":
            return resolver
        raise ServiceError(f"benchmark runtime does not provide {name!r}")

    return lookup


def _fixture_sources(
    case: BenchmarkCase,
) -> dict[str, FixtureLiteratureSource]:
    return {
        provider: FixtureLiteratureSource(provider, papers)
        for provider, papers in (case.input.get("fixture_sources") or {}).items()
    }


# ---------------------------------------------------------------------------
# novelty_validation workflow (Phase 6A)
# ---------------------------------------------------------------------------


async def run_novelty_workflow(
    *,
    artifact_store: Any,
    ingestor: Any,
    identity_resolver: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    from research_harness.plugins.research.novelty_validator.plugin import (
        NoveltyValidationService,
    )

    before = {e.artifact_id for e in await artifact_store.list()}

    sub: dict[str, Any] = case.input.get("submission") or {}
    manuscript = FormattedManuscript(
        draft_id=f"{case.id}-draft",
        results_package_id=f"{case.id}-results",
        profile_id=f"{case.id}-profile",
        profile_name="benchmark",
        front_matter=FrontMatter(title=sub.get("title", ""), abstract=sub.get("abstract", "")),
        sections=[
            FormattedSection(section_id=sid, title=sid, body=body)
            for sid, body in (sub.get("sections") or {}).items()
        ],
    )
    await _put_explicit(
        artifact_store,
        ArtifactEnvelope.create(
            payload=manuscript,
            artifact_type="formatted_manuscript",
            producer=producer,
            artifact_id=f"{case.id}-manuscript",
        ),
    )
    package = SubmissionPackage(
        formatted_manuscript_id=f"{case.id}-manuscript",
        draft_id=manuscript.draft_id,
        profile_id=manuscript.profile_id,
    )
    await _put_explicit(
        artifact_store,
        ArtifactEnvelope.create(
            payload=package,
            artifact_type="submission_package",
            producer=producer,
            artifact_id=f"{case.id}-package",
        ),
    )

    sources = _fixture_sources(case)
    novelty_config = dict(case.input.get("novelty_config") or {})
    svc = NoveltyValidationService(
        model_router=FixtureModelRouter(case.input.get("llm_fixtures") or []),
        artifact_store=artifact_store,
        ingestor=ingestor,
        identity_resolver=identity_resolver,
        service_lookup=make_source_lookup(sources),
        enrichment_enabled=False,
        preacquisition_enabled=False,
        **novelty_config,
    )
    await svc.create_report(f"{case.id}-package", as_of=case.input.get("as_of"))

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# literature_retrieval workflow (Phase 6B)
# ---------------------------------------------------------------------------


async def run_retrieval_workflow(
    *,
    artifact_store: Any,
    ingestor: Any,
    identity_resolver: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    from research_harness.plugins.literature.search_orchestrator.plugin import (
        LiteratureSearchOrchestratorService,
    )
    from research_harness.research.schemas.query import LiteratureQuery
    from research_harness.research.schemas.strategy import LiteratureSearchStrategy

    before = {e.artifact_id for e in await artifact_store.list()}

    sources = _fixture_sources(case)
    query_ids: list[str] = []
    for i, q in enumerate(case.input.get("queries") or []):
        query = LiteratureQuery(
            query=q["query"],
            purpose=q.get("purpose"),
            concepts=list(q.get("concepts") or []),
            synonyms=list(q.get("synonyms") or []),
            year_from=q.get("year_from"),
            year_to=q.get("year_to"),
            target_sources=list(q.get("target_sources") or []),
            expected_relevance=q.get("expected_relevance"),
            generated_by=producer,
        )
        qid = f"{case.id}-query-{i}"
        await _put_explicit(
            artifact_store,
            ArtifactEnvelope.create(
                payload=query,
                artifact_type="literature_query",
                producer=producer,
                artifact_id=qid,
            ),
        )
        query_ids.append(qid)

    strategy = LiteratureSearchStrategy(
        research_question_id=f"{case.id}-rq",
        objective="benchmark retrieval",
        query_artifact_ids=query_ids,
        source_names=list(case.input.get("providers") or []),
    )
    sid = f"{case.id}-strategy"
    await _put_explicit(
        artifact_store,
        ArtifactEnvelope.create(
            payload=strategy,
            artifact_type="literature_search_strategy",
            producer=producer,
            artifact_id=sid,
        ),
    )

    retrieval_config = dict(case.input.get("retrieval_config") or {})
    orchestrator = LiteratureSearchOrchestratorService(
        artifact_store=artifact_store,
        ingestor=ingestor,
        service_lookup=make_retrieval_lookup(sources, identity_resolver),
        **retrieval_config,
    )
    await orchestrator.execute(sid)

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# citation_correctness workflow (Phase 6B)
# ---------------------------------------------------------------------------


async def run_citation_workflow(
    *,
    artifact_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> tuple[list[ArtifactEnvelope[Any]], str | None]:
    """Runs the real Phase 4C formatter over fixture papers/sections.
    Returns (produced artifacts, workflow error or None) — a formatter
    refusal (e.g. missing PaperIdentity) is surfaced so the evaluator can
    decide whether the refusal was the correct deterministic behavior."""
    from research_harness.plugins.research.publication_formatter.plugin import (
        PublicationFormatterService,
    )
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.manuscript import (
        CitationReference,
        ManuscriptDraft,
        ManuscriptSection,
    )
    from research_harness.research.schemas.publication import PublicationProfile

    before = {e.artifact_id for e in await artifact_store.list()}

    for p in case.input.get("papers") or []:
        record = PaperRecord(
            title=p["title"],
            authors=[Author(name=a) for a in p.get("authors") or []],
            year=p.get("year"),
            venue=p.get("venue"),
            doi=p.get("doi"),
            abstract=p.get("abstract"),
            publication_type=p.get("publication_type"),
        )
        await _put_explicit(
            artifact_store,
            ArtifactEnvelope.create(
                payload=record,
                artifact_type="paper_record",
                producer=producer,
                artifact_id=p["id"],
            ),
        )
        identity = PaperIdentity(
            member_paper_artifact_ids=[p["id"]],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.manual,
            resolution_evidence=[],
        )
        await _put_explicit(
            artifact_store,
            ArtifactEnvelope.create(
                payload=identity,
                artifact_type="paper_identity",
                producer=producer,
                artifact_id=p["identity_id"],
            ),
        )

    section_ids: list[str] = []
    for sec in case.input.get("sections") or []:
        section = ManuscriptSection(
            outline_id=f"{case.id}-outline",
            section_id=sec["section_id"],
            title=sec["title"],
            body=sec["body"],
            citations=[
                CitationReference(
                    citation_id=c["citation_id"],
                    paper_identity_id=c["paper_identity_id"],
                    evidence_item_id=c.get("evidence_item_id", "ev-benchmark"),
                    page_locator=c.get("page_locator"),
                )
                for c in sec.get("citations") or []
            ],
        )
        section_ids.append(sec["id"])
        await _put_explicit(
            artifact_store,
            ArtifactEnvelope.create(
                payload=section,
                artifact_type="manuscript_section",
                producer=producer,
                artifact_id=sec["id"],
            ),
        )

    draft = ManuscriptDraft(
        outline_id=f"{case.id}-outline",
        results_package_id=f"{case.id}-results",
        title=(case.input.get("draft") or {}).get("title", case.name),
        section_ids=section_ids,
    )
    # the Phase 4C formatter dedups on (draft, profile, role); a run-unique
    # draft id forces a fresh formatted manuscript per run
    draft_id = f"{case.id}-draft-{uuid.uuid4().hex[:8]}"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=draft,
            artifact_type="manuscript_draft",
            producer=producer,
            artifact_id=draft_id,
        )
    )

    profile_cfg = case.input.get("profile") or {}
    profile = PublicationProfile(
        name=profile_cfg.get("name", "Benchmark Profile"),
        citation_style=profile_cfg.get("citation_style", "author_year"),
        anonymous_review=bool(profile_cfg.get("anonymous_review", False)),
        abstract_required=False,
        abstract_max_words=100,
    )
    profile_id = f"{case.id}-profile"
    await _put_explicit(
        artifact_store,
        ArtifactEnvelope.create(
            payload=profile,
            artifact_type="publication_profile",
            producer=producer,
            artifact_id=profile_id,
        ),
    )

    formatter = PublicationFormatterService(
        model_router=FixtureModelRouter(case.input.get("llm_fixtures") or []),
        artifact_store=artifact_store,
        blob_store=None,
        formatter_role="reasoning",
    )
    error: str | None = None
    try:
        await formatter.format(draft_id, profile_id)
    except Exception as e:  # noqa: BLE001
        error = f"formatter failed: {e}"

    after = await artifact_store.list()
    produced = [e for e in after if e.artifact_id not in before]
    return produced, error


def _rewrite_ids(value: Any, id_map: dict[str, str]) -> Any:
    """Rewrite case-scoped fixture ids to run-unique ids inside scripted
    responses (recursive)."""
    if isinstance(value, dict):
        return {k: _rewrite_ids(v, id_map) for k, v in value.items()}
    if isinstance(value, list):
        return [_rewrite_ids(v, id_map) for v in value]
    if isinstance(value, str):
        return id_map.get(value, value)
    return value


async def _put_explicit(artifact_store: Any, env: ArtifactEnvelope[Any]) -> None:
    """Persist an explicit-id artifact idempotently: identical content is a
    no-op (re-runs), different content raises (immutability). The comparison
    ignores registration-time `created_at` so re-runs stay idempotent."""
    if not await artifact_store.exists(env.artifact_id):
        await artifact_store.put(env)
        return
    existing = await artifact_store.get(env.artifact_id)
    if _payload_hash(existing.payload) == _payload_hash(env.payload):
        return
    raise BenchmarkError(
        f"artifact {env.artifact_id!r} already exists with different content; "
        "a benchmark change must not silently alter historical results"
    )


def _payload_hash(payload: Any) -> str:
    if hasattr(payload, "model_dump"):
        data = payload.model_dump(mode="json")
    elif isinstance(payload, dict):
        data = payload
    else:
        data = {}
    _strip_created_at(data)
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _strip_created_at(data: Any) -> None:
    """Remove registration/operational timestamps at any depth so identical
    content re-registers idempotently regardless of when it was created."""
    if isinstance(data, dict):
        for key in ("created_at", "started_at", "completed_at", "attempted_at"):
            data.pop(key, None)
        for value in data.values():
            _strip_created_at(value)
    elif isinstance(data, list):
        for item in data:
            _strip_created_at(item)


# ---------------------------------------------------------------------------
# literature_screening workflow (Phase 6C): real Phase 2D pipeline
# ---------------------------------------------------------------------------


async def run_screening_workflow(
    *,
    artifact_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real screening pipeline: ScreeningProtocolBuilderService
    (model + real approval gate) -> ScreeningViewBuilderService ->
    TitleAbstractScreenerService -> ScreeningOrchestratorService."""
    from research_harness.plugins.autonomy.configurable.plugin import (
        ConfigurableAutonomyPolicy,
    )
    from research_harness.plugins.literature.screening_orchestrator.plugin import (
        ScreeningOrchestratorService,
    )
    from research_harness.plugins.literature.screening_protocol_builder.plugin import (
        ScreeningProtocolBuilderService,
    )
    from research_harness.plugins.literature.screening_view_builder.plugin import (
        ScreeningViewBuilderService,
    )
    from research_harness.plugins.literature.title_abstract_screener.plugin import (
        TitleAbstractScreenerService,
    )
    from research_harness.research.schemas.execution import LiteratureSearchExecution
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.project import ResearchQuestion

    before = {e.artifact_id for e in await artifact_store.list()}
    router = FixtureModelRouter(case.input.get("llm_fixtures") or [])
    autonomy = ConfigurableAutonomyPolicy(mode="high")
    run_suffix = uuid.uuid4().hex[:8]

    # fixture records + identities are run-unique so every run produces fresh
    # views/decisions and the evaluator's key mapping always sees them
    paper_ids: list[str] = []
    for i, p in enumerate(case.input.get("papers") or []):
        record = PaperRecord(
            title=p["title"],
            authors=[Author(name=a) for a in p.get("authors") or []],
            year=p.get("year"),
            venue=p.get("venue"),
            abstract=p.get("abstract"),
            doi=p.get("doi"),
        )
        pid = f"{case.id}-{run_suffix}-paper-{i}"
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=record,
                artifact_type="paper_record",
                producer=producer,
                artifact_id=pid,
            ),
        )
        paper_ids.append(pid)

    identity_ids: list[str] = []
    for i, idn in enumerate(case.input.get("identities") or []):
        identity = PaperIdentity(
            member_paper_artifact_ids=[paper_ids[j] for j in idn["member_indexes"]],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.manual,
            resolution_evidence=[],
        )
        iid = f"{case.id}-{run_suffix}-identity-{i}"
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=identity,
                artifact_type="paper_identity",
                producer=producer,
                artifact_id=iid,
            ),
        )
        identity_ids.append(iid)

    rq = ResearchQuestion(
        question=case.input["research_question"]["question"],
        motivation="benchmark fixture",
        scope="benchmark",
    )
    await _put_explicit(
        artifact_store,
        ArtifactEnvelope.create(
            payload=rq,
            artifact_type="research_question",
            producer=producer,
            artifact_id=f"{case.id}-rq",
        ),
    )

    # real protocol builder with the REAL approval gate (high autonomy
    # auto-approves through ConfigurableAutonomyPolicy)
    builder = ScreeningProtocolBuilderService(
        model_router=router,
        artifact_store=artifact_store,
        autonomy_policy=autonomy,
        model_role="reasoning",
    )
    protocol_id = await builder.build(f"{case.id}-rq")

    # fixture search execution listing the candidate identities
    search_exec = LiteratureSearchExecution(
        strategy_artifact_id=f"{case.id}-strategy",
        query_artifact_ids=[],
        paper_identity_artifact_ids=identity_ids,
        counts={},
    )
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=search_exec,
            artifact_type="literature_search_execution",
            producer=producer,
            artifact_id=f"{case.id}-{run_suffix}-search-exec",
        ),
    )

    view_builder = ScreeningViewBuilderService(artifact_store=artifact_store)
    screener = TitleAbstractScreenerService(
        model_router=router,
        artifact_store=artifact_store,
        model_role="fast",
    )
    screening_config = dict(case.input.get("screening_config") or {})
    orchestrator = ScreeningOrchestratorService(
        artifact_store=artifact_store,
        view_builder=view_builder,
        screener=screener,
        autonomy_policy=autonomy,
        max_candidates=screening_config.get("max_candidates", 500),
        max_model_calls=screening_config.get("max_model_calls", 500),
        review_uncertain=screening_config.get("review_uncertain", True),
        review_low_confidence_below=screening_config.get("review_low_confidence_below", 0.65),
    )
    await orchestrator.screen(f"{case.id}-{run_suffix}-search-exec", protocol_id)

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# evidence_extraction workflow (Phase 6C): real Phase 2F pipeline
# ---------------------------------------------------------------------------


async def run_evidence_workflow(
    *,
    artifact_store: Any,
    blob_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real evidence pipeline: fixture FullTextCorpus (blob-backed
    pages) -> EvidenceExtractorService -> EvidenceOrchestratorService ->
    EvidenceItem / PaperResearchProfile / EvidenceCorpus. Fixture documents are
    run-unique because the production evidence dedup is global per document."""
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractorService,
    )
    from research_harness.plugins.literature.evidence_orchestrator.plugin import (
        EvidenceOrchestratorService,
    )
    from research_harness.research.schemas.document_acquisition import (
        AcquisitionStatus,
        DocumentAcquisition,
    )
    from research_harness.research.schemas.full_text import (
        FullTextCorpus,
        FullTextDocument,
        TextStatus,
    )
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod

    if blob_store is None:
        raise BenchmarkError(
            "evidence_extraction requires a blob store (compose "
            "storage.blobs_filesystem or pass one to the harness)"
        )
    before = {e.artifact_id for e in await artifact_store.list()}
    router = FixtureModelRouter(case.input.get("llm_fixtures") or [])
    run_suffix = uuid.uuid4().hex[:8]

    doc_ids: list[str] = []
    for i, doc in enumerate(case.input.get("documents") or []):
        record = PaperRecord(
            title=doc["title"],
            year=doc.get("year"),
            venue=doc.get("venue"),
            abstract=doc.get("abstract"),
        )
        pid = f"{case.id}-{run_suffix}-paper-{i}"
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=record,
                artifact_type="paper_record",
                producer=producer,
                artifact_id=pid,
            )
        )
        identity = PaperIdentity(
            member_paper_artifact_ids=[pid],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.manual,
            resolution_evidence=[],
        )
        piid = f"{case.id}-{run_suffix}-identity-{i}"
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=identity,
                artifact_type="paper_identity",
                producer=producer,
                artifact_id=piid,
            )
        )

        text_status = TextStatus(doc.get("text_status", "extracted"))
        pages = list(doc.get("pages") or [])
        pdf_blob = await blob_store.put_bytes(b"%PDF-1.4 benchmark", media_type="application/pdf")
        text_blob = None
        if text_status == TextStatus.extracted:
            text_blob = await blob_store.put_bytes(
                json.dumps({"schema_version": 1, "pages": pages}, sort_keys=True).encode(),
                media_type="application/json",
            )
        acq_id = f"{case.id}-{run_suffix}-acq-{i}"
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=DocumentAcquisition(
                    paper_identity_id=piid,
                    document_location_id=None,
                    status=AcquisitionStatus.downloaded,
                    blob=pdf_blob,
                    sha256=pdf_blob.digest,
                    size_bytes=pdf_blob.size_bytes,
                    media_type="application/pdf",
                    source_type="http",
                ),
                artifact_type="document_acquisition",
                producer=producer,
                artifact_id=acq_id,
            )
        )
        doc_id = f"{case.id}-{run_suffix}-doc-{i}"
        ftd = FullTextDocument(
            paper_identity_id=piid,
            document_acquisition_id=acq_id,
            source_blob=pdf_blob,
            text_blob=text_blob,
            extractor="documents.extractor.pypdf",
            page_count=len(pages) if text_status == TextStatus.extracted else 0,
            pages_with_text=len(pages) if text_status == TextStatus.extracted else 0,
            character_count=sum(len(p.get("text", "")) for p in pages),
            text_status=text_status,
        )
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=ftd,
                artifact_type="full_text_document",
                producer=producer,
                artifact_id=doc_id,
            )
        )
        doc_ids.append(doc_id)

    corpus = FullTextCorpus(
        document_acquisition_execution_id=f"{case.id}-acq-exec",
        screened_literature_set_id=f"{case.id}-set",
        available_document_ids=doc_ids,
    )
    corpus_id = f"{case.id}-{run_suffix}-corpus"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=corpus,
            artifact_type="full_text_corpus",
            producer=producer,
            artifact_id=corpus_id,
        )
    )

    evidence_config = dict(case.input.get("evidence_config") or {})
    extractor = EvidenceExtractorService(
        model_router=router,
        artifact_store=artifact_store,
        blob_store=blob_store,
        model_role="reasoning",
    )
    orchestrator = EvidenceOrchestratorService(
        artifact_store=artifact_store,
        blob_store=blob_store,
        extractor=extractor,
        model_role="reasoning",
        pages_per_chunk=evidence_config.get("pages_per_chunk", 4),
        max_chunks_per_document=evidence_config.get("max_chunks_per_document", 50),
        max_model_calls=evidence_config.get("max_model_calls", 500),
    )
    await orchestrator.run(corpus_id)

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# gap_analysis workflow (Phase 6D): real Phase 2H gap analyzer
# ---------------------------------------------------------------------------


async def run_gap_workflow(
    *,
    artifact_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real gap analyzer: fixture evidence/synthesis artifacts ->
    real GapAnalyzerService -> ResearchGap + GapAnalysis. Statements, evidence
    and themes use stable case-scoped ids (the scripted response cites them);
    the LiteratureSynthesis artifact is run-unique so the analyzer's execution
    idempotency never reuses a stale run."""
    from research_harness.plugins.literature.gap_analyzer.plugin import GapAnalyzerService
    from research_harness.research.schemas.evidence import EvidenceItem, Locator
    from research_harness.research.schemas.evidence_extraction import EvidenceCorpus
    from research_harness.research.schemas.research_profile import PaperResearchProfile
    from research_harness.research.schemas.synthesis import (
        LiteratureSynthesis,
        SynthesisStatement,
        SynthesisTheme,
    )

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]

    evidence_ids: list[str] = []
    for i, ev in enumerate(case.input.get("evidence") or []):
        eid = f"{case.id}-evidence-{i}"
        await _put_explicit(
            artifact_store,
            ArtifactEnvelope.create(
                payload=EvidenceItem(
                    statement=ev["statement"],
                    source_artifact_id=ev.get("source_artifact_id") or f"{case.id}-doc-{i}",
                    category=ev.get("category"),
                    locator=Locator(page=1, pages=[1]),
                    extraction_method="model-assisted",
                    confidence=ev.get("confidence", 0.9),
                ),
                artifact_type="evidence_item",
                producer=producer,
                artifact_id=eid,
            ),
        )
        evidence_ids.append(eid)

    for i, prof in enumerate(case.input.get("profiles") or []):
        await _put_explicit(
            artifact_store,
            ArtifactEnvelope.create(
                payload=PaperResearchProfile(
                    paper_identity_id=prof.get("paper_identity_id") or f"{case.id}-paper",
                    full_text_document_id=prof.get("document_id") or f"{case.id}-doc",
                    evidence_item_ids=list(evidence_ids),
                ),
                artifact_type="paper_research_profile",
                producer=producer,
                artifact_id=f"{case.id}-profile-{i}",
            ),
        )

    stmt_ids: list[str] = []
    stmt_models: list[SynthesisStatement] = []
    for i, st in enumerate(case.input.get("statements") or []):
        statement = SynthesisStatement(
            statement=st["statement"],
            type=st["type"],
            supporting_evidence_ids=list(st.get("evidence_ids") or []),
            conflicting_evidence_ids=list(st.get("conflicting_evidence_ids") or []),
            supporting_paper_identity_ids=list(st.get("paper_ids") or []),
            conflicting_paper_identity_ids=list(st.get("conflicting_paper_ids") or []),
            papers_supporting=len(st.get("paper_ids") or []),
            evidence_items_supporting=len(st.get("evidence_ids") or []),
            papers_conflicting=len(st.get("conflicting_paper_ids") or []),
            evidence_items_conflicting=len(st.get("conflicting_evidence_ids") or []),
            support_type=st.get("support_type", "single_paper"),
            confidence=st.get("confidence", 0.9),
        )
        sid = f"{case.id}-statement-{i}"
        await _put_explicit(
            artifact_store,
            ArtifactEnvelope.create(
                payload=statement,
                artifact_type="synthesis_statement",
                producer=producer,
                artifact_id=sid,
            ),
        )
        stmt_ids.append(sid)
        stmt_models.append(statement)

    for i, theme in enumerate(case.input.get("themes") or []):
        await _put_explicit(
            artifact_store,
            ArtifactEnvelope.create(
                payload=SynthesisTheme(
                    title=theme["title"],
                    dimension=theme.get("dimension"),
                    statements=[stmt_models[j] for j in theme["statement_indexes"]],
                    evidence_item_ids=evidence_ids,
                    paper_identity_ids=list(
                        dict.fromkeys(
                            pid
                            for j in theme["statement_indexes"]
                            for pid in stmt_models[j].supporting_paper_identity_ids
                        )
                    ),
                    metadata={"statement_ids": [stmt_ids[j] for j in theme["statement_indexes"]]},
                ),
                artifact_type="synthesis_theme",
                producer=producer,
                artifact_id=f"{case.id}-theme-{i}",
            ),
        )

    corpus = EvidenceCorpus(
        evidence_extraction_execution_id=f"{case.id}-ev-exec",
        full_text_corpus_id=f"{case.id}-corpus",
        paper_profile_ids=[
            f"{case.id}-profile-{i}" for i in range(len(case.input.get("profiles") or []))
        ],
        evidence_item_ids=evidence_ids,
        documents_without_evidence=list(case.input.get("documents_without_evidence") or []),
    )
    corpus_id = f"{case.id}-corpus"
    await _put_explicit(
        artifact_store,
        ArtifactEnvelope.create(
            payload=corpus,
            artifact_type="evidence_corpus",
            producer=producer,
            artifact_id=corpus_id,
        ),
    )

    synthesis = LiteratureSynthesis(
        evidence_corpus_id=corpus_id,
        theme_ids=[f"{case.id}-theme-{i}" for i in range(len(case.input.get("themes") or []))],
        statement_ids=stmt_ids,
        counts={"statements": len(stmt_ids), "themes": len(case.input.get("themes") or [])},
    )
    synthesis_id = f"{case.id}-{run_suffix}-synthesis"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=synthesis,
            artifact_type="literature_synthesis",
            producer=producer,
            artifact_id=synthesis_id,
        )
    )

    gap_config = dict(case.input.get("gap_config") or {})
    analyzer = GapAnalyzerService(
        model_router=FixtureModelRouter(case.input.get("llm_fixtures") or []),
        artifact_store=artifact_store,
        model_role="reasoning",
        max_statements=gap_config.get("max_statements", 200),
        max_gaps=gap_config.get("max_gaps", 50),
        max_model_calls=gap_config.get("max_model_calls", 20),
    )
    await analyzer.run(
        synthesis_id, corpus_id, research_question_id=case.input.get("research_question_id")
    )

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# mechanism_development workflow (Phase 6D): real Phase 3A pipeline
# ---------------------------------------------------------------------------


async def run_mechanism_workflow(
    *,
    artifact_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real Phase 3A pipeline: fixture gap context -> real
    GapSelectionService (model + real approval gate) -> real
    MechanismGeneratorService -> real MechanismCriticService (critique +
    select per candidate). The GapAnalysis artifact is run-unique so selection
    idempotency never reuses a stale run; the gap/statements/evidence keep
    stable ids because the scripted responses cite them."""
    from research_harness.plugins.autonomy.configurable.plugin import (
        ConfigurableAutonomyPolicy,
    )
    from research_harness.plugins.research.gap_selection.plugin import GapSelectionService
    from research_harness.plugins.research.mechanism_critic.plugin import MechanismCriticService
    from research_harness.plugins.research.mechanism_generator.plugin import (
        MechanismGeneratorService,
    )
    from research_harness.research.schemas.evidence import EvidenceItem, Locator
    from research_harness.research.schemas.gap import GapAnalysis, ResearchGap
    from research_harness.research.schemas.synthesis import SynthesisStatement

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]
    # fixture inputs are run-unique so every run produces fresh artifacts and
    # the evaluator's grounding checks always see the fixture context; the
    # scripted responses cite the case-scoped ids and are rewritten below
    id_map: dict[str, str] = {}
    for i in range(len(case.input.get("evidence") or [])):
        id_map[f"{case.id}-evidence-{i}"] = f"{case.id}-{run_suffix}-evidence-{i}"
    for i in range(len(case.input.get("statements") or [])):
        id_map[f"{case.id}-statement-{i}"] = f"{case.id}-{run_suffix}-statement-{i}"
    id_map[f"{case.id}-gap"] = f"{case.id}-{run_suffix}-gap"
    router = FixtureModelRouter(_rewrite_ids(case.input.get("llm_fixtures") or [], id_map))
    autonomy = ConfigurableAutonomyPolicy(mode="high")

    evidence_ids: list[str] = []
    for i, ev in enumerate(case.input.get("evidence") or []):
        eid = id_map[f"{case.id}-evidence-{i}"]
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=EvidenceItem(
                    statement=ev["statement"],
                    source_artifact_id=ev.get("source_artifact_id") or f"{case.id}-doc-{i}",
                    category=ev.get("category"),
                    locator=Locator(page=1, pages=[1]),
                    extraction_method="model-assisted",
                    confidence=0.9,
                ),
                artifact_type="evidence_item",
                producer=producer,
                artifact_id=eid,
            ),
        )
        evidence_ids.append(eid)

    stmt_ids: list[str] = []
    for i, st in enumerate(case.input.get("statements") or []):
        statement = SynthesisStatement(
            statement=st["statement"],
            type=st["type"],
            supporting_evidence_ids=[
                id_map.get(str(eid), str(eid)) for eid in (st.get("evidence_ids") or [])
            ],
            conflicting_evidence_ids=list(st.get("conflicting_evidence_ids") or []),
            supporting_paper_identity_ids=list(st.get("paper_ids") or []),
            conflicting_paper_identity_ids=list(st.get("conflicting_paper_ids") or []),
            papers_supporting=len(st.get("paper_ids") or []),
            evidence_items_supporting=len(st.get("evidence_ids") or []),
            papers_conflicting=len(st.get("conflicting_paper_ids") or []),
            evidence_items_conflicting=len(st.get("conflicting_evidence_ids") or []),
            support_type=st.get("support_type", "single_paper"),
            confidence=st.get("confidence", 0.9),
        )
        sid = id_map[f"{case.id}-statement-{i}"]
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=statement,
                artifact_type="synthesis_statement",
                producer=producer,
                artifact_id=sid,
            ),
        )
        stmt_ids.append(sid)

    gap_cfg = case.input.get("gap") or {}
    gap = ResearchGap(
        title=gap_cfg["title"],
        gap_type=gap_cfg["gap_type"],
        description=gap_cfg["description"],
        supporting_synthesis_statement_ids=[
            id_map.get(str(sid), str(sid)) for sid in (gap_cfg.get("statement_ids") or [])
        ],
        supporting_evidence_ids=[
            id_map.get(str(eid), str(eid)) for eid in (gap_cfg.get("evidence_ids") or [])
        ],
        contradiction_statement_ids=list(gap_cfg.get("contradiction_statement_ids") or []),
        strength=gap_cfg.get("strength", "tentative"),
        confidence=gap_cfg.get("confidence", 0.8),
    )
    gap_id = id_map[f"{case.id}-gap"]
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=gap,
            artifact_type="research_gap",
            producer=producer,
            artifact_id=gap_id,
        ),
    )

    analysis = GapAnalysis(
        literature_synthesis_id=f"{case.id}-synthesis",
        evidence_corpus_id=f"{case.id}-corpus",
        gap_ids=[gap_id],
        ranked_gap_ids=[gap_id],
    )
    analysis_id = f"{case.id}-{run_suffix}-analysis"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=analysis,
            artifact_type="gap_analysis",
            producer=producer,
            artifact_id=analysis_id,
        ),
    )

    selection_svc = GapSelectionService(
        model_router=router,
        artifact_store=artifact_store,
        model_role="reasoning",
        autonomy_mode="high",
        autonomy=autonomy,
    )
    selection_id = await selection_svc.select(analysis_id)

    generator_svc = MechanismGeneratorService(
        model_router=router,
        artifact_store=artifact_store,
        model_role="reasoning",
        max_candidates=5,
        max_model_calls=20,
    )
    await generator_svc.generate(selection_id)

    produced = [e for e in await artifact_store.list() if e.artifact_id not in before]
    candidate_ids = [e.artifact_id for e in produced if e.artifact_type == "mechanism_candidate"]

    critic_svc = MechanismCriticService(
        model_router=router,
        artifact_store=artifact_store,
        critic_role="critic",
        revision_role="reasoning",
    )
    for cand_id in candidate_ids:
        await critic_svc.critique(cand_id)
        await critic_svc.select(cand_id)

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# equilibrium_derivation workflow (Phase 6E): real Phase 3C pipeline
# ---------------------------------------------------------------------------


async def run_equilibrium_workflow(
    *,
    artifact_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real Phase 3C pipeline: fixture FormalAnalyticalModel ->
    real EquilibriumDeriverService (SymPy derivation + symbolic verification +
    bounded LLM revision). The model is run-unique so deriver idempotency
    never stales re-runs."""
    from research_harness.plugins.research.equilibrium_deriver.plugin import (
        EquilibriumDeriverService,
    )
    from research_harness.plugins.research.equilibrium_verifier.plugin import (
        EquilibriumVerifierService,
    )
    from research_harness.research.schemas.model import (
        Expression,
        FormalAnalyticalModel,
        ModelActor,
        ModelParameter,
        ModelTimingStage,
        ModelVariable,
        PayoffFunction,
        SymbolKind,
    )

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]
    model_cfg = case.input.get("model") or {}

    model = FormalAnalyticalModel(
        selected_mechanism_id=f"{case.id}-mech",
        title=model_cfg["title"],
        description=model_cfg.get("description") or model_cfg["title"],
        actors=[
            ModelActor(actor_id=a["id"], name=a["name"], strategic=a.get("strategic", True))
            for a in model_cfg.get("actors") or []
        ],
        variables=[
            ModelVariable(
                symbol=v["symbol"],
                name=v.get("name", v["symbol"]),
                meaning=v.get("meaning", v["symbol"]),
                domain=v.get("domain", "R"),
                kind=SymbolKind(v.get("kind", "state_variable")),
                owner_actor_id=v.get("owner_actor_id"),
            )
            for v in model_cfg.get("variables") or []
        ],
        parameters=[
            ModelParameter(
                symbol=p["symbol"],
                name=p.get("name", p["symbol"]),
                meaning=p.get("meaning", p["symbol"]),
                domain=p.get("domain", "R"),
            )
            for p in model_cfg.get("parameters") or []
        ],
        timing=[
            ModelTimingStage(
                stage_number=stage["stage_number"],
                name=stage.get("name", f"stage {stage['stage_number']}"),
                description=stage.get("description") or f"Stage {stage['stage_number']}",
                actor_ids=list(stage.get("actor_ids") or []),
            )
            for stage in model_cfg.get("timing") or []
        ],
        payoffs=[
            PayoffFunction(
                actor_id=pf["actor_id"],
                objective_type=pf.get("objective_type", "profit"),
                expression=Expression(
                    expression=pf["expression"],
                    symbols_used=list(pf.get("symbols_used") or []),
                ),
                decision_variables=list(pf.get("decision_variables") or []),
                parameters=list(pf.get("parameters") or []),
            )
            for pf in model_cfg.get("payoffs") or []
        ],
    )
    model_id = f"{case.id}-{run_suffix}-model"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=model,
            artifact_type="formal_analytical_model",
            producer=producer,
            artifact_id=model_id,
        )
    )

    deriver_config = dict(case.input.get("equilibrium_config") or {})
    verifier = EquilibriumVerifierService(artifact_store=artifact_store)
    deriver = EquilibriumDeriverService(
        model_router=FixtureModelRouter(case.input.get("llm_fixtures") or []),
        artifact_store=artifact_store,
        verifier=verifier,
        model_role="reasoning",
        revision_role="reasoning",
        max_revisions=deriver_config.get("max_revisions", 2),
        max_llm_calls=deriver_config.get("max_llm_calls", 10),
    )
    await deriver.derive(model_id)

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# numerical_analysis workflow (Phase 6E): real Phase 3E pipeline
# ---------------------------------------------------------------------------


async def run_numerical_workflow(
    *,
    artifact_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real Phase 3E pipeline: fixture verified equilibrium
    (model + EquilibriumCandidate + EquilibriumAnalysis) -> real
    NumericalAnalysisService -> sweeps/results/robustness/welfare. All
    fixtures are run-unique so numerical idempotency never stales re-runs."""
    from research_harness.plugins.research.numerical_analysis.plugin import (
        NumericalAnalysisService,
    )
    from research_harness.research.schemas.equilibrium import (
        EquilibriumAnalysis,
        EquilibriumAnalysisStatus,
        EquilibriumCandidate,
        EquilibriumExpression,
        SolutionMethod,
        VerificationStatus,
    )
    from research_harness.research.schemas.model import (
        Expression,
        FormalAnalyticalModel,
        ModelActor,
        ModelParameter,
        ModelTimingStage,
        ModelVariable,
        PayoffFunction,
        SymbolKind,
    )
    from research_harness.research.schemas.proposition import (
        Proposition,
        PropositionClaimType,
        PropositionStatus,
        PropositionVerification,
        PropositionVerificationStatus,
    )

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]
    model_cfg = case.input.get("model") or {}

    model = FormalAnalyticalModel(
        selected_mechanism_id=f"{case.id}-mech",
        title=model_cfg["title"],
        description=model_cfg.get("description") or model_cfg["title"],
        actors=[
            ModelActor(actor_id=a["id"], name=a["name"], strategic=a.get("strategic", True))
            for a in model_cfg.get("actors") or []
        ],
        variables=[
            ModelVariable(
                symbol=v["symbol"],
                name=v.get("name", v["symbol"]),
                meaning=v.get("meaning", v["symbol"]),
                domain=v.get("domain", "R"),
                kind=SymbolKind(v.get("kind", "state_variable")),
                owner_actor_id=v.get("owner_actor_id"),
            )
            for v in model_cfg.get("variables") or []
        ],
        parameters=[
            ModelParameter(
                symbol=p["symbol"],
                name=p.get("name", p["symbol"]),
                meaning=p.get("meaning", p["symbol"]),
                domain=p.get("domain", "R"),
            )
            for p in model_cfg.get("parameters") or []
        ],
        timing=[
            ModelTimingStage(
                stage_number=stage["stage_number"],
                name=stage.get("name", f"stage {stage['stage_number']}"),
                description=stage.get("description") or f"Stage {stage['stage_number']}",
                actor_ids=list(stage.get("actor_ids") or []),
            )
            for stage in model_cfg.get("timing") or []
        ],
        payoffs=[
            PayoffFunction(
                actor_id=pf["actor_id"],
                objective_type=pf.get("objective_type", "profit"),
                expression=Expression(
                    expression=pf["expression"],
                    symbols_used=list(pf.get("symbols_used") or []),
                ),
                decision_variables=list(pf.get("decision_variables") or []),
                parameters=list(pf.get("parameters") or []),
            )
            for pf in model_cfg.get("payoffs") or []
        ],
    )
    model_id = f"{case.id}-{run_suffix}-model"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=model,
            artifact_type="formal_analytical_model",
            producer=producer,
            artifact_id=model_id,
        )
    )

    candidate_cfg = case.input.get("candidate") or {}
    candidate = EquilibriumCandidate(
        model_id=model_id,
        expressions=[
            EquilibriumExpression(
                variable=e["variable"],
                expression=Expression(
                    expression=e["expression"],
                    symbols_used=list(e.get("symbols_used") or []),
                ),
                conditions=list(e.get("conditions") or []),
                solution_method=SolutionMethod(candidate_cfg.get("method", "simultaneous")),
            )
            for e in candidate_cfg.get("expressions") or []
        ],
        decision_variables=list(candidate_cfg.get("decision_variables") or []),
        solution_method=SolutionMethod(candidate_cfg.get("method", "simultaneous")),
        proposed_by="sympy",
        verification_status=VerificationStatus.verified,
    )
    candidate_id = f"{case.id}-{run_suffix}-candidate"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=candidate,
            artifact_type="equilibrium_candidate",
            producer=producer,
            artifact_id=candidate_id,
        )
    )

    analysis = EquilibriumAnalysis(
        model_id=model_id,
        candidate_ids=[candidate_id],
        verification_ids=[],
        selected_candidate_id=candidate_id,
        status=EquilibriumAnalysisStatus.derived,
        solution_order=list(candidate_cfg.get("solution_order") or []),
        solution_method=SolutionMethod(candidate_cfg.get("method", "simultaneous")),
        revision_rounds=0,
    )
    analysis_id = f"{case.id}-{run_suffix}-analysis"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=analysis,
            artifact_type="equilibrium_analysis",
            producer=producer,
            artifact_id=analysis_id,
        )
    )

    for i, prop_cfg in enumerate(case.input.get("propositions") or []):
        prop = Proposition(
            model_id=model_id,
            equilibrium_candidate_id=candidate_id,
            comparative_statics_analysis_id=f"{case.id}-cs",
            statement=prop_cfg["statement"],
            claim_type=PropositionClaimType(prop_cfg.get("claim_type", "monotonicity")),
            outcome_variable=prop_cfg.get("outcome_variable"),
            parameter=prop_cfg.get("parameter"),
            expected_sign=prop_cfg.get("expected_sign"),
            conditions=list(prop_cfg.get("conditions") or []),
            supporting_static_ids=[],
            status=PropositionStatus.verified,
        )
        prop_id = f"{case.id}-{run_suffix}-prop-{i}"
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=prop,
                artifact_type="proposition",
                producer=producer,
                artifact_id=prop_id,
            )
        )
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=PropositionVerification(
                    proposition_id=prop_id,
                    model_id=model_id,
                    status=PropositionVerificationStatus.verified,
                    checks=[],
                ),
                artifact_type="proposition_verification",
                producer=producer,
                artifact_id=f"{case.id}-{run_suffix}-propv-{i}",
            )
        )

    numerical_config = dict(case.input.get("numerical_config") or {})
    svc = NumericalAnalysisService(
        artifact_store=artifact_store,
        blob_store=None,
        model_role="reasoning",
        max_points=numerical_config.get("max_points", 10000),
        artifact_point_threshold=numerical_config.get("artifact_point_threshold", 500),
    )
    await svc.run(analysis_id)

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# Phase 6F shared fixture: run-unique model + verified candidate + analysis
# ---------------------------------------------------------------------------


async def _put_equilibrium_fixture(
    *,
    artifact_store: Any,
    case: BenchmarkCase,
    run_suffix: str,
    producer: str,
    mechanism_id: str | None = None,
) -> tuple[str, str, str]:
    """Create the run-unique fixture FormalAnalyticalModel + verified
    EquilibriumCandidate + EquilibriumAnalysis used by the Phase 6F/6G
    workflows (comparative statics / propositions / results / manuscript),
    returning their artifact ids."""
    from research_harness.research.schemas.equilibrium import (
        EquilibriumAnalysis,
        EquilibriumAnalysisStatus,
        EquilibriumCandidate,
        EquilibriumExpression,
        SolutionMethod,
        VerificationStatus,
    )
    from research_harness.research.schemas.model import (
        Expression,
        FormalAnalyticalModel,
        ModelActor,
        ModelParameter,
        ModelTimingStage,
        ModelVariable,
        PayoffFunction,
        SymbolKind,
    )

    model_cfg = case.input.get("model") or {}
    model = FormalAnalyticalModel(
        selected_mechanism_id=mechanism_id or f"{case.id}-mech",
        title=model_cfg["title"],
        description=model_cfg.get("description") or model_cfg["title"],
        actors=[
            ModelActor(actor_id=a["id"], name=a["name"], strategic=a.get("strategic", True))
            for a in model_cfg.get("actors") or []
        ],
        variables=[
            ModelVariable(
                symbol=v["symbol"],
                name=v.get("name", v["symbol"]),
                meaning=v.get("meaning", v["symbol"]),
                domain=v.get("domain", "R"),
                kind=SymbolKind(v.get("kind", "state_variable")),
                owner_actor_id=v.get("owner_actor_id"),
            )
            for v in model_cfg.get("variables") or []
        ],
        parameters=[
            ModelParameter(
                symbol=p["symbol"],
                name=p.get("name", p["symbol"]),
                meaning=p.get("meaning", p["symbol"]),
                domain=p.get("domain", "R"),
            )
            for p in model_cfg.get("parameters") or []
        ],
        timing=[
            ModelTimingStage(
                stage_number=stage["stage_number"],
                name=stage.get("name", f"stage {stage['stage_number']}"),
                description=stage.get("description") or f"Stage {stage['stage_number']}",
                actor_ids=list(stage.get("actor_ids") or []),
            )
            for stage in model_cfg.get("timing") or []
        ],
        payoffs=[
            PayoffFunction(
                actor_id=pf["actor_id"],
                objective_type=pf.get("objective_type", "profit"),
                expression=Expression(
                    expression=pf["expression"],
                    symbols_used=list(pf.get("symbols_used") or []),
                ),
                decision_variables=list(pf.get("decision_variables") or []),
                parameters=list(pf.get("parameters") or []),
            )
            for pf in model_cfg.get("payoffs") or []
        ],
    )
    model_id = f"{case.id}-{run_suffix}-model"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=model,
            artifact_type="formal_analytical_model",
            producer=producer,
            artifact_id=model_id,
        )
    )

    candidate_cfg = case.input.get("candidate") or {}
    candidate = EquilibriumCandidate(
        model_id=model_id,
        expressions=[
            EquilibriumExpression(
                variable=e["variable"],
                expression=Expression(
                    expression=e["expression"],
                    symbols_used=list(e.get("symbols_used") or []),
                ),
                conditions=list(e.get("conditions") or []),
                solution_method=SolutionMethod(candidate_cfg.get("method", "simultaneous")),
            )
            for e in candidate_cfg.get("expressions") or []
        ],
        decision_variables=list(candidate_cfg.get("decision_variables") or []),
        solution_method=SolutionMethod(candidate_cfg.get("method", "simultaneous")),
        proposed_by="sympy",
        verification_status=VerificationStatus.verified,
    )
    candidate_id = f"{case.id}-{run_suffix}-candidate"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=candidate,
            artifact_type="equilibrium_candidate",
            producer=producer,
            artifact_id=candidate_id,
        )
    )

    analysis = EquilibriumAnalysis(
        model_id=model_id,
        candidate_ids=[candidate_id],
        verification_ids=[],
        selected_candidate_id=candidate_id,
        status=EquilibriumAnalysisStatus.derived,
        solution_order=list(candidate_cfg.get("solution_order") or []),
        solution_method=SolutionMethod(candidate_cfg.get("method", "simultaneous")),
        revision_rounds=0,
    )
    analysis_id = f"{case.id}-{run_suffix}-analysis"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=analysis,
            artifact_type="equilibrium_analysis",
            producer=producer,
            artifact_id=analysis_id,
        )
    )
    return model_id, candidate_id, analysis_id


# ---------------------------------------------------------------------------
# comparative_statics workflow (Phase 6F): real Phase 3D pipeline
# ---------------------------------------------------------------------------


async def run_comparative_statics_workflow(
    *,
    artifact_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real Phase 3D comparative statics: fixture verified
    equilibrium -> real ComparativeStaticsService -> ComparativeStatic
    artifacts. Fixtures are run-unique so service idempotency never stales
    re-runs."""
    from research_harness.plugins.research.comparative_statics.plugin import (
        ComparativeStaticsService,
    )

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]
    _, _, analysis_id = await _put_equilibrium_fixture(
        artifact_store=artifact_store,
        case=case,
        run_suffix=run_suffix,
        producer=producer,
    )

    svc = ComparativeStaticsService(artifact_store=artifact_store)
    await svc.run(analysis_id)

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# proposition_generation workflow (Phase 6F): real Phase 3D pipeline
# ---------------------------------------------------------------------------


async def run_proposition_workflow(
    *,
    artifact_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real Phase 3D proposition pipeline: fixture verified
    equilibrium -> real ComparativeStaticsService -> real
    PropositionGeneratorService (scripted proposition/critique/interpretation
    responses) -> real PropositionVerifierService + PropositionCriticService.
    The ComparativeStatic ids are run-unique; the scripted responses cite the
    case-scoped ids and are rewritten below."""
    from research_harness.plugins.research.comparative_statics.plugin import (
        ComparativeStaticsService,
    )
    from research_harness.plugins.research.proposition_critic.plugin import (
        PropositionCriticService,
    )
    from research_harness.plugins.research.proposition_generator.plugin import (
        PropositionGeneratorService,
    )
    from research_harness.plugins.research.proposition_verifier.plugin import (
        PropositionVerifierService,
    )
    from research_harness.research.schemas.proposition import ComparativeStatic

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]
    model_cfg = case.input.get("model") or {}
    candidate_cfg = case.input.get("candidate") or {}
    _, _, analysis_id = await _put_equilibrium_fixture(
        artifact_store=artifact_store,
        case=case,
        run_suffix=run_suffix,
        producer=producer,
    )

    cs_svc = ComparativeStaticsService(artifact_store=artifact_store)
    cs_execution_id = await cs_svc.run(analysis_id)
    cs_analysis_id = await cs_svc.resolve_analysis(cs_execution_id)

    static_ids_by_pair: dict[tuple[str, str], str] = {}
    for env in await artifact_store.list():
        if env.artifact_type != "comparative_static":
            continue
        try:
            s = env.parse_payload(ComparativeStatic)
        except Exception:  # noqa: BLE001
            continue
        static_ids_by_pair[(s.outcome_variable, s.parameter)] = env.artifact_id

    id_map: dict[str, str] = {}
    for i, (variable, param) in enumerate(
        (e["variable"], p["symbol"])
        for e in candidate_cfg.get("expressions") or []
        for p in model_cfg.get("parameters") or []
    ):
        produced_id = static_ids_by_pair.get((variable, param))
        if produced_id is None:
            raise BenchmarkError(f"no comparative static produced for {variable}/{param}")
        id_map[f"{case.id}-static-{i}"] = produced_id

    router = FixtureModelRouter(_rewrite_ids(case.input.get("llm_fixtures") or [], id_map))
    verifier = PropositionVerifierService(artifact_store=artifact_store)
    critic = PropositionCriticService(
        model_router=router,
        artifact_store=artifact_store,
        critic_role="critic",
        interpretation_role="reasoning",
    )
    generator = PropositionGeneratorService(
        model_router=router,
        artifact_store=artifact_store,
        verifier=verifier,
        critic=critic,
        generator_role="reasoning",
        max_propositions=8,
        max_llm_calls=20,
    )
    await generator.generate(cs_analysis_id)

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# Phase 6G shared fixture: run-unique Phase 3 chain + id map for scripted
# responses (results assembly / manuscript grounding workflows)
# ---------------------------------------------------------------------------


async def _put_phase3_fixture(
    *,
    artifact_store: Any,
    case: BenchmarkCase,
    run_suffix: str,
    producer: str,
) -> dict[str, Any]:
    """Create the run-unique fixture Phase 3 chain used by the Phase 6G
    workflows: model + real mechanism + gap + verified candidate/analysis +
    propositions/verifications + comparative statics + numerical
    results/robustness/experiment (+ evidence/synthesis/papers for
    manuscript grounding). Returns the run-unique ids and the case-scoped ->
    run-unique id map used to rewrite scripted responses."""
    from research_harness.research.schemas.evidence import EvidenceItem, Locator
    from research_harness.research.schemas.gap import GapAnalysis, GapType, ResearchGap
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.mechanism import SelectedMechanism
    from research_harness.research.schemas.numerical import (
        NumericalExperiment,
        NumericalResult,
        RobustnessCheck,
        RobustnessCheckType,
        RobustnessOutcome,
    )
    from research_harness.research.schemas.proposition import (
        ComparativeStatic,
        Proposition,
        PropositionClaimType,
        PropositionStatus,
        PropositionVerification,
        PropositionVerificationStatus,
        StaticSign,
    )
    from research_harness.research.schemas.synthesis import (
        SynthesisStatement,
        SynthesisStatementType,
    )

    gap_cfg = case.input.get("gap") or {}
    mechanism_cfg = case.input.get("mechanism") or {}
    gap_id = f"{case.id}-{run_suffix}-gap"
    mechanism_id = f"{case.id}-{run_suffix}-mechanism"

    model_id, candidate_id, analysis_id = await _put_equilibrium_fixture(
        artifact_store=artifact_store,
        case=case,
        run_suffix=run_suffix,
        producer=producer,
        mechanism_id=mechanism_id,
    )
    id_map: dict[str, str] = {}
    id_map[f"{case.id}-model"] = model_id
    id_map[f"{case.id}-candidate"] = candidate_id
    id_map[f"{case.id}-analysis"] = analysis_id
    id_map[f"{case.id}-gap"] = gap_id
    id_map[f"{case.id}-mechanism"] = mechanism_id

    evidence_ids: list[str] = []
    for i, ev in enumerate(case.input.get("evidence") or []):
        eid = f"{case.id}-{run_suffix}-evidence-{i}"
        id_map[f"{case.id}-evidence-{i}"] = eid
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=EvidenceItem(
                    statement=ev["statement"],
                    source_artifact_id=ev.get("source_artifact_id") or f"{case.id}-doc-{i}",
                    category=ev.get("category"),
                    locator=Locator(page=1, pages=[1]),
                    extraction_method="model-assisted",
                    confidence=0.9,
                ),
                artifact_type="evidence_item",
                producer=producer,
                artifact_id=eid,
            )
        )
        evidence_ids.append(eid)

    paper_ids: list[str] = []
    for i, _ in enumerate(case.input.get("papers") or []):
        pid = f"{case.id}-{run_suffix}-paper-{i}"
        id_map[f"{case.id}-paper-{i}"] = pid
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=PaperIdentity(
                    member_paper_artifact_ids=[f"{case.id}-record-{i}"],
                    resolution_method=ResolutionMethod.exact_identifier,
                ),
                artifact_type="paper_identity",
                producer=producer,
                artifact_id=pid,
            )
        )
        paper_ids.append(pid)

    stmt_ids: list[str] = []
    for i, st in enumerate(case.input.get("synthesis") or []):
        sid = f"{case.id}-{run_suffix}-stmt-{i}"
        id_map[f"{case.id}-stmt-{i}"] = sid
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=SynthesisStatement(
                    statement=st["statement"],
                    type=SynthesisStatementType(st.get("type", "consensus")),
                    supporting_evidence_ids=[
                        id_map.get(str(x), str(x)) for x in (st.get("evidence_ids") or [])
                    ],
                    supporting_paper_identity_ids=[
                        id_map.get(str(x), str(x)) for x in (st.get("paper_ids") or [])
                    ],
                    evidence_items_supporting=len(st.get("evidence_ids") or []),
                    papers_supporting=len(st.get("paper_ids") or []),
                ),
                artifact_type="synthesis_statement",
                producer=producer,
                artifact_id=sid,
            )
        )
        stmt_ids.append(sid)

    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=ResearchGap(
                title=gap_cfg["title"],
                gap_type=GapType(gap_cfg.get("gap_type", "mechanism_gap")),
                description=gap_cfg.get("description") or gap_cfg["title"],
                supporting_synthesis_statement_ids=stmt_ids,
                supporting_evidence_ids=evidence_ids,
                relevant_paper_identity_ids=paper_ids,
                supporting_papers=len(paper_ids),
                supporting_evidence_items=len(evidence_ids),
                strength=gap_cfg.get("strength", "tentative"),
            ),
            artifact_type="research_gap",
            producer=producer,
            artifact_id=gap_id,
        )
    )
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=GapAnalysis(
                literature_synthesis_id=f"{case.id}-synthesis",
                evidence_corpus_id=f"{case.id}-corpus",
                gap_ids=[gap_id],
                ranked_gap_ids=[gap_id],
            ),
            artifact_type="gap_analysis",
            producer=producer,
            artifact_id=f"{case.id}-{run_suffix}-gapanalysis",
        )
    )
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=SelectedMechanism(
                gap_id=gap_id,
                gap_selection_id=f"{case.id}-selection",
                mechanism_candidate_id=f"{case.id}-mechcand",
                name=mechanism_cfg.get("name", "Fixture mechanism"),
                description=mechanism_cfg.get(
                    "description", "Fixture mechanism for benchmark cases."
                ),
                causal_logic=mechanism_cfg.get("causal_logic", "fixture causal logic"),
                actors=list(mechanism_cfg.get("actors") or []),
            ),
            artifact_type="selected_mechanism",
            producer=producer,
            artifact_id=mechanism_id,
        )
    )
    prop_ids: list[str] = []
    failed_prop_ids: list[str] = []
    for i, p in enumerate(case.input.get("propositions") or []):
        pid = f"{case.id}-{run_suffix}-prop-{i}"
        id_map[f"{case.id}-prop-{i}"] = pid
        verification = p.get("verification", "verified")
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=Proposition(
                    model_id=model_id,
                    equilibrium_candidate_id=candidate_id,
                    comparative_statics_analysis_id=f"{case.id}-{run_suffix}-cs",
                    statement=p["statement"],
                    claim_type=PropositionClaimType(p.get("claim_type", "monotonicity")),
                    outcome_variable=p.get("outcome_variable"),
                    parameter=p.get("parameter"),
                    expected_sign=p.get("expected_sign"),
                    conditions=list(p.get("conditions") or []),
                    supporting_static_ids=[
                        id_map.get(str(x), str(x)) for x in (p.get("supporting_static_ids") or [])
                    ],
                    status=(
                        PropositionStatus.failed
                        if verification == "failed"
                        else PropositionStatus.verified
                    ),
                    proposed_by="llm",
                ),
                artifact_type="proposition",
                producer=producer,
                artifact_id=pid,
            )
        )
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=PropositionVerification(
                    proposition_id=pid,
                    model_id=model_id,
                    status=PropositionVerificationStatus(verification),
                    checks=[],
                ),
                artifact_type="proposition_verification",
                producer=producer,
                artifact_id=f"{case.id}-{run_suffix}-propv-{i}",
            )
        )
        prop_ids.append(pid)
        if verification == "failed":
            failed_prop_ids.append(pid)

    static_ids: list[str] = []
    for i, s in enumerate(case.input.get("statics") or []):
        sid = f"{case.id}-{run_suffix}-static-{i}"
        id_map[f"{case.id}-static-{i}"] = sid
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=ComparativeStatic(
                    model_id=model_id,
                    equilibrium_candidate_id=candidate_id,
                    outcome_variable=s["outcome_variable"],
                    parameter=s["parameter"],
                    derivative_expression=__import__(
                        "research_harness.research.schemas.model", fromlist=["Expression"]
                    ).Expression(expression=s["derivative"], symbols_used=[]),
                    sign=StaticSign(s["sign"]),
                    conditions=list(s.get("conditions") or []),
                    derived_by="sympy",
                ),
                artifact_type="comparative_static",
                producer=producer,
                artifact_id=sid,
            )
        )
        static_ids.append(sid)

    result_ids: list[str] = []
    for i, r in enumerate(case.input.get("numerical_results") or []):
        rid = f"{case.id}-{run_suffix}-result-{i}"
        id_map[f"{case.id}-result-{i}"] = rid
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=NumericalResult(
                    model_id=model_id,
                    equilibrium_candidate_id=candidate_id,
                    experiment_id=f"{case.id}-{run_suffix}-experiment",
                    scenario=r.get("scenario", "baseline"),
                    group=r.get("group"),
                    x_parameter=r.get("x_parameter"),
                    x_value=r.get("x_value"),
                    parameter_values=r.get("parameter_values") or {},
                    outcomes=r.get("outcomes") or {},
                    feasible=r.get("feasible", True),
                    conditions=list(r.get("conditions") or []),
                ),
                artifact_type="numerical_result",
                producer=producer,
                artifact_id=rid,
            )
        )
        result_ids.append(rid)

    robustness_ids: list[str] = []
    for i, rb in enumerate(case.input.get("robustness") or []):
        rbid = f"{case.id}-{run_suffix}-robust-{i}"
        id_map[f"{case.id}-robust-{i}"] = rbid
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=RobustnessCheck(
                    model_id=model_id,
                    equilibrium_candidate_id=candidate_id,
                    experiment_id=f"{case.id}-{run_suffix}-experiment",
                    proposition_id=id_map.get(
                        str(rb.get("proposition_id")), rb.get("proposition_id")
                    ),
                    check_type=RobustnessCheckType(rb.get("check_type", "proposition_support")),
                    description=rb.get("description", "fixture robustness check"),
                    outcome=RobustnessOutcome(rb.get("outcome", "supported")),
                    admissible_points=rb.get("admissible_points", 10),
                ),
                artifact_type="robustness_check",
                producer=producer,
                artifact_id=rbid,
            )
        )
        robustness_ids.append(rbid)

    experiment_id = f"{case.id}-{run_suffix}-experiment"
    id_map[f"{case.id}-experiment"] = experiment_id
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=NumericalExperiment(
                model_id=model_id,
                equilibrium_candidate_id=candidate_id,
                results=result_ids,
                robustness=robustness_ids,
                welfare=[],
                status="completed",
                summary=case.input.get("experiment_summary")
                or "Fixture deterministic numerical experiment.",
            ),
            artifact_type="numerical_experiment",
            producer=producer,
            artifact_id=experiment_id,
        )
    )

    return {
        "model_id": model_id,
        "candidate_id": candidate_id,
        "analysis_id": analysis_id,
        "gap_id": gap_id,
        "mechanism_id": mechanism_id,
        "experiment_id": experiment_id,
        "prop_ids": prop_ids,
        "failed_prop_ids": failed_prop_ids,
        "static_ids": static_ids,
        "result_ids": result_ids,
        "evidence_ids": evidence_ids,
        "paper_ids": paper_ids,
        "stmt_ids": stmt_ids,
        "id_map": id_map,
    }


# ---------------------------------------------------------------------------
# results_assembly workflow (Phase 6G): real Phase 4A pipeline
# ---------------------------------------------------------------------------


async def run_results_assembly_workflow(
    *,
    artifact_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real Phase 4A pipeline: fixture Phase 3 outputs -> real
    ResultsAssemblerService (scripted responses; deterministic validation
    rejects failed-proposition support, unsupported ids, dropped conditions,
    and normalizes global-novelty claims) -> real ResultsCriticService.
    Fixtures are run-unique so service idempotency never stales re-runs."""
    from research_harness.plugins.research.results_assembler.plugin import (
        ResultsAssemblerService,
    )
    from research_harness.plugins.research.results_critic.plugin import (
        ResultsCriticService,
    )

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]
    fixture = await _put_phase3_fixture(
        artifact_store=artifact_store,
        case=case,
        run_suffix=run_suffix,
        producer=producer,
    )

    router = FixtureModelRouter(
        _rewrite_ids(case.input.get("llm_fixtures") or [], fixture["id_map"])
    )
    assembler = ResultsAssemblerService(
        model_router=router,
        artifact_store=artifact_store,
        assembler_role="reasoning",
        max_findings=12,
        max_contributions=8,
        max_implications=12,
        max_llm_calls=10,
    )
    await assembler.assemble(fixture["experiment_id"])

    package_envs = [
        e
        for e in await artifact_store.list()
        if e.artifact_id not in before and e.artifact_type == "results_package"
    ]
    if not package_envs:
        raise BenchmarkError("results assembly produced no results_package")
    package_env = max(package_envs, key=lambda e: e.created_at)

    critic = ResultsCriticService(
        model_router=router,
        artifact_store=artifact_store,
        critic_role="critic",
    )
    await critic.critique(package_env.artifact_id)

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# manuscript_grounding workflow (Phase 6G): real Phase 4B pipeline
# ---------------------------------------------------------------------------


async def run_manuscript_grounding_workflow(
    *,
    artifact_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real Phase 4B pipeline: fixture ResearchResultsPackage +
    literature artifacts -> real ManuscriptDrafterService (deterministic
    outline + scripted section drafts; validation rejects unsupported claims,
    missing/hallucinated citations, failed-proposition grounding, and
    normalizes novelty) -> real ManuscriptCriticService -> optional real
    revision (flagged sections re-drafted, others reused)."""
    from research_harness.plugins.research.manuscript_critic.plugin import (
        ManuscriptCriticService,
    )
    from research_harness.plugins.research.manuscript_drafter.plugin import (
        ManuscriptDrafterService,
    )
    from research_harness.research.schemas.results import (
        ContributionClaim,
        ContributionType,
        FindingType,
        ImplicationClaimType,
        ImplicationKind,
        ResearchFinding,
        ResearchImplication,
        ResearchResultsPackage,
        ResultsPackageStatus,
    )

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]
    fixture = await _put_phase3_fixture(
        artifact_store=artifact_store,
        case=case,
        run_suffix=run_suffix,
        producer=producer,
    )
    id_map = fixture["id_map"]

    finding_ids: list[str] = []
    for i, f in enumerate(case.input.get("findings") or []):
        fid = f"{case.id}-{run_suffix}-finding-{i}"
        id_map[f"{case.id}-finding-{i}"] = fid
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=ResearchFinding(
                    model_id=fixture["model_id"],
                    equilibrium_candidate_id=fixture["candidate_id"],
                    statement=f["statement"],
                    finding_type=FindingType(f.get("finding_type", "analytical_result")),
                    supporting_proposition_ids=[
                        id_map.get(str(x), str(x)) for x in (f.get("proposition_ids") or [])
                    ],
                    supporting_comparative_static_ids=[
                        id_map.get(str(x), str(x)) for x in (f.get("static_ids") or [])
                    ],
                    supporting_numerical_result_ids=[
                        id_map.get(str(x), str(x)) for x in (f.get("result_ids") or [])
                    ],
                    conditions=list(f.get("conditions") or []),
                ),
                artifact_type="research_finding",
                producer=producer,
                artifact_id=fid,
            )
        )
        finding_ids.append(fid)

    contribution_ids: list[str] = []
    for i, c in enumerate(case.input.get("contributions") or []):
        cid = f"{case.id}-{run_suffix}-contribution-{i}"
        id_map[f"{case.id}-contribution-{i}"] = cid
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=ContributionClaim(
                    gap_id=c.get("gap_id", fixture["gap_id"]),
                    finding_ids=[id_map.get(str(x), str(x)) for x in (c.get("finding_ids") or [])],
                    claim=c["claim"],
                    contribution_type=ContributionType(c.get("contribution_type", "theoretical")),
                    advances_literature=c.get("advances_literature", ""),
                    novelty_claim=c.get("novelty_claim"),
                    novelty_normalized=bool(c.get("novelty_normalized", False)),
                ),
                artifact_type="contribution_claim",
                producer=producer,
                artifact_id=cid,
            )
        )
        contribution_ids.append(cid)

    implication_ids: list[str] = []
    for i, imp in enumerate(case.input.get("implications") or []):
        iid = f"{case.id}-{run_suffix}-implication-{i}"
        id_map[f"{case.id}-implication-{i}"] = iid
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=ResearchImplication(
                    implication_kind=ImplicationKind(imp.get("implication_kind", "theory")),
                    claim_type=ImplicationClaimType(imp.get("claim_type", "interpretation")),
                    text=imp["text"],
                    grounded_in_finding_ids=[
                        id_map.get(str(x), str(x)) for x in (imp.get("finding_ids") or [])
                    ],
                ),
                artifact_type="research_implication",
                producer=producer,
                artifact_id=iid,
            )
        )
        implication_ids.append(iid)

    package_cfg = case.input.get("package") or {}
    package_id = f"{case.id}-{run_suffix}-package"
    id_map[f"{case.id}-package"] = package_id
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=ResearchResultsPackage(
                research_question_id=package_cfg.get("research_question_id"),
                gap_id=package_cfg.get("gap_id", fixture["gap_id"]),
                selected_mechanism_id=fixture["mechanism_id"],
                model_id=fixture["model_id"],
                equilibrium_analysis_id=fixture["analysis_id"],
                equilibrium_candidate_id=fixture["candidate_id"],
                numerical_experiment_id=fixture["experiment_id"],
                finding_ids=finding_ids,
                contribution_claim_ids=contribution_ids,
                implication_ids=implication_ids,
                limitations=list(package_cfg.get("limitations") or []),
                status=ResultsPackageStatus.assembled,
                summary="Fixture results package.",
                metadata={"robustness_ids": []},
            ),
            artifact_type="results_package",
            producer=producer,
            artifact_id=package_id,
        )
    )

    router = FixtureModelRouter(_rewrite_ids(case.input.get("llm_fixtures") or [], id_map))
    drafter = ManuscriptDrafterService(
        model_router=router,
        artifact_store=artifact_store,
        drafter_role="reasoning",
        max_llm_calls=100,
    )
    outline_id = await drafter.outline(package_id)
    draft_sections = case.input.get("sections") or []
    await drafter.draft(outline_id, section_ids=draft_sections)

    draft_envs = [
        e
        for e in await artifact_store.list()
        if e.artifact_id not in before and e.artifact_type == "manuscript_draft"
    ]
    if not draft_envs:
        raise BenchmarkError("manuscript drafting produced no manuscript_draft")
    draft_env = max(draft_envs, key=lambda e: e.created_at)

    critic = ManuscriptCriticService(
        model_router=router,
        artifact_store=artifact_store,
        critic_role="critic",
    )
    await critic.critique(draft_env.artifact_id)

    if case.input.get("revise"):
        await drafter.revise(draft_env.artifact_id)

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]
