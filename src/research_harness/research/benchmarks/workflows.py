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

from pydantic import BaseModel, Field, field_validator

from research_harness.contracts.evaluator import envelope_payload_dict
from research_harness.contracts.literature import LiteratureSearchHit, LiteratureSearchPage
from research_harness.contracts.model import Message, ModelCapabilities, ModelResponse
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


class RevalidationReportRecord(BaseModel):
    """Benchmark-only record of per-stage recomputation state (Phase 7A)."""

    benchmark_case_id: str
    stages: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @field_validator("benchmark_case_id")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("benchmark_case_id must be non-empty")
        return v

    model_config = {"extra": "forbid"}


class IngestionIdentityReport(BaseModel):
    """Benchmark-only record of fixture paper id mapping + resolution result
    (Phase 7A.1)."""

    benchmark_case_id: str
    paper_ids: dict[str, str] = Field(default_factory=dict)
    superseded_identity_ids: list[str] = Field(default_factory=list)
    failed_providers: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class GapSelectionReport(BaseModel):
    """Benchmark-only record of fixture gap id mapping + selection outcome
    (Phase 7A.1)."""

    benchmark_case_id: str
    gap_ids: dict[str, str] = Field(default_factory=dict)
    selection_id: str | None = None
    reuse_selection_id: str | None = None
    error: str | None = None

    model_config = {"extra": "forbid"}


class NoveltyRevalidationReport(BaseModel):
    """Benchmark-only record of the two novelty runs (baseline + changed
    literature) for the novelty-revalidation benchmark (Phase 7A.1)."""

    benchmark_case_id: str
    report_a: str
    report_b: str
    gate_a: str | None = None
    gate_b: str | None = None
    package_id: str = ""
    manuscript_id: str = ""
    overall_a: str = ""
    overall_b: str = ""
    assessments_a: list[str] = Field(default_factory=list)
    assessments_b: list[str] = Field(default_factory=list)
    report_b_supersedes_a: bool = False

    model_config = {"extra": "forbid"}


class PackagingReport(BaseModel):
    """Benchmark-only record of the packaging run (Phase 7A.1)."""

    benchmark_case_id: str
    formatted_manuscript_id: str
    package_id: str
    export_ids: list[str] = Field(default_factory=list)
    reexport_ids: list[str] = Field(default_factory=list)
    error: str | None = None

    model_config = {"extra": "forbid"}


class EnrichmentRunRecord(BaseModel):
    """One run of the enrichment workflow against one source set."""

    label: str
    report_id: str
    enrichment_execution_ids: list[str] = Field(default_factory=list)
    preacquisition_execution_ids: list[str] = Field(default_factory=list)
    candidate_bases: dict[str, str] = Field(
        default_factory=dict, description="candidate_assessment_id -> evidence_basis"
    )

    model_config = {"extra": "forbid"}


class EvidenceEnrichmentReport(BaseModel):
    """Benchmark-only record of the Phase 5C-5D enrichment run."""

    benchmark_case_id: str
    runs: list[EnrichmentRunRecord] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


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


def _case_router(
    case: BenchmarkCase,
    model_router: Any | None = None,
    fixtures: list[dict[str, Any]] | None = None,
    id_map: dict[str, str] | None = None,
) -> Any:
    """Return the caller-provided model router (e.g. a tournament candidate
    binding) when given, otherwise build the scripted fixture router from the
    case input (with optional id rewriting). This is how model tournaments
    reuse the existing benchmark workflows without changing benchmark
    definitions or evaluators: the same production services run, but the LLM
    calls are served by the candidate model instead of scripted fixtures."""
    if model_router is not None:
        return model_router
    if fixtures is None:
        fixtures = case.input.get("llm_fixtures") or []
    return FixtureModelRouter(_rewrite_ids(fixtures, id_map or {}))


