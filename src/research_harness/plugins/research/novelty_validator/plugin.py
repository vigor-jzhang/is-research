"""Phase 5A novelty validation service.

External literature-based validation of manuscript novelty claims:

    SubmissionPackage -> NoveltyValidationReport -> SubmissionReadinessGate

Design principles (research-integrity):
- novelty validation is an evidence-backed search-and-comparison problem, not
  an LLM opinion;
- search failure never equals novelty; exhaustive coverage is never claimed;
- bibliographic information is never invented; title-only evidence can never
  yield a strong semantic judgment;
- contradictory prior literature stays linked to the report even when another
  model disagrees;
- the manuscript is never modified; recommendations only.

All artifacts are immutable; reassessment creates a superseding report.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.contracts.blob import BlobReference
from research_harness.contracts.literature import (
    LiteratureNotFoundError,
    LiteratureRateLimitError,
    LiteratureSearchRequest,
)
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.common import normalize_doi
from research_harness.research.schemas.evidence import EvidenceCategory, EvidenceItem, Locator
from research_harness.research.schemas.full_text import FullTextDocument
from research_harness.research.schemas.identity import PaperIdentity
from research_harness.research.schemas.novelty import (
    CandidateRelationship,
    ClaimImportance,
    ClaimRiskLevel,
    CriticVerdict,
    EnrichmentAttemptStatus,
    EnrichmentOutcome,
    EvidenceBasis,
    EvidenceEnrichmentAttempt,
    EvidenceEnrichmentExecution,
    EvidenceEnrichmentPlan,
    ManuscriptChangeSet,
    ManuscriptSectionChange,
    ManuscriptSectionChangeType,
    MatchLevel,
    NoveltyCandidateAssessment,
    NoveltyCandidateRef,
    NoveltyCandidateSet,
    NoveltyClaim,
    NoveltyClaimAssessment,
    NoveltyClaimLocation,
    NoveltyClaimStatus,
    NoveltyClaimType,
    NoveltyCoverage,
    NoveltyCriticAssessment,
    NoveltyDimension,
    NoveltyDimensionScore,
    NoveltyExclusion,
    NoveltyPlanGeneration,
    NoveltyPlanQuery,
    NoveltyQueryType,
    NoveltyReportStatus,
    NoveltyRevalidationExecution,
    NoveltyRevalidationPlan,
    NoveltyRevisionRecommendation,
    NoveltySearchExecution,
    NoveltySearchPlan,
    NoveltyValidationExecution,
    NoveltyValidationReport,
    PreAcquisitionExecution,
    ReadinessStatus,
    StalenessStatus,
    SubmissionReadinessGate,
)
from research_harness.research.schemas.paper import PaperRecord
from research_harness.research.schemas.publication import (
    FormattedManuscript,
    SubmissionPackage,
    SubmissionPackageStatus,
)
from research_harness.research.schemas.query import LiteratureQuery
from research_harness.research.schemas.search_record import LiteratureSearchRecord

from . import detection

logger = logging.getLogger(__name__)

_PRODUCER = "research.novelty_validator"
_MAX_VALIDATION_RETRIES = 2
_RETRY_BACKOFF_S = 1.0

_UNSUPPORTED_DOCUMENT_TYPES = {"dataset", "editorial", "news-item", "book-review", "errata"}

_STRONG_RELATIONSHIPS = {
    CandidateRelationship.direct_prior_art,
    CandidateRelationship.strong_overlap,
}

_MANUSCRIPT_SECTIONS_FOR_MODEL = [
    "introduction",
    "research_gap",
    "theory_mechanism",
    "discussion",
    "contributions",
    "conclusion",
]


class _ExtractionResponse(BaseModel):
    claims: list[_ExtractionItem] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class _ExtractionItem(BaseModel):
    claim_text: str
    source_quote: str
    section_id: str
    claim_type: str
    importance: str = "major"

    model_config = {"extra": "forbid"}


class _PlanQuery(BaseModel):
    query: str
    query_type: str
    synonyms: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class _PlanResponse(BaseModel):
    queries: list[_PlanQuery] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class _AssessmentResponse(BaseModel):
    dimensions: list[NoveltyDimensionScore] = Field(default_factory=list)
    relationship: str
    assessment: str = ""

    @field_validator("dimensions")
    @classmethod
    def check_dimensions(cls, v: list[NoveltyDimensionScore]) -> list[NoveltyDimensionScore]:
        seen: set[NoveltyDimension] = set()
        for score in v:
            if score.dimension in seen:
                raise ValueError(f"duplicate dimension {score.dimension}")
            seen.add(score.dimension)
        return v

    model_config = {"extra": "forbid"}


class _CriticResponse(BaseModel):
    verdict: str
    reasoning: str = ""

    model_config = {"extra": "forbid"}


class _RecommendationResponse(BaseModel):
    suggested_scope_change: str = ""
    suggested_wording: str = ""

    model_config = {"extra": "forbid"}


class _NoveltyEvidenceChunk(BaseModel):
    """Structurally compatible page chunk for the existing evidence-extractor
    service (local model; no cross-plugin imports)."""

    document_id: str
    chunk_index: int = 0
    start_page: int = 1
    end_page: int = 1
    page_texts: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def pages(self) -> list[int]:
        return [p["page"] for p in self.page_texts]

    @property
    def text(self) -> str:
        return "\n\n".join(f"[Page {p['page']}]\n{p.get('text', '')}" for p in self.page_texts)

    model_config = {"extra": "forbid"}


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return text
    return text[start : end + 1]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def _enclosing_sentence(text: str, start: int, end: int) -> str:
    for sent in _sentences(text):
        idx = text.find(sent)
        if idx != -1 and idx <= start < idx + len(sent):
            return sent
    return text[start:end]


class NoveltyValidationService:
    def __init__(
        self,
        model_router: Any,
        artifact_store: Any,
        ingestor: Any,
        identity_resolver: Any,
        service_lookup: Any,
        blob_store: Any | None = None,
        *,
        extractor_role: str = "reasoning",
        critic_role: str = "critic",
        max_llm_calls: int = 40,
        max_queries_per_claim: int = 12,
        queries_per_risk: dict[str, int] | None = None,
        max_results_per_query: int = 10,
        providers: list[str] | None = None,
        search_year_window: int = 50,
        require_all_searches_succeed: bool = True,
        require_candidate_evidence: bool = True,
        enrichment_enabled: bool = True,
        acquire_abstract: bool = True,
        acquire_full_text: bool = True,
        max_enrichment_attempts: int = 3,
        abstract_providers: list[str] | None = None,
        preacquisition_enabled: bool = False,
        preacquisition_risk_levels: list[str] | None = None,
        preacquisition_max_per_claim: int = 10,
        preacquisition_max_total: int = 30,
        preacquisition_acquire_full_text: bool = False,
    ) -> None:
        self._router = model_router
        self._store = artifact_store
        self._ingestor = ingestor
        self._resolver = identity_resolver
        self._lookup = service_lookup
        self._blobs = blob_store
        self._extractor_role = extractor_role
        self._critic_role = critic_role
        self._max_llm_calls = max_llm_calls
        self._max_queries_per_claim = max_queries_per_claim
        self._queries_per_risk = dict(
            queries_per_risk or {"critical": 10, "high": 6, "medium": 3, "low": 1}
        )
        self._max_results_per_query = max_results_per_query
        self._providers = list(providers or ["semantic_scholar", "crossref"])
        self._search_year_window = search_year_window
        self._require_all_searches_succeed = require_all_searches_succeed
        self._require_candidate_evidence = require_candidate_evidence
        self._enrichment_enabled = enrichment_enabled
        self._acquire_abstract = acquire_abstract
        self._acquire_full_text = acquire_full_text
        self._max_enrichment_attempts = max_enrichment_attempts
        self._abstract_providers = list(abstract_providers or ["semantic_scholar", "crossref"])
        self._preacquisition_enabled = preacquisition_enabled
        self._preacq_risk_levels = list(preacquisition_risk_levels or ["critical", "high"])
        self._preacq_max_per_claim = preacquisition_max_per_claim
        self._preacq_max_total = preacquisition_max_total
        self._preacq_acquire_full_text = preacquisition_acquire_full_text
        self._preacq_total_used = 0
        self._calls = 0

    @property
    def service_id(self) -> str:
        return "research.novelty_validator"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _risk_budget(self, risk: ClaimRiskLevel) -> int:
        return min(
            int(self._queries_per_risk.get(risk.value, 1)),
            self._max_queries_per_claim,
        )

    async def _manuscript_sources(self, manuscript: FormattedManuscript) -> dict[str, str]:
        sources: dict[str, str] = {
            "title": manuscript.front_matter.title,
            "abstract": manuscript.front_matter.abstract,
        }
        for section in manuscript.sections:
            sources[section.section_id] = section.body
        return sources

    async def _call_llm(
        self,
        role: str,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        *,
        attempts: int,
    ) -> dict[str, Any]:
        """Bounded, validated LLM call returning parsed JSON. Raises on final
        failure; callers degrade deterministically."""
        from research_harness.contracts.model import Message, ModelRequest

        last_error: Exception | None = None
        for _ in range(1 + attempts):
            if self._calls >= self._max_llm_calls:
                raise ValueError("max LLM calls exceeded")
            prior = (
                f"\n\nYour previous attempt was REJECTED by deterministic validation: "
                f"{last_error}\nFix the output and retry."
                if last_error
                else ""
            )
            request = ModelRequest(
                messages=[
                    Message(
                        role="system",
                        content=(
                            f"{system} Return valid JSON matching the schema exactly. "
                            "Never include chain-of-thought."
                        ),
                    ),
                    Message(role="user", content=f"{prompt}{prior}"),
                ],
                response_schema=schema,
                temperature=0.0,
            )
            try:
                response = await self._router.complete(role, request)
                self._calls += 1
                return json.loads(_extract_json(response.message.content or ""))
            except Exception as e:  # noqa: BLE001
                last_error = e
                await asyncio.sleep(_RETRY_BACKOFF_S)
        raise ValueError(f"LLM call failed after {attempts + 1} attempts: {last_error}")

    async def _put(self, payload: Any, artifact_type: str) -> str:
        env = ArtifactEnvelope.create(
            payload=payload, artifact_type=artifact_type, producer=_PRODUCER
        )
        await self._store.put(env)
        return env.artifact_id

    async def _link(self, source_id: str, target_id: str) -> None:
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=source_id,
                target_artifact_id=target_id,
                producer=_PRODUCER,
            )
        )

    # ------------------------------------------------------------------
    # 1. Claim extraction (Layer A deterministic + Layer B model)
    # ------------------------------------------------------------------

    async def extract_claims(
        self,
        manuscript_id: str,
        offline: bool = False,
        previous_claims: list[NoveltyClaim] | None = None,
    ) -> list[str]:
        m_env = await self._store.get(manuscript_id)
        manuscript = m_env.parse_payload(FormattedManuscript)
        sources = await self._manuscript_sources(manuscript)

        prev_by_key: dict[str, NoveltyClaim] = {}
        if previous_claims:
            for pc in previous_claims:
                prev_by_key.setdefault(detection.normalize_claim_text(pc.claim_text), pc)

        claims: dict[str, dict[str, Any]] = {}
        quote_map: list[tuple[str, str]] = []  # normalized quote -> claim key

        # ---- Layer A: deterministic high-risk detection ------------------
        for section_id, text in sources.items():
            if not text:
                continue
            norm = detection.normalize(text)
            for claim_type, risk, start, end in detection.detect_high_risk(text):
                quote = norm[start:end]
                sentence = _enclosing_sentence(norm, start, end) or quote
                key = detection.normalize_claim_text(sentence)
                location = NoveltyClaimLocation(
                    section_id=section_id,
                    quote=quote,
                    paragraph=detection.claim_paragraph(text, quote),
                )
                entry = claims.get(key)
                if entry is None:
                    claims[key] = {
                        "section_id": section_id,
                        "claim_text": sentence,
                        "claim_type": claim_type,
                        "risk": risk,
                        "source_quote": quote,
                        "locations": [location],
                        "method": "deterministic",
                    }
                    quote_map.append((detection.normalize_claim_text(quote), key))
                else:
                    entry["locations"].append(location)

        # ---- Layer B: model-assisted extraction --------------------------
        if not offline:
            try:
                model_items = await self._model_extract(manuscript, sources)
                for item in model_items:
                    text = sources.get(item.section_id, "")
                    norm = detection.normalize(text)
                    quote_norm = detection.normalize_claim_text(item.source_quote)
                    if not quote_norm or quote_norm not in detection.normalize_claim_text(norm):
                        continue
                    sentence = item.claim_text.strip()
                    key = detection.normalize_claim_text(sentence or item.source_quote)
                    location = NoveltyClaimLocation(
                        section_id=item.section_id,
                        quote=item.source_quote,
                        paragraph=detection.claim_paragraph(text, item.source_quote),
                    )
                    if key in claims:
                        claims[key]["locations"].append(location)
                        continue
                    # merge with an existing claim whose quote overlaps
                    merged = False
                    for qkey, ckey in quote_map:
                        if quote_norm in qkey or qkey in quote_norm:
                            claims[ckey]["locations"].append(location)
                            merged = True
                            break
                    if merged:
                        continue
                    # explicit priority language found by the model is
                    # upgraded deterministically — never by LLM opinion
                    claim_type, risk = self._classify_model_claim(
                        item.claim_type, item.source_quote, item.claim_text
                    )
                    claims[key] = {
                        "section_id": item.section_id,
                        "claim_text": sentence,
                        "claim_type": claim_type,
                        "risk": risk,
                        "source_quote": item.source_quote,
                        "locations": [location],
                        "method": "hybrid",
                    }
                    quote_map.append((quote_norm, key))
            except Exception as e:  # noqa: BLE001
                logger.warning("model-assisted claim extraction failed: %s", e)

        # ---- persist ------------------------------------------------------
        claim_ids: list[str] = []
        for entry in claims.values():
            key = detection.normalize_claim_text(entry["claim_text"])
            previous = prev_by_key.get(key)
            claim = NoveltyClaim(
                manuscript_id=manuscript_id,
                section_id=entry["section_id"],
                claim_text=entry["claim_text"],
                claim_type=NoveltyClaimType(entry["claim_type"]),
                risk=ClaimRiskLevel(entry["risk"]),
                scope=None,
                importance=(
                    ClaimImportance.major
                    if entry["risk"] in ("critical", "high")
                    else ClaimImportance.minor
                ),
                extraction_method=entry["method"],
                source_quote=entry["source_quote"],
                locations=entry["locations"],
                source_artifact_ids=[manuscript_id],
                equivalent_claim_id=previous.id if previous is not None else None,
            )
            cid = await self._put(claim, "novelty_claim")
            await self._link(manuscript_id, cid)
            claim_ids.append(cid)
        return claim_ids

    def _classify_model_claim(
        self, model_type: str, quote: str, claim_text: str
    ) -> tuple[str, str]:
        for text in (quote, claim_text):
            for claim_type, risk, _s, _e in detection.detect_high_risk(text):
                return claim_type, risk
        if model_type in {t.value for t in NoveltyClaimType}:
            claim_type, risk = detection.classify_claim_type_risk(model_type)
            return claim_type.value, risk.value
        return "contribution_difference", "medium"

    async def _model_extract(
        self, manuscript: FormattedManuscript, sources: dict[str, str]
    ) -> list[_ExtractionItem]:
        prompt_parts: list[str] = [
            f"Title: {manuscript.front_matter.title}",
            f"Abstract: {manuscript.front_matter.abstract}",
        ]
        for section in manuscript.sections:
            if section.section_id in _MANUSCRIPT_SECTIONS_FOR_MODEL:
                prompt_parts.append(f"[{section.section_id}]\n{section.body[:1500]}")
        allowed_sections = ", ".join(_MANUSCRIPT_SECTIONS_FOR_MODEL)
        prompt = (
            "Identify novelty and contribution claims in this manuscript: priority "
            "claims, categorical absence claims, mechanism/model/result novelty, "
            "and contribution differences. Do NOT invent claims absent from the "
            "text; source_quote must be a verbatim span of the manuscript. "
            "Ordinary positioning language is allowed with claim_type "
            "contribution_difference.\n\nManuscript:\n"
            + "\n\n".join(prompt_parts)[:12000]
            + f"\n\nReturn claims: claim_text (paraphrase ok), source_quote "
            f"(verbatim span), section_id (one of title, abstract, {allowed_sections}), "
            f"claim_type, importance (major|minor)."
        )
        data = await self._call_llm(
            self._extractor_role,
            "You extract novelty claims for external validation.",
            prompt,
            {
                "type": "object",
                "properties": {
                    "claims": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "claim_text": {"type": "string"},
                                "source_quote": {"type": "string"},
                                "section_id": {"type": "string"},
                                "claim_type": {
                                    "type": "string",
                                    "enum": [t.value for t in NoveltyClaimType],
                                },
                                "importance": {"type": "string", "enum": ["major", "minor"]},
                            },
                            "required": [
                                "claim_text",
                                "source_quote",
                                "section_id",
                                "claim_type",
                                "importance",
                            ],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["claims"],
                "additionalProperties": False,
            },
            attempts=_MAX_VALIDATION_RETRIES,
        )
        parsed = _ExtractionResponse.model_validate(data)
        valid: list[_ExtractionItem] = []
        for item in parsed.claims:
            text = sources.get(item.section_id, "")
            if not item.claim_text.strip() or not item.source_quote.strip():
                continue
            if detection.normalize_claim_text(
                item.source_quote
            ) not in detection.normalize_claim_text(text):
                continue
            valid.append(item)
        return valid

    # ------------------------------------------------------------------
    # 2. Search planning
    # ------------------------------------------------------------------

    async def plan_searches(
        self,
        claim_id: str,
        as_of: date | None = None,
        offline: bool = False,
    ) -> str:
        claim_env = await self._store.get(claim_id)
        claim = claim_env.parse_payload(NoveltyClaim)
        as_of = as_of or date.today()
        year_from = as_of.year - self._search_year_window
        year_to = as_of.year
        budget = self._risk_budget(claim.risk)

        plan_queries: list[tuple[str, str, list[str]]] = detection.deterministic_queries(claim)
        generation_method = "deterministic"

        if not offline:
            try:
                model_queries = await self._model_plan(claim, budget - len(plan_queries))
                for q, qt, syn in model_queries:
                    if len(plan_queries) >= budget:
                        break
                    if any(
                        detection.normalize_query(q) == detection.normalize_query(existing[0])
                        for existing in plan_queries
                    ):
                        continue
                    plan_queries.append((q, qt, syn))
                if model_queries:
                    generation_method = "hybrid"
            except Exception as e:  # noqa: BLE001
                logger.warning("model query expansion failed: %s", e)

        query_ids: list[str] = []
        plan_queries_out: list[NoveltyPlanQuery] = []
        for q, qt, syn in plan_queries[:budget]:
            lq = LiteratureQuery(
                query=q,
                purpose=f"novelty:{qt}",
                concepts=[],
                synonyms=list(syn),
                year_from=year_from,
                year_to=year_to,
                target_sources=list(self._providers),
                expected_relevance="high",
                generated_by="research.novelty_validator",
                metadata={
                    "novelty_claim_id": claim_id,
                    "novelty_query_type": qt,
                },
            )
            qid = await self._put(lq, "literature_query")
            await self._link(claim_id, qid)
            query_ids.append(qid)
            plan_queries_out.append(
                NoveltyPlanQuery(
                    literature_query_id=qid,
                    query=q,
                    query_type=NoveltyQueryType(qt),
                    synonyms=list(syn),
                )
            )

        plan = NoveltySearchPlan(
            claim_id=claim_id,
            manuscript_id=claim.manuscript_id,
            queries=plan_queries_out,
            query_artifact_ids=query_ids,
            providers=list(self._providers),
            date_cutoff=as_of,
            year_from=year_from,
            year_to=year_to,
            maximum_results=self._max_results_per_query,
            search_scope=(
                f"external literature databases ({', '.join(self._providers)}) "
                f"covering {year_from}-{year_to}, bounded to {budget} queries per claim"
            ),
            generation=NoveltyPlanGeneration(
                method=generation_method, model_role=self._extractor_role
            ),
        )
        plan_id = await self._put(plan, "novelty_search_plan")
        await self._link(claim_id, plan_id)
        return plan_id

    async def _model_plan(
        self, claim: NoveltyClaim, budget: int
    ) -> list[tuple[str, str, list[str]]]:
        if budget <= 0:
            return []
        prompt = (
            f"Claim ({claim.claim_type.value}, risk {claim.risk.value}): {claim.claim_text}\n"
            f"Scope: {claim.scope or 'unspecified'}\n\n"
            "Generate concrete literature-search queries that could find prior work "
            "challenging this claim. Cover multiple perspectives: exact phrase, "
            "mechanism, relationship between constructs, setting/context, theory "
            "(independent of the manuscript's terminology), and synonym expansion. "
            f"Maximum {budget} queries. Do not generate hundreds of queries.\n\n"
            "Return queries: query (string), query_type (one of exact, mechanism, "
            "relationship, setting, theory, synonym), synonyms (list of alternate "
            "terms)."
        )
        data = await self._call_llm(
            self._extractor_role,
            "You generate bounded novelty search queries.",
            prompt,
            {
                "type": "object",
                "properties": {
                    "queries": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "maxLength": 500},
                                "query_type": {
                                    "type": "string",
                                    "enum": [t.value for t in NoveltyQueryType],
                                },
                                "synonyms": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["query", "query_type", "synonyms"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["queries"],
                "additionalProperties": False,
            },
            attempts=_MAX_VALIDATION_RETRIES,
        )
        parsed = _PlanResponse.model_validate(data)
        out: list[tuple[str, str, list[str]]] = []
        seen: set[str] = set()
        for q in parsed.queries:
            query = q.query.strip()
            if not query or len(query) > 500:
                continue
            key = detection.normalize_query(query)
            if key in seen:
                continue
            seen.add(key)
            out.append((query, q.query_type, [s.strip() for s in q.synonyms if s.strip()]))
            if len(out) >= budget:
                break
        return out

    # ------------------------------------------------------------------
    # 3. Search execution
    # ------------------------------------------------------------------

    async def execute_searches(self, plan_id: str) -> str:
        plan_env = await self._store.get(plan_id)
        plan = plan_env.parse_payload(NoveltySearchPlan)
        started = datetime.now(UTC)
        search_record_ids: list[str] = []
        provider_failures: list[dict[str, Any]] = []
        attempted = 0
        succeeded = 0
        paper_ids: list[str] = []

        for qid in plan.query_artifact_ids:
            q_env = await self._store.get(qid)
            query = q_env.parse_payload(LiteratureQuery)
            for provider in plan.providers:
                attempted += 1
                try:
                    source = self._lookup(f"literature_source.{provider}")
                    req = LiteratureSearchRequest(
                        query=query.query,
                        year_from=plan.year_from,
                        year_to=plan.year_to,
                        limit=min(plan.maximum_results, self._max_results_per_query),
                    )
                    search_env, _snaps, paper_envs = await self._ingestor.ingest_search(
                        source, req, query_artifact_id=qid, producer=_PRODUCER
                    )
                    await self._link(qid, search_env.artifact_id)
                    search_record_ids.append(search_env.artifact_id)
                    paper_ids.extend(pe.artifact_id for pe in paper_envs)
                    succeeded += 1
                except Exception as e:  # noqa: BLE001
                    provider_failures.append(
                        {"query_id": qid, "provider": provider, "error": str(e)}
                    )
                    logger.warning("novelty search failed (%s/%s): %s", provider, qid, e)

        execution = NoveltySearchExecution(
            claim_id=plan.claim_id,
            search_plan_id=plan_id,
            search_record_artifact_ids=search_record_ids,
            as_of_date=plan.date_cutoff,
            planned_searches=len(plan.query_artifact_ids) * len(plan.providers),
            executed_searches=attempted,
            successful_searches=succeeded,
            provider_failures=provider_failures,
            counts={
                "paper_records_ingested": len(paper_ids),
                "providers": list(plan.providers),
                "queries": len(plan.query_artifact_ids),
            },
            started_at=started,
            completed_at=datetime.now(UTC),
        )
        exec_id = await self._put(execution, "novelty_search_execution")
        await self._link(plan_id, exec_id)
        for sid in search_record_ids:
            await self._link(sid, exec_id)
        return exec_id

    # ------------------------------------------------------------------
    # 4. Candidate set (dedup via PaperIdentity, deterministic filtering)
    # ------------------------------------------------------------------

    async def build_candidate_set(self, claim_id: str, plan_id: str, execution_id: str) -> str:
        plan_env = await self._store.get(plan_id)
        plan = plan_env.parse_payload(NoveltySearchPlan)
        exec_env = await self._store.get(execution_id)
        execution = exec_env.parse_payload(NoveltySearchExecution)

        paper_ids: list[str] = []
        for sid in execution.search_record_artifact_ids:
            rec = (await self._store.get(sid)).parse_payload(LiteratureSearchRecord)
            paper_ids.extend(rec.paper_artifact_ids)

        result = await self._resolver.resolve(paper_ids)
        identity_ids = result.identities_created + result.identities_reused

        # query -> paper ids found by that query
        query_paper_ids: dict[str, list[str]] = {qid: [] for qid in plan.query_artifact_ids}
        for sid in execution.search_record_artifact_ids:
            rec = (await self._store.get(sid)).parse_payload(LiteratureSearchRecord)
            if rec.query_artifact_id in query_paper_ids:
                query_paper_ids[rec.query_artifact_id].extend(rec.paper_artifact_ids)

        candidates: list[NoveltyCandidateRef] = []
        excluded: list[NoveltyExclusion] = []
        for identity_id in identity_ids:
            identity = (await self._store.get(identity_id)).parse_payload(PaperIdentity)
            member_ids = set(identity.member_paper_artifact_ids)
            years: list[int] = []
            titles: list[str] = []
            pub_types: set[str] = set()
            for pid in member_ids:
                rec_env = await self._store.get(pid)
                if rec_env.artifact_type != "paper_record":
                    continue
                rec = rec_env.parse_payload(PaperRecord)
                if rec.year is not None:
                    years.append(rec.year)
                if rec.title:
                    titles.append(rec.title)
                if rec.publication_type:
                    pub_types.add(rec.publication_type)
            earliest = min(years) if years else None

            # deterministic filtering with exclusion reasons
            if not titles:
                excluded.append(
                    NoveltyExclusion(paper_identity_id=identity_id, reason="missing_title")
                )
                continue
            unsupported = pub_types & _UNSUPPORTED_DOCUMENT_TYPES
            if unsupported:
                excluded.append(
                    NoveltyExclusion(
                        paper_identity_id=identity_id,
                        reason=f"unsupported_document_type:{sorted(unsupported)[0]}",
                    )
                )
                continue
            if (
                earliest is not None
                and plan.date_cutoff is not None
                and earliest > plan.date_cutoff.year
            ):
                excluded.append(
                    NoveltyExclusion(
                        paper_identity_id=identity_id,
                        reason=f"post_cutoff:{earliest}>{plan.date_cutoff.year}",
                    )
                )
                continue

            found_queries = [
                qid for qid, found in query_paper_ids.items() if member_ids & set(found)
            ]
            found_providers: list[str] = []
            for sid in execution.search_record_artifact_ids:
                rec = (await self._store.get(sid)).parse_payload(LiteratureSearchRecord)
                if rec.query_artifact_id in found_queries and rec.provider not in found_providers:
                    found_providers.append(rec.provider)

            candidates.append(
                NoveltyCandidateRef(
                    paper_identity_id=identity_id,
                    found_by_query_ids=found_queries,
                    found_by_providers=found_providers,
                    rank=None,
                    score=None,
                    earliest_year=earliest,
                )
            )

        cset = NoveltyCandidateSet(
            claim_id=claim_id,
            search_plan_id=plan_id,
            search_execution_id=execution_id,
            candidates=candidates,
            excluded=excluded,
        )
        cset_id = await self._put(cset, "novelty_candidate_set")
        for target in (claim_id, plan_id, execution_id):
            await self._link(target, cset_id)
        return cset_id

    # ------------------------------------------------------------------
    # 5. Candidate prior-art assessment (evidence-backed) + critic pass
    # ------------------------------------------------------------------

    async def assess_candidates(
        self, claim_id: str, candidate_set_id: str, offline: bool = False
    ) -> list[str]:
        claim_env = await self._store.get(claim_id)
        claim = claim_env.parse_payload(NoveltyClaim)
        cset = (await self._store.get(candidate_set_id)).parse_payload(NoveltyCandidateSet)

        assessment_ids: list[str] = []
        for candidate in cset.candidates:
            identity_id = candidate.paper_identity_id
            basis, evidence_text, evidence_ids = await self._gather_evidence(identity_id)

            # ---- Phase 5C: enrich sparse evidence before assessment -------
            enrichment_exec_id: str | None = None
            if self._enrichment_enabled and (
                basis == EvidenceBasis.title_only
                or (
                    basis == EvidenceBasis.indexed_metadata
                    and claim.risk in (ClaimRiskLevel.critical, ClaimRiskLevel.high)
                )
            ):
                # Phase 5D: do not repeat strategies that already failed in
                # this run's pre-acquisition for the same identity+claim
                repeated_failure = (
                    self._preacquisition_enabled
                    and await self._preacquisition_failed_for(identity_id, claim_id)
                )
                if not repeated_failure:
                    try:
                        enrichment_exec_id = await self._enrich(
                            claim_id, identity_id, basis, offline=offline
                        )
                        basis, evidence_text, evidence_ids = await self._gather_evidence(
                            identity_id
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning("evidence enrichment failed (%s): %s", identity_id, e)

            relationship = CandidateRelationship.insufficient_evidence
            dimensions: list[NoveltyDimensionScore] = []
            assessment_text = ""

            if offline:
                assessment_text = (
                    "offline deterministic mode: no model-assisted semantic comparison "
                    "was performed"
                )
            else:
                try:
                    data = await self._assess_one(claim, candidate, basis, evidence_text)
                    relationship = CandidateRelationship(data["relationship"])
                    dimensions = data["dimensions"]
                    assessment_text = data["assessment"]
                    # deterministic guards: weak evidence cannot yield strong
                    # semantic judgments
                    if basis == EvidenceBasis.title_only:
                        relationship = CandidateRelationship.insufficient_evidence
                        assessment_text = (
                            "title-only evidence: contents of the paper were not "
                            "verified; no strong semantic judgment is possible"
                        )
                    elif basis == EvidenceBasis.indexed_metadata and relationship in (
                        _STRONG_RELATIONSHIPS | {CandidateRelationship.partial_overlap}
                    ):
                        relationship = CandidateRelationship.insufficient_evidence
                        assessment_text = (
                            "indexed metadata only (no abstract or full text): "
                            "overlap could not be verified from evidence"
                        )
                except Exception as e:  # noqa: BLE001
                    logger.warning("candidate assessment failed (%s): %s", identity_id, e)
                    relationship = CandidateRelationship.insufficient_evidence
                    assessment_text = f"assessment failed: {e}"
            cand_id = str(uuid.uuid4())
            assessment = NoveltyCandidateAssessment(
                id=cand_id,
                claim_id=claim_id,
                candidate_set_id=candidate_set_id,
                paper_identity_id=identity_id,
                dimensions=dimensions,
                relationship=relationship,
                evidence_basis=basis,
                evidence_artifact_ids=evidence_ids,
                assessment_text=assessment_text,
                critic_assessment_ids=[],
                model_role=self._extractor_role if not offline else None,
            )

            # ---- independent critic pass --------------------------------
            # Runs BEFORE the candidate assessment is persisted, so the
            # assessment never needs a superseding copy; disagreement is
            # preserved as a separate critic artifact.
            critic_ids: list[str] = []
            if not offline and (
                claim.risk == ClaimRiskLevel.critical or relationship in _STRONG_RELATIONSHIPS
            ):
                critic_ids = [
                    await self._critic_pass(
                        claim, identity_id, cand_id, basis, evidence_text, assessment
                    )
                ]
            if critic_ids:
                assessment = assessment.model_copy(update={"critic_assessment_ids": critic_ids})

            env = ArtifactEnvelope.create(
                payload=assessment,
                artifact_type="novelty_candidate_assessment",
                producer=_PRODUCER,
                artifact_id=cand_id,
            )
            await self._store.put(env)
            for target in (candidate_set_id, identity_id):
                await self._link(target, cand_id)
            for cid in critic_ids:
                await self._link(cand_id, cid)
            for eid in evidence_ids:
                try:
                    await self._link(eid, cand_id)
                except Exception:  # noqa: BLE001
                    pass
            if enrichment_exec_id is not None:
                await self._link(enrichment_exec_id, cand_id)
            assessment_ids.append(cand_id)
        return assessment_ids

    # ------------------------------------------------------------------
    # 5b. Phase 5C: evidence enrichment for sparse candidates
    # ------------------------------------------------------------------

    async def _enrich(
        self,
        claim_id: str,
        identity_id: str,
        before_basis: EvidenceBasis,
        *,
        offline: bool = False,
        candidate_assessment_id: str | None = None,
        requested_types: list[str] | None = None,
    ) -> str:
        """Acquire missing abstracts/full text for a sparse candidate through
        the existing provider/document infrastructure. Returns the
        EvidenceEnrichmentExecution artifact id. `claim_id` is the claim's
        envelope artifact id. `requested_types` overrides the configured
        evidence types (used by Phase 5D pre-acquisition)."""
        requested: list[str] = list(requested_types or [])
        strategies: list[str] = []
        if "abstract" in requested and self._acquire_abstract:
            strategies.append("provider_get_abstract")
        if "full_text" in requested and self._acquire_full_text:
            strategies.append("document_full_text")
        if not requested:
            if self._acquire_abstract:
                requested.append("abstract")
                strategies.append("provider_get_abstract")
            if self._acquire_full_text:
                requested.append("full_text")
                strategies.append("document_full_text")

        plan = EvidenceEnrichmentPlan(
            candidate_assessment_id=candidate_assessment_id,
            paper_identity_id=identity_id,
            claim_id=claim_id,
            requested_evidence_types=requested,
            acquisition_strategies=strategies,
            reason=(
                f"evidence basis '{before_basis.value}' is insufficient for a "
                "defensible novelty comparison"
            ),
            provider_service_policy={
                "abstract_providers": list(self._abstract_providers),
                "acquire_full_text": self._acquire_full_text,
                "max_attempts": self._max_enrichment_attempts,
            },
        )
        plan_id = await self._put(plan, "evidence_enrichment_plan")
        await self._link(identity_id, plan_id)
        await self._link(claim_id, plan_id)

        attempt_ids: list[str] = []
        if "provider_get_abstract" in strategies:
            attempt_ids.extend(await self._attempt_abstract_acquisition(plan_id, identity_id))
        if "document_full_text" in strategies:
            attempt_ids.extend(
                await self._attempt_full_text_acquisition(plan_id, identity_id, claim_id, offline)
            )

        after_basis, _text, after_evidence = await self._gather_evidence(identity_id)
        outcome = await self._enrichment_outcome(before_basis, after_basis, attempt_ids)
        execution = EvidenceEnrichmentExecution(
            plan_id=plan_id,
            attempt_ids=attempt_ids,
            resulting_evidence_ids=after_evidence,
            before_evidence_basis=before_basis,
            after_evidence_basis=after_basis,
            outcome=outcome,
        )
        exec_id = await self._put(execution, "evidence_enrichment_execution")
        await self._link(plan_id, exec_id)
        for aid in attempt_ids:
            await self._link(aid, exec_id)
        return exec_id

    async def _enrichment_outcome(
        self,
        before: EvidenceBasis,
        after: EvidenceBasis,
        attempt_ids: list[str],
    ) -> EnrichmentOutcome:
        rank = {
            EvidenceBasis.title_only: 0,
            EvidenceBasis.indexed_metadata: 1,
            EvidenceBasis.abstract: 2,
            EvidenceBasis.full_text: 3,
        }
        if rank[after] > rank[before]:
            return EnrichmentOutcome.enriched
        if not attempt_ids:
            return EnrichmentOutcome.no_improvement
        succeeded = False
        for aid in attempt_ids:
            try:
                a = (await self._store.get(aid)).parse_payload(EvidenceEnrichmentAttempt)
            except Exception:  # noqa: BLE001
                continue
            if a.status == EnrichmentAttemptStatus.success:
                succeeded = True
                break
        if succeeded:
            return EnrichmentOutcome.partially_enriched
        return EnrichmentOutcome.failed

    async def _identity_identifiers(self, identity_id: str) -> dict[str, Any]:
        identity = (await self._store.get(identity_id)).parse_payload(PaperIdentity)
        doi: str | None = None
        provider_ids: dict[str, str] = {}
        url: str | None = None
        for pid in identity.member_paper_artifact_ids:
            try:
                rec_env = await self._store.get(pid)
                if rec_env.artifact_type != "paper_record":
                    continue
                rec = rec_env.parse_payload(PaperRecord)
                if doi is None and rec.doi:
                    doi = normalize_doi(rec.doi)
                for ext in rec.external_identifiers:
                    if ext.scheme not in provider_ids:
                        provider_ids[ext.scheme] = ext.value
                if url is None and (rec.url or rec.open_access_url):
                    url = rec.url or rec.open_access_url
            except Exception:  # noqa: BLE001
                continue
        return {"doi": doi, "provider_ids": provider_ids, "url": url}

    async def _attempt_abstract_acquisition(self, plan_id: str, identity_id: str) -> list[str]:
        """External abstract acquisition via existing literature sources
        (`literature_source.{provider}.get` + the ingestor). Identifiers are
        tried in order (DOI -> provider paper id -> canonical URL); attempts
        are bounded by max_attempts_per_candidate."""
        attempt_ids: list[str] = []
        identifiers = await self._identity_identifiers(identity_id)
        if (
            identifiers["doi"] is None
            and not identifiers["provider_ids"]
            and identifiers["url"] is None
        ):
            attempt_ids.append(
                await self._persist_attempt(
                    plan_id,
                    "provider_get_abstract",
                    None,
                    EnrichmentAttemptStatus.failed,
                    failure_reason="no usable identifier (doi/provider id/url)",
                )
            )
            return attempt_ids

        for provider in self._abstract_providers:
            if len(attempt_ids) >= self._max_enrichment_attempts:
                break
            try:
                source = self._lookup(f"literature_source.{provider}")
            except Exception:  # noqa: BLE001
                attempt_ids.append(
                    await self._persist_attempt(
                        plan_id,
                        "provider_get_abstract",
                        provider,
                        EnrichmentAttemptStatus.skipped,
                        failure_reason="provider service unavailable",
                    )
                )
                continue
            for identifier, ident_desc in self._provider_identifiers(provider, identifiers):
                if len(attempt_ids) >= self._max_enrichment_attempts:
                    break
                try:
                    _snap_env, paper_env = await self._ingestor.ingest_get(
                        source, identifier, producer=_PRODUCER
                    )
                except LiteratureRateLimitError as e:
                    attempt_ids.append(
                        await self._persist_attempt(
                            plan_id,
                            "provider_get_abstract",
                            provider,
                            EnrichmentAttemptStatus.rate_limited,
                            failure_reason=str(e),
                        )
                    )
                    continue
                except LiteratureNotFoundError as e:
                    attempt_ids.append(
                        await self._persist_attempt(
                            plan_id,
                            "provider_get_abstract",
                            provider,
                            EnrichmentAttemptStatus.not_found,
                            failure_reason=f"{ident_desc}: {e}",
                        )
                    )
                    continue
                except Exception as e:  # noqa: BLE001
                    attempt_ids.append(
                        await self._persist_attempt(
                            plan_id,
                            "provider_get_abstract",
                            provider,
                            EnrichmentAttemptStatus.failed,
                            failure_reason=f"{ident_desc}: {e}",
                        )
                    )
                    continue
                rec = paper_env.parse_payload(PaperRecord)
                if not rec.abstract:
                    attempt_ids.append(
                        await self._persist_attempt(
                            plan_id,
                            "provider_get_abstract",
                            provider,
                            EnrichmentAttemptStatus.not_found,
                            failure_reason=f"{ident_desc}: provider record has no abstract",
                        )
                    )
                    continue
                item_id = await self._persist_abstract_item(
                    paper_env.artifact_id, rec, provider, identity_id
                )
                attempt_ids.append(
                    await self._persist_attempt(
                        plan_id,
                        "provider_get_abstract",
                        provider,
                        EnrichmentAttemptStatus.success,
                        retrieved_artifact_ids=[paper_env.artifact_id, item_id],
                    )
                )
                return attempt_ids
        return attempt_ids

    def _provider_identifiers(
        self, provider: str, identifiers: dict[str, Any]
    ) -> list[tuple[str, str]]:
        """Ordered identifiers for a provider: DOI -> provider paper id ->
        canonical URL -> any other provider id."""
        out: list[tuple[str, str]] = []
        if identifiers["doi"]:
            out.append((identifiers["doi"], "doi"))
        if provider == "semantic_scholar":
            pid = identifiers["provider_ids"].get("semantic_scholar")
            if pid:
                out.append((pid, "semantic_scholar paper id"))
        if identifiers["url"]:
            out.append((identifiers["url"], "canonical url"))
        for value in identifiers["provider_ids"].values():
            if value not in [i for i, _ in out]:
                out.append((value, "provider identifier"))
        return out

    async def _persist_abstract_item(
        self, paper_id: str, rec: PaperRecord, provider: str, identity_id: str
    ) -> str:
        item = EvidenceItem(
            statement=rec.abstract or "",
            source_artifact_id=paper_id,
            category=EvidenceCategory.result,
            extraction_method="provider_import",
            metadata={
                "novelty_enrichment": True,
                "acquired_abstract": True,
                "provider": provider,
            },
        )
        item_id = await self._put(item, "evidence_item")
        await self._link(paper_id, item_id)
        await self._link(identity_id, item_id)
        return item_id

    async def _persist_attempt(
        self,
        plan_id: str,
        strategy: str,
        provider: str | None,
        status: EnrichmentAttemptStatus,
        *,
        retrieved_artifact_ids: list[str] | None = None,
        failure_reason: str | None = None,
    ) -> str:
        attempt = EvidenceEnrichmentAttempt(
            plan_id=plan_id,
            strategy=strategy,
            provider=provider,
            status=status,
            retrieved_artifact_ids=retrieved_artifact_ids or [],
            failure_reason=failure_reason,
        )
        attempt_id = await self._put(attempt, "evidence_enrichment_attempt")
        await self._link(plan_id, attempt_id)
        return attempt_id

    async def _attempt_full_text_acquisition(
        self, plan_id: str, identity_id: str, claim_id: str, offline: bool
    ) -> list[str]:
        """Full-text acquisition via existing document services: locator ->
        fetcher -> extractor -> novelty-relevant EvidenceItems."""
        claim = (await self._store.get(claim_id)).parse_payload(NoveltyClaim)
        attempt_ids: list[str] = []
        existing_docs = [
            env
            for env in await self._store.list(artifact_type="full_text_document")
            if env.payload.get("paper_identity_id") == identity_id
        ]
        if existing_docs:
            attempt_ids.append(
                await self._persist_attempt(
                    plan_id,
                    "document_full_text",
                    None,
                    EnrichmentAttemptStatus.skipped,
                    failure_reason="full text already available in the repository",
                )
            )
            return attempt_ids

        locator_names = ["document_locator.metadata", "document_locator.unpaywall"]
        for locator_name in locator_names:
            if len(attempt_ids) >= self._max_enrichment_attempts:
                break
            try:
                locator = self._lookup(locator_name)
                location_ids = await locator.resolve(identity_id)
            except Exception as e:  # noqa: BLE001
                attempt_ids.append(
                    await self._persist_attempt(
                        plan_id,
                        "document_full_text",
                        locator_name,
                        EnrichmentAttemptStatus.skipped
                        if "not available" in str(e)
                        else EnrichmentAttemptStatus.failed,
                        failure_reason=str(e),
                    )
                )
                continue
            if not location_ids:
                attempt_ids.append(
                    await self._persist_attempt(
                        plan_id,
                        "document_full_text",
                        locator_name,
                        EnrichmentAttemptStatus.not_found,
                        failure_reason="no document locations resolved",
                    )
                )
                continue
            try:
                fetcher = self._lookup("document_fetcher.default")
            except Exception as e:  # noqa: BLE001
                attempt_ids.append(
                    await self._persist_attempt(
                        plan_id,
                        "document_full_text",
                        "document_fetcher.default",
                        EnrichmentAttemptStatus.skipped,
                        failure_reason=str(e),
                    )
                )
                return attempt_ids
            acquired_doc: str | None = None
            for loc_id in location_ids:
                try:
                    acq_id = await fetcher.fetch(loc_id)
                except Exception as e:  # noqa: BLE001
                    attempt_ids.append(
                        await self._persist_attempt(
                            plan_id,
                            "document_full_text",
                            locator_name,
                            EnrichmentAttemptStatus.failed,
                            failure_reason=f"fetch failed: {e}",
                        )
                    )
                    continue
                from research_harness.research.schemas.document_acquisition import (
                    AcquisitionStatus,
                    DocumentAcquisition,
                )

                try:
                    acq = (await self._store.get(acq_id)).parse_payload(DocumentAcquisition)
                except Exception:  # noqa: BLE001
                    continue
                if acq.status == AcquisitionStatus.downloaded:
                    acquired_doc = acq_id
                    break
                attempt_ids.append(
                    await self._persist_attempt(
                        plan_id,
                        "document_full_text",
                        locator_name,
                        EnrichmentAttemptStatus.restricted
                        if acq.status == AcquisitionStatus.access_restricted
                        else EnrichmentAttemptStatus.failed,
                        failure_reason=f"acquisition status {acq.status.value}",
                    )
                )
            if acquired_doc is None:
                continue
            try:
                extractor = self._lookup("document_extractor.pypdf")
                doc_id = await extractor.extract(acquired_doc)
            except Exception as e:  # noqa: BLE001
                attempt_ids.append(
                    await self._persist_attempt(
                        plan_id,
                        "document_full_text",
                        locator_name,
                        EnrichmentAttemptStatus.failed,
                        failure_reason=f"extraction failed: {e}",
                    )
                )
                continue
            doc_env = await self._store.get(doc_id)
            doc = doc_env.parse_payload(FullTextDocument)
            if doc.text_status.value != "extracted":
                attempt_ids.append(
                    await self._persist_attempt(
                        plan_id,
                        "document_full_text",
                        locator_name,
                        EnrichmentAttemptStatus.failed,
                        failure_reason=f"text status {doc.text_status.value}",
                    )
                )
                continue
            item_ids = await self._extract_novelty_evidence(
                claim, doc_id, doc, identity_id, offline
            )
            attempt_ids.append(
                await self._persist_attempt(
                    plan_id,
                    "document_full_text",
                    locator_name,
                    EnrichmentAttemptStatus.success,
                    retrieved_artifact_ids=[acquired_doc, doc_id, *item_ids],
                )
            )
            return attempt_ids
        return attempt_ids

    async def _load_document_pages(self, doc: FullTextDocument) -> list[dict[str, Any]]:
        if self._blobs is None or doc.text_blob is None:
            return []
        data = await self._blobs.get_bytes(BlobReference(**doc.text_blob.model_dump()))
        try:
            payload = json.loads(data.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return []
        return payload.get("pages", []) if isinstance(payload, dict) else []

    async def _extract_novelty_evidence(
        self,
        claim: NoveltyClaim,
        doc_id: str,
        doc: FullTextDocument,
        identity_id: str,
        offline: bool,
    ) -> list[str]:
        pages = await self._load_document_pages(doc)
        if not pages:
            return []
        item_ids: list[str] = []
        extractor = None
        if not offline:
            try:
                extractor = self._lookup("evidence_extractor.default")
            except Exception:  # noqa: BLE001
                extractor = None
        if extractor is not None:
            try:
                # structurally compatible chunk (no cross-plugin imports)
                chunk = _NoveltyEvidenceChunk(
                    document_id=doc_id,
                    chunk_index=0,
                    start_page=pages[0]["page"],
                    end_page=pages[-1]["page"],
                    page_texts=pages[:20],
                )
                candidates = await extractor.extract_chunk(chunk)
                for cand in candidates[:6]:
                    item = EvidenceItem(
                        statement=cand.statement,
                        source_artifact_id=doc_id,
                        category=EvidenceCategory(cand.category),
                        locator=Locator(page=cand.page_numbers[0], pages=cand.page_numbers),
                        extraction_method="model-assisted",
                        confidence=cand.confidence,
                        metadata={
                            "novelty_enrichment": True,
                            "extractor": "literature.evidence_extractor",
                            "excerpt": cand.excerpt,
                        },
                    )
                    item_ids.append(await self._persist_evidence_item(item, doc_id, identity_id))
            except Exception as e:  # noqa: BLE001
                logger.warning("model-assisted novelty evidence extraction failed: %s", e)
        if not item_ids:
            # deterministic relevance extraction: sentences overlapping claim tokens
            tokens = set(re.findall(r"[a-z0-9]{4,}", claim.claim_text.lower()))
            for page in pages[:20]:
                for sent in _sentences(page.get("text", "")):
                    sent_tokens = set(re.findall(r"[a-z0-9]{4,}", sent.lower()))
                    if len(sent_tokens & tokens) >= 2:
                        item = EvidenceItem(
                            statement=sent,
                            source_artifact_id=doc_id,
                            category=EvidenceCategory.result,
                            locator=Locator(page=page["page"]),
                            extraction_method="deterministic",
                            metadata={"novelty_enrichment": True, "relevance": "claim tokens"},
                        )
                        item_ids.append(
                            await self._persist_evidence_item(item, doc_id, identity_id)
                        )
                        if len(item_ids) >= 8:
                            break
        return item_ids

    async def _persist_evidence_item(
        self, item: EvidenceItem, doc_id: str, identity_id: str
    ) -> str:
        item_id = await self._put(item, "evidence_item")
        await self._link(doc_id, item_id)
        await self._link(identity_id, item_id)
        return item_id

    # ------------------------------------------------------------------
    # 5c. Phase 5D: bounded evidence pre-acquisition before assessment
    # ------------------------------------------------------------------

    async def preacquire_evidence(
        self, claim_id: str, candidate_set_id: str, *, offline: bool = False
    ) -> str | None:
        """Deterministic, bounded evidence pre-acquisition for a claim's
        sparse candidates. Reuses the Phase 5C acquisition path; returns the
        PreAcquisitionExecution artifact id, or None when the claim's risk is
        outside the configured risk levels."""
        claim = (await self._store.get(claim_id)).parse_payload(NoveltyClaim)
        if claim.risk.value not in self._preacq_risk_levels:
            return None
        cset = (await self._store.get(candidate_set_id)).parse_payload(NoveltyCandidateSet)

        considered: list[str] = []
        skipped: list[str] = []
        reasons: dict[str, str] = {}
        metrics = {
            "candidates_considered": 0,
            "candidates_selected": 0,
            "cache_hits": 0,
            "external_attempts": 0,
            "abstracts_acquired": 0,
            "full_texts_acquired": 0,
            "failures": 0,
            "candidates_upgraded": 0,
        }
        priority_scores: dict[str, tuple[int, int, bool, bool]] = {}

        for candidate in cset.candidates:
            identity_id = candidate.paper_identity_id
            basis, _text, _ids = await self._gather_evidence(identity_id)
            if basis in (EvidenceBasis.full_text, EvidenceBasis.abstract):
                metrics["cache_hits"] += 1
                reasons[identity_id] = "cache_hit: adequate evidence already available"
                continue
            identifiers = await self._identity_identifiers(identity_id)
            usable = bool(identifiers["doi"] or identifiers["provider_ids"] or identifiers["url"])
            if basis == EvidenceBasis.title_only and not usable:
                skipped.append(identity_id)
                reasons[identity_id] = "skipped: title_only with no usable external identifier"
                continue
            if basis == EvidenceBasis.title_only or basis == EvidenceBasis.indexed_metadata:
                considered.append(identity_id)
                priority_scores[identity_id] = (
                    len(candidate.found_by_query_ids) + len(candidate.found_by_providers),
                    len(candidate.found_by_providers),
                    bool(identifiers["doi"]),
                    candidate.earliest_year is not None,
                )
                continue
            skipped.append(identity_id)
            reasons[identity_id] = f"skipped: evidence basis {basis.value} not pre-acquirable"
        metrics["candidates_considered"] = len(considered)

        # deterministic prioritization: discovery breadth -> providers -> DOI -> year
        ordered = sorted(
            considered,
            key=lambda iid: priority_scores.get(iid, (0, 0, False, False)),
            reverse=True,
        )
        selected: list[str] = []
        enrichment_exec_ids: list[str] = []
        for identity_id in ordered:
            if len(selected) >= self._preacq_max_per_claim:
                reasons[identity_id] = "skipped: per-claim budget (max_candidates_per_claim)"
                skipped.append(identity_id)
                continue
            if self._preacq_total_used >= self._preacq_max_total:
                reasons[identity_id] = "skipped: global budget (max_total_candidates)"
                skipped.append(identity_id)
                continue
            selected.append(identity_id)
            self._preacq_total_used += 1
            score = priority_scores.get(identity_id, (0, 0, False, False))
            reasons[identity_id] = (
                "selected: sparse candidate; discovery breadth "
                f"{score[0]}, providers {score[1]}, doi {'yes' if score[2] else 'no'}, "
                f"year {'yes' if score[3] else 'no'}"
            )
        metrics["candidates_selected"] = len(selected)

        for identity_id in selected:
            basis, _text, _ids = await self._gather_evidence(identity_id)
            if basis in (EvidenceBasis.full_text, EvidenceBasis.abstract):
                metrics["cache_hits"] += 1
                continue
            requested = ["abstract"]
            if self._preacq_acquire_full_text:
                requested.append("full_text")
            exec_id = await self._enrich(
                claim_id,
                identity_id,
                basis,
                offline=offline,
                requested_types=requested,
            )
            enrichment_exec_ids.append(exec_id)
            execution = (await self._store.get(exec_id)).parse_payload(EvidenceEnrichmentExecution)
            metrics["external_attempts"] += len(execution.attempt_ids)
            for aid in execution.attempt_ids:
                attempt = (await self._store.get(aid)).parse_payload(EvidenceEnrichmentAttempt)
                if attempt.status == EnrichmentAttemptStatus.success:
                    if attempt.strategy == "provider_get_abstract":
                        metrics["abstracts_acquired"] += 1
                    elif attempt.strategy == "document_full_text":
                        metrics["full_texts_acquired"] += 1
                else:
                    metrics["failures"] += 1
            if execution.after_evidence_basis != execution.before_evidence_basis:
                metrics["candidates_upgraded"] += 1

        execution_record = PreAcquisitionExecution(
            claim_id=claim_id,
            candidate_set_id=candidate_set_id,
            considered_candidate_ids=considered,
            selected_candidate_ids=selected,
            skipped_candidate_ids=skipped,
            selection_reasons=reasons,
            budget={
                "risk_levels": list(self._preacq_risk_levels),
                "max_candidates_per_claim": self._preacq_max_per_claim,
                "max_total_candidates": self._preacq_max_total,
                "prefer_abstract": True,
                "acquire_full_text": self._preacq_acquire_full_text,
            },
            metrics=metrics,
            enrichment_execution_ids=enrichment_exec_ids,
        )
        exec_id = await self._put(execution_record, "evidence_preacquisition_execution")
        await self._link(claim_id, exec_id)
        await self._link(candidate_set_id, exec_id)
        for eid in enrichment_exec_ids:
            await self._link(eid, exec_id)
        return exec_id

    async def _preacquisition_failed_for(self, identity_id: str, claim_id: str) -> bool:
        """True when Phase 5D pre-acquisition already ran (and failed or made
        no progress) for this identity+claim in the current run — the Phase
        5C inline fallback must not repeat the same failed strategies."""
        for env in await self._store.list(artifact_type="evidence_enrichment_execution"):
            try:
                execution = env.parse_payload(EvidenceEnrichmentExecution)
                plan = (await self._store.get(execution.plan_id)).parse_payload(
                    EvidenceEnrichmentPlan
                )
            except Exception:  # noqa: BLE001
                continue
            if (
                plan.paper_identity_id == identity_id
                and plan.claim_id == claim_id
                and execution.outcome
                in (
                    EnrichmentOutcome.failed,
                    EnrichmentOutcome.no_improvement,
                )
            ):
                return True
        return False

    async def _critic_pass(
        self,
        claim: NoveltyClaim,
        identity_id: str,
        candidate_assessment_id: str,
        basis: EvidenceBasis,
        evidence_text: str,
        first_pass: NoveltyCandidateAssessment,
    ) -> str:
        verdict = CriticVerdict.uncertain
        reasoning = "critic pass failed; treated as uncertain"
        try:
            prompt = (
                f"Novelty claim ({claim.claim_type.value}, risk {claim.risk.value}): "
                f"{claim.claim_text}\n\n"
                f"Candidate paper identity: {identity_id}\n"
                f"Evidence basis: {basis.value}\n"
                f"Evidence:\n{evidence_text[:4000]}\n\n"
                f"First-pass structured assessment:\n"
                f"relationship={first_pass.relationship.value}\n"
                f"dimensions={[f'{d.dimension.value}={d.value.value}' for d in first_pass.dimensions]}\n"
                f"assessment={first_pass.assessment_text[:800]}\n\n"
                "Independently decide whether this candidate actually threatens the "
                "claim. Base your verdict ONLY on the evidence provided; do not infer "
                "paper contents beyond it. Verdict: concurs (first pass is right), "
                "disputes (first pass is wrong), or uncertain."
            )
            data = await self._call_llm(
                self._critic_role,
                "You independently verify a prior-art assessment for a novelty claim.",
                prompt,
                {
                    "type": "object",
                    "properties": {
                        "verdict": {
                            "type": "string",
                            "enum": [v.value for v in CriticVerdict],
                        },
                        "reasoning": {"type": "string"},
                    },
                    "required": ["verdict", "reasoning"],
                    "additionalProperties": False,
                },
                attempts=0,
            )
            parsed = _CriticResponse.model_validate(data)
            verdict = CriticVerdict(parsed.verdict)
            reasoning = parsed.reasoning
        except Exception as e:  # noqa: BLE001
            logger.warning("critic pass failed (%s): %s", identity_id, e)
        critic = NoveltyCriticAssessment(
            claim_id=claim.id,
            candidate_assessment_id=candidate_assessment_id,
            paper_identity_id=identity_id,
            verdict=verdict,
            reasoning=reasoning,
            evidence_artifact_ids=first_pass.evidence_artifact_ids,
            model_role=self._critic_role,
        )
        critic_id = await self._put(critic, "novelty_critic_assessment")
        await self._link(identity_id, critic_id)
        return critic_id

    async def _assess_one(
        self,
        claim: NoveltyClaim,
        candidate: NoveltyCandidateRef,
        basis: EvidenceBasis,
        evidence_text: str,
    ) -> dict[str, Any]:
        identity = (await self._store.get(candidate.paper_identity_id)).parse_payload(PaperIdentity)
        titles: list[str] = []
        for pid in identity.member_paper_artifact_ids:
            try:
                rec_env = await self._store.get(pid)
                if rec_env.artifact_type == "paper_record":
                    titles.append(rec_env.parse_payload(PaperRecord).title)
            except Exception:  # noqa: BLE001
                continue
        prompt = (
            f"Novelty claim ({claim.claim_type.value}, risk {claim.risk.value}): "
            f"{claim.claim_text}\n"
            f"Claim scope: {claim.scope or 'unspecified'}\n\n"
            f"Candidate paper: {'; '.join(titles) or candidate.paper_identity_id}\n"
            f"Candidate paper identity: {candidate.paper_identity_id}\n"
            f"Evidence basis: {basis.value}\n"
            f"Evidence:\n{evidence_text[:5000]}\n\n"
            "Assess whether this candidate prior-art paper materially contradicts "
            "the novelty claim. Score each dimension (match | partial_match | "
            "different | unknown): focal_phenomenon, actors, setting, mechanism, "
            "key_assumptions, strategic_decision, causal_equilibrium_relationship, "
            "theoretical_result, claimed_contribution. Then relationship: "
            "direct_prior_art (same mechanism+setting+result, predating), "
            "strong_overlap, partial_overlap, adjacent, distinct, "
            "insufficient_evidence. Base everything ONLY on the evidence above."
        )
        data = await self._call_llm(
            self._extractor_role,
            "You assess whether a candidate prior-art paper threatens a novelty claim.",
            prompt,
            {
                "type": "object",
                "properties": {
                    "dimensions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "dimension": {
                                    "type": "string",
                                    "enum": [d.value for d in NoveltyDimension],
                                },
                                "value": {
                                    "type": "string",
                                    "enum": [m.value for m in MatchLevel],
                                },
                            },
                            "required": ["dimension", "value"],
                            "additionalProperties": False,
                        },
                    },
                    "relationship": {
                        "type": "string",
                        "enum": [r.value for r in CandidateRelationship],
                    },
                    "assessment": {"type": "string"},
                },
                "required": ["dimensions", "relationship", "assessment"],
                "additionalProperties": False,
            },
            attempts=_MAX_VALIDATION_RETRIES,
        )
        parsed = _AssessmentResponse.model_validate(data)
        return {
            "relationship": parsed.relationship,
            "dimensions": parsed.dimensions,
            "assessment": parsed.assessment,
        }

    async def _gather_evidence(
        self, paper_identity_id: str
    ) -> tuple[EvidenceBasis, str, list[str]]:
        """Evidence preference order: full text (evidence items) -> abstract ->
        indexed metadata -> title only. Returns (basis, text, artifact ids)."""
        identity = (await self._store.get(paper_identity_id)).parse_payload(PaperIdentity)
        evidence_ids: list[str] = []

        # 1. full-text evidence items already in the repository
        statements: list[str] = []
        for env in await self._store.list(artifact_type="evidence_item"):
            try:
                item = env.parse_payload(EvidenceItem)
                doc_env = await self._store.get(item.source_artifact_id)
                if doc_env.artifact_type != "full_text_document":
                    continue
                doc = doc_env.parse_payload(FullTextDocument)
            except Exception:  # noqa: BLE001
                continue
            if doc.paper_identity_id != paper_identity_id:
                continue
            statements.append(item.statement)
            evidence_ids.append(item.source_artifact_id)
        if statements:
            return (
                EvidenceBasis.full_text,
                "\n".join(dict.fromkeys(statements))[:8000],
                list(dict.fromkeys(evidence_ids)),
            )

        # 2. abstracts from member PaperRecords
        abstracts: list[str] = []
        member_dois: set[str] = set()
        for pid in identity.member_paper_artifact_ids:
            try:
                rec_env = await self._store.get(pid)
                if rec_env.artifact_type != "paper_record":
                    continue
                rec = rec_env.parse_payload(PaperRecord)
                if rec.abstract:
                    abstracts.append(rec.abstract)
                if rec.doi:
                    member_dois.add(normalize_doi(rec.doi))
                for ext in rec.external_identifiers:
                    if ext.scheme == "doi":
                        member_dois.add(normalize_doi(ext.value))
                evidence_ids.append(pid)
            except Exception:  # noqa: BLE001
                continue
        if abstracts:
            return (
                EvidenceBasis.abstract,
                "\n\n".join(dict.fromkeys(abstracts))[:8000],
                list(dict.fromkeys(evidence_ids)),
            )

        # 2b. enrichment-acquired abstracts (EvidenceItems imported by the
        # provider_get_abstract strategy, matched deterministically by DOI)
        for env in await self._store.list(artifact_type="evidence_item"):
            try:
                item = env.parse_payload(EvidenceItem)
                if not item.metadata.get("novelty_enrichment"):
                    continue
                rec_env = await self._store.get(item.source_artifact_id)
                if rec_env.artifact_type != "paper_record":
                    continue
                rec = rec_env.parse_payload(PaperRecord)
            except Exception:  # noqa: BLE001
                continue
            if rec.doi and normalize_doi(rec.doi) in member_dois:
                abstracts.append(item.statement)
                evidence_ids.append(item.source_artifact_id)
        if abstracts:
            return (
                EvidenceBasis.abstract,
                "\n\n".join(dict.fromkeys(abstracts))[:8000],
                list(dict.fromkeys(evidence_ids)),
            )

        # 3. indexed metadata (title + at least one of year/venue/doi)
        meta_lines: list[str] = []
        has_meta = False
        for pid in identity.member_paper_artifact_ids:
            try:
                rec_env = await self._store.get(pid)
                if rec_env.artifact_type != "paper_record":
                    continue
                rec = rec_env.parse_payload(PaperRecord)
                meta_lines.append(
                    f"title: {rec.title} | year: {rec.year or 'unavailable'} | "
                    f"venue: {rec.venue or 'unavailable'} | doi: {rec.doi or 'unavailable'}"
                )
                if rec.year or rec.venue or rec.doi:
                    has_meta = True
                evidence_ids.append(pid)
            except Exception:  # noqa: BLE001
                continue
        if meta_lines and has_meta:
            return (
                EvidenceBasis.indexed_metadata,
                "\n".join(meta_lines)[:4000],
                list(dict.fromkeys(evidence_ids)),
            )

        # 4. title only (can never justify a strong semantic judgment)
        return (
            EvidenceBasis.title_only,
            "\n".join(meta_lines)[:2000] or "no bibliographic metadata available",
            list(dict.fromkeys(evidence_ids)),
        )

    # ------------------------------------------------------------------
    # 6. Claim-level assessment (deterministic aggregation + coverage)
    # ------------------------------------------------------------------

    async def _derive_claim_status(
        self,
        claim: NoveltyClaim,
        coverage: NoveltyCoverage,
        candidate_assessments: list[NoveltyCandidateAssessment],
    ) -> tuple[NoveltyClaimStatus, str]:
        """Deterministic claim status from coverage + candidate relationships."""
        relationships = [a.relationship for a in candidate_assessments]
        if not coverage.coverage_sufficient:
            return NoveltyClaimStatus.unverified, "coverage insufficient"
        if any(r in _STRONG_RELATIONSHIPS for r in relationships):
            strong = [
                a.paper_identity_id
                for a in candidate_assessments
                if a.relationship in _STRONG_RELATIONSHIPS
            ]
            return (
                NoveltyClaimStatus.threatened,
                f"candidate(s) assessed as direct prior art / strong overlap: {strong}",
            )
        if CandidateRelationship.partial_overlap in relationships:
            partial = [
                a.paper_identity_id
                for a in candidate_assessments
                if a.relationship == CandidateRelationship.partial_overlap
            ]
            return (
                NoveltyClaimStatus.weakened,
                f"related prior work with partial overlap found: {partial}; "
                "the current wording may be too strong",
            )
        if CandidateRelationship.insufficient_evidence in relationships:
            return (
                NoveltyClaimStatus.unverified,
                "candidate(s) could not be assessed from available evidence "
                "(title-only or failed assessment)",
            )
        return (
            NoveltyClaimStatus.not_threatened_within_search_scope,
            "no candidate materially contradicts the claim within the declared "
            "search scope; this does not prove global novelty",
        )

    async def assess_claim(self, claim_id: str) -> str:
        claim = (await self._store.get(claim_id)).parse_payload(NoveltyClaim)

        plans = [
            (env.artifact_id, env.parse_payload(NoveltySearchPlan))
            for env in await self._store.list(artifact_type="novelty_search_plan")
            if env.payload.get("claim_id") == claim_id
        ]
        if not plans:
            raise ValueError(f"no search plan for claim {claim_id}")
        plan_id, plan = max(plans, key=lambda p: p[1].created_at)

        executions = [
            (env.artifact_id, env.parse_payload(NoveltySearchExecution))
            for env in await self._store.list(artifact_type="novelty_search_execution")
            if env.payload.get("claim_id") == claim_id
        ]
        if not executions:
            raise ValueError(f"no search execution for claim {claim_id}")
        exec_id, execution = max(executions, key=lambda e: e[1].created_at)

        csets = [
            (env.artifact_id, env.parse_payload(NoveltyCandidateSet))
            for env in await self._store.list(artifact_type="novelty_candidate_set")
            if env.payload.get("claim_id") == claim_id
        ]
        if not csets:
            raise ValueError(f"no candidate set for claim {claim_id}")
        cset_id, cset = max(csets, key=lambda c: c[1].created_at)

        assessments = [
            env.parse_payload(NoveltyCandidateAssessment)
            for env in await self._store.list(artifact_type="novelty_candidate_assessment")
            if env.payload.get("candidate_set_id") == cset_id
        ]

        # ---- coverage ---------------------------------------------------
        planned = execution.planned_searches
        date_limits: list[str] = []
        if execution.as_of_date is not None:
            date_limits.append(
                f"search restricted to {plan.year_from}-{plan.year_to} "
                f"(as_of {execution.as_of_date.isoformat()})"
            )
        unknown_year = sum(1 for c in cset.candidates if c.earliest_year is None)
        if unknown_year:
            date_limits.append(
                f"publication year unavailable for {unknown_year} candidate(s); "
                "treated conservatively"
            )
        with_evidence = sum(
            1
            for a in assessments
            if a.evidence_basis in (EvidenceBasis.full_text, EvidenceBasis.abstract)
        )
        coverage = NoveltyCoverage(
            planned_query_count=planned,
            executed_query_count=execution.executed_searches,
            successful_query_count=execution.successful_searches,
            provider_count=len(execution.counts.get("providers", [])),
            provider_failures=execution.provider_failures,
            candidate_count=len(cset.candidates),
            candidates_with_evidence_count=with_evidence,
            date_coverage_limitations=date_limits,
        )
        coverage_sufficient = True
        reasons: list[str] = []
        if self._require_all_searches_succeed and (
            execution.successful_searches < planned or execution.provider_failures
        ):
            coverage_sufficient = False
            reasons.append(
                f"{execution.successful_searches}/{planned} searches succeeded "
                f"({len(execution.provider_failures)} provider failures)"
            )
        if (
            self._require_candidate_evidence
            and claim.risk in (ClaimRiskLevel.critical, ClaimRiskLevel.high)
            and cset.candidates
            and with_evidence == 0
        ):
            coverage_sufficient = False
            reasons.append("all candidates lack abstract/full-text evidence")
        coverage.coverage_sufficient = coverage_sufficient

        # ---- deterministic status ---------------------------------------
        if not coverage_sufficient:
            coverage_reasons = "; ".join(reasons)
            status, reasoning = await self._derive_claim_status(claim, coverage, assessments)
            reasoning = f"coverage insufficient: {coverage_reasons}"
        else:
            status, reasoning = await self._derive_claim_status(claim, coverage, assessments)

        assessment = NoveltyClaimAssessment(
            claim_id=claim_id,
            manuscript_id=claim.manuscript_id,
            search_plan_id=plan_id,
            search_execution_id=exec_id,
            candidate_set_id=cset_id,
            candidate_assessment_ids=[a.id for a in assessments],
            critic_assessment_ids=[cid for a in assessments for cid in a.critic_assessment_ids],
            status=status,
            coverage=coverage,
            reasoning=reasoning,
        )
        assessment_id = await self._put(assessment, "novelty_claim_assessment")
        for target in (
            claim_id,
            plan_id,
            exec_id,
            cset_id,
            *[a.id for a in assessments],
        ):
            await self._link(target, assessment_id)
        return assessment_id

    # ------------------------------------------------------------------
    # 7. Revision recommendations (never modify the manuscript)
    # ------------------------------------------------------------------

    async def recommend_revision(self, claim_id: str, assessment_id: str) -> str | None:
        claim = (await self._store.get(claim_id)).parse_payload(NoveltyClaim)
        assessment = (await self._store.get(assessment_id)).parse_payload(NoveltyClaimAssessment)

        coverage_strong = (
            assessment.coverage.coverage_sufficient
            and assessment.coverage.candidate_count > 0
            and assessment.coverage.candidates_with_evidence_count
            == assessment.coverage.candidate_count
        )
        triggered = assessment.status in (
            NoveltyClaimStatus.threatened,
            NoveltyClaimStatus.weakened,
        ) or (
            claim.risk == ClaimRiskLevel.critical
            and assessment.status
            in (
                NoveltyClaimStatus.not_threatened_within_search_scope,
                NoveltyClaimStatus.unverified,
            )
            and not coverage_strong
        )
        if not triggered:
            return None

        supporting = [
            a.id
            for a in (
                env.parse_payload(NoveltyCandidateAssessment)
                for env in await self._store.list(artifact_type="novelty_candidate_assessment")
            )
            if a.id in assessment.candidate_assessment_ids
            and a.relationship in _STRONG_RELATIONSHIPS | {CandidateRelationship.partial_overlap}
        ]

        reason = f"claim status {assessment.status.value}: {assessment.reasoning}"
        if assessment.status == NoveltyClaimStatus.not_threatened_within_search_scope:
            reason = (
                "absolute-priority claim with no confirmed threat: conservative "
                "language recommended unless search coverage is unusually strong"
            )
        elif assessment.status == NoveltyClaimStatus.unverified:
            reason = (
                "critical claim could not be verified from available coverage: "
                "conservative language recommended"
            )
        scope_change: str | None = None
        wording: str | None = None
        try:
            data = await self._call_llm(
                self._extractor_role,
                "You recommend conservative rewording for a novelty claim.",
                (
                    f"Claim ({claim.claim_type.value}, risk {claim.risk.value}): "
                    f"{claim.claim_text}\n"
                    f"Reason: {reason}\n\n"
                    "Suggest a scope change and conservative rewording that preserves "
                    "the actual contribution without overstating novelty. Do not "
                    "replace the claim with vague language. Return "
                    "suggested_scope_change and suggested_wording."
                ),
                {
                    "type": "object",
                    "properties": {
                        "suggested_scope_change": {"type": "string"},
                        "suggested_wording": {"type": "string"},
                    },
                    "required": ["suggested_scope_change", "suggested_wording"],
                    "additionalProperties": False,
                },
                attempts=_MAX_VALIDATION_RETRIES,
            )
            parsed = _RecommendationResponse.model_validate(data)
            scope_change = parsed.suggested_scope_change or None
            wording = parsed.suggested_wording or None
        except Exception as e:  # noqa: BLE001
            logger.warning("revision recommendation LLM failed: %s", e)
            wording = detection.conservative_wording(claim)

        rec = NoveltyRevisionRecommendation(
            claim_id=claim_id,
            original_text=claim.claim_text,
            risk=claim.risk,
            reason=reason,
            supporting_candidate_ids=supporting,
            suggested_scope_change=scope_change,
            suggested_wording=wording,
            model_role=self._extractor_role,
        )
        rec_id = await self._put(rec, "novelty_revision_recommendation")
        await self._link(claim_id, rec_id)
        for sid in supporting:
            await self._link(sid, rec_id)
        return rec_id

    # ------------------------------------------------------------------
    # 7b. Phase 5C: explicit candidate reassessment after enrichment
    # ------------------------------------------------------------------

    async def enrich_candidate(self, candidate_assessment_id: str, *, offline: bool = False) -> str:
        """Enrich a sparse candidate assessment and reassess it. If evidence
        improves, a superseding candidate assessment is created and the
        claim assessment + report + gate are recomputed. Returns the current
        candidate assessment id (the new one when enrichment succeeded)."""
        old_env = await self._store.get(candidate_assessment_id)
        old = old_env.parse_payload(NoveltyCandidateAssessment)
        claim = (await self._store.get(old.claim_id)).parse_payload(NoveltyClaim)
        if not self._enrichment_enabled:
            return candidate_assessment_id
        before_basis, _t, _e = await self._gather_evidence(old.paper_identity_id)
        if before_basis not in (EvidenceBasis.title_only, EvidenceBasis.indexed_metadata):
            return candidate_assessment_id  # already sufficient

        exec_id = await self._enrich(
            old.claim_id,
            old.paper_identity_id,
            before_basis,
            offline=offline,
            candidate_assessment_id=old.id,
        )
        basis, evidence_text, evidence_ids = await self._gather_evidence(old.paper_identity_id)
        if basis == before_basis:
            await self._link(exec_id, old.id)
            return candidate_assessment_id  # no improvement -> keep old assessment

        # ---- superseding candidate assessment ----------------------------
        relationship = CandidateRelationship.insufficient_evidence
        dimensions: list[NoveltyDimensionScore] = []
        assessment_text = ""
        if offline:
            assessment_text = (
                "offline deterministic mode: no model-assisted semantic comparison was performed"
            )
        else:
            try:
                data = await self._assess_one(
                    claim,
                    NoveltyCandidateRef(paper_identity_id=old.paper_identity_id),
                    basis,
                    evidence_text,
                )
                relationship = CandidateRelationship(data["relationship"])
                dimensions = data["dimensions"]
                assessment_text = data["assessment"]
                if (
                    basis == EvidenceBasis.title_only
                    or basis == EvidenceBasis.indexed_metadata
                    and relationship
                    in (_STRONG_RELATIONSHIPS | {CandidateRelationship.partial_overlap})
                ):
                    relationship = CandidateRelationship.insufficient_evidence
            except Exception as e:  # noqa: BLE001
                logger.warning("candidate reassessment failed (%s): %s", old.paper_identity_id, e)
                relationship = CandidateRelationship.insufficient_evidence
                assessment_text = f"assessment failed: {e}"

        new_id = str(uuid.uuid4())
        new_assessment = old.model_copy(
            update={
                "id": new_id,
                "dimensions": dimensions,
                "relationship": relationship,
                "evidence_basis": basis,
                "evidence_artifact_ids": evidence_ids,
                "assessment_text": assessment_text,
                "critic_assessment_ids": [],
                "metadata": {**old.metadata, "enrichment_execution_id": exec_id},
            }
        )
        # critic pass for critical claims / strong relationships
        critic_ids: list[str] = []
        if not offline and (
            claim.risk == ClaimRiskLevel.critical or relationship in _STRONG_RELATIONSHIPS
        ):
            critic_ids = [
                await self._critic_pass(
                    claim, old.paper_identity_id, new_id, basis, evidence_text, new_assessment
                )
            ]
        if critic_ids:
            new_assessment = new_assessment.model_copy(update={"critic_assessment_ids": critic_ids})
        env = ArtifactEnvelope.create(
            payload=new_assessment,
            artifact_type="novelty_candidate_assessment",
            producer=_PRODUCER,
            artifact_id=new_id,
        )
        await self._store.put(env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.supersedes,
                source_artifact_id=old.id,
                target_artifact_id=new_id,
                producer=_PRODUCER,
            )
        )
        for target in (old.candidate_set_id, old.paper_identity_id, exec_id):
            await self._link(target, new_id)
        for eid in evidence_ids:
            try:
                await self._link(eid, new_id)
            except Exception:  # noqa: BLE001
                pass
        for cid in critic_ids:
            await self._link(new_id, cid)

        # ---- recompute the claim assessment ------------------------------
        new_claim_assessment_id = await self._refresh_claim_assessment(old.id, new_id)
        # ---- recompute report + gate if the claim assessment changed -----
        if new_claim_assessment_id is not None:
            await self._refresh_report_and_gate(new_claim_assessment_id)
        return new_id

    async def _refresh_claim_assessment(
        self, old_candidate_assessment_id: str, new_candidate_assessment_id: str
    ) -> str | None:
        """Supersede the claim assessment that referenced the old candidate
        assessment, recomputing coverage + status deterministically."""
        claim_assessments = [
            env
            for env in await self._store.list(artifact_type="novelty_claim_assessment")
            if old_candidate_assessment_id in env.payload.get("candidate_assessment_ids", [])
        ]
        if not claim_assessments:
            return None
        env = claim_assessments[0]
        old = env.parse_payload(NoveltyClaimAssessment)
        claim = (await self._store.get(old.claim_id)).parse_payload(NoveltyClaim)

        candidate_ids = [
            cid for cid in old.candidate_assessment_ids if cid != old_candidate_assessment_id
        ] + [new_candidate_assessment_id]
        assessments = [
            (await self._store.get(cid)).parse_payload(NoveltyCandidateAssessment)
            for cid in candidate_ids
        ]
        with_evidence = sum(
            1
            for a in assessments
            if a.evidence_basis in (EvidenceBasis.full_text, EvidenceBasis.abstract)
        )
        coverage_sufficient = (
            old.coverage.successful_query_count >= old.coverage.planned_query_count
            and not old.coverage.provider_failures
        ) and not (
            self._require_candidate_evidence
            and claim.risk in (ClaimRiskLevel.critical, ClaimRiskLevel.high)
            and old.coverage.candidate_count > 0
            and with_evidence == 0
        )
        coverage = old.coverage.model_copy(
            update={
                "candidates_with_evidence_count": with_evidence,
                "coverage_sufficient": coverage_sufficient,
            }
        )
        status, reasoning = await self._derive_claim_status(claim, coverage, assessments)

        new = old.model_copy(
            update={
                "candidate_assessment_ids": candidate_ids,
                "critic_assessment_ids": [
                    cid for a in assessments for cid in a.critic_assessment_ids
                ],
                "status": status,
                "coverage": coverage,
                "reasoning": reasoning,
            }
        )
        new_id = await self._put(new, "novelty_claim_assessment")
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.supersedes,
                source_artifact_id=env.artifact_id,
                target_artifact_id=new_id,
                producer=_PRODUCER,
            )
        )
        for target in (
            old.claim_id,
            old.search_plan_id,
            old.search_execution_id,
            old.candidate_set_id,
            new_candidate_assessment_id,
        ):
            await self._link(target, new_id)
        return new_id

    async def _refresh_report_and_gate(self, new_claim_assessment_id: str) -> None:
        """Supersede the latest report that referenced the superseded claim
        assessment and produce a fresh gate for its package."""
        # the old claim assessment is the source of the supersedes edge
        old_claim_assessment_id: str | None = None
        for link in await self._store.get_parents(new_claim_assessment_id):
            if link.relation.value == "supersedes":
                old_claim_assessment_id = link.source_artifact_id
                break
        if old_claim_assessment_id is None:
            return
        reports = [
            env
            for env in await self._store.list(artifact_type="novelty_validation_report")
            if old_claim_assessment_id in env.payload.get("claim_assessment_ids", [])
        ]
        if not reports:
            return
        # newest report among those (supersedes-aware leaf)
        parsed = [(env.artifact_id, env.parse_payload(NoveltyValidationReport)) for env in reports]
        superseded_ids = {r.supersedes for _, r in parsed if r.supersedes}
        report_id, old_report = max(
            ((aid, r) for aid, r in parsed if aid not in superseded_ids),
            key=lambda t: t[1].created_at,
        )
        claim_assessment_ids = [
            new_claim_assessment_id if aid == old_claim_assessment_id else aid
            for aid in old_report.claim_assessment_ids
        ]
        status_by_claim: dict[str, NoveltyClaimStatus] = {}
        for aid in claim_assessment_ids:
            try:
                a = (await self._store.get(aid)).parse_payload(NoveltyClaimAssessment)
                status_by_claim[a.claim_id] = a.status
            except Exception:  # noqa: BLE001
                continue
        claims = {
            env.artifact_id: env.parse_payload(NoveltyClaim)
            for env in await self._store.list(artifact_type="novelty_claim")
            if env.artifact_id in old_report.claim_ids
        }
        critical, weakened, unverified, safe, overall = self._aggregate_report_status(
            old_report.claim_ids, status_by_claim, claims
        )
        new_report = old_report.model_copy(
            update={
                "claim_assessment_ids": claim_assessment_ids,
                "critical_threats": critical,
                "weakened_claims": weakened,
                "unverified_claims": unverified,
                "safe_within_scope_claims": safe,
                "overall_status": overall,
                "supersedes": report_id,
                "metadata": {
                    **old_report.metadata,
                    "recomputed_after_enrichment": True,
                },
            }
        )
        new_report_id = await self._put(new_report, "novelty_validation_report")
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.supersedes,
                source_artifact_id=report_id,
                target_artifact_id=new_report_id,
                producer=_PRODUCER,
            )
        )
        for target in (
            old_report.submission_package_id,
            old_report.manuscript_id,
            new_claim_assessment_id,
        ):
            await self._link(target, new_report_id)
        # fresh gate for the same package (the new report is current)
        await self.create_gate(old_report.submission_package_id, new_report_id)

    # ------------------------------------------------------------------
    # 9. Phase 5B: incremental revalidation + staleness
    # ------------------------------------------------------------------

    def _aggregate_report_status(
        self,
        claim_ids: list[str],
        status_by_claim: dict[str, NoveltyClaimStatus],
        claims: dict[str, NoveltyClaim],
    ) -> tuple[list[str], list[str], list[str], list[str], NoveltyReportStatus]:
        critical_threats = [
            cid
            for cid in claim_ids
            if claims.get(cid) is not None
            and claims[cid].risk == ClaimRiskLevel.critical
            and status_by_claim.get(cid) == NoveltyClaimStatus.threatened
        ]
        weakened = [
            cid for cid in claim_ids if status_by_claim.get(cid) == NoveltyClaimStatus.weakened
        ]
        unverified = [
            cid for cid in claim_ids if status_by_claim.get(cid) == NoveltyClaimStatus.unverified
        ]
        safe = [
            cid
            for cid in claim_ids
            if status_by_claim.get(cid) == NoveltyClaimStatus.not_threatened_within_search_scope
        ]
        if critical_threats:
            overall = NoveltyReportStatus.blocked
        elif any(
            s in (NoveltyClaimStatus.threatened, NoveltyClaimStatus.weakened)
            for s in status_by_claim.values()
        ):
            overall = NoveltyReportStatus.revise
        elif unverified:
            overall = NoveltyReportStatus.unverified
        else:
            overall = NoveltyReportStatus.clear
        return critical_threats, weakened, unverified, safe, overall

    def _diff_sections(
        self, old: FormattedManuscript, new: FormattedManuscript
    ) -> list[ManuscriptSectionChange]:
        old_map = {s.section_id: s for s in old.sections}
        new_map = {s.section_id: s for s in new.sections}
        changes: list[ManuscriptSectionChange] = []
        for sid in sorted(set(old_map) | set(new_map)):
            os_ = old_map.get(sid)
            ns_ = new_map.get(sid)
            old_hash = hashlib.sha256(os_.body.encode()).hexdigest() if os_ else None
            new_hash = hashlib.sha256(ns_.body.encode()).hexdigest() if ns_ else None
            if os_ is None and ns_ is not None:
                change_type = ManuscriptSectionChangeType.added
            elif ns_ is None and os_ is not None:
                change_type = ManuscriptSectionChangeType.removed
            elif (
                os_ is not None
                and ns_ is not None
                and os_.body == ns_.body
                and os_.title == ns_.title
            ):
                change_type = ManuscriptSectionChangeType.unchanged
            else:
                change_type = ManuscriptSectionChangeType.changed
            changes.append(
                ManuscriptSectionChange(
                    section_id=sid,
                    change_type=change_type,
                    old_body_hash=old_hash,
                    new_body_hash=new_hash,
                )
            )
        return changes

    @staticmethod
    def _token_jaccard(a: str, b: str) -> float:
        ta = set(re.findall(r"[a-z0-9]+", a.lower()))
        tb = set(re.findall(r"[a-z0-9]+", b.lower()))
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)

    @staticmethod
    def _material_difference(old: NoveltyClaim, new: NoveltyClaim) -> bool:
        return (
            old.claim_type != new.claim_type
            or old.risk != new.risk
            or (old.scope or None) != (new.scope or None)
        )

    def _best_similar_old(
        self, new_claim: NoveltyClaim, old_claims: list[NoveltyClaim]
    ) -> NoveltyClaim | None:
        best: NoveltyClaim | None = None
        best_score = 0.0
        for old in old_claims:
            if old.section_id != new_claim.section_id:
                continue
            score = self._token_jaccard(new_claim.claim_text, old.claim_text)
            if score > best_score:
                best, best_score = old, score
        return best if best_score >= 0.5 else None

    async def _materially_changed(self, new_claim: NoveltyClaim, old_claim: NoveltyClaim) -> bool:
        """Bounded structured model comparison for the ambiguous similarity
        band only; conservative default (changed) on any failure."""
        try:
            data = await self._call_llm(
                self._extractor_role,
                "You compare two novelty claim statements for semantic change.",
                (
                    f"Old manuscript claim: {old_claim.claim_text}\n"
                    f"New manuscript claim: {new_claim.claim_text}\n\n"
                    "Has the novelty claim changed materially (wording, scope, "
                    "mechanism, setting, claimed result, or priority language)? "
                    "Return materially_changed (boolean). Minor rewording that "
                    "preserves meaning is NOT a material change."
                ),
                {
                    "type": "object",
                    "properties": {"materially_changed": {"type": "boolean"}},
                    "required": ["materially_changed"],
                    "additionalProperties": False,
                },
                attempts=0,
            )
            return bool(data.get("materially_changed", True))
        except Exception as e:  # noqa: BLE001
            logger.warning("ambiguous claim comparison failed: %s", e)
            return True

    async def _policy_compatible(
        self, old_assessment: NoveltyClaimAssessment, as_of_date: date
    ) -> bool:
        """Reuse requires: same date cutoff/as_of, same providers, same
        max-results, same search window."""
        try:
            plan = (await self._store.get(old_assessment.search_plan_id)).parse_payload(
                NoveltySearchPlan
            )
        except Exception:  # noqa: BLE001
            return False
        return (
            plan.date_cutoff == as_of_date
            and set(plan.providers) == set(self._providers)
            and plan.maximum_results == self._max_results_per_query
            and (plan.year_to - plan.year_from) == self._search_year_window
        )

    async def _latest_manuscript_in_lineage(self, draft_id: str) -> Any | None:
        """The newest FormattedManuscript envelope in the draft's supersedes
        lineage (leaf draft -> leaf of the formatted-manuscript supersedes
        chain, else newest by created_at)."""
        current_draft_id = draft_id
        while True:
            newer = [
                env
                for env in await self._store.list(artifact_type="manuscript_draft")
                if env.payload.get("supersedes") == current_draft_id
            ]
            if not newer:
                break
            current_draft_id = newer[0].artifact_id
        candidates = {
            env.artifact_id: env
            for env in await self._store.list(artifact_type="formatted_manuscript")
            if env.payload.get("draft_id") == current_draft_id
        }
        if not candidates:
            return None
        cand_ids = set(candidates)
        superseded = {
            env.artifact_id
            for env in candidates.values()
            for child in await self._store.get_children(env.artifact_id)
            if child.relation.value == "supersedes" and child.target_artifact_id in cand_ids
        }
        leaves = [aid for aid in candidates if aid not in superseded]
        return max(
            (candidates[aid] for aid in leaves),
            key=lambda e: str(e.payload.get("created_at", "")),
        )

    async def staleness(self, report_or_gate_id: str) -> StalenessStatus:
        """A report/gate is stale when its manuscript content hash no longer
        matches the current manuscript/package lineage."""
        env = await self._store.get(report_or_gate_id)
        if env.artifact_type == "submission_readiness_gate":
            gate = env.parse_payload(SubmissionReadinessGate)
            report = (await self._store.get(gate.novelty_report_id)).parse_payload(
                NoveltyValidationReport
            )
        else:
            report = env.parse_payload(NoveltyValidationReport)
        latest = await self._latest_manuscript_in_lineage(report.draft_id)
        if latest is None:
            return StalenessStatus.current
        if latest.content_hash != report.manuscript_content_hash:
            return StalenessStatus.stale
        return StalenessStatus.current

    async def revalidate(
        self,
        previous_report_id: str,
        new_package_id: str,
        *,
        offline: bool = False,
        as_of: str | None = None,
        force_all: bool = False,
        max_results: int | None = None,
    ) -> str:
        """Incremental revalidation after a manuscript supersession.

        Detects novelty-relevant changes, reuses unaffected claim
        assessments, revalidates affected claims through the Phase 5A
        pipeline, and produces a NEW report + gate. Returns the new report id.
        """
        started = datetime.now(UTC)
        old_report = (await self._store.get(previous_report_id)).parse_payload(
            NoveltyValidationReport
        )
        self._preacq_total_used = 0
        old_manuscript_env = await self._store.get(old_report.manuscript_id)
        old_manuscript = old_manuscript_env.parse_payload(FormattedManuscript)
        new_pkg_env = await self._store.get(new_package_id)
        new_package = new_pkg_env.parse_payload(SubmissionPackage)
        new_manuscript_env = await self._store.get(new_package.formatted_manuscript_id)
        new_manuscript = new_manuscript_env.parse_payload(FormattedManuscript)
        as_of_date = self._parse_as_of(as_of)
        if max_results is not None:
            self._max_results_per_query = max(1, min(int(max_results), 50))

        # ---- previous claims + assessments --------------------------------
        old_claims: dict[str, NoveltyClaim] = {}
        old_claims_env: dict[str, str] = {}  # payload id -> envelope artifact id
        for cid in old_report.claim_ids:
            try:
                c = (await self._store.get(cid)).parse_payload(NoveltyClaim)
                old_claims[c.id] = c  # keyed by payload id (matches equivalent_claim_id)
                old_claims_env[c.id] = cid
            except Exception:  # noqa: BLE001
                continue
        old_assessments: dict[str, tuple[str, NoveltyClaimAssessment]] = {}
        for aid in old_report.claim_assessment_ids:
            try:
                a = (await self._store.get(aid)).parse_payload(NoveltyClaimAssessment)
                old_assessments[a.claim_id] = (aid, a)
            except Exception:  # noqa: BLE001
                continue

        # ---- 1. change detection (deterministic) --------------------------
        section_changes = self._diff_sections(old_manuscript, new_manuscript)

        new_claim_ids = await self.extract_claims(
            new_package.formatted_manuscript_id,
            offline=offline,
            previous_claims=list(old_claims.values()),
        )
        new_claims = {
            cid: (await self._store.get(cid)).parse_payload(NoveltyClaim) for cid in new_claim_ids
        }

        added: list[str] = []
        modified: list[str] = []
        unchanged: list[str] = []
        identity_map: dict[str, str] = {}
        for cid, claim in new_claims.items():
            eq = claim.equivalent_claim_id
            if eq is not None and eq in old_claims:
                identity_map[cid] = eq
                if self._material_difference(old_claims[eq], claim):
                    modified.append(cid)
                else:
                    unchanged.append(cid)
                continue
            similar = self._best_similar_old(claim, list(old_claims.values()))
            if similar is not None and not self._material_difference(similar, claim):
                changed = True
                if not offline:
                    changed = await self._materially_changed(claim, similar)
                if changed:
                    modified.append(cid)
                else:
                    identity_map[cid] = similar.id
                    unchanged.append(cid)
            else:
                added.append(cid)
        removed = [cid for cid in old_claims if cid not in set(identity_map.values())]

        # ---- 2. reuse policy (deterministic) ------------------------------
        reusable: dict[str, str] = {}
        revalidation_reasons: dict[str, str] = {}
        affected: list[str] = []
        for cid in unchanged:
            eq = identity_map[cid]
            if force_all:
                affected.append(cid)
                revalidation_reasons[cid] = "force_all"
                continue
            entry = old_assessments.get(old_claims_env.get(eq, ""))
            if entry is None:
                affected.append(cid)
                revalidation_reasons[cid] = "no previous assessment"
                continue
            old_aid, old_a = entry
            if old_a.status == NoveltyClaimStatus.unverified:
                affected.append(cid)
                revalidation_reasons[cid] = "previous assessment unverified"
                continue
            if not await self._policy_compatible(old_a, as_of_date):
                affected.append(cid)
                revalidation_reasons[cid] = (
                    "search policy changed (as_of/providers/max-results/window)"
                )
                continue
            reusable[old_aid] = (
                "unchanged claim; previous assessment complete and search policy compatible"
            )
        for cid in modified:
            affected.append(cid)
            revalidation_reasons[cid] = "modified claim (wording/type/risk/scope)"
        for cid in added:
            affected.append(cid)
            revalidation_reasons[cid] = "new claim"

        # ---- 3. persist change set + plan (before any search) -------------
        change_set = ManuscriptChangeSet(
            old_manuscript_id=old_report.manuscript_id,
            new_manuscript_id=new_package.formatted_manuscript_id,
            old_content_hash=old_manuscript_env.content_hash,
            new_content_hash=new_manuscript_env.content_hash,
            changed_sections=section_changes,
            added_claim_ids=added,
            removed_claim_ids=removed,
            modified_claim_ids=modified,
            unchanged_claim_ids=unchanged,
            claim_identity_map=identity_map,
        )
        change_set_id = await self._put(change_set, "manuscript_change_set")
        change_targets = list(
            dict.fromkeys([old_report.manuscript_id, new_package.formatted_manuscript_id])
        )
        for target in change_targets:
            await self._link(target, change_set_id)

        plan = NoveltyRevalidationPlan(
            previous_report_id=previous_report_id,
            manuscript_change_set_id=change_set_id,
            new_manuscript_id=new_package.formatted_manuscript_id,
            affected_claim_ids=affected,
            reusable_claim_assessment_ids=list(reusable),
            reuse_reasons=reusable,
            revalidation_reasons=revalidation_reasons,
        )
        plan_id = await self._put(plan, "novelty_revalidation_plan")
        await self._link(change_set_id, plan_id)
        await self._link(previous_report_id, plan_id)

        # ---- 4. revalidate affected claims (Phase 5A pipeline) ------------
        failures: list[dict[str, Any]] = []
        new_assessment_ids: list[str] = []
        search_execution_ids: list[str] = []
        candidate_set_ids: list[str] = []
        candidate_assessment_ids: list[str] = []
        critic_assessment_ids: list[str] = []
        recommendation_ids: list[str] = []
        for claim_id in affected:
            try:
                plan_id_ = await self.plan_searches(claim_id, as_of=as_of_date, offline=offline)
                exec_id = await self.execute_searches(plan_id_)
                cset_id = await self.build_candidate_set(claim_id, plan_id_, exec_id)
                if self._preacquisition_enabled:
                    try:
                        claim = (await self._store.get(claim_id)).parse_payload(NoveltyClaim)
                        if claim.risk.value in self._preacq_risk_levels:
                            await self.preacquire_evidence(claim_id, cset_id, offline=offline)
                    except Exception as e:  # noqa: BLE001
                        failures.append({"claim_id": claim_id, "error": f"preacquisition: {e}"})
                cand_ids = await self.assess_candidates(claim_id, cset_id, offline=offline)
                assessment_id = await self.assess_claim(claim_id)
                rec_id = await self.recommend_revision(claim_id, assessment_id)

                new_assessment_ids.append(assessment_id)
                search_execution_ids.append(exec_id)
                candidate_set_ids.append(cset_id)
                candidate_assessment_ids.extend(cand_ids)
                for aid in cand_ids:
                    a = (await self._store.get(aid)).parse_payload(NoveltyCandidateAssessment)
                    critic_assessment_ids.extend(a.critic_assessment_ids)
                if rec_id:
                    recommendation_ids.append(rec_id)
            except Exception as e:  # noqa: BLE001
                failures.append({"claim_id": claim_id, "error": str(e)})
                logger.warning("revalidation failed for claim %s: %s", claim_id, e)

        # ---- 5. new report (reused + new assessments) ---------------------
        reused_ids = list(reusable)
        status_by_claim: dict[str, NoveltyClaimStatus] = {}
        for aid in reused_ids + new_assessment_ids:
            a = (await self._store.get(aid)).parse_payload(NoveltyClaimAssessment)
            status_by_claim[a.claim_id] = a.status
        claims = {
            env.artifact_id: env.parse_payload(NoveltyClaim)
            for env in await self._store.list(artifact_type="novelty_claim")
            if env.artifact_id in new_claim_ids
        }
        critical_threats, weakened, unverified, safe, overall = self._aggregate_report_status(
            new_claim_ids, status_by_claim, claims
        )

        report = NoveltyValidationReport(
            submission_package_id=new_package_id,
            manuscript_id=new_package.formatted_manuscript_id,
            draft_id=new_package.draft_id,
            manuscript_content_hash=new_manuscript_env.content_hash,
            as_of_date=as_of_date,
            claim_ids=new_claim_ids,
            claim_assessment_ids=reused_ids + new_assessment_ids,
            search_execution_ids=search_execution_ids,
            candidate_set_ids=candidate_set_ids,
            candidate_assessment_ids=candidate_assessment_ids,
            critic_assessment_ids=critic_assessment_ids,
            revision_recommendation_ids=recommendation_ids,
            critical_threats=critical_threats,
            weakened_claims=weakened,
            unverified_claims=unverified,
            safe_within_scope_claims=safe,
            coverage_summary={
                "claims_assessed": len(new_claim_ids),
                "claims_reused": len(reused_ids),
                "claims_revalidated": len(new_assessment_ids),
                "claims_failed": len(failures),
                "searches_executed": len(search_execution_ids),
                "candidates_assessed": len(candidate_assessment_ids),
                "critic_passes": len(critic_assessment_ids),
                "revision_recommendations": len(recommendation_ids),
                "providers": list(self._providers),
                "revalidation": True,
            },
            overall_status=overall,
            aggregation_policy=NoveltyValidationReport.model_fields["aggregation_policy"].default,
            supersedes=previous_report_id,
            model_role=self._extractor_role,
        )
        report_id = await self._put(report, "novelty_validation_report")
        for target in (
            new_package_id,
            new_package.formatted_manuscript_id,
            *new_claim_ids,
            *reused_ids,
            *new_assessment_ids,
            *recommendation_ids,
        ):
            await self._link(target, report_id)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.supersedes,
                source_artifact_id=previous_report_id,
                target_artifact_id=report_id,
                producer=_PRODUCER,
            )
        )

        # ---- 6. new gate + execution --------------------------------------
        gate_id = await self.create_gate(new_package_id, report_id)
        execution = NoveltyRevalidationExecution(
            plan_id=plan_id,
            previous_report_id=previous_report_id,
            reused_assessment_ids=reused_ids,
            newly_validated_claim_ids=affected,
            newly_assessment_ids=new_assessment_ids,
            resulting_report_id=report_id,
            resulting_gate_id=gate_id,
            failures=failures,
            counts={
                "claims_unchanged": len(unchanged),
                "claims_modified": len(modified),
                "claims_added": len(added),
                "claims_removed": len(removed),
                "assessments_reused": len(reused_ids),
                "assessments_new": len(new_assessment_ids),
                "overall_status": overall.value,
            },
            started_at=started,
            completed_at=datetime.now(UTC),
        )
        exec_id = await self._put(execution, "novelty_revalidation_execution")
        await self._link(plan_id, exec_id)
        await self._link(report_id, exec_id)
        return report_id

    # ------------------------------------------------------------------
    # 8. Report (full validation)
    # ------------------------------------------------------------------

    async def create_report(
        self,
        submission_package_id: str,
        *,
        as_of: str | None = None,
        offline: bool = False,
        max_results: int | None = None,
        max_claims: int | None = None,
    ) -> str:
        started = datetime.now(UTC)
        pkg_env = await self._store.get(submission_package_id)
        package = pkg_env.parse_payload(SubmissionPackage)
        m_env = await self._store.get(package.formatted_manuscript_id)
        as_of_date = self._parse_as_of(as_of)
        self._preacq_total_used = 0
        if max_results is not None:
            self._max_results_per_query = max(1, min(int(max_results), 50))

        claim_ids = await self.extract_claims(package.formatted_manuscript_id, offline=offline)
        claim_ids = await self._order_claims_by_risk(claim_ids)
        if max_claims is not None and max_claims > 0:
            claim_ids = claim_ids[: int(max_claims)]

        failures: list[dict[str, Any]] = []
        claim_assessment_ids: list[str] = []
        search_execution_ids: list[str] = []
        candidate_set_ids: list[str] = []
        candidate_assessment_ids: list[str] = []
        critic_assessment_ids: list[str] = []
        recommendation_ids: list[str] = []

        for claim_id in claim_ids:
            try:
                plan_id = await self.plan_searches(claim_id, as_of=as_of_date, offline=offline)
                exec_id = await self.execute_searches(plan_id)
                cset_id = await self.build_candidate_set(claim_id, plan_id, exec_id)
                if self._preacquisition_enabled:
                    try:
                        claim = (await self._store.get(claim_id)).parse_payload(NoveltyClaim)
                        if claim.risk.value in self._preacq_risk_levels:
                            await self.preacquire_evidence(claim_id, cset_id, offline=offline)
                    except Exception as e:  # noqa: BLE001
                        failures.append({"claim_id": claim_id, "error": f"preacquisition: {e}"})
                cand_ids = await self.assess_candidates(claim_id, cset_id, offline=offline)
                assessment_id = await self.assess_claim(claim_id)
                rec_id = await self.recommend_revision(claim_id, assessment_id)

                claim_assessment_ids.append(assessment_id)
                search_execution_ids.append(exec_id)
                candidate_set_ids.append(cset_id)
                candidate_assessment_ids.extend(cand_ids)
                for aid in cand_ids:
                    a = (await self._store.get(aid)).parse_payload(NoveltyCandidateAssessment)
                    critic_assessment_ids.extend(a.critic_assessment_ids)
                if rec_id:
                    recommendation_ids.append(rec_id)
            except Exception as e:  # noqa: BLE001
                failures.append({"claim_id": claim_id, "error": str(e)})
                logger.warning("novelty validation failed for claim %s: %s", claim_id, e)

        # ---- deterministic aggregation -----------------------------------
        status_by_claim: dict[str, NoveltyClaimStatus] = {}
        for aid in claim_assessment_ids:
            a = (await self._store.get(aid)).parse_payload(NoveltyClaimAssessment)
            status_by_claim[a.claim_id] = a.status

        claims = {
            env.artifact_id: env.parse_payload(NoveltyClaim)
            for env in await self._store.list(artifact_type="novelty_claim")
            if env.artifact_id in claim_ids
        }
        critical_threats, weakened_claims, unverified_claims, safe_claims, overall = (
            self._aggregate_report_status(claim_ids, status_by_claim, claims)
        )

        supersedes: str | None = None
        prev = await self.latest_report(submission_package_id)
        if prev is not None:
            supersedes = prev

        report = NoveltyValidationReport(
            submission_package_id=submission_package_id,
            manuscript_id=package.formatted_manuscript_id,
            draft_id=package.draft_id,
            manuscript_content_hash=m_env.content_hash,
            as_of_date=as_of_date,
            claim_ids=claim_ids,
            claim_assessment_ids=claim_assessment_ids,
            search_execution_ids=search_execution_ids,
            candidate_set_ids=candidate_set_ids,
            candidate_assessment_ids=candidate_assessment_ids,
            critic_assessment_ids=critic_assessment_ids,
            revision_recommendation_ids=recommendation_ids,
            critical_threats=critical_threats,
            weakened_claims=weakened_claims,
            unverified_claims=unverified_claims,
            safe_within_scope_claims=safe_claims,
            coverage_summary={
                "claims_assessed": len(claim_ids),
                "claims_with_assessments": len(claim_assessment_ids),
                "claims_failed": len(failures),
                "searches_executed": len(search_execution_ids),
                "candidates_assessed": len(candidate_assessment_ids),
                "critic_passes": len(critic_assessment_ids),
                "revision_recommendations": len(recommendation_ids),
                "providers": list(self._providers),
            },
            overall_status=overall,
            aggregation_policy=NoveltyValidationReport.model_fields["aggregation_policy"].default,
            supersedes=supersedes,
            model_role=self._extractor_role,
        )
        report_id = await self._put(report, "novelty_validation_report")
        for target in (
            submission_package_id,
            package.formatted_manuscript_id,
            *claim_ids,
            *claim_assessment_ids,
            *recommendation_ids,
        ):
            await self._link(target, report_id)
        if supersedes is not None:
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.supersedes,
                    source_artifact_id=supersedes,
                    target_artifact_id=report_id,
                    producer=_PRODUCER,
                )
            )

        execution = NoveltyValidationExecution(
            submission_package_id=submission_package_id,
            report_id=report_id,
            claims_extracted=len(claim_ids),
            search_executions=len(search_execution_ids),
            candidate_assessments=len(candidate_assessment_ids),
            critic_assessments=len(critic_assessment_ids),
            revision_recommendations=len(recommendation_ids),
            unverified_claims=len(unverified_claims),
            blocked_claims=len(critical_threats),
            model_role=self._extractor_role,
            failures=failures,
            counts={
                "overall_status": overall.value,
                "offline": offline,
                "as_of_date": as_of_date.isoformat(),
            },
            started_at=started,
            completed_at=datetime.now(UTC),
        )
        exec_id = await self._put(execution, "novelty_validation_execution")
        await self._link(report_id, exec_id)
        return report_id

    async def create_gate(self, submission_package_id: str, report_id: str | None = None) -> str:
        report_id = report_id or await self.latest_report(submission_package_id)
        if report_id is None:
            raise ValueError(
                f"no NoveltyValidationReport for package {submission_package_id}; "
                "run novelty validation first"
            )
        report = (await self._store.get(report_id)).parse_payload(NoveltyValidationReport)
        package = (await self._store.get(submission_package_id)).parse_payload(SubmissionPackage)
        if package.formatted_manuscript_id != report.manuscript_id:
            raise ValueError(
                "report does not cover this package's manuscript "
                "(manuscript changed since validation)"
            )
        if await self.staleness(report_id) == StalenessStatus.stale:
            raise ValueError(
                "report is stale: the assessed manuscript is no longer the "
                "current manuscript; revalidate first"
            )

        blocking = list(report.critical_threats)
        revision = list(report.weakened_claims)
        unverified = list(report.unverified_claims)

        if package.status != SubmissionPackageStatus.ready:
            status = ReadinessStatus.blocked
        elif report.overall_status == NoveltyReportStatus.clear:
            status = ReadinessStatus.ready
        elif report.overall_status == NoveltyReportStatus.revise:
            status = ReadinessStatus.needs_revision
        elif report.overall_status == NoveltyReportStatus.blocked:
            status = ReadinessStatus.blocked
        else:
            status = ReadinessStatus.unverified

        gate = SubmissionReadinessGate(
            submission_package_id=submission_package_id,
            novelty_report_id=report_id,
            manuscript_id=report.manuscript_id,
            draft_id=report.draft_id,
            package_status=package.status.value,
            novelty_status=report.overall_status,
            status=status,
            blocking_claim_ids=blocking,
            revision_claim_ids=revision,
            unverified_claim_ids=unverified,
            decision_policy=SubmissionReadinessGate.model_fields["decision_policy"].default,
        )
        gate_id = await self._put(gate, "submission_readiness_gate")
        await self._link(submission_package_id, gate_id)
        await self._link(report_id, gate_id)
        return gate_id

    async def latest_report(self, submission_package_id: str) -> str | None:
        reports = [
            (env.artifact_id, env.parse_payload(NoveltyValidationReport))
            for env in await self._store.list(artifact_type="novelty_validation_report")
            if env.payload.get("submission_package_id") == submission_package_id
        ]
        if not reports:
            return None
        superseded = {r.supersedes for _, r in reports if r.supersedes}
        leaves = [aid for aid, _r in reports if aid not in superseded]
        return max(
            leaves,
            key=lambda aid: next(r.created_at for i, r in reports if i == aid),
        )

    async def latest_gate(self, submission_package_id: str) -> str | None:
        gates = [
            (env.artifact_id, env.parse_payload(SubmissionReadinessGate))
            for env in await self._store.list(artifact_type="submission_readiness_gate")
            if env.payload.get("submission_package_id") == submission_package_id
        ]
        if not gates:
            return None
        return max(gates, key=lambda g: g[1].created_at)[0]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_as_of(self, as_of: str | None) -> date:
        if as_of is None:
            return date.today()
        try:
            return date.fromisoformat(as_of)
        except ValueError as e:
            raise ValueError(f"invalid --as-of date {as_of!r}; use YYYY-MM-DD") from e

    async def _order_claims_by_risk(self, claim_ids: list[str]) -> list[str]:
        risk_order = {
            ClaimRiskLevel.critical: 0,
            ClaimRiskLevel.high: 1,
            ClaimRiskLevel.medium: 2,
            ClaimRiskLevel.low: 3,
        }
        claims: list[tuple[int, str]] = []
        for cid in claim_ids:
            c = (await self._store.get(cid)).parse_payload(NoveltyClaim)
            claims.append((risk_order.get(c.risk, 9), cid))
        claims.sort(key=lambda t: t[0])
        return [cid for _rank, cid in claims]


class NoveltyValidatorPlugin(Plugin):
    def __init__(
        self,
        extractor_role: str | None = None,
        critic_role: str | None = None,
    ) -> None:
        self._extractor_role_override = extractor_role
        self._critic_role_override = critic_role
        self._service: NoveltyValidationService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.novelty_validator",
            version="0.1.0",
            plugin_type="research",
            description=(
                "External novelty validation and submission-readiness gate (Phase 5A/5B/5C)"
            ),
            provides=["novelty_validator.default"],
            requires=[
                "model_router.default",
                "artifact_store.default",
                "literature_ingestor.default",
                "paper_identity_resolver.default",
            ],
            optional_requires=[
                "blob_store.default",
                "document_locator.metadata",
                "document_locator.unpaywall",
                "document_fetcher.default",
                "document_extractor.pypdf",
                "evidence_extractor.default",
            ],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        research_cfg: dict[str, Any] = {}
        if "research" in cfg and isinstance(cfg["research"], dict):
            research_cfg = (
                cfg["research"].get("novelty", {})
                if isinstance(cfg["research"].get("novelty"), dict)
                else {}
            )
        extractor_role = (
            self._extractor_role_override or research_cfg.get("extractor_role") or "reasoning"
        )
        critic_role = self._critic_role_override or research_cfg.get("critic_role") or "critic"
        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        ingestor = ctx.require("literature_ingestor.default")
        resolver = ctx.require("paper_identity_resolver.default")
        blobs = ctx.try_get("blob_store.default")
        enrichment_cfg: dict[str, Any] = {}
        if isinstance(research_cfg.get("evidence_enrichment"), dict):
            enrichment_cfg = research_cfg["evidence_enrichment"]
        preacq_cfg: dict[str, Any] = {}
        if isinstance(research_cfg.get("evidence_preacquisition"), dict):
            preacq_cfg = research_cfg["evidence_preacquisition"]

        def lookup(name: str):  # type: ignore[no-untyped-def]
            return ctx.require(name)

        self._service = NoveltyValidationService(
            model_router=router,
            artifact_store=store,
            ingestor=ingestor,
            identity_resolver=resolver,
            service_lookup=lookup,
            blob_store=blobs,
            extractor_role=str(extractor_role),
            critic_role=str(critic_role),
            max_llm_calls=int(research_cfg.get("max_llm_calls", 40)),
            max_queries_per_claim=int(research_cfg.get("max_queries_per_claim", 12)),
            queries_per_risk=research_cfg.get("queries_per_risk"),
            max_results_per_query=int(research_cfg.get("max_results_per_query", 10)),
            providers=research_cfg.get("providers"),
            search_year_window=int(research_cfg.get("search_year_window", 50)),
            require_all_searches_succeed=bool(
                research_cfg.get("require_all_searches_succeed", True)
            ),
            require_candidate_evidence=bool(research_cfg.get("require_candidate_evidence", True)),
            enrichment_enabled=bool(enrichment_cfg.get("enabled", True)),
            acquire_abstract=bool(enrichment_cfg.get("acquire_abstract", True)),
            acquire_full_text=bool(enrichment_cfg.get("acquire_full_text", True)),
            max_enrichment_attempts=int(enrichment_cfg.get("max_attempts_per_candidate", 3)),
            abstract_providers=enrichment_cfg.get("abstract_providers"),
            preacquisition_enabled=bool(preacq_cfg.get("enabled", False)),
            preacquisition_risk_levels=preacq_cfg.get("risk_levels"),
            preacquisition_max_per_claim=int(preacq_cfg.get("max_candidates_per_claim", 10)),
            preacquisition_max_total=int(preacq_cfg.get("max_total_candidates", 30)),
            preacquisition_acquire_full_text=bool(preacq_cfg.get("acquire_full_text", False)),
        )
        ctx.register("novelty_validator.default", self._service)