class FixtureLiteratureSource:
    """Literature source backed by benchmark fixture paper records. Hits are
    returned in fixture order, which doubles as the provider ranking. `get_hits`
    optionally backs `get(identifier)` for Phase 5C-5D enrichment acquisitions."""

    def __init__(
        self,
        provider_name: str,
        papers: list[dict[str, Any]] | str,
        get_hits: dict[str, dict[str, Any]] | None = None,
        get_errors: dict[str, str] | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.fail_all = papers == "fail_all"
        self.papers = (
            [] if self.fail_all else [PaperRecord(**p) for p in papers]  # type: ignore[arg-type]
        )
        self.get_hits = get_hits or {}
        self.get_errors = get_errors or {}  # identifier -> "not_found"|"rate_limited"|"failed"

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
        from research_harness.contracts.literature import (
            LiteratureNotFoundError,
            LiteratureRateLimitError,
        )

        error = self.get_errors.get(identifier)
        if error == "rate_limited":
            raise LiteratureRateLimitError(f"fixture rate limited for {identifier}")
        if error == "failed":
            raise RuntimeError(f"fixture provider failed for {identifier}")
        hit = self.get_hits.get(identifier)
        if hit is None:
            raise LiteratureNotFoundError(f"no fixture record for {identifier}")
        record = PaperRecord(**hit)
        return LiteratureSearchHit(
            paper=record,
            raw_payload={"title": record.title},
            provider=self.provider_name,
            provider_record_id=record.doi or identifier,
        )


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
    get_sources = case.input.get("get_sources") or {}
    return {
        provider: FixtureLiteratureSource(
            provider,
            papers,
            get_hits=dict((get_sources.get(provider) or {}).get("get_hits") or {}),
            get_errors=dict((get_sources.get(provider) or {}).get("get_errors") or {}),
        )
        for provider, papers in (case.input.get("fixture_sources") or {}).items()
    }


# ---------------------------------------------------------------------------
# novelty_validation workflow (Phase 6A)
# ---------------------------------------------------------------------------


async def run_novelty_workflow(
    *,
    model_router: Any | None = None,
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
        model_router=_case_router(case, model_router),
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
    model_router: Any | None = None,
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
    model_router: Any | None = None,
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
        model_router=_case_router(case, model_router),
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
    model_router: Any | None = None,
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
    router = _case_router(case, model_router)
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
    model_router: Any | None = None,
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
    router = _case_router(case, model_router)
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
    model_router: Any | None = None,
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
        model_router=_case_router(case, model_router),
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
    model_router: Any | None = None,
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
    router = _case_router(case, model_router, id_map=id_map)
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
    model_router: Any | None = None,
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
        model_router=_case_router(case, model_router),
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
    model_router: Any | None = None,
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
    model_router: Any | None = None,
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
    model_router: Any | None = None,
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

    router = _case_router(case, model_router, id_map=id_map)
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
    model_router: Any | None = None,
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

    router = _case_router(case, model_router, id_map=fixture["id_map"])
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
    model_router: Any | None = None,
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

    router = _case_router(case, model_router, id_map=id_map)
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


# ---------------------------------------------------------------------------
# research_pipeline_e2e workflow (Phase 6H): the real production chain
# retrieval -> screening -> evidence -> synthesis -> gap -> mechanism ->
# model -> equilibrium -> propositions -> numerical -> results -> manuscript
# -> citation formatting, over a small fixture corpus with scripted responses
# ---------------------------------------------------------------------------


def _e2e_router(
    case: BenchmarkCase,
    fixtures: list[dict[str, Any]],
    id_map: dict[str, str],
    model_router: Any | None = None,
) -> Any:
    if model_router is not None:
        return model_router
    return FixtureModelRouter(_rewrite_ids(fixtures, id_map))


async def run_e2e_workflow(
    *,
    model_router: Any | None = None,
    artifact_store: Any,
    ingestor: Any,
    identity_resolver: Any,
    blob_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real production chain end to end across representative
    stages. Every stage uses the real production service; model responses
    are scripted. Fixtures are run-unique so production idempotency never
    stales re-runs."""
    from research_harness.plugins.autonomy.configurable.plugin import (
        ConfigurableAutonomyPolicy,
    )
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractorService,
    )
    from research_harness.plugins.literature.evidence_orchestrator.plugin import (
        EvidenceOrchestratorService,
    )
    from research_harness.plugins.literature.gap_analyzer.plugin import GapAnalyzerService
    from research_harness.plugins.literature.screening_orchestrator.plugin import (
        ScreeningOrchestratorService,
    )
    from research_harness.plugins.literature.screening_protocol_builder.plugin import (
        ScreeningProtocolBuilderService,
    )
    from research_harness.plugins.literature.screening_view_builder.plugin import (
        ScreeningViewBuilderService,
    )
    from research_harness.plugins.literature.search_orchestrator.plugin import (
        LiteratureSearchOrchestratorService,
    )
    from research_harness.plugins.literature.synthesis.plugin import (
        LiteratureSynthesizerService,
    )
    from research_harness.plugins.literature.title_abstract_screener.plugin import (
        TitleAbstractScreenerService,
    )
    from research_harness.plugins.research.comparative_statics.plugin import (
        ComparativeStaticsService,
    )
    from research_harness.plugins.research.equilibrium_deriver.plugin import (
        EquilibriumDeriverService,
    )
    from research_harness.plugins.research.equilibrium_verifier.plugin import (
        EquilibriumVerifierService,
    )
    from research_harness.plugins.research.gap_selection.plugin import GapSelectionService
    from research_harness.plugins.research.manuscript_critic.plugin import (
        ManuscriptCriticService,
    )
    from research_harness.plugins.research.manuscript_drafter.plugin import (
        ManuscriptDrafterService,
    )
    from research_harness.plugins.research.mechanism_critic.plugin import (
        MechanismCriticService,
    )
    from research_harness.plugins.research.mechanism_generator.plugin import (
        MechanismGeneratorService,
    )
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderService
    from research_harness.plugins.research.numerical_analysis.plugin import (
        NumericalAnalysisService,
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
    from research_harness.plugins.research.publication_formatter.plugin import (
        PublicationFormatterService,
    )
    from research_harness.plugins.research.results_assembler.plugin import (
        ResultsAssemblerService,
    )
    from research_harness.plugins.research.results_critic.plugin import (
        ResultsCriticService,
    )
    from research_harness.research.schemas.document_acquisition import (
        AcquisitionStatus,
        DocumentAcquisition,
    )
    from research_harness.research.schemas.execution import LiteratureSearchExecution
    from research_harness.research.schemas.full_text import (
        FullTextCorpus,
        FullTextDocument,
        TextStatus,
    )
    from research_harness.research.schemas.identity import PaperIdentity
    from research_harness.research.schemas.project import ResearchQuestion
    from research_harness.research.schemas.publication import PublicationProfile
    from research_harness.research.schemas.query import LiteratureQuery
    from research_harness.research.schemas.strategy import LiteratureSearchStrategy

    if blob_store is None:
        raise BenchmarkError("research_pipeline_e2e requires a blob store")

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]
    fixtures = case.input.get("llm_fixtures") or []
    id_map: dict[str, str] = {}
    autonomy = ConfigurableAutonomyPolicy(mode="high")

    # ---- stage 1: literature retrieval (real orchestrator) ---------------
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
        objective="e2e retrieval",
        query_artifact_ids=query_ids,
        source_names=list(case.input.get("providers") or []),
    )
    await _put_explicit(
        artifact_store,
        ArtifactEnvelope.create(
            payload=strategy,
            artifact_type="literature_search_strategy",
            producer=producer,
            artifact_id=f"{case.id}-strategy",
        ),
    )
    orchestrator = LiteratureSearchOrchestratorService(
        artifact_store=artifact_store,
        ingestor=ingestor,
        service_lookup=make_retrieval_lookup(sources, identity_resolver),
    )
    await orchestrator.execute(f"{case.id}-strategy")

    # map ingested records -> identity ids by member title
    produced_now = await artifact_store.list()
    records_by_title: dict[str, str] = {}
    for env in produced_now:
        if env.artifact_type != "paper_record":
            continue
        try:
            title = str(env.payload.get("title") or "")
        except Exception:  # noqa: BLE001
            continue
        if title:
            records_by_title[title] = env.artifact_id
    identity_by_title: dict[str, str] = {}
    for env in produced_now:
        if env.artifact_type != "paper_identity":
            continue
        try:
            identity = env.parse_payload(PaperIdentity)
        except Exception:  # noqa: BLE001
            continue
        for member in identity.member_paper_artifact_ids:
            title = next((t for t, rid in records_by_title.items() if rid == member), None)
            if title:
                identity_by_title[title] = env.artifact_id
    paper_order = list(case.input.get("paper_order") or [])
    for i, title in enumerate(paper_order):
        identity_id = identity_by_title.get(title)
        if identity_id is None:
            raise BenchmarkError(f"no paper identity produced for {title!r}")
        id_map[f"{case.id}-identity-{i}"] = identity_id

    # ---- stage 2: screening (real protocol/view/screener/orchestrator) ----
    rq = ResearchQuestion(
        question=case.input["research_question"]["question"],
        motivation="e2e fixture",
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
    protocol_builder = ScreeningProtocolBuilderService(
        model_router=_e2e_router(case, fixtures, id_map, model_router),
        artifact_store=artifact_store,
        autonomy_policy=autonomy,
        model_role="reasoning",
    )
    protocol_id = await protocol_builder.build(f"{case.id}-rq")
    identity_ids = [id_map[f"{case.id}-identity-{i}"] for i in range(len(paper_order))]
    search_exec = LiteratureSearchExecution(
        strategy_artifact_id=f"{case.id}-strategy",
        query_artifact_ids=query_ids,
        paper_identity_artifact_ids=identity_ids,
        counts={},
    )
    search_exec_id = f"{case.id}-{run_suffix}-search-exec"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=search_exec,
            artifact_type="literature_search_execution",
            producer=producer,
            artifact_id=search_exec_id,
        )
    )
    view_builder = ScreeningViewBuilderService(artifact_store=artifact_store)
    screener = TitleAbstractScreenerService(
        model_router=_e2e_router(case, fixtures, id_map, model_router),
        artifact_store=artifact_store,
        model_role="fast",
    )
    screening_orchestrator = ScreeningOrchestratorService(
        artifact_store=artifact_store,
        view_builder=view_builder,
        screener=screener,
        autonomy_policy=autonomy,
        max_candidates=100,
        max_model_calls=500,
    )
    await screening_orchestrator.screen(search_exec_id, protocol_id)

    # ---- stage 3: evidence extraction (real pipeline) --------------------
    doc_ids: list[str] = []
    for i, doc in enumerate(case.input.get("documents") or []):
        title = doc["title"]
        identity_id = identity_by_title[title]
        pdf_blob = await blob_store.put_bytes(b"%PDF-1.4 e2e", media_type="application/pdf")
        pages = list(doc.get("pages") or [])
        text_blob = await blob_store.put_bytes(
            json.dumps({"schema_version": 1, "pages": pages}, sort_keys=True).encode(),
            media_type="application/json",
        )
        acq_id = f"{case.id}-{run_suffix}-acq-{i}"
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=DocumentAcquisition(
                    paper_identity_id=identity_id,
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
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=FullTextDocument(
                    paper_identity_id=identity_id,
                    document_acquisition_id=acq_id,
                    source_blob=pdf_blob,
                    text_blob=text_blob,
                    extractor="documents.extractor.pypdf",
                    page_count=len(pages),
                    pages_with_text=len(pages),
                    character_count=sum(len(p.get("text", "")) for p in pages),
                    text_status=TextStatus.extracted,
                ),
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
    extractor = EvidenceExtractorService(
        model_router=_e2e_router(case, fixtures, id_map, model_router),
        artifact_store=artifact_store,
        blob_store=blob_store,
        model_role="reasoning",
    )
    evidence_orchestrator = EvidenceOrchestratorService(
        artifact_store=artifact_store,
        blob_store=blob_store,
        extractor=extractor,
        model_role="reasoning",
        pages_per_chunk=4,
        max_chunks_per_document=50,
        max_model_calls=500,
    )
    await evidence_orchestrator.run(corpus_id)

    # map evidence ids by fixture item order (statements persist verbatim)
    evidence_by_statement: dict[str, str] = {}
    for env in await artifact_store.list():
        if env.artifact_id in before or env.artifact_type != "evidence_item":
            continue
        try:
            statement = str(env.payload.get("statement") or "")
        except Exception:  # noqa: BLE001
            continue
        if statement:
            evidence_by_statement[statement] = env.artifact_id
    ev_index = 0
    for fixture in fixtures:
        for item in (fixture.get("response") or {}).get("items") or []:
            statement = str(item.get("statement") or "")
            env_id = evidence_by_statement.get(statement)
            if env_id is not None:
                id_map[f"{case.id}-evidence-{ev_index}"] = env_id
            ev_index += 1

    evidence_corpus_env = next(
        (
            e
            for e in await artifact_store.list()
            if e.artifact_id not in before and e.artifact_type == "evidence_corpus"
        ),
        None,
    )
    if evidence_corpus_env is None:
        raise BenchmarkError("evidence stage produced no evidence_corpus")

    # ---- stage 4: synthesis (real synthesizer) ---------------------------
    synthesizer = LiteratureSynthesizerService(
        model_router=_e2e_router(case, fixtures, id_map, model_router),
        artifact_store=artifact_store,
        model_role="reasoning",
        batch_profiles=3,
        max_batches=20,
        max_model_calls=100,
    )
    await synthesizer.run(evidence_corpus_env.artifact_id)
    stmt_by_statement: dict[str, str] = {}
    for env in await artifact_store.list():
        if env.artifact_id in before or env.artifact_type != "synthesis_statement":
            continue
        try:
            statement = str(env.payload.get("statement") or "")
        except Exception:  # noqa: BLE001
            continue
        if statement:
            stmt_by_statement[statement] = env.artifact_id
    stmt_index = 0
    for fixture in fixtures:
        for theme in (fixture.get("response") or {}).get("themes") or []:
            for item in theme.get("statements") or []:
                statement = str(item.get("statement") or "")
                env_id = stmt_by_statement.get(statement)
                if env_id is not None:
                    id_map[f"{case.id}-stmt-{stmt_index}"] = env_id
                stmt_index += 1

    synthesis_env = next(
        (
            e
            for e in await artifact_store.list()
            if e.artifact_id not in before and e.artifact_type == "literature_synthesis"
        ),
        None,
    )
    if synthesis_env is None:
        raise BenchmarkError("synthesis stage produced no literature_synthesis")

    # ---- stage 5: gap analysis (real analyzer) ---------------------------
    gap_analyzer = GapAnalyzerService(
        model_router=_e2e_router(case, fixtures, id_map, model_router),
        artifact_store=artifact_store,
        model_role="reasoning",
    )
    await gap_analyzer.run(synthesis_env.artifact_id, evidence_corpus_env.artifact_id)
    gap_envs = [
        e
        for e in await artifact_store.list()
        if e.artifact_id not in before and e.artifact_type == "research_gap"
    ]
    if not gap_envs:
        raise BenchmarkError("gap stage produced no research_gap")
    gap_env = max(gap_envs, key=lambda e: e.created_at)
    id_map[f"{case.id}-gap"] = gap_env.artifact_id
    gap_analysis_env = next(
        (
            e
            for e in await artifact_store.list()
            if e.artifact_id not in before and e.artifact_type == "gap_analysis"
        ),
        None,
    )
    if gap_analysis_env is None:
        raise BenchmarkError("gap stage produced no gap_analysis")

    # ---- stage 6: mechanism (real selection/generation/critic) -----------
    selection_svc = GapSelectionService(
        model_router=_e2e_router(case, fixtures, id_map, model_router),
        artifact_store=artifact_store,
        model_role="reasoning",
        autonomy_mode="high",
        autonomy=autonomy,
    )
    selection_id = await selection_svc.select(gap_analysis_env.artifact_id)
    generator_svc = MechanismGeneratorService(
        model_router=_e2e_router(case, fixtures, id_map, model_router),
        artifact_store=artifact_store,
        model_role="reasoning",
        max_candidates=5,
        max_model_calls=20,
    )
    await generator_svc.generate(selection_id)
    produced_now = await artifact_store.list()
    candidate_ids = [
        e.artifact_id
        for e in produced_now
        if e.artifact_id not in before and e.artifact_type == "mechanism_candidate"
    ]
    critic_svc = MechanismCriticService(
        model_router=_e2e_router(case, fixtures, id_map, model_router),
        artifact_store=artifact_store,
        critic_role="critic",
        revision_role="reasoning",
    )
    for cand_id in candidate_ids:
        await critic_svc.critique(cand_id)
        await critic_svc.select(cand_id)
    mechanism_env = next(
        (
            e
            for e in await artifact_store.list()
            if e.artifact_id not in before and e.artifact_type == "selected_mechanism"
        ),
        None,
    )
    if mechanism_env is None:
        raise BenchmarkError("mechanism stage produced no selected_mechanism")
    id_map[f"{case.id}-mechanism"] = mechanism_env.artifact_id

    # ---- stage 7: analytical model (real model builder) ------------------
    model_builder = ModelBuilderService(
        model_router=_e2e_router(case, fixtures, id_map, model_router),
        artifact_store=artifact_store,
        model_role="reasoning",
    )
    await model_builder.build(mechanism_env.artifact_id)
    model_env = next(
        (
            e
            for e in await artifact_store.list()
            if e.artifact_id not in before and e.artifact_type == "formal_analytical_model"
        ),
        None,
    )
    if model_env is None:
        raise BenchmarkError("model stage produced no formal_analytical_model")
    id_map[f"{case.id}-model"] = model_env.artifact_id

    # ---- stage 8: equilibrium (real deriver + verifier) ------------------
    verifier = EquilibriumVerifierService(artifact_store=artifact_store)
    deriver = EquilibriumDeriverService(
        model_router=_e2e_router(case, fixtures, id_map, model_router),
        artifact_store=artifact_store,
        verifier=verifier,
        model_role="reasoning",
        revision_role="reasoning",
        max_revisions=2,
        max_llm_calls=10,
    )
    await deriver.derive(model_env.artifact_id)
    analysis_candidates = [
        e
        for e in await artifact_store.list()
        if e.artifact_id not in before and e.artifact_type == "equilibrium_analysis"
    ]
    if not analysis_candidates:
        raise BenchmarkError("equilibrium stage produced no equilibrium_analysis")
    # the deriver supersedes its initial analysis; pick the one that selected a
    # candidate (latest status carries the final selection)
    analysis_env = max(analysis_candidates, key=lambda e: e.created_at)
    id_map[f"{case.id}-analysis"] = analysis_env.artifact_id

    # ---- stage 9: propositions (real statics + generator) ----------------
    cs_svc = ComparativeStaticsService(artifact_store=artifact_store)
    cs_execution_id = await cs_svc.run(analysis_env.artifact_id)
    cs_analysis_id = await cs_svc.resolve_analysis(cs_execution_id)
    for env in await artifact_store.list():
        if env.artifact_id in before or env.artifact_type != "comparative_static":
            continue
        try:
            payload = env.payload
            if isinstance(payload, dict):
                key = (payload.get("outcome_variable"), payload.get("parameter"))
            else:
                key = (payload.outcome_variable, payload.parameter)
        except Exception:  # noqa: BLE001
            continue
        if key[0] and key[1]:
            id_map[f"{case.id}-static-{key[0]}-{key[1]}"] = env.artifact_id
    prop_verifier = PropositionVerifierService(artifact_store=artifact_store)
    prop_critic = PropositionCriticService(
        model_router=_e2e_router(case, fixtures, id_map, model_router),
        artifact_store=artifact_store,
        critic_role="critic",
        interpretation_role="reasoning",
    )
    prop_generator = PropositionGeneratorService(
        model_router=_e2e_router(case, fixtures, id_map, model_router),
        artifact_store=artifact_store,
        verifier=prop_verifier,
        critic=prop_critic,
        generator_role="reasoning",
        max_propositions=8,
        max_llm_calls=20,
    )
    await prop_generator.generate(cs_analysis_id)
    prop_by_statement: dict[str, str] = {}
    for env in await artifact_store.list():
        if env.artifact_id in before or env.artifact_type != "proposition":
            continue
        try:
            statement = str(env.payload.get("statement") or "")
        except Exception:  # noqa: BLE001
            continue
        if statement:
            prop_by_statement[statement] = env.artifact_id
    prop_index = 0
    for fixture in fixtures:
        for item in (fixture.get("response") or {}).get("propositions") or []:
            statement = str(item.get("statement") or "")
            env_id = prop_by_statement.get(statement)
            if env_id is not None:
                id_map[f"{case.id}-prop-{prop_index}"] = env_id
            prop_index += 1

    # ---- stage 10: numerical analysis (real service) ---------------------
    numerical_svc = NumericalAnalysisService(
        artifact_store=artifact_store,
        blob_store=None,
        model_role="reasoning",
        max_points=10000,
        artifact_point_threshold=500,
    )
    await numerical_svc.run(analysis_env.artifact_id)
    experiment_env = next(
        (
            e
            for e in await artifact_store.list()
            if e.artifact_id not in before and e.artifact_type == "numerical_experiment"
        ),
        None,
    )
    if experiment_env is None:
        raise BenchmarkError("numerical stage produced no numerical_experiment")
    id_map[f"{case.id}-experiment"] = experiment_env.artifact_id

    # ---- stage 11: results assembly (real assembler + critic) ------------
    assembler = ResultsAssemblerService(
        model_router=_e2e_router(case, fixtures, id_map, model_router),
        artifact_store=artifact_store,
        assembler_role="reasoning",
        max_findings=12,
        max_contributions=8,
        max_implications=12,
        max_llm_calls=10,
    )
    await assembler.assemble(experiment_env.artifact_id)
    package_env = next(
        (
            e
            for e in await artifact_store.list()
            if e.artifact_id not in before and e.artifact_type == "results_package"
        ),
        None,
    )
    if package_env is None:
        raise BenchmarkError("results stage produced no results_package")
    id_map[f"{case.id}-package"] = package_env.artifact_id
    results_critic = ResultsCriticService(
        model_router=_e2e_router(case, fixtures, id_map, model_router),
        artifact_store=artifact_store,
        critic_role="critic",
    )
    await results_critic.critique(package_env.artifact_id)

    # findings follow the scripted assembly response order
    finding_by_statement: dict[str, str] = {}
    for env in await artifact_store.list():
        if env.artifact_id in before or env.artifact_type != "research_finding":
            continue
        try:
            statement = str(env.payload.get("statement") or "")
        except Exception:  # noqa: BLE001
            continue
        if statement:
            finding_by_statement[statement] = env.artifact_id
    finding_index = 0
    for fixture in fixtures:
        for item in (fixture.get("response") or {}).get("findings") or []:
            statement = str(item.get("statement") or "")
            env_id = finding_by_statement.get(statement)
            if env_id is not None:
                id_map[f"{case.id}-finding-{finding_index}"] = env_id
            finding_index += 1

    # ---- stage 12: manuscript grounding (real drafter + critic) ----------
    drafter = ManuscriptDrafterService(
        model_router=_e2e_router(case, fixtures, id_map, model_router),
        artifact_store=artifact_store,
        drafter_role="reasoning",
        max_llm_calls=100,
    )
    outline_id = await drafter.outline(package_env.artifact_id)
    draft_sections = list(case.input.get("sections") or [])
    await drafter.draft(outline_id, section_ids=draft_sections)
    draft_env = next(
        (
            e
            for e in await artifact_store.list()
            if e.artifact_id not in before and e.artifact_type == "manuscript_draft"
        ),
        None,
    )
    if draft_env is None:
        raise BenchmarkError("manuscript stage produced no manuscript_draft")
    id_map[f"{case.id}-draft"] = draft_env.artifact_id
    manuscript_critic = ManuscriptCriticService(
        model_router=_e2e_router(case, fixtures, id_map, model_router),
        artifact_store=artifact_store,
        critic_role="critic",
    )
    await manuscript_critic.critique(draft_env.artifact_id)

    # ---- stage 13: citation formatting (real formatter) ------------------
    profile_cfg = case.input.get("profile") or {}
    profile = PublicationProfile(
        name=profile_cfg.get("name", "E2E Profile"),
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
        model_router=_e2e_router(case, fixtures, id_map, model_router),
        artifact_store=artifact_store,
        blob_store=None,
        formatter_role="reasoning",
    )
    await formatter.format(draft_env.artifact_id, profile_id)

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# literature_synthesis workflow (Phase 7A): real Phase 2G synthesizer
# ---------------------------------------------------------------------------


async def run_synthesis_workflow(
    *,
    model_router: Any | None = None,
    artifact_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real Phase 2G synthesizer: fixture papers + EvidenceItem +
    PaperResearchProfile + EvidenceCorpus -> LiteratureSynthesizerService ->
    SynthesisStatement / SynthesisTheme / LiteratureSynthesis. Evidence ids are
    run-unique and scripted responses are rewritten so grounding checks always
    see the fixture context."""
    from research_harness.plugins.literature.synthesis.plugin import (
        LiteratureSynthesizerService,
    )
    from research_harness.research.schemas.evidence import EvidenceItem, Locator
    from research_harness.research.schemas.evidence_extraction import EvidenceCorpus
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.research_profile import PaperResearchProfile

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]
    id_map: dict[str, str] = {}
    for i in range(len(case.input.get("evidence") or [])):
        id_map[f"{case.id}-evidence-{i}"] = f"{case.id}-{run_suffix}-evidence-{i}"
    for i in range(len(case.input.get("papers") or [])):
        id_map[f"{case.id}-paper-{i}"] = f"{case.id}-{run_suffix}-paper-{i}"
    router = _case_router(case, model_router, id_map=id_map)

    paper_ids: list[str] = []
    for i in range(len(case.input.get("papers") or [])):
        pid = id_map[f"{case.id}-paper-{i}"]
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=PaperIdentity(
                    member_paper_artifact_ids=[f"{case.id}-record-{i}"],
                    canonical_identifiers=[],
                    resolution_method=ResolutionMethod.manual,
                    resolution_evidence=[],
                ),
                artifact_type="paper_identity",
                producer=producer,
                artifact_id=pid,
            )
        )
        paper_ids.append(pid)

    evidence_ids: list[str] = []
    for i, ev in enumerate(case.input.get("evidence") or []):
        eid = id_map[f"{case.id}-evidence-{i}"]
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=EvidenceItem(
                    statement=ev["statement"],
                    source_artifact_id=f"{case.id}-{run_suffix}-doc-{i}",
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

    profile_ids: list[str] = []
    for i, prof in enumerate(case.input.get("profiles") or []):
        paper_id = paper_ids[prof["paper_index"]]
        pids = [evidence_ids[j] for j in (prof.get("evidence_indexes") or [])]
        pid = f"{case.id}-{run_suffix}-profile-{i}"
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=PaperResearchProfile(
                    paper_identity_id=paper_id,
                    full_text_document_id=f"{case.id}-{run_suffix}-doc-{prof['paper_index']}",
                    evidence_item_ids=pids,
                    extraction_method="model-assisted",
                ),
                artifact_type="paper_research_profile",
                producer=producer,
                artifact_id=pid,
            )
        )
        profile_ids.append(pid)

    corpus = EvidenceCorpus(
        evidence_extraction_execution_id=f"{case.id}-acq-exec",
        full_text_corpus_id=f"{case.id}-corpus",
        paper_profile_ids=profile_ids,
        evidence_item_ids=evidence_ids,
        documents_without_evidence=[],
        failed_document_ids=[],
    )
    corpus_id = f"{case.id}-{run_suffix}-corpus"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=corpus,
            artifact_type="evidence_corpus",
            producer=producer,
            artifact_id=corpus_id,
        )
    )

    synth_config = dict(case.input.get("synthesis_config") or {})
    synthesizer = LiteratureSynthesizerService(
        model_router=router,
        artifact_store=artifact_store,
        model_role="reasoning",
        batch_profiles=int(synth_config.get("batch_profiles", 3)),
        max_batches=int(synth_config.get("max_batches", 20)),
        max_model_calls=int(synth_config.get("max_model_calls", 100)),
    )
    await synthesizer.run(corpus_id)

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# analytical_model_specification workflow (Phase 7A): real Phase 3B pipeline
# ---------------------------------------------------------------------------


async def run_model_specification_workflow(
    *,
    model_router: Any | None = None,
    artifact_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real Phase 3B pipeline: fixture SelectedMechanism with
    literature-supported grounding (real SynthesisStatements) ->
    ModelBuilderService (deterministic structural validation) ->
    ModelSpecificationCriticService (scripted critique). Mechanism/statement ids
    are run-unique and scripted responses are rewritten."""
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderService
    from research_harness.plugins.research.model_specification_critic.plugin import (
        ModelSpecificationCriticService,
    )
    from research_harness.research.schemas.mechanism import (
        GroundingElement,
        KnowledgeBasis,
        SelectedMechanism,
    )
    from research_harness.research.schemas.synthesis import (
        SynthesisStatement,
        SynthesisStatementType,
    )

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]
    id_map: dict[str, str] = {}
    for i in range(len(case.input.get("statements") or [])):
        id_map[f"{case.id}-statement-{i}"] = f"{case.id}-{run_suffix}-statement-{i}"
    id_map[f"{case.id}-mechanism"] = f"{case.id}-{run_suffix}-mechanism"
    router = _case_router(case, model_router, id_map=id_map)

    statement_ids: list[str] = []
    for i, st in enumerate(case.input.get("statements") or []):
        sid = id_map[f"{case.id}-statement-{i}"]
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=SynthesisStatement(
                    statement=st["statement"],
                    type=SynthesisStatementType.consensus,
                    supporting_evidence_ids=[f"{case.id}-evidence-0"],
                    supporting_paper_identity_ids=[f"{case.id}-paper-0"],
                    evidence_items_supporting=1,
                    papers_supporting=1,
                ),
                artifact_type="synthesis_statement",
                producer=producer,
                artifact_id=sid,
            )
        )
        statement_ids.append(sid)

    mechanism_cfg = case.input.get("mechanism") or {}
    mechanism = SelectedMechanism(
        gap_id=f"{case.id}-gap",
        gap_selection_id=f"{case.id}-selection",
        mechanism_candidate_id=f"{case.id}-candidate",
        name=mechanism_cfg.get("name", "Mechanism"),
        description=mechanism_cfg.get("description", "mechanism description"),
        causal_logic=mechanism_cfg.get("causal_logic", "causal logic"),
        actors=list(mechanism_cfg.get("actors") or []),
        strategic_interactions=list(mechanism_cfg.get("strategic_interactions") or []),
        information_structure=mechanism_cfg.get("information_structure"),
        incentives=list(mechanism_cfg.get("incentives") or []),
        boundary_conditions=list(mechanism_cfg.get("boundary_conditions") or []),
        grounding=[
            GroundingElement(
                element="demand decreases in price",
                basis=KnowledgeBasis.literature_supported,
                source_ids=statement_ids,
            )
        ],
    )
    mechanism_id = id_map[f"{case.id}-mechanism"]
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=mechanism,
            artifact_type="selected_mechanism",
            producer=producer,
            artifact_id=mechanism_id,
        )
    )

    builder = ModelBuilderService(
        model_router=router,
        artifact_store=artifact_store,
        model_role="reasoning",
    )
    await builder.build(mechanism_id)

    model_envs = [
        e
        for e in await artifact_store.list()
        if e.artifact_id not in before and e.artifact_type == "formal_analytical_model"
    ]
    if model_envs:
        model_env = max(model_envs, key=lambda e: e.created_at)
        critic = ModelSpecificationCriticService(
            model_router=router,
            artifact_store=artifact_store,
            builder=builder,
            critic_role="critic",
            revision_role="reasoning",
        )
        await critic.critique(model_env.artifact_id)

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# document_acquisition workflow (Phase 7A): real Phase 2E pipeline (mocked HTTP)
# ---------------------------------------------------------------------------


async def run_acquisition_workflow(
    *,
    model_router: Any | None = None,
    artifact_store: Any,
    blob_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real Phase 2E pipeline with mocked HTTP: fixture PaperIdentity
    + PaperRecord (OA URLs) + ScreenedLiteratureSet -> MetadataLocatorService ->
    HttpFetcherService (mocked httpx) -> PypdfExtractorService ->
    DocumentAcquisitionOrchestratorService -> FullTextCorpus. All fetches go
    through a deterministic mocked httpx transport keyed by URL."""
    import httpx

    from research_harness.plugins.documents.acquisition_orchestrator.plugin import (
        DocumentAcquisitionOrchestratorService,
    )
    from research_harness.plugins.documents.extractor_pypdf.plugin import (
        PypdfExtractorService,
    )
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService
    from research_harness.plugins.documents.locator_metadata.plugin import (
        MetadataLocatorService,
    )
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.paper import PaperRecord
    from research_harness.research.schemas.screening_execution import ScreenedLiteratureSet

    if blob_store is None:
        raise BenchmarkError(
            "document_acquisition requires a blob store (compose "
            "storage.blobs_filesystem or pass one to the harness)"
        )

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]

    # deterministic mocked HTTP: returns a real (reportlab-generated) PDF for
    # matching URLs, HTML for routes declared as HTML, and honors declared
    # sizes / statuses. Per-route overrides win over case-level defaults.
    def _make_pdf_bytes(body: str) -> bytes:
        import io

        from reportlab.pdfgen import canvas

        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        c.drawString(100, 750, body)
        c.showPage()
        c.save()
        return buf.getvalue()

    pdf_body = case.input.get("pdf_body") or (
        "This study documents the welfare effects of algorithmic pricing in "
        "platform markets. We measure consumer surplus, seller profits, and "
        "total welfare across a range of market conditions."
    )
    default_status = int(case.input.get("http_status", 200))
    default_type = str(case.input.get("content_type", "application/pdf"))
    content_length = case.input.get("content_length")
    route_by_url: dict[str, dict[str, Any]] = {}
    for route in case.input.get("routes") or []:
        route_by_url[str(route.get("url"))] = route

    async def _handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        route = route_by_url.get(url, {})
        status = int(route.get("status", default_status))
        ctype = str(route.get("content_type", default_type))
        if status == 404:
            return httpx.Response(404, request=request)
        if status >= 400:
            return httpx.Response(status, request=request)
        headers: dict[str, str] = {}
        if content_length is not None:
            headers["content-length"] = str(content_length)
        if ctype:
            headers["content-type"] = ctype
        if ctype != "application/pdf":
            body = (
                b"<!doctype html><html><head><title>login</title></head><body>sign in</body></html>"
            )
        else:
            body = _make_pdf_bytes(pdf_body)
        return httpx.Response(200, headers=headers, content=body, request=request)

    transport = httpx.MockTransport(_handler)
    http_client = httpx.AsyncClient(transport=transport, follow_redirects=False)

    # fixture papers + identities with OA URLs
    paper_ids: list[str] = []
    identity_ids: list[str] = []
    for i, p in enumerate(case.input.get("papers") or []):
        record = PaperRecord(
            title=p["title"],
            year=2021,
            venue="Journal of Platform Studies",
            open_access_url=p.get("open_access_url"),
            metadata={} if not p.get("pdf_url") else {"open_access_pdf_url": p["pdf_url"]},
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
        paper_ids.append(pid)
        identity = PaperIdentity(
            member_paper_artifact_ids=[pid],
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
            )
        )
        identity_ids.append(iid)

    screened_set = ScreenedLiteratureSet(
        screening_execution_id=f"{case.id}-screen-exec",
        screening_protocol_id=f"{case.id}-protocol",
        included_identity_ids=identity_ids,
        excluded_identity_ids=[],
        uncertain_identity_ids=[],
    )
    set_id = f"{case.id}-{run_suffix}-set"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=screened_set,
            artifact_type="screened_literature_set",
            producer=producer,
            artifact_id=set_id,
        )
    )

    locator = MetadataLocatorService(artifact_store=artifact_store)
    extractor = PypdfExtractorService(artifact_store=artifact_store, blob_store=blob_store)
    fetcher = HttpFetcherService(
        artifact_store=artifact_store,
        blob_store=blob_store,
        http_client=http_client,
        timeout_seconds=5.0,
        max_redirects=3,
        max_bytes=int((case.input.get("acquisition_config") or {}).get("max_bytes", 52428800)),
    )
    orchestrator = DocumentAcquisitionOrchestratorService(
        artifact_store=artifact_store,
        blob_store=blob_store,
        fetcher=fetcher,
        extractor=extractor,
        metadata_locator=locator,
        unpaywall_locator=None,
    )
    await orchestrator.run(set_id)
    try:
        await fetcher.close()
    except Exception:  # noqa: BLE001
        pass

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# incremental_revalidation workflow (Phase 7A): immutable recomputation
# ---------------------------------------------------------------------------


async def run_revalidation_workflow(
    *,
    model_router: Any | None = None,
    artifact_store: Any,
    blob_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives REAL production services twice per stage (baseline + changed
    upstream) and records for each stage whether the downstream execution was
    recomputed (new artifact id) or deterministically reused (same artifact id).
    Any stale reuse of incompatible upstream state is recorded as a defect."""
    from research_harness.plugins.autonomy.configurable.plugin import (
        ConfigurableAutonomyPolicy,
    )
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractorService,
    )
    from research_harness.plugins.literature.evidence_orchestrator.plugin import (
        EvidenceOrchestratorService,
    )
    from research_harness.plugins.literature.gap_analyzer.plugin import GapAnalyzerService
    from research_harness.plugins.literature.screening_orchestrator.plugin import (
        ScreeningOrchestratorService,
    )
    from research_harness.plugins.literature.screening_protocol_builder.plugin import (
        ScreeningProtocolBuilderService,
    )
    from research_harness.plugins.literature.screening_view_builder.plugin import (
        ScreeningViewBuilderService,
    )
    from research_harness.plugins.literature.synthesis.plugin import (
        LiteratureSynthesizerService,
    )
    from research_harness.plugins.literature.title_abstract_screener.plugin import (
        TitleAbstractScreenerService,
    )
    from research_harness.plugins.research.equilibrium_deriver.plugin import (
        EquilibriumDeriverService,
    )
    from research_harness.plugins.research.equilibrium_verifier.plugin import (
        EquilibriumVerifierService,
    )
    from research_harness.research.schemas.evidence import EvidenceCategory, EvidenceItem, Locator
    from research_harness.research.schemas.evidence_extraction import EvidenceCorpus
    from research_harness.research.schemas.execution import LiteratureSearchExecution
    from research_harness.research.schemas.full_text import (
        FullTextCorpus,
        FullTextDocument,
        TextStatus,
    )
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
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
    from research_harness.research.schemas.project import ResearchQuestion
    from research_harness.research.schemas.research_profile import PaperResearchProfile

    if blob_store is None:
        raise BenchmarkError("incremental_revalidation requires a blob store")

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]
    fixtures = case.input.get("llm_fixtures") or []
    router = _case_router(case, model_router, fixtures=fixtures)
    autonomy = ConfigurableAutonomyPolicy(mode="high")

    async def _put_model(prefix: str) -> str:
        model = FormalAnalyticalModel(
            selected_mechanism_id=f"{prefix}-mech",
            title="Strategic Pricing",
            description="A strategic pricing model.",
            actors=[ModelActor(actor_id="firm", name="Firm")],
            variables=[
                ModelVariable(
                    symbol="p",
                    name="price",
                    meaning="price",
                    domain="R_+",
                    kind=SymbolKind.decision_variable,
                    owner_actor_id="firm",
                )
            ],
            parameters=[ModelParameter(symbol="c", name="cost", meaning="cost", domain="R_+")],
            timing=[
                ModelTimingStage(
                    stage_number=0, name="pricing", description="price", actor_ids=["firm"]
                )
            ],
            payoffs=[
                PayoffFunction(
                    actor_id="firm",
                    expression=Expression(expression="(p - c) * (10 - p)", symbols_used=["p", "c"]),
                    decision_variables=["p"],
                    parameters=["c"],
                )
            ],
        )
        mid = f"{prefix}-model"
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=model,
                artifact_type="formal_analytical_model",
                producer=producer,
                artifact_id=mid,
            )
        )
        return mid

    async def _put_evidence_corpus(
        prefix: str, label: str, ev_prefix: str
    ) -> tuple[str, list[str]]:
        # EvidenceCorpus fixture (input to Phase 2G synthesis): papers +
        # EvidenceItem + PaperResearchProfile.
        ev_ids: list[str] = []
        profile_ids: list[str] = []
        for i in range(2):
            pid = f"{prefix}-paper-{i}"
            await artifact_store.put(
                ArtifactEnvelope.create(
                    payload=PaperIdentity(
                        member_paper_artifact_ids=[f"{prefix}-record-{i}"],
                        canonical_identifiers=[],
                        resolution_method=ResolutionMethod.manual,
                        resolution_evidence=[],
                    ),
                    artifact_type="paper_identity",
                    producer=producer,
                    artifact_id=pid,
                )
            )
            eid = f"{prefix}-{ev_prefix}-evidence-{i}"
            await artifact_store.put(
                ArtifactEnvelope.create(
                    payload=EvidenceItem(
                        statement=f"{label} evidence statement {i}.",
                        source_artifact_id=f"{prefix}-doc-{i}",
                        category=EvidenceCategory.finding,
                        locator=Locator(page=1, pages=[1]),
                        extraction_method="model-assisted",
                        confidence=0.9,
                    ),
                    artifact_type="evidence_item",
                    producer=producer,
                    artifact_id=eid,
                )
            )
            ev_ids.append(eid)
            prid = f"{prefix}-profile-{i}"
            await artifact_store.put(
                ArtifactEnvelope.create(
                    payload=PaperResearchProfile(
                        paper_identity_id=pid,
                        full_text_document_id=f"{prefix}-doc-{i}",
                        evidence_item_ids=[eid],
                        extraction_method="model-assisted",
                    ),
                    artifact_type="paper_research_profile",
                    producer=producer,
                    artifact_id=prid,
                )
            )
            profile_ids.append(prid)
        corpus = EvidenceCorpus(
            evidence_extraction_execution_id=f"{prefix}-acq-exec",
            full_text_corpus_id=f"{prefix}-corpus",
            paper_profile_ids=profile_ids,
            evidence_item_ids=ev_ids,
            documents_without_evidence=[],
            failed_document_ids=[],
        )
        cid = f"{prefix}-corpus"
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=corpus,
                artifact_type="evidence_corpus",
                producer=producer,
                artifact_id=cid,
            )
        )
        return cid, ev_ids

    async def _put_full_text_corpus(prefix: str, label: str) -> str:
        # FullTextCorpus fixture (input to Phase 2F evidence extraction):
        # PaperIdentity + FullTextDocument with a blob-backed text page.
        pid = f"{prefix}-paper-0"
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=PaperIdentity(
                    member_paper_artifact_ids=[f"{prefix}-record-0"],
                    canonical_identifiers=[],
                    resolution_method=ResolutionMethod.manual,
                    resolution_evidence=[],
                ),
                artifact_type="paper_identity",
                producer=producer,
                artifact_id=pid,
            )
        )
        text_blob = await blob_store.put_bytes(
            json.dumps(
                {
                    "schema_version": 1,
                    "pages": [{"page": 1, "text": f"{label} page text for evidence extraction."}],
                },
                sort_keys=True,
            ).encode(),
            media_type="application/json",
        )
        source_blob = await blob_store.put_bytes(
            b"%PDF-1.4 benchmark", media_type="application/pdf"
        )
        ftd = FullTextDocument(
            paper_identity_id=pid,
            document_acquisition_id=f"{prefix}-acq",
            source_blob=source_blob,
            text_blob=text_blob,
            extractor="documents.extractor.pypdf",
            page_count=1,
            pages_with_text=1,
            character_count=len(f"{label} page text for evidence extraction."),
            text_status=TextStatus.extracted,
        )
        doc_id = f"{prefix}-doc-0"
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=ftd,
                artifact_type="full_text_document",
                producer=producer,
                artifact_id=doc_id,
            )
        )
        corpus = FullTextCorpus(
            document_acquisition_execution_id=f"{prefix}-acq-exec",
            screened_literature_set_id=f"{prefix}-set",
            available_document_ids=[doc_id],
            unavailable_identity_ids=[],
            restricted_identity_ids=[],
            failed_identity_ids=[],
        )
        cid = f"{prefix}-corpus"
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=corpus,
                artifact_type="full_text_corpus",
                producer=producer,
                artifact_id=cid,
            )
        )
        return cid

    async def _run_synthesis(prefix: str, label: str, ev_prefix: str) -> tuple[str, str]:
        """Create a fixture EvidenceCorpus and run the real synthesizer with a
        corpus-scoped router, returning (corpus_id, synthesis_id)."""
        corpus_id, ev_ids = await _put_evidence_corpus(prefix, label, ev_prefix)
        id_map = {f"{case.id}-rev-{ev_prefix}-ev-{i}": eid for i, eid in enumerate(ev_ids)}
        syn_fixtures = [
            {
                "match": label,
                "response": {
                    "themes": [
                        {
                            "title": f"{label} theme",
                            "statements": [
                                {
                                    "statement": f"{label} consensus statement.",
                                    "type": "consensus",
                                    "supporting_evidence_ids": [
                                        f"{case.id}-rev-{ev_prefix}-ev-0",
                                        f"{case.id}-rev-{ev_prefix}-ev-1",
                                    ],
                                }
                            ],
                        }
                    ]
                },
            }
        ]
        synthesizer = LiteratureSynthesizerService(
            model_router=_case_router(case, model_router, fixtures=syn_fixtures, id_map=id_map),
            artifact_store=artifact_store,
            model_role="reasoning",
            batch_profiles=10,
            max_batches=20,
            max_model_calls=100,
        )
        await synthesizer.run(corpus_id)
        syn_env = next(
            (
                e
                for e in await artifact_store.list()
                if e.artifact_id not in before
                and e.artifact_type == "literature_synthesis"
                and envelope_payload_dict(e).get("evidence_corpus_id") == corpus_id
            ),
            None,
        )
        if syn_env is None:
            raise BenchmarkError(f"revalidation synthesis stage produced no synthesis ({prefix})")
        return corpus_id, syn_env.artifact_id

    def _gap_fixtures() -> list[dict[str, Any]]:
        return [
            {
                "match": "identify candidate research gaps",
                "response": {
                    "gaps": [
                        {
                            "title": "No analytical model links algorithmic pricing to welfare",
                            "gap_type": "mechanism_gap",
                            "description": "No included study models how algorithmic pricing affects welfare.",
                            "evidence_strength": 0.6,
                            "research_importance": 0.7,
                            "theoretical_relevance": 0.6,
                            "analytical_model_potential": 0.8,
                            "tractability": 0.7,
                            "model_domains": ["pricing"],
                            "model_opportunity_rationale": "closed-form pricing model",
                        }
                    ]
                },
            }
        ]

    results: dict[str, dict[str, Any]] = {}
    for stage in case.input.get("stages") or []:
        kind = stage["kind"]
        state: dict[str, Any] = {"kind": kind}
        if kind == "screening_protocol":
            # new protocol -> new decisions
            rq = ResearchQuestion(
                question="Which studies examine algorithmic pricing? rev-protocol",
                motivation="fixture",
                scope="fixture",
            )
            await artifact_store.put(
                ArtifactEnvelope.create(
                    payload=rq,
                    artifact_type="research_question",
                    producer=producer,
                    artifact_id=f"{case.id}-{run_suffix}-rq",
                )
            )
            builder = ScreeningProtocolBuilderService(
                model_router=router,
                artifact_store=artifact_store,
                autonomy_policy=autonomy,
                model_role="reasoning",
            )
            protocol_a = await builder.build(f"{case.id}-{run_suffix}-rq")
            protocol_b = await builder.build(f"{case.id}-{run_suffix}-rq")

            # candidate identities for both screens
            pid = f"{case.id}-{run_suffix}-paper-0"
            await artifact_store.put(
                ArtifactEnvelope.create(
                    payload=PaperRecord(
                        title="Algorithmic Pricing Study",
                        year=2021,
                        venue="Journal",
                        abstract="Algorithmic pricing effects.",
                    ),
                    artifact_type="paper_record",
                    producer=producer,
                    artifact_id=pid,
                )
            )
            iid = f"{case.id}-{run_suffix}-identity-0"
            await artifact_store.put(
                ArtifactEnvelope.create(
                    payload=PaperIdentity(
                        member_paper_artifact_ids=[pid],
                        canonical_identifiers=[],
                        resolution_method=ResolutionMethod.manual,
                        resolution_evidence=[],
                    ),
                    artifact_type="paper_identity",
                    producer=producer,
                    artifact_id=iid,
                )
            )
            search_exec = LiteratureSearchExecution(
                strategy_artifact_id=f"{case.id}-strategy",
                query_artifact_ids=[],
                paper_identity_artifact_ids=[iid],
                counts={},
            )
            sexec_id = f"{case.id}-{run_suffix}-search-exec"
            await artifact_store.put(
                ArtifactEnvelope.create(
                    payload=search_exec,
                    artifact_type="literature_search_execution",
                    producer=producer,
                    artifact_id=sexec_id,
                )
            )
            orchestrator = ScreeningOrchestratorService(
                artifact_store=artifact_store,
                view_builder=ScreeningViewBuilderService(artifact_store=artifact_store),
                screener=TitleAbstractScreenerService(
                    model_router=router, artifact_store=artifact_store, model_role="fast"
                ),
                autonomy_policy=autonomy,
                max_candidates=100,
                max_model_calls=500,
            )
            await orchestrator.screen(sexec_id, protocol_a)
            decisions_a = [
                e.artifact_id
                for e in await artifact_store.list()
                if e.artifact_id not in before and e.artifact_type == "screening_decision"
            ]
            execs_a = [
                e.artifact_id
                for e in await artifact_store.list()
                if e.artifact_id not in before and e.artifact_type == "screening_execution"
            ]
            await orchestrator.screen(sexec_id, protocol_b)
            decisions_b = [
                e.artifact_id
                for e in await artifact_store.list()
                if e.artifact_id not in before and e.artifact_type == "screening_decision"
            ]
            execs_b = [
                e.artifact_id
                for e in await artifact_store.list()
                if e.artifact_id not in before and e.artifact_type == "screening_execution"
            ]
            new_decisions = [d for d in decisions_b if d not in decisions_a]
            state["protocol_a"] = protocol_a
            state["protocol_b"] = protocol_b
            state["upstream_b"] = protocol_b
            state["decision_count_a"] = len(decisions_a)
            state["decision_count_b"] = len(decisions_b)
            state["new_decisions"] = len(new_decisions)
            state["downstream_a"] = execs_a[-1] if execs_a else None
            state["downstream_b"] = execs_b[-1] if execs_b else None
            state["recomputed"] = (
                bool(execs_b) and (not execs_a or execs_a[-1] != execs_b[-1])
            ) and len(new_decisions) > 0
        elif kind == "screening_identity":
            # superseding identity -> new screening view for the current identity
            pid_a = f"{case.id}-{run_suffix}-pa"
            pid_b = f"{case.id}-{run_suffix}-pb"
            for _idx, (apid, title) in enumerate(
                [(pid_a, "Identity A Study"), (pid_b, "Identity B Study")]
            ):
                await artifact_store.put(
                    ArtifactEnvelope.create(
                        payload=PaperRecord(
                            title=title, year=2021, venue="Journal", abstract="abstract"
                        ),
                        artifact_type="paper_record",
                        producer=producer,
                        artifact_id=apid,
                    )
                )
            ia = f"{case.id}-{run_suffix}-ia"
            ib = f"{case.id}-{run_suffix}-ib"
            await artifact_store.put(
                ArtifactEnvelope.create(
                    payload=PaperIdentity(
                        member_paper_artifact_ids=[pid_a],
                        canonical_identifiers=[],
                        resolution_method=ResolutionMethod.manual,
                        resolution_evidence=[],
                    ),
                    artifact_type="paper_identity",
                    producer=producer,
                    artifact_id=ia,
                )
            )
            await artifact_store.put(
                ArtifactEnvelope.create(
                    payload=PaperIdentity(
                        member_paper_artifact_ids=[pid_a, pid_b],
                        canonical_identifiers=[],
                        resolution_method=ResolutionMethod.manual,
                        resolution_evidence=[],
                    ),
                    artifact_type="paper_identity",
                    producer=producer,
                    artifact_id=ib,
                )
            )
            view_builder = ScreeningViewBuilderService(artifact_store=artifact_store)
            view_a = await view_builder.build(ia)
            view_b = await view_builder.build(ib)
            state["view_a"] = view_a
            state["view_b"] = view_b
            state["view_reused"] = view_a == view_b
            state["upstream_b"] = ib
            state["downstream_a"] = view_a
            state["downstream_b"] = view_b
            state["recomputed"] = view_a != view_b
        elif kind == "evidence_config":
            # changed model role/config -> new evidence execution
            corpus_id = await _put_full_text_corpus(
                f"{case.id}-{run_suffix}-corpusA", "pricing evidence baseline"
            )
            evidence_fixtures = [
                {
                    "match": "pricing evidence baseline page text",
                    "response": {"items": []},
                }
            ]
            extractor = EvidenceExtractorService(
                model_router=_case_router(case, model_router, fixtures=evidence_fixtures),
                artifact_store=artifact_store,
                blob_store=blob_store,
                model_role="reasoning",
            )
            orch_a = EvidenceOrchestratorService(
                artifact_store=artifact_store,
                blob_store=blob_store,
                extractor=extractor,
                model_role="reasoning",
                pages_per_chunk=4,
            )
            await orch_a.run(corpus_id)
            exec_a = [
                e.artifact_id
                for e in await artifact_store.list()
                if e.artifact_id not in before
                and e.artifact_type == "evidence_extraction_execution"
            ][-1]
            orch_b = EvidenceOrchestratorService(
                artifact_store=artifact_store,
                blob_store=blob_store,
                extractor=extractor,
                model_role="reasoning",
                pages_per_chunk=2,
            )
            await orch_b.run(corpus_id)
            exec_b = [
                e.artifact_id
                for e in await artifact_store.list()
                if e.artifact_id not in before
                and e.artifact_type == "evidence_extraction_execution"
            ][-1]
            state["execution_a"] = exec_a
            state["execution_b"] = exec_b
            state["upstream_b"] = corpus_id
            state["downstream_a"] = exec_a
            state["downstream_b"] = exec_b
            state["recomputed"] = exec_a != exec_b
        elif kind == "synthesis":
            # changed EvidenceCorpus -> new synthesis
            corpus_a, syn_a = await _run_synthesis(
                f"{case.id}-{run_suffix}-synA", "baseline", "eva"
            )
            corpus_b, syn_b = await _run_synthesis(f"{case.id}-{run_suffix}-synB", "changed", "evb")
            state["synthesis_a"] = syn_a
            state["synthesis_b"] = syn_b
            state["upstream_b"] = corpus_b
            state["downstream_a"] = syn_a
            state["downstream_b"] = syn_b
            state["recomputed"] = syn_a != syn_b
        elif kind == "gap":
            # changed synthesis -> new gap analysis
            corpus_a, syn_a = await _run_synthesis(
                f"{case.id}-{run_suffix}-gapA", "baseline", "gapa"
            )
            corpus_b, syn_b = await _run_synthesis(
                f"{case.id}-{run_suffix}-gapB", "changed", "gapb"
            )
            analyzer = GapAnalyzerService(
                model_router=_case_router(case, model_router, fixtures=_gap_fixtures()),
                artifact_store=artifact_store,
                model_role="reasoning",
            )
            await analyzer.run(syn_a, corpus_a)
            gap_a = [
                e.artifact_id
                for e in await artifact_store.list()
                if e.artifact_id not in before and e.artifact_type == "gap_analysis"
            ][-1]
            await analyzer.run(syn_b, corpus_b)
            gap_b = [
                e.artifact_id
                for e in await artifact_store.list()
                if e.artifact_id not in before and e.artifact_type == "gap_analysis"
            ][-1]
            state["gap_a"] = gap_a
            state["gap_b"] = gap_b
            state["upstream_b"] = syn_b
            state["downstream_a"] = gap_a
            state["downstream_b"] = gap_b
            state["recomputed"] = gap_a != gap_b
        elif kind == "equilibrium":
            # changed model specification -> new equilibrium analysis
            model_a = await _put_model(f"{case.id}-{run_suffix}-eqA")
            verifier = EquilibriumVerifierService(artifact_store=artifact_store)
            deriver_a = EquilibriumDeriverService(
                model_router=router,
                artifact_store=artifact_store,
                verifier=verifier,
                model_role="reasoning",
                revision_role="reasoning",
                max_revisions=2,
                max_llm_calls=10,
            )
            await deriver_a.derive(model_a)
            eq_a = [
                e.artifact_id
                for e in await artifact_store.list()
                if e.artifact_id not in before and e.artifact_type == "equilibrium_analysis"
            ][-1]
            model_b = await _put_model(f"{case.id}-{run_suffix}-eqB")
            await deriver_a.derive(model_b)
            eq_b = [
                e.artifact_id
                for e in await artifact_store.list()
                if e.artifact_id not in before and e.artifact_type == "equilibrium_analysis"
            ][-1]
            state["model_a"] = model_a
            state["model_b"] = model_b
            state["analysis_a"] = eq_a
            state["analysis_b"] = eq_b
            state["upstream_b"] = model_b
            state["downstream_a"] = eq_a
            state["downstream_b"] = eq_b
            state["recomputed"] = eq_a != eq_b
        elif kind == "unchanged_reuse":
            # identical inputs -> deterministic reuse
            corpus_id, ev_ids = await _put_evidence_corpus(
                f"{case.id}-{run_suffix}-reuse", "baseline", "ev"
            )
            id_map = {f"{case.id}-rev-ev-{i}": eid for i, eid in enumerate(ev_ids)}
            reuse_fixtures = [
                {
                    "match": "baseline",
                    "response": {
                        "themes": [
                            {
                                "title": "synthesis theme",
                                "statements": [
                                    {
                                        "statement": "Algorithmic pricing affects welfare.",
                                        "type": "consensus",
                                        "supporting_evidence_ids": [
                                            f"{case.id}-rev-ev-0",
                                            f"{case.id}-rev-ev-1",
                                        ],
                                    }
                                ],
                            }
                        ]
                    },
                }
            ]
            synthesizer = LiteratureSynthesizerService(
                model_router=_case_router(
                    case, model_router, fixtures=reuse_fixtures, id_map=id_map
                ),
                artifact_store=artifact_store,
                model_role="reasoning",
                batch_profiles=10,
                max_batches=20,
                max_model_calls=100,
            )
            await synthesizer.run(corpus_id)
            syn_1 = [
                e.artifact_id
                for e in await artifact_store.list()
                if e.artifact_id not in before and e.artifact_type == "literature_synthesis"
            ][-1]
            exec_1 = [
                e.artifact_id
                for e in await artifact_store.list()
                if e.artifact_id not in before and e.artifact_type == "synthesis_execution"
            ][-1]
            await synthesizer.run(corpus_id)
            syn_2 = [
                e.artifact_id
                for e in await artifact_store.list()
                if e.artifact_id not in before and e.artifact_type == "literature_synthesis"
            ][-1]
            exec_2 = [
                e.artifact_id
                for e in await artifact_store.list()
                if e.artifact_id not in before and e.artifact_type == "synthesis_execution"
            ][-1]
            state["synthesis_1"] = syn_1
            state["synthesis_2"] = syn_2
            state["execution_1"] = exec_1
            state["execution_2"] = exec_2
            state["upstream_b"] = corpus_id
            state["downstream_a"] = exec_1
            state["downstream_b"] = exec_2
            state["reused"] = syn_2 == syn_1
            state["execution_reused"] = exec_2 == exec_1
        else:
            raise BenchmarkError(f"unknown revalidation stage {kind!r}")
        results[kind] = state

    # persist the stage-revalidation record artifact for the evaluator
    reval_id = f"{case.id}-{run_suffix}-revalidation"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=RevalidationReportRecord(
                benchmark_case_id=case.id,
                stages=results,
            ),
            artifact_type="revalidation_report",
            producer=producer,
            artifact_id=reval_id,
        )
    )
    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# literature_ingestion_identity workflow (Phase 7A.1): real Phase 2B/2C
# ---------------------------------------------------------------------------


async def run_ingestion_identity_workflow(
    *,
    model_router: Any | None = None,
    artifact_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real Phase 2B/2C pipeline: fixture LiteratureSources ->
    LiteratureIngestor (ProviderRecordSnapshot -> PaperRecord ->
    LiteratureSearchRecord) -> PaperIdentityResolver. Optionally resolves in
    two stages to exercise identity supersession when a new member appears."""
    from research_harness.contracts.literature import LiteratureSearchRequest
    from research_harness.plugins.literature.identity_resolver.plugin import (
        PaperIdentityResolverService,
    )
    from research_harness.plugins.literature.ingestion.plugin import LiteratureIngestor

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]
    ingestor = LiteratureIngestor(artifact_store=artifact_store)
    resolver = PaperIdentityResolverService(artifact_store=artifact_store)

    provider_papers: list[list[str]] = []
    failed_providers: list[str] = []
    search_record_ids: list[str] = []
    for provider, papers in (case.input.get("providers") or {}).items():
        source = FixtureLiteratureSource(provider, papers)
        try:
            request = LiteratureSearchRequest(
                query="benchmark ingestion",
                year_from=2015,
                year_to=2026,
                limit=int(
                    (case.input.get("ingestion_config") or {}).get("max_results_per_query", 20)
                ),
            )
            search_env, _snaps, paper_envs = await ingestor.ingest_search(
                source, request, producer=producer
            )
            search_record_ids.append(search_env.artifact_id)
            provider_papers.append([e.artifact_id for e in paper_envs])
        except Exception:
            failed_providers.append(provider)

    all_paper_ids = [pid for group in provider_papers for pid in group]
    paper_map: dict[str, str] = {f"ing-paper-{i}": pid for i, pid in enumerate(all_paper_ids)}

    superseded: list[str] = []
    if case.input.get("supersede_after") and provider_papers:
        # first resolve only the first provider's papers -> identity1
        await resolver.resolve(provider_papers[0])
    result = await resolver.resolve(all_paper_ids)
    superseded = list(result.identities_superseded)

    report_id = f"{case.id}-{run_suffix}-ingestion-identity"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=IngestionIdentityReport(
                benchmark_case_id=case.id,
                paper_ids=paper_map,
                superseded_identity_ids=superseded,
                failed_providers=failed_providers,
            ),
            artifact_type="ingestion_identity_report",
            producer=producer,
            artifact_id=report_id,
        )
    )

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# gap_selection workflow (Phase 7A.1): real Phase 3A gap selection
# ---------------------------------------------------------------------------


class _BenchmarkApprovalPolicy:
    """Deterministic approval policy for autonomy-checkpoint cases."""

    def __init__(self, approved: bool) -> None:
        self._approved = bool(approved)

    async def request_approval(self, request: Any) -> Any:
        from research_harness.contracts.autonomy import ApprovalDecision

        return ApprovalDecision(
            request_id=request.request_id,
            approved=self._approved,
            reason="fixture approval policy",
            decided_by="fixture",
        )

    async def requires_approval(self, checkpoint: str) -> bool:
        return True


async def run_gap_selection_workflow(
    *,
    model_router: Any | None = None,
    artifact_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real Phase 3A GapSelectionService over a fixture GapAnalysis
    + ranked ResearchGaps: model selection (or operator override) + autonomy
    checkpoint. Case-scoped gap ids are rewritten to run-unique ids."""
    from research_harness.plugins.autonomy.configurable.plugin import (
        ConfigurableAutonomyPolicy,
    )
    from research_harness.plugins.research.gap_selection.plugin import GapSelectionService
    from research_harness.research.schemas.gap import (
        GapAnalysis,
        GapRankDimension,
        GapType,
        ResearchGap,
    )

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]
    id_map: dict[str, str] = {}
    for i in range(len(case.input.get("gaps") or [])):
        id_map[f"gap-{i}"] = f"{case.id}-{run_suffix}-gap-{i}"
    router = _case_router(case, model_router, id_map=id_map)

    gap_ids: list[str] = []
    for i, g in enumerate(case.input.get("gaps") or []):
        gid = id_map[f"gap-{i}"]
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=ResearchGap(
                    title=g["title"],
                    gap_type=GapType.mechanism_gap,
                    description=f"Within the reviewed corpus, no model addresses {g['title']}.",
                    supporting_papers=2,
                    supporting_evidence_items=3,
                    ranking=GapRankDimension(
                        evidence_strength=0.8,
                        research_importance=float(g.get("importance", 0.8)),
                        theoretical_relevance=0.8,
                        analytical_model_potential=0.8,
                        tractability=float(g.get("tractability", 0.8)),
                    ),
                ),
                artifact_type="research_gap",
                producer=producer,
                artifact_id=gid,
            )
        )
        gap_ids.append(gid)

    analysis = GapAnalysis(
        literature_synthesis_id=f"{case.id}-synthesis",
        evidence_corpus_id=f"{case.id}-corpus",
        gap_ids=gap_ids,
        ranked_gap_ids=gap_ids,
    )
    analysis_id = f"{case.id}-{run_suffix}-analysis"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=analysis,
            artifact_type="gap_analysis",
            producer=producer,
            artifact_id=analysis_id,
        )
    )

    autonomy_mode = str(case.input.get("autonomy_mode") or "high")
    approval = case.input.get("approval")
    if approval is not None:
        autonomy: Any = _BenchmarkApprovalPolicy(approved=bool(approval))
    else:
        autonomy = ConfigurableAutonomyPolicy(mode=autonomy_mode)

    selected = case.input.get("selected_gap_id")
    if selected is not None:
        selected = id_map.get(str(selected), str(selected))

    svc = GapSelectionService(
        model_router=router,
        artifact_store=artifact_store,
        model_role="reasoning",
        autonomy_mode=autonomy_mode,
        autonomy=autonomy,
    )
    selection_id: str | None = None
    error: str | None = None
    try:
        selection_id = await svc.select(analysis_id, selected_gap_id=selected)
    except ValueError as e:
        error = str(e)

    reuse_selection_id: str | None = None
    if (case.reference or {}).get("expected_reuse"):
        try:
            reuse_selection_id = await svc.select(analysis_id)
        except Exception:  # noqa: BLE001
            reuse_selection_id = None

    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=GapSelectionReport(
                benchmark_case_id=case.id,
                gap_ids=id_map,
                selection_id=selection_id,
                reuse_selection_id=reuse_selection_id,
                error=error,
            ),
            artifact_type="gap_selection_report",
            producer=producer,
            artifact_id=f"{case.id}-{run_suffix}-gap-report",
        )
    )

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# novelty_revalidation workflow (Phase 7A.1): real Phase 5A/5B novelty over
# changing literature
# ---------------------------------------------------------------------------


def _novelty_assess(relationship: str) -> dict[str, Any]:
    return {
        "dimensions": [
            {"dimension": "focal_phenomenon", "value": "match"},
            {"dimension": "actors", "value": "match"},
            {"dimension": "setting", "value": "match"},
            {"dimension": "mechanism", "value": "match"},
            {"dimension": "key_assumptions", "value": "match"},
            {"dimension": "strategic_decision", "value": "match"},
            {"dimension": "causal_equilibrium_relationship", "value": "match"},
            {"dimension": "theoretical_result", "value": "match"},
            {"dimension": "claimed_contribution", "value": "match"},
        ],
        "relationship": relationship,
        "assessment": f"fixture assessment: {relationship}",
    }


def _novelty_revalidation_assess_fixtures(case: BenchmarkCase) -> list[dict[str, Any]]:
    fixtures: list[dict[str, Any]] = []
    papers: list[dict[str, Any]] = []
    for src in (case.input.get("baseline_sources") or []) + (
        case.input.get("changed_sources") or []
    ):
        if isinstance(src, dict):
            papers.append(src)
    for p in papers:
        title = str(p.get("title") or "")
        if "Microbiomes" in title:
            fixtures.append(
                {
                    "match": "Soil Microbiomes in Agricultural Systems",
                    "response": _novelty_assess("distinct"),
                }
            )
        else:
            fixtures.append(
                {
                    "match": "reduces consumer welfare in online markets",
                    "response": _novelty_assess("direct_prior_art"),
                }
            )
    return fixtures


async def run_novelty_revalidation_workflow(
    *,
    model_router: Any | None = None,
    artifact_store: Any,
    ingestor: Any,
    identity_resolver: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Runs the real NoveltyValidationService.create_report twice — once
    against baseline fixture sources, once against changed sources — and
    records report/gate ids + overall statuses for the evaluator."""
    from research_harness.plugins.research.novelty_validator.plugin import (
        NoveltyValidationService,
    )
    from research_harness.research.schemas.novelty import (
        NoveltyValidationReport,
    )

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]

    sub: dict[str, Any] = case.input.get("submission") or {}
    manuscript = FormattedManuscript(
        draft_id=f"{case.id}-{run_suffix}-draft",
        results_package_id=f"{case.id}-results",
        profile_id=f"{case.id}-profile",
        profile_name="benchmark",
        front_matter=FrontMatter(title=sub.get("title", ""), abstract=sub.get("abstract", "")),
        sections=[
            FormattedSection(section_id=sid, title=sid, body=body)
            for sid, body in (sub.get("sections") or {}).items()
        ],
    )
    manuscript_id = f"{case.id}-{run_suffix}-manuscript"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=manuscript,
            artifact_type="formatted_manuscript",
            producer=producer,
            artifact_id=manuscript_id,
        )
    )
    package = SubmissionPackage(
        formatted_manuscript_id=manuscript_id,
        draft_id=manuscript.draft_id,
        profile_id=manuscript.profile_id,
    )
    package_id = f"{case.id}-{run_suffix}-package"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=package,
            artifact_type="submission_package",
            producer=producer,
            artifact_id=package_id,
        )
    )

    def _sources(papers: list[dict[str, Any]] | str) -> dict[str, FixtureLiteratureSource]:
        if isinstance(papers, str):
            return {"semantic_scholar": FixtureLiteratureSource("semantic_scholar", papers)}
        return {"semantic_scholar": FixtureLiteratureSource("semantic_scholar", papers)}

    assess_fixtures = _novelty_revalidation_assess_fixtures(case)
    base_fixtures = list(case.input.get("llm_fixtures") or []) + assess_fixtures
    novelty_config = dict(case.input.get("novelty_config") or {})
    as_of = case.input.get("as_of")

    def _service(sources: dict[str, FixtureLiteratureSource]) -> NoveltyValidationService:
        return NoveltyValidationService(
            model_router=_case_router(case, model_router, fixtures=base_fixtures),
            artifact_store=artifact_store,
            ingestor=ingestor,
            identity_resolver=identity_resolver,
            service_lookup=make_source_lookup(sources),
            enrichment_enabled=False,
            preacquisition_enabled=False,
            **novelty_config,
        )

    svc_a = _service(_sources(list(case.input.get("baseline_sources") or [])))
    report_a = await svc_a.create_report(package_id, as_of=as_of)
    gate_a = await svc_a.create_gate(package_id, report_a)

    svc_b = _service(_sources(case.input.get("changed_sources") or []))
    report_b = await svc_b.create_report(package_id, as_of=as_of)
    gate_b = await svc_b.create_gate(package_id, report_b)

    async def _overall(rid: str) -> str:
        try:
            rep = (await artifact_store.get(rid)).parse_payload(NoveltyValidationReport)
            return rep.overall_status.value
        except Exception:  # noqa: BLE001
            return ""

    async def _assessments(rid: str) -> list[str]:
        try:
            rep = (await artifact_store.get(rid)).parse_payload(NoveltyValidationReport)
            return list(rep.claim_assessment_ids)
        except Exception:  # noqa: BLE001
            return []

    async def _supersedes_a(rid_b: str) -> bool:
        try:
            children = await artifact_store.get_children(report_a)
            return any(
                c.relation.value == "supersedes" and c.target_artifact_id == rid_b for c in children
            )
        except Exception:  # noqa: BLE001
            return False

    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=NoveltyRevalidationReport(
                benchmark_case_id=case.id,
                report_a=report_a,
                report_b=report_b,
                gate_a=gate_a,
                gate_b=gate_b,
                package_id=package_id,
                manuscript_id=manuscript_id,
                overall_a=await _overall(report_a),
                overall_b=await _overall(report_b),
                assessments_a=await _assessments(report_a),
                assessments_b=await _assessments(report_b),
                report_b_supersedes_a=await _supersedes_a(report_b),
            ),
            artifact_type="novelty_revalidation_report",
            producer=producer,
            artifact_id=f"{case.id}-{run_suffix}-nvr-report",
        )
    )

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# evidence_enrichment workflow (Phase 7C): real Phase 5C-5D enrichment +
# pre-acquisition over fixture sources with a working get() path
# ---------------------------------------------------------------------------


async def run_evidence_enrichment_workflow(
    *,
    model_router: Any | None = None,
    artifact_store: Any,
    ingestor: Any,
    identity_resolver: Any,
    blob_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real NoveltyValidationService.create_report with
    enrichment_enabled=True and preacquisition_enabled=True over fixture
    sources whose get() returns acquired abstracts. One fresh submission
    package per source set (baseline + optional changed) so stale-enrichment
    reuse and provenance versioning can be verified. Records the produced
    enrichment/preacquisition executions + candidate evidence bases for the
    evaluator."""
    from research_harness.plugins.research.novelty_validator.plugin import (
        NoveltyValidationService,
    )
    from research_harness.research.schemas.novelty import NoveltyCandidateAssessment

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]

    source_sets = case.input.get("source_sets") or []
    if not source_sets:
        source_sets = [{"label": "baseline", "papers": [], "get_hits": {}}]

    novelty_config = dict(case.input.get("novelty_config") or {})
    as_of = case.input.get("as_of")
    fixtures = list(case.input.get("llm_fixtures") or [])

    async def _make_package(label: str) -> str:
        sub: dict[str, Any] = case.input.get("submission") or {}
        manuscript = FormattedManuscript(
            draft_id=f"{case.id}-{run_suffix}-{label}-draft",
            results_package_id=f"{case.id}-results",
            profile_id=f"{case.id}-profile",
            profile_name="benchmark",
            front_matter=FrontMatter(title=sub.get("title", ""), abstract=sub.get("abstract", "")),
            sections=[
                FormattedSection(section_id=sid, title=sid, body=body)
                for sid, body in (sub.get("sections") or {}).items()
            ],
        )
        manuscript_id = f"{case.id}-{run_suffix}-{label}-manuscript"
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=manuscript,
                artifact_type="formatted_manuscript",
                producer=producer,
                artifact_id=manuscript_id,
            )
        )
        package = SubmissionPackage(
            formatted_manuscript_id=manuscript_id,
            draft_id=manuscript.draft_id,
            profile_id=manuscript.profile_id,
        )
        package_id = f"{case.id}-{run_suffix}-{label}-package"
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=package,
                artifact_type="submission_package",
                producer=producer,
                artifact_id=package_id,
            )
        )
        return package_id

    async def _run_set(set_cfg: dict[str, Any]) -> EnrichmentRunRecord:
        label = str(set_cfg.get("label") or "run")
        papers = set_cfg.get("papers")
        get_hits = set_cfg.get("get_hits") or {}
        get_errors = set_cfg.get("get_errors") or {}
        run_before = {e.artifact_id for e in await artifact_store.list()}
        sources = {
            "semantic_scholar": FixtureLiteratureSource(
                "semantic_scholar",
                papers if papers is not None else [],
                get_hits=get_hits,
                get_errors=get_errors,
            )
        }
        svc = NoveltyValidationService(
            model_router=_case_router(case, model_router, fixtures=fixtures),
            artifact_store=artifact_store,
            ingestor=ingestor,
            identity_resolver=identity_resolver,
            service_lookup=make_source_lookup(sources),
            blob_store=blob_store,
            enrichment_enabled=True,
            preacquisition_enabled=True,
            **novelty_config,
        )
        package_id = await _make_package(label)
        report_id = await svc.create_report(package_id, as_of=as_of)
        produced_after = {e.artifact_id for e in await artifact_store.list()} - run_before
        executions: list[str] = []
        preacquisitions: list[str] = []
        for aid in produced_after:
            try:
                env = await artifact_store.get(aid)
            except Exception:  # noqa: BLE001
                continue
            if env.artifact_type == "evidence_enrichment_execution":
                executions.append(aid)
            elif env.artifact_type == "evidence_preacquisition_execution":
                preacquisitions.append(aid)
        bases: dict[str, str] = {}
        for aid in produced_after:
            env = await artifact_store.get(aid)
            if env.artifact_type != "novelty_candidate_assessment":
                continue
            try:
                bases[aid] = env.parse_payload(NoveltyCandidateAssessment).evidence_basis.value
            except Exception:  # noqa: BLE001
                continue
        return EnrichmentRunRecord(
            label=label,
            report_id=report_id,
            enrichment_execution_ids=sorted(executions),
            preacquisition_execution_ids=sorted(preacquisitions),
            candidate_bases=bases,
        )

    runs: list[EnrichmentRunRecord] = []
    for set_cfg in source_sets:
        runs.append(await _run_set(set_cfg))

    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=EvidenceEnrichmentReport(
                benchmark_case_id=case.id,
                runs=runs,
            ),
            artifact_type="evidence_enrichment_report",
            producer=producer,
            artifact_id=f"{case.id}-{run_suffix}-enrich-report",
        )
    )

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# model_routing workflow (Phase 7C): the real policy router over synthetic
# role leaderboards (shadow mode only — production models are never switched)
# ---------------------------------------------------------------------------


class _FakeCapabilityProvider:
    """Offline provider exposing only capability metadata for the router."""

    def __init__(self, capabilities: ModelCapabilities) -> None:
        self.capabilities = capabilities

    async def complete(self, request: Any) -> ModelResponse:
        raise AssertionError("the policy router never calls the model provider")

    async def close(self) -> None:
        pass


def _routing_capability_lookup(capabilities: dict[str, dict[str, Any]]) -> Callable[[str], Any]:
    def lookup(name: str) -> Any:
        provider = name[len("model_provider.") :] if name.startswith("model_provider.") else name
        caps = capabilities.get(provider)
        if caps is None:
            raise ServiceError(f"no benchmark capability fixture for provider {provider!r}")
        return _FakeCapabilityProvider(ModelCapabilities(**caps))

    return lookup


async def run_model_routing_workflow(
    *,
    artifact_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real PolicyModelRouterService over synthetic RoleLeaderboard
    fixtures (persisted for the case) with a capability lookup backed by case
    fixtures. Records one RoutingDecision artifact for the evaluator. Shadow
    mode only — no production role model is ever replaced."""
    from research_harness.plugins.routing.policy_router.plugin import (
        PolicyModelRouterService,
    )
    from research_harness.research.schemas.routing import RoutingRequest
    from research_harness.research.schemas.tournament import RoleLeaderboard

    before = {e.artifact_id for e in await artifact_store.list()}

    role = str(case.input.get("role") or "reasoning")
    policy = str(case.input.get("policy") or "quality_first")
    use_fallback = bool(case.input.get("use_fallback") or False)
    shadow = bool(case.input.get("shadow") or False)

    leaderboards: list[RoleLeaderboard] = []
    for i, lb in enumerate(case.input.get("leaderboards") or []):
        board = RoleLeaderboard.model_validate({**lb, "id": lb.get("id") or f"{case.id}-lb-{i}"})
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=board,
                artifact_type="role_leaderboard",
                producer=producer,
                artifact_id=board.id,
            )
        )
        leaderboards.append(board)

    request_data = dict(case.input.get("request") or {})
    request_data.setdefault("role", role)
    request_data["leaderboard_ids"] = [b.id for b in leaderboards]
    request = RoutingRequest.model_validate(request_data)

    capabilities = case.input.get("capabilities") or {}
    current_roles = case.input.get("current_roles") or {}

    service = PolicyModelRouterService(
        artifact_store=artifact_store,
        service_lookup=_routing_capability_lookup(capabilities),
        current_roles=current_roles,
    )
    if shadow:
        await service.shadow(role, policy, request, use_fallback=use_fallback)
    else:
        await service.decide(role, policy, request, use_fallback=use_fallback)

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# lq_critique workflow (Phase 7D.0): inject a defective fixture artifact and
# run the real production critic service; validate the critique structurally
# ---------------------------------------------------------------------------


def _lq_critique_service(task: str, router: Any, store: Any, producer: str):
    from research_harness.plugins.research.manuscript_critic.plugin import ManuscriptCriticService
    from research_harness.plugins.research.mechanism_critic.plugin import MechanismCriticService
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderService
    from research_harness.plugins.research.model_specification_critic.plugin import (
        ModelSpecificationCriticService,
    )
    from research_harness.plugins.research.proposition_critic.plugin import PropositionCriticService
    from research_harness.plugins.research.results_critic.plugin import ResultsCriticService

    if task == "mechanism_critique":
        return "mechanism_critique", MechanismCriticService(
            model_router=router, artifact_store=store
        )
    if task == "model_critique":
        builder = ModelBuilderService(model_router=router, artifact_store=store)
        return "model_specification_critique", ModelSpecificationCriticService(
            model_router=router, artifact_store=store, builder=builder
        )
    if task == "proposition_critique":
        return "proposition_critique", PropositionCriticService(
            model_router=router, artifact_store=store
        )
    if task == "results_critique":
        return "results_critique", ResultsCriticService(model_router=router, artifact_store=store)
    if task == "manuscript_critique":
        return "manuscript_critique", ManuscriptCriticService(
            model_router=router, artifact_store=store
        )
    raise BenchmarkError(f"unknown live-quality critic task {task!r}")


def _lq_fixture_schema(artifact_type: str):
    from research_harness.research.schemas.gap import ResearchGap
    from research_harness.research.schemas.manuscript import ManuscriptDraft, ManuscriptSection
    from research_harness.research.schemas.mechanism import MechanismCandidate, SelectedMechanism
    from research_harness.research.schemas.model import FormalAnalyticalModel
    from research_harness.research.schemas.proposition import Proposition
    from research_harness.research.schemas.results import (
        ContributionClaim,
        ResearchFinding,
        ResearchResultsPackage,
    )

    return {
        "research_gap": ResearchGap,
        "mechanism_candidate": MechanismCandidate,
        "selected_mechanism": SelectedMechanism,
        "formal_analytical_model": FormalAnalyticalModel,
        "proposition": Proposition,
        "research_results_package": ResearchResultsPackage,
        "research_finding": ResearchFinding,
        "contribution_claim": ContributionClaim,
        "manuscript_draft": ManuscriptDraft,
        "manuscript_section": ManuscriptSection,
    }.get(artifact_type)


def _resolve_placeholders(value: Any, id_map: dict[str, str]) -> Any:
    """Replace {artifact_type#index} placeholders with assigned fixture ids."""
    if isinstance(value, dict):
        return {k: _resolve_placeholders(v, id_map) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_placeholders(v, id_map) for v in value]
    if isinstance(value, str):
        if value.startswith("{") and value.endswith("}"):
            key = value[1:-1]
            if key in id_map:
                return id_map[key]
        return value
    return value


async def run_lq_critique_workflow(
    *,
    model_router: Any | None = None,
    artifact_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Injects a defective fixture artifact (case.input['fixtures']) and runs
    the real production critic service for the task. The critic's model output
    is validated structurally by the live-quality critic evaluator against the
    known injected defects."""
    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]
    task = str(case.input.get("task") or "")
    router = _case_router(case, model_router, fixtures=case.input.get("llm_fixtures") or [])

    fixtures = case.input.get("fixtures") or {}
    id_plan: dict[str, str] = {}
    for artifact_type, payloads in fixtures.items():
        for i in range(len(payloads or [])):
            id_plan[f"{artifact_type}#{i}"] = f"{case.id}-{run_suffix}-{artifact_type}-{i}"

    for artifact_type, payloads in fixtures.items():
        schema = _lq_fixture_schema(artifact_type)
        if schema is None:
            raise BenchmarkError(f"unsupported live-quality fixture type {artifact_type!r}")
        for i, payload in enumerate(payloads or []):
            aid = id_plan[f"{artifact_type}#{i}"]
            payload = dict(payload)
            payload.pop("created_at", None)
            resolved = _resolve_placeholders(payload, id_plan)
            env = ArtifactEnvelope.create(
                payload=schema.model_validate(resolved),
                artifact_type=artifact_type,
                producer=producer,
                artifact_id=aid,
            )
            await artifact_store.put(env)

    target_type = str(case.input.get("target_artifact_type") or "")
    target_index = int(case.input.get("target_index") or 0)
    target_id = id_plan.get(f"{target_type}#{target_index}")
    if target_id is None:
        raise BenchmarkError(
            f"live-quality critic target {target_type}#{target_index} not found among fixtures"
        )

    _artifact_type, service = _lq_critique_service(task, router, artifact_store, producer)
    await service.critique(target_id)

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


# ---------------------------------------------------------------------------
# routing_readiness workflow (Phase 7D.0): the real deterministic readiness
# assessment over synthetic live-quality results
# ---------------------------------------------------------------------------


async def run_routing_readiness_workflow(
    *,
    artifact_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real `assess_role_readiness` deterministic logic over
    synthetic LiveQualityModelResult fixtures and persists the resulting
    RoutingReadinessAssessment for the evaluator. No network."""
    from research_harness.research.routing.readiness import (
        assess_role_readiness,
        criteria_for_role,
    )
    from research_harness.research.schemas.live_quality import (
        LiveQualityModelResult,
        QualificationCriteria,
        RoutingReadinessAssessment,
    )

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]
    role = str(case.input.get("role") or "reasoning")
    configured_model = case.input.get("configured_model")
    require_fallback = bool(case.input.get("require_fallback") or False)

    live_results: dict[str, LiveQualityModelResult] = {}
    for candidate_id, payload in (case.input.get("live_results") or {}).items():
        live_results[str(candidate_id)] = LiveQualityModelResult.model_validate(
            dict(payload, candidate_id=str(candidate_id))
        )

    criteria_data = dict(case.input.get("criteria") or {})
    criteria = (
        QualificationCriteria.model_validate(criteria_data)
        if criteria_data
        else criteria_for_role(role)
    )
    if criteria.role != role:
        criteria = criteria.model_copy(update={"role": role})

    verdict = assess_role_readiness(
        live_results,
        criteria,
        configured_model=str(configured_model) if configured_model else None,
        require_fallback=require_fallback,
    )

    assessment = RoutingReadinessAssessment(
        role=role,
        criteria=criteria,
        qualified=bool(verdict["qualified"]),
        reasons=list(verdict["reasons"]),
        qualified_models=list(verdict["qualified_models"]),
        fallback_qualified=bool(verdict["fallback_qualified"]),
        fallback_model=verdict["fallback_model"],
        configured_model=verdict["configured_model"],
        evidence={cid: summary_payload(r) for cid, r in live_results.items()},
        unsafe_production_qualification=False,
    )
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=assessment,
            artifact_type="routing_readiness_assessment",
            producer=producer,
            artifact_id=f"{case.id}-{run_suffix}-readiness",
        )
    )

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]


def summary_payload(result: Any) -> dict[str, Any]:
    return {
        "repetitions": result.repetitions,
        "deterministic_pass_rate_mean": result.deterministic_pass_rate_mean,
        "structured_output_success_rate": result.structured_output_success_rate,
        "provider_error_frequency": result.provider_error_frequency,
        "critical_grounding_failures": result.critical_grounding_failures,
        "qualified": result.qualification,
    }


# ---------------------------------------------------------------------------
# publication_packaging workflow (Phase 7A.1): real Phase 4C formatter +
# exporters + submission package
# ---------------------------------------------------------------------------


async def run_publication_packaging_workflow(
    *,
    model_router: Any | None = None,
    artifact_store: Any,
    blob_store: Any,
    case: BenchmarkCase,
    producer: str = _DEFAULT_PRODUCER,
) -> list[ArtifactEnvelope[Any]]:
    """Drives the real Phase 4C pipeline: fixture papers + ManuscriptDraft
    sections -> PublicationFormatterService.format (citation resolution +
    bibliography) -> validate -> export (Markdown/LaTeX/DOCX/PDF -> BlobStore)
    -> SubmissionPackage."""
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

    if blob_store is None:
        raise BenchmarkError("publication_packaging requires a blob store")

    before = {e.artifact_id for e in await artifact_store.list()}
    run_suffix = uuid.uuid4().hex[:8]

    id_map: dict[str, str] = {}
    for p in case.input.get("papers") or []:
        rid = f"{case.id}-{run_suffix}-{p['id']}"
        id_map[str(p["id"])] = rid
        identity_id = f"{case.id}-{run_suffix}-{p['identity_id']}"
        id_map[str(p["identity_id"])] = identity_id
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=PaperRecord(
                    title=p["title"],
                    authors=[Author(name=a) for a in p.get("authors") or []],
                    year=p.get("year"),
                    venue=p.get("venue"),
                    doi=p.get("doi"),
                    abstract=p.get("abstract"),
                ),
                artifact_type="paper_record",
                producer=producer,
                artifact_id=rid,
            )
        )
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=PaperIdentity(
                    member_paper_artifact_ids=[rid],
                    canonical_identifiers=[],
                    resolution_method=ResolutionMethod.manual,
                    resolution_evidence=[],
                ),
                artifact_type="paper_identity",
                producer=producer,
                artifact_id=identity_id,
            )
        )

    section_ids: list[str] = []
    for i, sec in enumerate(case.input.get("sections") or []):
        sid = f"{case.id}-{run_suffix}-sec-{i}"
        section = ManuscriptSection(
            outline_id=f"{case.id}-outline",
            section_id=sec["section_id"],
            title=sec["title"],
            body=sec["body"],
            citations=[
                CitationReference(
                    citation_id=c["citation_id"],
                    paper_identity_id=id_map.get(
                        str(c["paper_identity_id"]), str(c["paper_identity_id"])
                    ),
                    evidence_item_id="ev-benchmark",
                    page_locator=c.get("page_locator"),
                )
                for c in sec.get("citations") or []
            ],
        )
        await artifact_store.put(
            ArtifactEnvelope.create(
                payload=section,
                artifact_type="manuscript_section",
                producer=producer,
                artifact_id=sid,
            )
        )
        section_ids.append(sid)

    draft = ManuscriptDraft(
        outline_id=f"{case.id}-outline",
        results_package_id=f"{case.id}-results",
        title="Packaging Benchmark Draft",
        section_ids=section_ids,
    )
    draft_id = f"{case.id}-{run_suffix}-draft"
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
        required_sections=list(profile_cfg.get("required_sections") or []),
        section_order=list(profile_cfg.get("section_order") or []),
    )
    profile_id = f"{case.id}-{run_suffix}-profile"
    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=profile,
            artifact_type="publication_profile",
            producer=producer,
            artifact_id=profile_id,
        )
    )

    formatter = PublicationFormatterService(
        model_router=_case_router(case, model_router),
        artifact_store=artifact_store,
        blob_store=blob_store,
        formatter_role="reasoning",
    )
    fm_id: str | None = None
    error: str | None = None
    try:
        fm_id = await formatter.format(draft_id, profile_id)
    except Exception as e:  # noqa: BLE001
        error = f"formatter refused: {e}"

    leaf_id: str | None = None
    package_id: str | None = None
    export_ids: list[str] = []
    reexport_ids: list[str] = []
    if fm_id is not None:
        leaf_id, _passed = await formatter.validate(fm_id)
        try:
            package_id = await formatter.package(leaf_id)
        except Exception as e:  # noqa: BLE001
            error = f"{error}; package failed: {e}" if error else f"package failed: {e}"

        # deterministic rerender: re-export each format and record the ids so the
        # evaluator can verify the export idempotency (same artifact, same hash)
        if package_id is not None:
            formats = ["markdown", "latex", "docx", "pdf"]
            for fmt in formats:
                try:
                    export_ids.append(await formatter.export(leaf_id, fmt))
                except Exception:  # noqa: BLE001
                    continue
            for fmt in formats:
                try:
                    reexport_ids.append(await formatter.export(leaf_id, fmt))
                except Exception:  # noqa: BLE001
                    continue

    await artifact_store.put(
        ArtifactEnvelope.create(
            payload=PackagingReport(
                benchmark_case_id=case.id,
                formatted_manuscript_id=leaf_id or "",
                package_id=package_id or "",
                export_ids=export_ids,
                reexport_ids=reexport_ids,
                error=error,
            ),
            artifact_type="packaging_report",
            producer=producer,
            artifact_id=f"{case.id}-{run_suffix}-packaging-report",
        )
    )

    after = await artifact_store.list()
    return [e for e in after if e.artifact_id not in before]
