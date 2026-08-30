"""Built-in benchmark definitions — Phase 6A.

Benchmarks are immutable, versioned definitions. A change to a definition
requires a new benchmark version (the harness refuses to silently overwrite a
registered benchmark whose content differs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass(frozen=True)
class BenchmarkCaseDefinition:
    id: str
    name: str
    description: str
    input: dict[str, Any]
    reference: dict[str, Any]
    evaluation_dimensions: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    version: int = 1


@dataclass(frozen=True)
class BenchmarkDefinition:
    benchmark_id: str
    version: int
    name: str
    description: str
    category: str
    config: dict[str, Any]
    cases: list[BenchmarkCaseDefinition]


# ---------------------------------------------------------------------------
# literature-retrieval-v1
# ---------------------------------------------------------------------------
# Ranking note: the persisted search contract has no explicit rank field;
# the ranked list is the deduplicated first-hit order of search-record
# paper ids (fixture providers return hits in their intended rank order).

_RET_P1 = {
    "title": "Algorithmic Pricing and Consumer Welfare in Online Markets",
    "year": 2020,
    "venue": "Journal of Platform Studies",
    "doi": "10.6000/ret-1",
    "abstract": "Algorithmic pricing reduces consumer welfare in online markets.",
}
_RET_P2 = {
    "title": "Dynamic Pricing in Two-Sided Markets",
    "year": 2019,
    "venue": "Journal of Platform Studies",
    "doi": "10.6000/ret-2",
    "abstract": "Dynamic pricing shapes outcomes in two-sided markets.",
}
_RET_P3 = {
    "title": "Recommendation Systems and Price Dispersion",
    "year": 2021,
    "venue": "Journal of Platform Studies",
    "doi": "10.6000/ret-3",
    "abstract": "Recommendation systems amplify price dispersion.",
}
_RET_IRRELEVANT = {
    "title": "Algorithmic Pricing: A Survey of Estimation Methods",
    "year": 2018,
    "venue": "Methods Review",
    "doi": "10.6000/ret-9",
    "abstract": "Survey of estimation methods for pricing data.",
}
_RET_SPARSE = {
    "title": "Working Notes on Platform Pricing",
    "year": 2022,
}
_RET_UNRELATED = {
    "title": "Soil Microbiomes in Agricultural Systems",
    "year": 2017,
    "venue": "Agronomy",
    "doi": "10.6000/ret-10",
    "abstract": "Microbial communities in agricultural soils.",
}


def _retrieval_case(
    case_id: str,
    name: str,
    description: str,
    queries: list[dict[str, Any]],
    fixture_sources: dict[str, Any],
    providers: list[str],
    relevant: list[str],
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "literature_retrieval",
            "queries": queries,
            "fixture_sources": fixture_sources,
            "providers": providers,
            "retrieval_config": {
                "max_queries": 8,
                "max_results_per_query_per_source": 50,
                "max_total_provider_requests": 50,
                "max_total_papers": 500,
            },
        },
        reference={"relevant": relevant},
        evaluation_dimensions=["retrieval"],
        tags=["retrieval", "offline"],
    )


LITERATURE_RETRIEVAL_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="literature-retrieval-v1",
    version=1,
    name="Literature Retrieval",
    description=(
        "Offline benchmark over the production literature-search pipeline "
        "(literature.search_orchestrator): fixture queries + fixture provider "
        "hits -> ingestion -> PaperIdentity dedup -> ranked result set. "
        "Ranking = provider hit order (documented limitation of the persisted "
        "contract). Metrics: precision@k, recall@k, F1@k, MRR, duplicate_rate, "
        "relevant missed, irrelevant retrieved."
    ),
    category="literature_retrieval",
    config={
        "evaluators": ["evaluator.retrieval"],
        "k": [5, 10],
    },
    cases=[
        _retrieval_case(
            "ret-exact-terminology",
            "exact terminology",
            "Query terms match the relevant paper exactly.",
            [
                {
                    "query": "algorithmic pricing consumer welfare",
                    "purpose": "benchmark",
                    "target_sources": ["crossref"],
                    "year_from": 2015,
                    "year_to": 2026,
                }
            ],
            {"crossref": [_RET_P1]},
            ["crossref"],
            ["10.6000/ret-1"],
        ),
        _retrieval_case(
            "ret-synonym-terminology",
            "synonym terminology",
            "Query uses synonyms; the fixture provider still returns the paper.",
            [
                {
                    "query": "automated price optimization welfare effects",
                    "purpose": "benchmark",
                    "target_sources": ["semantic_scholar"],
                    "year_from": 2015,
                    "year_to": 2026,
                }
            ],
            {"semantic_scholar": [_RET_P1]},
            ["semantic_scholar"],
            ["10.6000/ret-1"],
        ),
        _retrieval_case(
            "ret-multiple-relevant",
            "multiple relevant papers",
            "Three relevant papers found across two providers.",
            [
                {
                    "query": "pricing dynamics in digital platforms",
                    "purpose": "benchmark",
                    "target_sources": ["crossref", "semantic_scholar"],
                    "year_from": 2015,
                    "year_to": 2026,
                }
            ],
            {"crossref": [_RET_P1, _RET_P2], "semantic_scholar": [_RET_P3]},
            ["crossref", "semantic_scholar"],
            ["10.6000/ret-1", "10.6000/ret-2", "10.6000/ret-3"],
        ),
        _retrieval_case(
            "ret-duplicate-provider-results",
            "duplicate provider results",
            "Both providers return the same paper (same DOI, different metadata); "
            "identity resolution must collapse it into one result.",
            [
                {
                    "query": "algorithmic pricing",
                    "purpose": "benchmark",
                    "target_sources": ["crossref", "semantic_scholar"],
                    "year_from": 2015,
                    "year_to": 2026,
                }
            ],
            {
                "crossref": [_RET_P1],
                "semantic_scholar": [
                    {
                        **_RET_P1,
                        "abstract": "We quantify how algorithmic pricing affects welfare.",
                    }
                ],
            },
            ["crossref", "semantic_scholar"],
            ["10.6000/ret-1"],
        ),
        _retrieval_case(
            "ret-irrelevant-high-keyword-overlap",
            "irrelevant high-keyword-overlap paper",
            "An irrelevant paper sharing the query keywords plus a sparse paper "
            "are retrieved alongside the relevant one.",
            [
                {
                    "query": "algorithmic pricing",
                    "purpose": "benchmark",
                    "target_sources": ["crossref"],
                    "year_from": 2015,
                    "year_to": 2026,
                }
            ],
            {"crossref": [_RET_P1, _RET_IRRELEVANT, _RET_SPARSE]},
            ["crossref"],
            ["10.6000/ret-1"],
        ),
        _retrieval_case(
            "ret-no-relevant-result",
            "no relevant result",
            "Nothing relevant is in the corpus; retrieval must not fabricate.",
            [
                {
                    "query": "soil microbiome",
                    "purpose": "benchmark",
                    "target_sources": ["crossref"],
                    "year_from": 2015,
                    "year_to": 2026,
                }
            ],
            {"crossref": [_RET_UNRELATED]},
            ["crossref"],
            [],
        ),
    ],
)

# ---------------------------------------------------------------------------
# citation-correctness-v1
# ---------------------------------------------------------------------------

_CIT_PAPER_A = {
    "id": "paper-cit-a",
    "identity_id": "identity-cit-a",
    "title": "Consumer Welfare Under Algorithmic Pricing",
    "authors": ["Smith, Jane"],
    "year": 2021,
    "venue": "Journal of Platform Studies",
    "doi": "10.6000/cit-a",
    "abstract": "Algorithmic pricing affects consumer welfare.",
}
_CIT_PAPER_B = {
    "id": "paper-cit-b",
    "identity_id": "identity-cit-b",
    "title": "Dynamic Pricing and Seller Collusion",
    "authors": ["Doe, John"],
    "year": 2019,
    "venue": "Journal of Industrial Economics",
    "doi": "10.6000/cit-b",
    "abstract": "Dynamic pricing may facilitate collusion.",
}
_CIT_PAPER_S = {
    "id": "paper-cit-s",
    "identity_id": "identity-cit-s",
    "title": "Working Paper on Quantity Games",
}


def _citation_case(
    case_id: str,
    name: str,
    description: str,
    papers: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    reference: dict[str, Any],
    *,
    anonymous: bool = False,
) -> BenchmarkCaseDefinition:
    # section artifact ids must be unique per case (re-runs stay idempotent)
    scoped_sections = [{**sec, "id": f"{case_id}-{sec['id']}"} for sec in sections]
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "citation_correctness",
            "papers": papers,
            "sections": scoped_sections,
            "draft": {"title": "Pricing and Welfare in Digital Markets"},
            "profile": {
                "name": "Benchmark Profile",
                "citation_style": "author_year",
                "anonymous_review": anonymous,
            },
        },
        reference=reference,
        evaluation_dimensions=["citation"],
        tags=["citation", "offline"],
    )


def _section(
    sec_id: str,
    body: str,
    citations: list[dict[str, Any]],
    *,
    section_id: str = "introduction",
) -> dict[str, Any]:
    return {
        "id": sec_id,
        "section_id": section_id,
        "title": "Introduction" if section_id == "introduction" else "Related Work",
        "body": body,
        "citations": citations,
    }


def _cite(citation_id: str, identity_id: str, page_locator: str | None = None) -> dict[str, Any]:
    return {
        "citation_id": citation_id,
        "paper_identity_id": identity_id,
        "page_locator": page_locator,
    }


_CITATION_REF = {
    "evaluators": ["evaluator.citation_correctness"],
    "citation_mode": "manuscript_citation",
}

CITATION_CORRECTNESS_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="citation-correctness-v1",
    version=1,
    name="Citation Correctness",
    description=(
        "Offline benchmark over the real Phase 4C formatter: fixture manuscript "
        "sections -> CitationReference -> rendered inline citations -> "
        "bibliography -> citation_map. Detects unresolved citations, leftover "
        "placeholders, wrong identity mappings, invented bibliographic fields, "
        "and bibliography deduplication failures."
    ),
    category="citation_correctness",
    config=_CITATION_REF,
    cases=[
        _citation_case(
            "cit-valid-citation",
            "valid citation",
            "A single citation resolves to its paper and bibliography entry.",
            [_CIT_PAPER_A],
            [
                _section(
                    "cit-sec-1",
                    "Prior work studies welfare effects of pricing [CITE:c1].",
                    [_cite("c1", "identity-cit-a")],
                )
            ],
            {
                "expected_identity_map": {"c1": "identity-cit-a"},
                "expected_bibliography_citation_ids": {"identity-cit-a": ["c1"]},
                "expected_leftover_placeholders": [],
            },
        ),
        _citation_case(
            "cit-multiple-citation-ids-same-paper",
            "same paper cited through multiple citation ids",
            "Two citation ids pointing to one paper must produce one bibliography entry.",
            [_CIT_PAPER_A],
            [
                _section(
                    "cit-sec-1",
                    "One view [CITE:c1] and another [CITE:c2] cite the same work.",
                    [_cite("c1", "identity-cit-a"), _cite("c2", "identity-cit-a")],
                )
            ],
            {
                "expected_identity_map": {"c1": "identity-cit-a", "c2": "identity-cit-a"},
                "expected_bibliography_citation_ids": {"identity-cit-a": ["c1", "c2"]},
                "expected_leftover_placeholders": [],
            },
        ),
        _citation_case(
            "cit-missing-citation-id",
            "missing citation id",
            "A citation reference never appears in the text; the bibliography "
            "must not silently drop it without being flagged.",
            [_CIT_PAPER_A, _CIT_PAPER_B],
            [
                _section(
                    "cit-sec-1",
                    "Prior work studies welfare effects of pricing [CITE:c1].",
                    [_cite("c1", "identity-cit-a"), _cite("c2", "identity-cit-b")],
                )
            ],
            {
                "expected_identity_map": {
                    "c1": "identity-cit-a",
                    "c2": "identity-cit-b",
                },
                "expected_bibliography_citation_ids": {"identity-cit-a": ["c1"]},
                "expected_leftover_placeholders": [],
            },
        ),
        _citation_case(
            "cit-missing-paper-identity",
            "citation pointing to missing PaperIdentity",
            "The formatter must refuse (not fabricate) when a citation points "
            "to a nonexistent identity.",
            [_CIT_PAPER_A],
            [
                _section(
                    "cit-sec-1",
                    "Prior work studies welfare effects [CITE:c1].",
                    [_cite("c1", "identity-missing")],
                )
            ],
            {
                "expected_formatter_failure": True,
                "expected_identity_map": {},
                "expected_bibliography_citation_ids": {},
                "expected_leftover_placeholders": [],
            },
        ),
        _citation_case(
            "cit-sparse-metadata",
            "sparse metadata without invention",
            "A paper with only a title must render without invented fields.",
            [_CIT_PAPER_S],
            [
                _section(
                    "cit-sec-1",
                    "An unpublished note [CITE:c1] discusses quantity games.",
                    [_cite("c1", "identity-cit-s")],
                )
            ],
            {
                "expected_identity_map": {"c1": "identity-cit-s"},
                "expected_bibliography_citation_ids": {"identity-cit-s": ["c1"]},
                "expected_leftover_placeholders": [],
            },
        ),
        _citation_case(
            "cit-page-locator",
            "page locator",
            "A page locator must be preserved in the rendered inline citation.",
            [_CIT_PAPER_A],
            [
                _section(
                    "cit-sec-1",
                    "The welfare result appears in [CITE:c1].",
                    [_cite("c1", "identity-cit-a", "p. 42")],
                )
            ],
            {
                "expected_identity_map": {"c1": "identity-cit-a"},
                "expected_bibliography_citation_ids": {"identity-cit-a": ["c1"]},
                "expected_leftover_placeholders": [],
                "expected_inline": {"c1": "(Smith, Jane, 2021, p. 42)"},
            },
        ),
        _citation_case(
            "cit-anonymous-manuscript",
            "anonymous manuscript",
            "An anonymous profile must strip author front matter.",
            [_CIT_PAPER_A],
            [
                _section(
                    "cit-sec-1",
                    "Prior work [CITE:c1] studies welfare effects.",
                    [_cite("c1", "identity-cit-a")],
                )
            ],
            {
                "expected_identity_map": {"c1": "identity-cit-a"},
                "expected_bibliography_citation_ids": {"identity-cit-a": ["c1"]},
                "expected_leftover_placeholders": [],
                "expected_anonymous": True,
            },
            anonymous=True,
        ),
        _citation_case(
            "cit-multiple-sections-same-paper",
            "multiple sections citing same paper",
            "The same paper cited from two sections yields one bibliography "
            "entry with both citation ids.",
            [_CIT_PAPER_A],
            [
                _section(
                    "cit-sec-1",
                    "Prior work [CITE:c1] studies welfare.",
                    [_cite("c1", "identity-cit-a")],
                ),
                _section(
                    "cit-sec-2",
                    "We build on [CITE:c2] throughout.",
                    [_cite("c2", "identity-cit-a")],
                    section_id="literature_review",
                ),
            ],
            {
                "expected_identity_map": {"c1": "identity-cit-a", "c2": "identity-cit-a"},
                "expected_bibliography_citation_ids": {"identity-cit-a": ["c1", "c2"]},
                "expected_leftover_placeholders": [],
            },
        ),
        _citation_case(
            "cit-leftover-placeholder",
            "leftover [CITE:*] placeholder",
            "An unresolved placeholder left in the text is a deterministic failure.",
            [_CIT_PAPER_A],
            [
                _section(
                    "cit-sec-1",
                    "Prior work [CITE:c1] is relevant, and [CITE:ghost] is not.",
                    [_cite("c1", "identity-cit-a")],
                )
            ],
            {
                "expected_identity_map": {"c1": "identity-cit-a"},
                "expected_bibliography_citation_ids": {"identity-cit-a": ["c1"]},
                "expected_leftover_placeholders": [],
            },
        ),
        _citation_case(
            "cit-wrong-identity-mapping",
            "citation mapped to wrong PaperIdentity",
            "A citation wired to the wrong identity must be flagged via the citation map.",
            [_CIT_PAPER_A, _CIT_PAPER_B],
            [
                _section(
                    "cit-sec-1",
                    "Prior work [CITE:c1] studies welfare effects.",
                    [_cite("c1", "identity-cit-b")],
                )
            ],
            {
                "expected_identity_map": {"c1": "identity-cit-a"},
                "expected_bibliography_citation_ids": {"identity-cit-a": ["c1"]},
                "expected_leftover_placeholders": [],
            },
        ),
    ],
)

# ---------------------------------------------------------------------------
# novelty-threat-v1
# ---------------------------------------------------------------------------

_CLAIM_DIRECT = (
    "We are the first to show that algorithmic pricing reduces consumer welfare in online markets."
)
_CLAIM_STRONG = "We are the first to demonstrate that recommendation algorithms amplify price dispersion in e-commerce."
_CLAIM_PARTIAL = (
    "We are the first to show that dynamic pricing increases seller collusion in two-sided markets."
)
_CLAIM_DISTINCT = "We are the first to model how rating volatility affects platform investment in gig work markets."
_CLAIM_EVIDENCE = "We are the first to show that ad-targeting algorithms reduce content diversity in news platforms."
_CLAIM_PROVIDER = "We are the first to show that subscription fatigue reduces platform switching in streaming markets."
_CLAIM_MISSED = (
    "We are the first to show that personalized pricing lowers consumer trust in online retail."
)


def _submission(title: str, abstract: str, claim: str) -> dict[str, Any]:
    """Fixture manuscript with exactly one Layer-A detectable high-risk claim."""
    return {
        "title": title,
        "abstract": abstract,
        "sections": {
            "introduction": (
                "Prior work examines platform pricing but not consumer welfare effects. "
                f"{claim} We study this question in a stylized analytical model."
            ),
            "conclusion": "Our model connects pricing, welfare, and platform design.",
        },
    }


def _assess_fixture(relationship: str, claim: str) -> dict[str, Any]:
    return {
        "match": claim,
        "response": {
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
        },
    }


def _base_fixtures() -> list[dict[str, Any]]:
    return [
        {
            "match": "Judge whether the produced research output satisfies the reference.",
            "response": {
                "score": 1.0,
                "status": "pass",
                "explanation": "fixture judge: output satisfies the reference",
            },
        },
        {
            "match": "grounded in the cited evidence",
            "response": {
                "verdict": "grounded",
                "explanation": "fixture grounding: assessment cites evidence",
            },
        },
        {"match": "Identify novelty and contribution claims", "response": {"claims": []}},
        {
            "match": "Generate concrete literature-search queries",
            "response": {"queries": []},
        },
        {
            "match": "You independently verify a prior-art assessment",
            "response": {"verdict": "concurs", "reasoning": "fixture critic concurs"},
        },
        {
            "match": "You recommend conservative rewording",
            "response": {
                "suggested_scope_change": "scope the claim to the studied setting",
                "suggested_wording": "To our knowledge, this study is among the first to address this question.",
            },
        },
    ]


def _fixtures(
    assess: dict[str, Any], *, judge_fail: bool = False, grounding_verdict: str | None = None
) -> list[dict[str, Any]]:
    fixtures = _base_fixtures()
    if judge_fail:
        fixtures[0] = {
            "match": "Judge whether the produced research output satisfies the reference.",
            "response": {
                "score": 0.4,
                "status": "fail",
                "explanation": "fixture judge: output does not satisfy the reference",
            },
        }
    if grounding_verdict is not None:
        fixtures[1] = {
            "match": "grounded in the cited evidence",
            "response": {
                "verdict": grounding_verdict,
                "explanation": f"fixture grounding: {grounding_verdict}",
            },
        }
    return fixtures + [assess]


def _prior_art(title: str, relationship: str) -> dict[str, Any]:
    return {"title": title, "relationship": relationship}


NOVELTY_THREAT_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="novelty-threat-v1",
    version=1,
    name="Novelty Threat Detection",
    description=(
        "Offline benchmark over the production novelty-validation workflow. "
        "Each case feeds a fixture manuscript + scripted literature hits into "
        "the real NoveltyValidationService and checks candidate relationships, "
        "claim statuses, report status, and false-clear behavior. "
        "The missed-prior-art case deliberately scripts a model misjudgment so "
        "false-clear is measured end-to-end."
    ),
    category="novelty_threat",
    config={
        "mode": "novelty_threat",
        "evaluators": [
            "evaluator.deterministic",
            "evaluator.claim_grounding",
            "evaluator.citation_correctness",
            "evaluator.llm_judge",
        ],
        "judge_role": "critic",
        "llm_judge": {
            "system": "You are an evaluation judge for a research system.",
            "prompt_template": (
                "Case: {case_name}\nProduced: {produced_summary}\n"
                "Reference: {reference_summary}\nJudge whether the produced "
                "research output satisfies the reference."
            ),
        },
    },
    cases=[
        BenchmarkCaseDefinition(
            id="nt-direct-prior-art",
            name="clear direct prior art",
            description="A directly contradicting paper exists; the system must detect it.",
            input={
                "workflow": "novelty_validation",
                "submission": _submission(
                    "Algorithmic Pricing and Consumer Welfare",
                    "We study how algorithmic pricing shapes welfare in online markets.",
                    _CLAIM_DIRECT,
                ),
                "fixture_sources": {
                    "semantic_scholar": [
                        {
                            "title": "Algorithmic Pricing and Consumer Welfare in Online Markets",
                            "abstract": "We show that algorithmic pricing reduces consumer welfare in online markets.",
                            "year": 2020,
                            "doi": "10.2000/nt-direct",
                            "venue": "Journal of Platform Studies",
                        }
                    ]
                },
                "llm_fixtures": _fixtures(_assess_fixture("direct_prior_art", _CLAIM_DIRECT)),
                "providers": ["semantic_scholar"],
                "as_of": "2026-08-01",
                "novelty_config": {
                    "providers": ["semantic_scholar"],
                    "max_queries_per_claim": 4,
                },
            },
            reference={
                "prior_art": [
                    _prior_art(
                        "Algorithmic Pricing and Consumer Welfare in Online Markets",
                        "direct_prior_art",
                    )
                ],
                "expected_claim_statuses": {_CLAIM_DIRECT: "threatened"},
                "expected_report_status": "blocked",
            },
            evaluation_dimensions=["candidate_relationship", "claim_status", "report_status"],
            tags=["novelty", "threat", "offline"],
        ),
        BenchmarkCaseDefinition(
            id="nt-strong-overlap",
            name="strong overlap",
            description="A paper strongly overlaps the claim; system must detect the threat.",
            input={
                "workflow": "novelty_validation",
                "submission": _submission(
                    "Recommendation Algorithms and Price Dispersion",
                    "We analyze how recommendation systems shape e-commerce pricing.",
                    _CLAIM_STRONG,
                ),
                "fixture_sources": {
                    "semantic_scholar": [
                        {
                            "title": "Recommendation Systems and Price Dispersion in E-Commerce",
                            "abstract": "Recommendation algorithms amplify price dispersion in e-commerce.",
                            "year": 2021,
                            "doi": "10.2000/nt-strong",
                            "venue": "Journal of Platform Studies",
                        }
                    ]
                },
                "llm_fixtures": _fixtures(_assess_fixture("strong_overlap", _CLAIM_STRONG)),
                "providers": ["semantic_scholar"],
                "as_of": "2026-08-01",
                "novelty_config": {
                    "providers": ["semantic_scholar"],
                    "max_queries_per_claim": 4,
                },
            },
            reference={
                "prior_art": [
                    _prior_art(
                        "Recommendation Systems and Price Dispersion in E-Commerce",
                        "strong_overlap",
                    )
                ],
                "expected_claim_statuses": {_CLAIM_STRONG: "threatened"},
                "expected_report_status": "blocked",
            },
            evaluation_dimensions=["candidate_relationship", "claim_status", "report_status"],
            tags=["novelty", "threat", "offline"],
        ),
        BenchmarkCaseDefinition(
            id="nt-partial-overlap",
            name="partial overlap",
            description="Related prior work weakens but does not kill the claim.",
            input={
                "workflow": "novelty_validation",
                "submission": _submission(
                    "Dynamic Pricing and Seller Collusion",
                    "We study dynamic pricing in two-sided markets.",
                    _CLAIM_PARTIAL,
                ),
                "fixture_sources": {
                    "semantic_scholar": [
                        {
                            "title": "Dynamic Pricing and Market Outcomes in Two-Sided Platforms",
                            "abstract": "Dynamic pricing shapes market outcomes in two-sided platforms.",
                            "year": 2019,
                            "doi": "10.2000/nt-partial",
                            "venue": "Journal of Platform Studies",
                        }
                    ]
                },
                "llm_fixtures": _fixtures(_assess_fixture("partial_overlap", _CLAIM_PARTIAL)),
                "providers": ["semantic_scholar"],
                "as_of": "2026-08-01",
                "novelty_config": {
                    "providers": ["semantic_scholar"],
                    "max_queries_per_claim": 4,
                },
            },
            reference={
                "prior_art": [
                    _prior_art(
                        "Dynamic Pricing and Market Outcomes in Two-Sided Platforms",
                        "partial_overlap",
                    )
                ],
                "expected_claim_statuses": {_CLAIM_PARTIAL: "weakened"},
                "expected_report_status": "revise",
            },
            evaluation_dimensions=["candidate_relationship", "claim_status", "report_status"],
            tags=["novelty", "overlap", "offline"],
        ),
        BenchmarkCaseDefinition(
            id="nt-distinct-paper",
            name="distinct paper",
            description="Prior work is unrelated; the system must not raise a false threat.",
            input={
                "workflow": "novelty_validation",
                "submission": _submission(
                    "Rating Volatility and Platform Investment",
                    "We model rating dynamics in gig work platforms.",
                    _CLAIM_DISTINCT,
                ),
                "fixture_sources": {
                    "semantic_scholar": [
                        {
                            "title": "Rating Volatility and Consumer Choice in Gig Work Platforms",
                            "abstract": "Consumers choose platforms based on rating volatility.",
                            "year": 2022,
                            "doi": "10.2000/nt-distinct",
                            "venue": "Journal of Platform Studies",
                        }
                    ]
                },
                "llm_fixtures": _fixtures(_assess_fixture("distinct", _CLAIM_DISTINCT)),
                "providers": ["semantic_scholar"],
                "as_of": "2026-08-01",
                "novelty_config": {
                    "providers": ["semantic_scholar"],
                    "max_queries_per_claim": 4,
                },
            },
            reference={
                "prior_art": [
                    _prior_art(
                        "Rating Volatility and Consumer Choice in Gig Work Platforms",
                        "distinct",
                    )
                ],
                "expected_claim_statuses": {_CLAIM_DISTINCT: "not_threatened_within_search_scope"},
                "expected_report_status": "clear",
            },
            evaluation_dimensions=["candidate_relationship", "claim_status", "report_status"],
            tags=["novelty", "distinct", "offline"],
        ),
        BenchmarkCaseDefinition(
            id="nt-insufficient-evidence",
            name="insufficient evidence",
            description="Candidate has no abstract/full text; the system must stay unverified.",
            input={
                "workflow": "novelty_validation",
                "submission": _submission(
                    "Ad Targeting and Content Diversity",
                    "We study how ad targeting shapes news content.",
                    _CLAIM_EVIDENCE,
                ),
                "fixture_sources": {
                    "semantic_scholar": [
                        {
                            "title": "Ad Targeting and Content Diversity in News Platforms",
                            "year": 2021,
                            "doi": "10.2000/nt-evidence",
                            "venue": "Journal of Platform Studies",
                        }
                    ]
                },
                "llm_fixtures": _fixtures(
                    _assess_fixture("direct_prior_art", _CLAIM_EVIDENCE),
                    grounding_verdict="ungrounded",
                ),
                "providers": ["semantic_scholar"],
                "as_of": "2026-08-01",
                "novelty_config": {
                    "providers": ["semantic_scholar"],
                    "max_queries_per_claim": 4,
                },
            },
            reference={
                "prior_art": [
                    _prior_art(
                        "Ad Targeting and Content Diversity in News Platforms",
                        "insufficient_evidence",
                    )
                ],
                "expected_claim_statuses": {_CLAIM_EVIDENCE: "unverified"},
                "expected_report_status": "unverified",
            },
            evaluation_dimensions=["candidate_relationship", "claim_status", "report_status"],
            tags=["novelty", "evidence", "offline"],
        ),
        BenchmarkCaseDefinition(
            id="nt-provider-failure",
            name="provider/search failure",
            description="All external searches fail; the system must stay unverified, not clear.",
            input={
                "workflow": "novelty_validation",
                "submission": _submission(
                    "Subscription Fatigue and Platform Switching",
                    "We analyze subscription fatigue in streaming markets.",
                    _CLAIM_PROVIDER,
                ),
                "fixture_sources": {"semantic_scholar": "fail_all"},
                "llm_fixtures": _base_fixtures(),
                "providers": ["semantic_scholar"],
                "as_of": "2026-08-01",
                "novelty_config": {
                    "providers": ["semantic_scholar"],
                    "max_queries_per_claim": 4,
                },
            },
            reference={
                "prior_art": [],
                "expected_claim_statuses": {_CLAIM_PROVIDER: "unverified"},
                "expected_report_status": "unverified",
            },
            evaluation_dimensions=["claim_status", "report_status"],
            tags=["novelty", "provider-failure", "offline"],
        ),
        BenchmarkCaseDefinition(
            id="nt-missed-prior-art",
            name="missed prior art (false clear)",
            description=(
                "Prior art exists but the assessment misjudges it as distinct; "
                "the false-clear metric must catch the resulting clear report."
            ),
            input={
                "workflow": "novelty_validation",
                "submission": _submission(
                    "Personalized Pricing and Consumer Trust",
                    "We study personalized pricing in online retail.",
                    _CLAIM_MISSED,
                ),
                "fixture_sources": {
                    "semantic_scholar": [
                        {
                            "title": "Personalized Pricing and Consumer Trust in Online Retail",
                            "abstract": "Personalized pricing lowers consumer trust in online retail.",
                            "year": 2020,
                            "doi": "10.2000/nt-missed",
                            "venue": "Journal of Platform Studies",
                        }
                    ]
                },
                "llm_fixtures": _fixtures(
                    _assess_fixture("distinct", _CLAIM_MISSED), judge_fail=True
                ),
                "providers": ["semantic_scholar"],
                "as_of": "2026-08-01",
                "novelty_config": {
                    "providers": ["semantic_scholar"],
                    "max_queries_per_claim": 4,
                },
            },
            reference={
                "prior_art": [
                    _prior_art(
                        "Personalized Pricing and Consumer Trust in Online Retail",
                        "direct_prior_art",
                    )
                ],
                "expected_claim_statuses": {_CLAIM_MISSED: "threatened"},
                "expected_report_status": "blocked",
            },
            evaluation_dimensions=[
                "candidate_relationship",
                "claim_status",
                "report_status",
                "false_clear",
            ],
            tags=["novelty", "false-clear", "offline"],
        ),
    ],
)


# ---------------------------------------------------------------------------
# literature-screening-v1
# ---------------------------------------------------------------------------


def _protocol_fixture(marker: str) -> dict[str, Any]:
    return {
        "match": marker,
        "response": {
            "objective": "Select studies on algorithmic pricing in digital markets.",
            "inclusion_criteria": [
                {
                    "criterion_id": "I1",
                    "description": "Studies algorithmic pricing effects in digital markets",
                    "rationale": "Core focus of the review",
                    "required": True,
                }
            ],
            "exclusion_criteria": [
                {
                    "criterion_id": "E1",
                    "description": "Purely technical or non-scholarly work",
                    "rationale": "Out of scope",
                    "required": False,
                }
            ],
            "decision_rules": "Include if I1 is satisfied and no exclusion criterion matches.",
        },
    }


def _screen_fixture(
    marker: str,
    decision: str,
    confidence: float,
    *,
    sufficiency: str = "sufficient",
) -> dict[str, Any]:
    return {
        "match": marker,
        "response": {
            "decision": decision,
            "matched_inclusion_criteria": ["I1"] if decision == "include" else [],
            "matched_exclusion_criteria": ["E1"] if decision == "exclude" else [],
            "reason_codes": [],
            "rationale_summary": "fixture screening decision",
            "confidence": confidence,
            "information_sufficiency": sufficiency,
        },
    }


def _screening_case(
    case_id: str,
    name: str,
    description: str,
    question: str,
    papers: list[dict[str, Any]],
    identities: list[dict[str, Any]],
    decision: str | None,
    confidence: float = 0.9,
    *,
    sufficiency: str = "sufficient",
    expected_review: bool = False,
    expected_failure: bool = False,
) -> BenchmarkCaseDefinition:
    fixtures: list[dict[str, Any]] = [_protocol_fixture(question)]
    if decision is not None:
        fixtures.append(
            _screen_fixture(papers[0]["title"], decision, confidence, sufficiency=sufficiency)
        )
    reference: dict[str, Any] = {}
    if expected_failure:
        reference["expected_failed_identities"] = [papers[0]["title"]]
    else:
        reference["expected_decisions"] = {papers[0]["title"]: decision}
        reference["expected_reviews"] = {papers[0]["title"]: expected_review}
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "literature_screening",
            "research_question": {"question": question},
            "papers": papers,
            "identities": identities,
            "llm_fixtures": fixtures,
            "screening_config": {"max_candidates": 100, "max_model_calls": 500},
        },
        reference=reference,
        evaluation_dimensions=["screening"],
        tags=["screening", "offline"],
    )


LITERATURE_SCREENING_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="literature-screening-v1",
    version=1,
    name="Literature Screening",
    description=(
        "Offline benchmark over the real Phase 2D screening pipeline: real "
        "protocol builder (model + approval gate), real view builder, real "
        "title/abstract screener, real orchestrator -> ScreeningDecision + "
        "ScreenedLiteratureSet."
    ),
    category="literature_screening",
    config={"evaluators": ["evaluator.screening"]},
    cases=[
        _screening_case(
            "scr-clear-include",
            "clear include",
            "A clearly relevant paper is included.",
            "Which studies examine algorithmic pricing effects on consumer welfare in online markets? scr-clear-include",
            [
                {
                    "title": "Algorithmic Pricing and Consumer Welfare",
                    "year": 2021,
                    "venue": "Journal of Platform Studies",
                    "abstract": "We study how algorithmic pricing affects consumer welfare in online markets.",
                }
            ],
            [{"member_indexes": [0]}],
            "include",
            confidence=0.95,
        ),
        _screening_case(
            "scr-clear-exclude",
            "clear exclude",
            "A clearly out-of-scope paper is excluded.",
            "Which studies examine algorithmic pricing effects on consumer welfare in online markets? scr-clear-exclude",
            [
                {
                    "title": "Survey of Estimation Methods for Pricing Data",
                    "year": 2018,
                    "venue": "Methods Review",
                    "abstract": "A methodological survey of estimation techniques.",
                }
            ],
            [{"member_indexes": [0]}],
            "exclude",
            confidence=0.95,
        ),
        _screening_case(
            "scr-ambiguous-uncertain",
            "ambiguous -> uncertain",
            "An ambiguous paper is marked uncertain and sent to review.",
            "Which studies examine algorithmic pricing effects on consumer welfare in online markets? scr-ambiguous",
            [
                {
                    "title": "Pricing and Platforms",
                    "year": 2020,
                    "venue": "Working Paper",
                    "abstract": "Discusses pricing in platform markets at a general level.",
                }
            ],
            [{"member_indexes": [0]}],
            "uncertain",
            confidence=0.5,
            sufficiency="insufficient",
            expected_review=True,
        ),
        _screening_case(
            "scr-missing-abstract",
            "missing abstract",
            "A paper without an abstract must be uncertain, never excluded.",
            "Which studies examine algorithmic pricing effects on consumer welfare in online markets? scr-missing-abstract",
            [{"title": "Working Paper on Pricing Dynamics", "year": 2022}],
            [{"member_indexes": [0]}],
            "uncertain",
            confidence=0.5,
            sufficiency="insufficient",
            expected_review=True,
        ),
        _screening_case(
            "scr-conflicting-metadata",
            "conflicting metadata",
            "An identity with conflicting member records still screens once.",
            "Which studies examine algorithmic pricing effects on consumer welfare in online markets? scr-conflicting",
            [
                {
                    "title": "Conflicting Pricing Study Alpha",
                    "year": 2020,
                    "venue": "Journal A",
                    "abstract": "Study alpha on pricing effects.",
                },
                {
                    "title": "Conflicting Pricing Study Beta",
                    "year": 2021,
                    "venue": "Journal B",
                    "abstract": "Study beta on pricing effects.",
                },
            ],
            [{"member_indexes": [0, 1]}],
            "include",
            confidence=0.9,
        ),
        _screening_case(
            "scr-duplicate-provider-identity",
            "duplicate provider identity screened once",
            "One identity with duplicate provider records is screened exactly once.",
            "Which studies examine algorithmic pricing effects on consumer welfare in online markets? scr-duplicate",
            [
                {
                    "title": "Platform Pricing Evidence Report",
                    "year": 2021,
                    "venue": "Journal of Platform Studies",
                    "doi": "10.6000/scr-dup",
                    "abstract": "Evidence on platform pricing.",
                },
                {
                    "title": "Platform Pricing Evidence Report",
                    "year": 2021,
                    "venue": "Journal of Platform Studies",
                    "doi": "10.6000/scr-dup",
                    "abstract": "Evidence on platform pricing.",
                },
            ],
            [{"member_indexes": [0, 1]}],
            "include",
            confidence=0.9,
        ),
        _screening_case(
            "scr-irrelevant-keyword-overlap",
            "irrelevant keyword overlap",
            "Keyword-heavy but irrelevant work is excluded.",
            "Which studies examine algorithmic pricing effects on consumer welfare in online markets? scr-overlap",
            [
                {
                    "title": "Algorithmic Pricing: Hardware Implementation Notes",
                    "year": 2019,
                    "venue": "Tech Notes",
                    "abstract": "Notes on hardware implementations of pricing algorithms.",
                }
            ],
            [{"member_indexes": [0]}],
            "exclude",
            confidence=0.95,
        ),
        _screening_case(
            "scr-low-confidence-review-trigger",
            "low-confidence review trigger",
            "A low-confidence include still triggers a review and stays included.",
            "Which studies examine algorithmic pricing effects on consumer welfare in online markets? scr-lowconf",
            [
                {
                    "title": "Dynamic Pricing and Seller Collusion",
                    "year": 2019,
                    "venue": "Journal of Industrial Economics",
                    "abstract": "Dynamic pricing may facilitate seller collusion.",
                }
            ],
            [{"member_indexes": [0]}],
            "include",
            confidence=0.5,
            expected_review=True,
        ),
        _screening_case(
            "scr-technical-failure-not-exclusion",
            "technical/model failure not treated as exclusion",
            "A screening model failure must be recorded as a failure, never as an exclusion.",
            "Which studies examine algorithmic pricing effects on consumer welfare in online markets? scr-failure",
            [
                {
                    "title": "Edge Case Paper on Pricing",
                    "year": 2020,
                    "venue": "Journal of Platform Studies",
                    "abstract": "A paper whose screening fails.",
                }
            ],
            [{"member_indexes": [0]}],
            None,
            expected_failure=True,
        ),
    ],
)

# ---------------------------------------------------------------------------
# evidence-extraction-v1
# ---------------------------------------------------------------------------


def _evidence_fixture(marker: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"match": marker, "response": {"items": items}}


def _evidence_item(
    category: str, statement: str, pages: list[int], confidence: float = 0.9
) -> dict[str, Any]:
    return {
        "category": category,
        "statement": statement,
        "page_numbers": pages,
        "confidence": confidence,
    }


def _evidence_case(
    case_id: str,
    name: str,
    description: str,
    documents: list[dict[str, Any]],
    fixtures: list[dict[str, Any]],
    reference: dict[str, Any],
    *,
    pages_per_chunk: int = 4,
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "evidence_extraction",
            "documents": documents,
            "llm_fixtures": fixtures,
            "evidence_config": {
                "pages_per_chunk": pages_per_chunk,
                "max_chunks_per_document": 50,
                "max_model_calls": 500,
            },
        },
        reference=reference,
        evaluation_dimensions=["evidence"],
        tags=["evidence", "offline"],
    )


def _doc(title: str, pages: list[str], *, text_status: str = "extracted") -> dict[str, Any]:
    return {
        "title": title,
        "pages": [{"page": i + 1, "text": text} for i, text in enumerate(pages)],
        "text_status": text_status,
    }


EVIDENCE_EXTRACTION_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="evidence-extraction-v1",
    version=1,
    name="Evidence Extraction",
    description=(
        "Offline benchmark over the real Phase 2F evidence pipeline: fixture "
        "full-text documents (blob-backed pages) -> real extractor + "
        "orchestrator -> EvidenceItem / PaperResearchProfile / EvidenceCorpus."
    ),
    category="evidence_extraction",
    config={"evaluators": ["evaluator.evidence"]},
    cases=[
        _evidence_case(
            "ev-single-page-finding",
            "single-page finding",
            "A finding on a single page is extracted with the correct locator.",
            [
                _doc(
                    "Evidence Benchmark Paper One",
                    [
                        "We introduce the model of platform pricing.",
                        "The baseline setup follows standard assumptions.",
                        "The platform maximizes consumer surplus in equilibrium.",
                        "Welfare comparisons close the section.",
                    ],
                )
            ],
            [
                _evidence_fixture(
                    "maximizes consumer surplus",
                    [
                        _evidence_item(
                            "result", "The platform maximizes consumer surplus in equilibrium.", [3]
                        )
                    ],
                )
            ],
            {
                "expected_statements": {
                    "The platform maximizes consumer surplus in equilibrium.": {
                        "category": "result",
                        "valid_pages": [3],
                        "required": True,
                    }
                },
                "expected_unsupported": 0,
                "expected_chunk_failures": 0,
            },
        ),
        _evidence_case(
            "ev-multi-page-evidence",
            "multi-page evidence",
            "Evidence spanning two pages keeps both pages in its locator.",
            [
                _doc(
                    "Evidence Benchmark Paper Two",
                    [
                        "Entry barriers shape market structure.",
                        "Rising entry costs reduce market participation.",
                        "The participation result extends to dynamic settings.",
                        "Implications for policy follow.",
                    ],
                )
            ],
            [
                _evidence_fixture(
                    "Rising entry costs",
                    [
                        _evidence_item(
                            "finding", "Rising entry costs reduce market participation.", [2, 3]
                        )
                    ],
                )
            ],
            {
                "expected_statements": {
                    "Rising entry costs reduce market participation.": {
                        "category": "finding",
                        "valid_pages": [2, 3],
                        "required": True,
                    }
                },
                "expected_unsupported": 0,
                "expected_chunk_failures": 0,
            },
        ),
        _evidence_case(
            "ev-multiple-categories",
            "multiple categories in one document",
            "Two categories extracted from two chunks of one document.",
            [
                _doc(
                    "Evidence Benchmark Paper Three",
                    [
                        "Prices rise with market concentration.",
                        "We document this correlation first.",
                        "Concentration amplifies price rigidity through entry barriers.",
                        "The mechanism is then formalized.",
                    ],
                )
            ],
            [
                _evidence_fixture(
                    "Prices rise with market concentration",
                    [_evidence_item("finding", "Prices rise with market concentration.", [1])],
                ),
                _evidence_fixture(
                    "amplifies price rigidity",
                    [
                        _evidence_item(
                            "mechanism",
                            "Concentration amplifies price rigidity through entry barriers.",
                            [4],
                        )
                    ],
                ),
            ],
            {
                "expected_statements": {
                    "Prices rise with market concentration.": {
                        "category": "finding",
                        "valid_pages": [1],
                        "required": True,
                    },
                    "Concentration amplifies price rigidity through entry barriers.": {
                        "category": "mechanism",
                        "valid_pages": [4],
                        "required": True,
                    },
                },
                "expected_unsupported": 0,
                "expected_chunk_failures": 0,
            },
            pages_per_chunk=2,
        ),
        _evidence_case(
            "ev-similar-text-wrong-page",
            "similar text on wrong page",
            "A locator pointing outside the supplied chunk must be refused, "
            "not silently corrected.",
            [
                _doc(
                    "Evidence Benchmark Paper Four",
                    [
                        "We describe the market setting.",
                        "The profit function is standard.",
                        "Equilibrium quantities are derived.",
                        "The main result follows immediately.",
                    ],
                )
            ],
            [
                _evidence_fixture(
                    "main result follows",
                    [_evidence_item("result", "The main result follows immediately.", [5])],
                )
            ],
            {
                "expected_statements": {},
                "expected_unsupported": 0,
                "expected_chunk_failures": 1,
            },
        ),
        _evidence_case(
            "ev-unsupported-claim",
            "unsupported claim",
            "A statement absent from the source text is unsupported evidence "
            "and must fail the case (deterministic grounding check).",
            [
                _doc(
                    "Evidence Benchmark Paper Five",
                    [
                        "We analyze price formation.",
                        "Equilibrium prices are characterized.",
                        "Comparative statics follow.",
                        "A final section discusses policy.",
                    ],
                )
            ],
            [
                _evidence_fixture(
                    "Equilibrium prices are characterized",
                    [
                        _evidence_item(
                            "result", "This claim is not supported by any page text.", [2]
                        )
                    ],
                )
            ],
            {
                "expected_statements": {},
                "expected_unsupported": 0,
                "expected_chunk_failures": 0,
            },
        ),
        _evidence_case(
            "ev-missing-evidence",
            "missing evidence",
            "Required evidence present in the text but missed by the "
            "extractor is a deterministic failure.",
            [
                _doc(
                    "Evidence Benchmark Paper Six",
                    [
                        "Advertising subsidies increase platform growth.",
                        "We measure growth effects.",
                        "Robustness checks use alternative samples.",
                        "The section concludes.",
                    ],
                )
            ],
            [
                _evidence_fixture("Advertising subsidies", []),
            ],
            {
                "expected_statements": {
                    "Advertising subsidies increase platform growth.": {
                        "category": "result",
                        "valid_pages": [1],
                        "required": True,
                    }
                },
                "expected_unsupported": 0,
                "expected_chunk_failures": 0,
            },
        ),
        _evidence_case(
            "ev-duplicate-evidence",
            "duplicate evidence",
            "The same statement from two chunks must dedup into one evidence item.",
            [
                _doc(
                    "Evidence Benchmark Paper Seven",
                    [
                        "Switching costs segment the market. Baseline data are reported here.",
                        "The static equilibrium is characterized.",
                        "Switching costs segment the market. Robustness checks follow.",
                        "Dynamic effects are discussed.",
                    ],
                )
            ],
            [
                _evidence_fixture(
                    "Baseline data",
                    [_evidence_item("finding", "Switching costs segment the market.", [1])],
                ),
                _evidence_fixture(
                    "Robustness checks",
                    [_evidence_item("finding", "Switching costs segment the market.", [3])],
                ),
            ],
            {
                "expected_statements": {
                    "Switching costs segment the market.": {
                        "category": "finding",
                        "valid_pages": [1, 3],
                        "required": True,
                    }
                },
                "expected_unsupported": 0,
                "expected_chunk_failures": 0,
            },
            pages_per_chunk=2,
        ),
        _evidence_case(
            "ev-partial-chunk-failure",
            "partial chunk/model failure",
            "A failing middle chunk must not lose evidence from other chunks.",
            [
                _doc(
                    "Evidence Benchmark Paper Eight",
                    [
                        "Platform fees transfer surplus to sellers.",
                        "Fee levels vary across markets.",
                        "The data appendix describes measurement.",
                        "We code the variables as stated.",
                        "Regulatory caps dampen platform entry.",
                        "The entry result is robust.",
                    ],
                )
            ],
            [
                _evidence_fixture(
                    "transfer surplus to sellers",
                    [_evidence_item("result", "Platform fees transfer surplus to sellers.", [1])],
                ),
                _evidence_fixture(
                    "Regulatory caps dampen",
                    [_evidence_item("result", "Regulatory caps dampen platform entry.", [5])],
                ),
            ],
            {
                "expected_statements": {
                    "Platform fees transfer surplus to sellers.": {
                        "category": "result",
                        "valid_pages": [1],
                        "required": True,
                    },
                    "Regulatory caps dampen platform entry.": {
                        "category": "result",
                        "valid_pages": [5],
                        "required": True,
                    },
                },
                "expected_unsupported": 0,
                "expected_chunk_failures": 1,
            },
            pages_per_chunk=2,
        ),
        _evidence_case(
            "ev-insufficient-text-document",
            "insufficient-text document",
            "A document with insufficient text yields no evidence and is reported as such.",
            [
                _doc(
                    "Evidence Benchmark Paper Nine",
                    ["Only a fragment of the paper is available."],
                    text_status="insufficient_text",
                )
            ],
            [],
            {
                "expected_statements": {},
                "expected_unsupported": 0,
                "expected_chunk_failures": 0,
                "expected_documents_without_evidence": ["Evidence Benchmark Paper Nine"],
            },
        ),
    ],
)

# ---------------------------------------------------------------------------
# research-gap-analysis-v1
# ---------------------------------------------------------------------------


def _gap_fixture(marker: str, gaps: list[dict[str, Any]]) -> dict[str, Any]:
    return {"match": marker, "response": {"gaps": gaps}}


def _gap_item(
    title: str,
    gap_type: str,
    description: str,
    *,
    stmt_ids: list[str] | None = None,
    ev_ids: list[str] | None = None,
    contradiction_ids: list[str] | None = None,
    scores: tuple[float, float, float, float, float] = (0.7, 0.7, 0.7, 0.7, 0.7),
    confidence: float = 0.8,
    domains: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "title": title,
        "gap_type": gap_type,
        "description": description,
        "why_it_matters": "Matters for theory.",
        "supporting_synthesis_statement_ids": list(stmt_ids or []),
        "supporting_evidence_ids": list(ev_ids or []),
        "contradiction_statement_ids": list(contradiction_ids or []),
        "confidence": confidence,
        "scope": "within the reviewed corpus",
        "limitations": [],
        "evidence_strength": scores[0],
        "research_importance": scores[1],
        "theoretical_relevance": scores[2],
        "analytical_model_potential": scores[3],
        "tractability": scores[4],
        "model_domains": list(domains or []),
        "model_opportunity_rationale": "Model opportunity.",
    }


def _gap_case(
    case_id: str,
    name: str,
    description: str,
    statements: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    gaps_response: list[dict[str, Any]],
    reference: dict[str, Any],
    *,
    marker: str | None = None,
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "gap_analysis",
            "statements": statements,
            "evidence": evidence,
            "profiles": [
                {
                    "paper_identity_id": f"{case_id}-paper-0",
                    "evidence_ids": [f"{case_id}-evidence-{i}" for i in range(len(evidence))],
                }
            ],
            "themes": [{"title": "Theme", "statement_indexes": list(range(len(statements)))}],
            "llm_fixtures": [_gap_fixture(marker or statements[0]["statement"][:40], gaps_response)]
            if gaps_response
            else [],
            "gap_config": {"max_statements": 200, "max_gaps": 50, "max_model_calls": 20},
        },
        reference=reference,
        evaluation_dimensions=["gap_analysis"],
        tags=["gap-analysis", "offline"],
    )


RESEARCH_GAP_ANALYSIS_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="research-gap-analysis-v1",
    version=1,
    name="Research Gap Analysis",
    description=(
        "Offline benchmark over the real Phase 2H gap analyzer: fixture "
        "synthesis/evidence artifacts -> real GapAnalyzerService -> "
        "ResearchGap + GapAnalysis. Deterministic grounding, support counts, "
        "sweeping-claim normalization, ranking, and hallucination rejection."
    ),
    category="gap_analysis",
    config={"evaluators": ["evaluator.gap_analysis"]},
    cases=[
        _gap_case(
            "gap-contradiction",
            "contradiction gap",
            "Contradictory evidence across papers yields a contradiction gap "
            "and the ranked order follows the deterministic composite.",
            [
                {
                    "statement": "Within the reviewed corpus, algorithmic pricing lowers consumer welfare.",
                    "type": "consensus",
                    "evidence_ids": ["gap-contradiction-evidence-0"],
                    "paper_ids": ["gap-contradiction-paper-0"],
                    "conflicting_paper_ids": ["gap-contradiction-paper-1"],
                },
                {
                    "statement": "Within the reviewed corpus, algorithmic pricing raises consumer welfare.",
                    "type": "contradiction",
                    "evidence_ids": ["gap-contradiction-evidence-1"],
                    "paper_ids": ["gap-contradiction-paper-1"],
                },
            ],
            [
                {"statement": "Algorithmic pricing lowers consumer welfare.", "category": "result"},
                {"statement": "Algorithmic pricing raises consumer welfare.", "category": "result"},
            ],
            [
                _gap_item(
                    "Contradictory evidence on algorithmic pricing and welfare",
                    "contradiction_gap",
                    "Within the reviewed corpus, the evidence on algorithmic pricing and welfare is contradictory.",
                    stmt_ids=["gap-contradiction-statement-0", "gap-contradiction-statement-1"],
                    ev_ids=["gap-contradiction-evidence-0", "gap-contradiction-evidence-1"],
                    contradiction_ids=["gap-contradiction-statement-1"],
                    scores=(0.9, 0.9, 0.9, 0.9, 0.9),
                    domains=["pricing"],
                ),
                _gap_item(
                    "Limited integration of findings",
                    "integration_gap",
                    "Within the reviewed corpus, findings are only weakly integrated across settings.",
                    stmt_ids=["gap-contradiction-statement-0"],
                    ev_ids=["gap-contradiction-evidence-0"],
                    scores=(0.2, 0.2, 0.2, 0.2, 0.2),
                ),
            ],
            {
                "expected_gaps": {
                    "Contradictory evidence on algorithmic pricing and welfare": {
                        "gap_type": "contradiction_gap",
                        "supporting_papers": 2,
                        "supporting_evidence_items": 2,
                        "contradicting_papers": 1,
                    },
                    "Limited integration of findings": {
                        "gap_type": "integration_gap",
                        "supporting_papers": 1,
                        "supporting_evidence_items": 1,
                    },
                },
                "expected_rank_order": [
                    "Contradictory evidence on algorithmic pricing and welfare",
                    "Limited integration of findings",
                ],
                "expected_tentative": ["Limited integration of findings"],
                "expected_hallucinated": 0,
                "expected_unsupported": 0,
                "expected_sweeping": False,
            },
        ),
        _gap_case(
            "gap-mechanism",
            "mechanism gap",
            "A missing mechanism is identified from a theoretical pattern.",
            [
                {
                    "statement": "Within the reviewed corpus, the mechanism linking fees to entry is unstudied.",
                    "type": "theoretical_pattern",
                    "evidence_ids": ["gap-mechanism-evidence-0"],
                    "paper_ids": ["gap-mechanism-paper-0"],
                }
            ],
            [{"statement": "Fees affect platform entry.", "category": "mechanism"}],
            [
                _gap_item(
                    "Missing mechanism for fee effects on entry",
                    "mechanism_gap",
                    "Within the reviewed corpus, the mechanism behind fee effects on entry is not developed.",
                    stmt_ids=["gap-mechanism-statement-0"],
                    ev_ids=["gap-mechanism-evidence-0"],
                    domains=["platform behavior"],
                ),
            ],
            {
                "expected_gaps": {
                    "Missing mechanism for fee effects on entry": {
                        "gap_type": "mechanism_gap",
                        "supporting_papers": 1,
                        "supporting_evidence_items": 1,
                    }
                },
                "expected_tentative": ["Missing mechanism for fee effects on entry"],
                "expected_hallucinated": 0,
                "expected_unsupported": 0,
                "expected_sweeping": False,
            },
        ),
        _gap_case(
            "gap-context",
            "context gap",
            "A context gap is identified for an under-studied setting.",
            [
                {
                    "statement": "Within the reviewed corpus, gig work settings are largely absent from pricing studies.",
                    "type": "pattern",
                    "evidence_ids": ["gap-context-evidence-0"],
                    "paper_ids": ["gap-context-paper-0"],
                }
            ],
            [
                {
                    "statement": "Pricing studies focus on non-gig settings.",
                    "category": "boundary_condition",
                }
            ],
            [
                _gap_item(
                    "Context gap: gig work settings",
                    "context_gap",
                    "Within the reviewed corpus, gig work settings are largely absent from pricing studies.",
                    stmt_ids=["gap-context-statement-0"],
                    ev_ids=["gap-context-evidence-0"],
                ),
            ],
            {
                "expected_gaps": {
                    "Context gap: gig work settings": {
                        "gap_type": "context_gap",
                        "supporting_papers": 1,
                        "supporting_evidence_items": 1,
                    }
                },
                "expected_tentative": ["Context gap: gig work settings"],
                "expected_hallucinated": 0,
                "expected_unsupported": 0,
                "expected_sweeping": False,
            },
        ),
        _gap_case(
            "gap-boundary-condition",
            "boundary-condition gap",
            "An unexplored boundary condition is identified.",
            [
                {
                    "statement": "Within the reviewed corpus, boundary conditions of pricing effects are under-specified.",
                    "type": "boundary_condition",
                    "evidence_ids": ["gap-boundary-condition-evidence-0"],
                    "paper_ids": ["gap-boundary-condition-paper-0"],
                }
            ],
            [
                {
                    "statement": "Boundary conditions of pricing effects are under-specified.",
                    "category": "boundary_condition",
                }
            ],
            [
                _gap_item(
                    "Boundary conditions of pricing effects unexplored",
                    "boundary_condition_gap",
                    "Within the reviewed corpus, the boundary conditions of pricing effects remain under-specified.",
                    stmt_ids=["gap-boundary-condition-statement-0"],
                    ev_ids=["gap-boundary-condition-evidence-0"],
                ),
            ],
            {
                "expected_gaps": {
                    "Boundary conditions of pricing effects unexplored": {
                        "gap_type": "boundary_condition_gap",
                        "supporting_papers": 1,
                        "supporting_evidence_items": 1,
                    }
                },
                "expected_tentative": ["Boundary conditions of pricing effects unexplored"],
                "expected_hallucinated": 0,
                "expected_unsupported": 0,
                "expected_sweeping": False,
            },
        ),
        _gap_case(
            "gap-methodological",
            "methodological gap",
            "A methodological concentration is identified.",
            [
                {
                    "statement": "Within the reviewed corpus, pricing studies rely on a narrow set of methods.",
                    "type": "methodological_pattern",
                    "evidence_ids": ["gap-methodological-evidence-0"],
                    "paper_ids": ["gap-methodological-paper-0"],
                }
            ],
            [
                {
                    "statement": "Pricing studies rely on a narrow set of methods.",
                    "category": "method",
                }
            ],
            [
                _gap_item(
                    "Methodological concentration in pricing studies",
                    "methodological_gap",
                    "Within the reviewed corpus, pricing studies rely on a narrow set of methods.",
                    stmt_ids=["gap-methodological-statement-0"],
                    ev_ids=["gap-methodological-evidence-0"],
                ),
            ],
            {
                "expected_gaps": {
                    "Methodological concentration in pricing studies": {
                        "gap_type": "methodological_gap",
                        "supporting_papers": 1,
                        "supporting_evidence_items": 1,
                    }
                },
                "expected_tentative": ["Methodological concentration in pricing studies"],
                "expected_hallucinated": 0,
                "expected_unsupported": 0,
                "expected_sweeping": False,
            },
        ),
        _gap_case(
            "gap-repeated-limitation",
            "repeated limitation",
            "A limitation repeated across papers yields a strongly-supported gap.",
            [
                {
                    "statement": "Within the reviewed corpus, study one reports sample limitations.",
                    "type": "limitation_pattern",
                    "evidence_ids": ["gap-repeated-limitation-evidence-0"],
                    "paper_ids": ["gap-repeated-limitation-paper-0"],
                },
                {
                    "statement": "Within the reviewed corpus, study two reports the same sample limitation.",
                    "type": "limitation_pattern",
                    "evidence_ids": ["gap-repeated-limitation-evidence-1"],
                    "paper_ids": ["gap-repeated-limitation-paper-1"],
                },
            ],
            [
                {"statement": "Sample limitations in study one.", "category": "limitation"},
                {"statement": "Sample limitations in study two.", "category": "limitation"},
            ],
            [
                _gap_item(
                    "Repeated sample limitation",
                    "empirical_gap",
                    "Within the reviewed corpus, a recurring sample limitation constrains pricing evidence.",
                    stmt_ids=[
                        "gap-repeated-limitation-statement-0",
                        "gap-repeated-limitation-statement-1",
                    ],
                    ev_ids=[
                        "gap-repeated-limitation-evidence-0",
                        "gap-repeated-limitation-evidence-1",
                    ],
                    scores=(0.8, 0.8, 0.8, 0.8, 0.8),
                ),
            ],
            {
                "expected_gaps": {
                    "Repeated sample limitation": {
                        "gap_type": "empirical_gap",
                        "supporting_papers": 2,
                        "supporting_evidence_items": 2,
                    }
                },
                "expected_hallucinated": 0,
                "expected_unsupported": 0,
                "expected_sweeping": False,
            },
        ),
        _gap_case(
            "gap-weak-evidence-tentative",
            "weak evidence -> tentative gap",
            "Weak single-paper evidence yields a tentative gap.",
            [
                {
                    "statement": "Within the reviewed corpus, one study hints at subsidy effects on growth.",
                    "type": "pattern",
                    "evidence_ids": ["gap-weak-evidence-tentative-evidence-0"],
                    "paper_ids": ["gap-weak-evidence-tentative-paper-0"],
                }
            ],
            [{"statement": "Subsidies may affect growth.", "category": "finding"}],
            [
                _gap_item(
                    "Tentative evidence on subsidy effects",
                    "mechanism_gap",
                    "Within the reviewed corpus, the evidence on subsidy effects on growth is weak.",
                    stmt_ids=["gap-weak-evidence-tentative-statement-0"],
                    ev_ids=["gap-weak-evidence-tentative-evidence-0"],
                    confidence=0.5,
                    scores=(0.4, 0.4, 0.4, 0.4, 0.4),
                ),
            ],
            {
                "expected_gaps": {
                    "Tentative evidence on subsidy effects": {
                        "gap_type": "mechanism_gap",
                        "supporting_papers": 1,
                        "supporting_evidence_items": 1,
                    }
                },
                "expected_tentative": ["Tentative evidence on subsidy effects"],
                "expected_hallucinated": 0,
                "expected_unsupported": 0,
                "expected_sweeping": False,
            },
        ),
        _gap_case(
            "gap-unsupported-global-novelty",
            "unsupported/global novelty claim",
            "An unsupported gap with a global-novelty claim must be detected "
            "as unsupported (the description is normalized to corpus-bounded "
            "language by the analyzer).",
            [
                {
                    "statement": "Within the reviewed corpus, dynamic pricing appears in platform studies.",
                    "type": "consensus",
                    "evidence_ids": ["gap-global-evidence-0"],
                    "paper_ids": ["gap-global-paper-0"],
                }
            ],
            [{"statement": "Dynamic pricing appears in platform studies.", "category": "finding"}],
            [
                _gap_item(
                    "Global novelty claim without grounding",
                    "mechanism_gap",
                    "No research has studied dynamic pricing in streaming markets.",
                    scores=(0.6, 0.6, 0.6, 0.6, 0.6),
                ),
            ],
            {
                "expected_gaps": {},
                "expected_hallucinated": 0,
                "expected_unsupported": 0,
                "expected_sweeping": False,
            },
        ),
        _gap_case(
            "gap-hallucinated-id",
            "hallucinated evidence/synthesis ID",
            "A gap citing a nonexistent statement id must be rejected by the "
            "analyzer's deterministic grounding.",
            [
                {
                    "statement": "Within the reviewed corpus, entry barriers shape competition.",
                    "type": "consensus",
                    "evidence_ids": ["gap-hallucinated-id-evidence-0"],
                    "paper_ids": ["gap-hallucinated-id-paper-0"],
                }
            ],
            [{"statement": "Entry barriers shape competition.", "category": "finding"}],
            [
                _gap_item(
                    "Gap citing a ghost statement",
                    "mechanism_gap",
                    "Within the reviewed corpus, entry barriers remain under-theorized.",
                    stmt_ids=["gap-hallucinated-id-statement-0", "statement-ghost"],
                    ev_ids=["gap-hallucinated-id-evidence-0"],
                ),
            ],
            {
                "expected_gaps": {},
                "expected_hallucinated": 1,
                "expected_unsupported": 0,
                "expected_sweeping": False,
            },
        ),
        _gap_case(
            "gap-no-defensible-gap",
            "no defensible gap",
            "When no defensible gap exists, the analyzer must produce none.",
            [
                {
                    "statement": "Within the reviewed corpus, pricing effects are well covered.",
                    "type": "consensus",
                    "evidence_ids": ["gap-none-evidence-0"],
                    "paper_ids": ["gap-none-paper-0"],
                }
            ],
            [{"statement": "Pricing effects are well covered.", "category": "finding"}],
            [],
            {
                "expected_gaps": {},
                "expected_hallucinated": 0,
                "expected_unsupported": 0,
                "expected_sweeping": False,
            },
        ),
    ],
)

# ---------------------------------------------------------------------------
# mechanism-development-v1
# ---------------------------------------------------------------------------


def _selection_fixture(gap_id: str) -> dict[str, Any]:
    return {
        "match": "Choose ONE research gap to develop a theoretical mechanism for.",
        "response": {
            "selected_gap_id": gap_id,
            "selection_rationale": "fixture selection",
            "evidence_synthesis_basis": "fixture",
            "research_importance": 0.8,
            "theoretical_relevance": 0.8,
            "analytical_model_suitability": 0.8,
            "tractability": 0.8,
        },
    }


def _generation_fixture(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    clean = [{k: v for k, v in c.items() if not k.startswith("_")} for c in candidates]
    return {
        "match": "Develop structured candidate mechanisms for the selected research gap.",
        "response": {"candidates": clean},
    }


def _candidate_payload(
    name: str,
    *,
    basis: str = "literature_supported",
    source_ids: list[str] | None = None,
    support_ids: list[str] | None = None,
    causal: str = "Higher fees reduce entry, which lowers consumer surplus.",
    assumptions: list[str] | None = None,
    actors: list[str] | None = None,
    evaluation: dict[str, float] | None = None,
    _valid: bool = True,
) -> dict[str, Any]:
    payload = {
        "name": name,
        "description": f"Mechanism: {name}.",
        "actors": actors or ["platform", "seller"],
        "strategic_interactions": ["entry decision"],
        "information_structure": "sellers observe fees",
        "incentives": ["profit maximization"],
        "causal_logic": causal,
        "key_assumptions": assumptions or ["Sellers are profit-maximizing."],
        "expected_outcomes": ["lower entry"],
        "boundary_conditions": ["single market"],
        "literature_support_ids": list(support_ids or []),
        "grounding": [
            {
                "element": "fee effects on entry",
                "basis": basis,
                "source_ids": list(source_ids or []),
            }
        ],
        "analytical_model_potential": {
            "suitable": True,
            "domains": ["pricing"],
            "rationale": "Model opportunity.",
        },
        "evaluation": evaluation
        or {
            "gap_alignment": 0.8,
            "theoretical_coherence": 0.8,
            "novelty_within_reviewed_corpus": 0.5,
            "analytical_tractability": 0.8,
            "managerial_economic_relevance": 0.8,
            "is_relevance": 0.8,
        },
        "_valid": _valid,
    }
    return payload


def _critique_fixture(
    verdict: str,
    issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "match": "You are an independent, skeptical critic of theoretical mechanisms.",
        "response": {
            "overall_assessment": "fixture critique",
            "verdict": verdict,
            "issues": issues or [],
            "revision_recommendations": ["Address the issues."],
        },
    }


_REVISION_KEYS = {
    "name",
    "description",
    "actors",
    "strategic_interactions",
    "information_structure",
    "incentives",
    "causal_logic",
    "key_assumptions",
    "expected_outcomes",
    "boundary_conditions",
    "grounding",
    "analytical_model_potential",
    "evaluation",
}


def _revision_fixture(candidate: dict[str, Any], *, revised: bool = False) -> dict[str, Any]:
    payload = {k: v for k, v in candidate.items() if k in _REVISION_KEYS}
    if revised:
        payload = dict(payload)
        payload["description"] = candidate["description"] + " (revised per critique)"
        payload["revision_notes"] = ["Revised per critique."]
    else:
        payload["revision_notes"] = ["No substantive revision needed."]
    return {
        "match": "Revise the following mechanism candidate in response to its critique.",
        "response": payload,
    }


def _mechanism_case(
    case_id: str,
    name: str,
    description: str,
    gap: dict[str, Any],
    candidates: list[dict[str, Any]],
    critic_verdict: str = "keep",
    critic_issues: list[dict[str, Any]] | None = None,
    *,
    revised: bool = False,
    expected_candidates: dict[str, dict[str, Any]] | None = None,
    expected_invalid: int = 0,
    expected_critic_issues: dict[str, list[dict[str, Any]]] | None = None,
    expected_revision: dict[str, bool] | None = None,
) -> BenchmarkCaseDefinition:
    statement = {
        "statement": f"Within the reviewed corpus, fees shape entry decisions. {case_id}",
        "type": "consensus",
        "evidence_ids": [f"{case_id}-evidence-0"],
        "paper_ids": [f"{case_id}-paper-0"],
    }
    stmt_id = f"{case_id}-statement-0"
    ev_id = f"{case_id}-evidence-0"
    fixtures: list[dict[str, Any]] = [
        _selection_fixture(f"{case_id}-gap"),
        _generation_fixture(candidates),
        _critique_fixture(critic_verdict, critic_issues),
    ]
    # per-candidate revision fixtures keyed by the candidate name (the
    # revision prompt contains the original candidate's name)
    for candidate in candidates:
        if not candidate.get("_valid", True):
            continue
        fixture = _revision_fixture(candidate, revised=revised)
        fixture["match"] = candidate["name"]
        fixtures.append(fixture)
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "mechanism_development",
            "evidence": [{"statement": "Fees shape entry decisions.", "category": "mechanism"}],
            "statements": [statement],
            "gap": {
                "title": gap["title"],
                "gap_type": gap["gap_type"],
                "description": gap["description"],
                "statement_ids": [stmt_id],
                "evidence_ids": [ev_id],
            },
            "llm_fixtures": fixtures,
        },
        reference={
            "expected_candidates": expected_candidates
            or {c["name"]: {} for c in candidates if c.get("_valid", True)},
            "expected_invalid_candidates": expected_invalid,
            "expected_critic_issues": expected_critic_issues or {},
            "expected_revision": expected_revision
            or {c["name"]: revised for c in candidates if c.get("_valid", True)},
            "expected_unsupported_support": 0,
        },
        evaluation_dimensions=["mechanism"],
        tags=["mechanism", "offline"],
    )


MECHANISM_DEVELOPMENT_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="mechanism-development-v1",
    version=1,
    name="Mechanism Development",
    description=(
        "Offline benchmark over the real Phase 3A pipeline: fixture gap "
        "context -> real GapSelectionService (model + approval gate) -> real "
        "MechanismGeneratorService (deterministic candidate validation) -> "
        "real MechanismCriticService (critique + revision selection)."
    ),
    category="mechanism_development",
    config={"evaluators": ["evaluator.mechanism"]},
    cases=[
        _mechanism_case(
            "mech-multiple-plausible",
            "multiple plausible mechanisms",
            "Two plausible mechanisms (one literature-supported, one new "
            "hypothesis) both survive generation.",
            {
                "title": "Fee effects on entry",
                "gap_type": "mechanism_gap",
                "description": "Within the reviewed corpus, fee effects on entry are under-theorized.",
            },
            [
                _candidate_payload(
                    "Fee-channel mechanism",
                    source_ids=["mech-multiple-plausible-statement-0"],
                    support_ids=["mech-multiple-plausible-statement-0"],
                ),
                _candidate_payload("Reputation-channel mechanism", basis="new_hypothesis"),
            ],
        ),
        _mechanism_case(
            "mech-literature-supported",
            "literature-supported mechanism",
            "A fully literature-supported mechanism survives with the "
            "deterministic support counts.",
            {
                "title": "Fee effects on entry",
                "gap_type": "mechanism_gap",
                "description": "Within the reviewed corpus, fee effects on entry are under-theorized.",
            },
            [
                _candidate_payload(
                    "Entry-fee mechanism",
                    source_ids=["mech-literature-supported-statement-0"],
                    support_ids=["mech-literature-supported-statement-0"],
                ),
            ],
            expected_candidates={"Entry-fee mechanism": {"expected_support_papers": 1}},
        ),
        _mechanism_case(
            "mech-genuinely-new-hypothesis",
            "genuinely new hypothesis",
            "A new hypothesis stays labeled new_hypothesis without invented literature support.",
            {
                "title": "Fee effects on entry",
                "gap_type": "mechanism_gap",
                "description": "Within the reviewed corpus, fee effects on entry are under-theorized.",
            },
            [
                _candidate_payload("Attention-scarcity mechanism", basis="new_hypothesis"),
            ],
        ),
        _mechanism_case(
            "mech-modeling-assumption",
            "modeling assumption",
            "Modeling assumptions remain explicit in the candidate.",
            {
                "title": "Fee effects on entry",
                "gap_type": "mechanism_gap",
                "description": "Within the reviewed corpus, fee effects on entry are under-theorized.",
            },
            [
                _candidate_payload(
                    "Symmetric-information mechanism",
                    basis="modeling_assumption",
                    assumptions=["Sellers observe all fees."],
                ),
            ],
        ),
        _mechanism_case(
            "mech-hallucinated-literature-support",
            "hallucinated literature support",
            "A candidate citing a nonexistent evidence id is rejected while "
            "the valid candidate survives.",
            {
                "title": "Fee effects on entry",
                "gap_type": "mechanism_gap",
                "description": "Within the reviewed corpus, fee effects on entry are under-theorized.",
            },
            [
                _candidate_payload(
                    "Valid fee mechanism",
                    source_ids=["mech-hallucinated-literature-support-statement-0"],
                    support_ids=["mech-hallucinated-literature-support-statement-0"],
                ),
                _candidate_payload(
                    "Ghost-cited mechanism",
                    source_ids=["evidence-ghost"],
                    support_ids=["evidence-ghost"],
                    _valid=False,
                ),
            ],
            expected_invalid=1,
            expected_candidates={"Valid fee mechanism": {}},
        ),
        _mechanism_case(
            "mech-weak-gap-alignment",
            "weak gap alignment",
            "Weak gap alignment is a quality issue, not a validity failure.",
            {
                "title": "Fee effects on entry",
                "gap_type": "mechanism_gap",
                "description": "Within the reviewed corpus, fee effects on entry are under-theorized.",
            },
            [
                _candidate_payload(
                    "Tangential fee mechanism",
                    source_ids=["mech-weak-gap-alignment-statement-0"],
                    support_ids=["mech-weak-gap-alignment-statement-0"],
                    evaluation={
                        "gap_alignment": 0.3,
                        "theoretical_coherence": 0.7,
                        "novelty_within_reviewed_corpus": 0.5,
                        "analytical_tractability": 0.8,
                        "managerial_economic_relevance": 0.7,
                        "is_relevance": 0.7,
                    },
                ),
            ],
        ),
        _mechanism_case(
            "mech-incoherent-causal-direction",
            "incoherent causal direction",
            "The critic flags the causal direction and the revision fixes it.",
            {
                "title": "Fee effects on entry",
                "gap_type": "mechanism_gap",
                "description": "Within the reviewed corpus, fee effects on entry are under-theorized.",
            },
            [
                _candidate_payload(
                    "Reversed-causality mechanism",
                    source_ids=["mech-incoherent-causal-direction-statement-0"],
                    support_ids=["mech-incoherent-causal-direction-statement-0"],
                    causal="Entry reduces fees, which raises surplus.",
                ),
            ],
            critic_verdict="revise",
            critic_issues=[
                {
                    "category": "unclear_causal_direction",
                    "description": "The causal direction is unclear.",
                    "severity": "high",
                }
            ],
            revised=True,
            expected_critic_issues={
                "Reversed-causality mechanism": [
                    {"category": "unclear_causal_direction", "verdict": "revise"}
                ]
            },
            expected_revision={"Reversed-causality mechanism": True},
        ),
        _mechanism_case(
            "mech-missing-actor-incentive",
            "missing actor/incentive",
            "The critic flags a missing actor and the revision supplies it.",
            {
                "title": "Fee effects on entry",
                "gap_type": "mechanism_gap",
                "description": "Within the reviewed corpus, fee effects on entry are under-theorized.",
            },
            [
                _candidate_payload(
                    "Actorless fee mechanism",
                    source_ids=["mech-missing-actor-incentive-statement-0"],
                    support_ids=["mech-missing-actor-incentive-statement-0"],
                    actors=[],
                ),
            ],
            critic_verdict="revise",
            critic_issues=[
                {
                    "category": "missing_actor_or_incentive",
                    "description": "No actor is specified.",
                    "severity": "high",
                }
            ],
            revised=True,
            expected_critic_issues={
                "Actorless fee mechanism": [
                    {"category": "missing_actor_or_incentive", "verdict": "revise"}
                ]
            },
            expected_revision={"Actorless fee mechanism": True},
        ),
        _mechanism_case(
            "mech-critic-requests-revision",
            "critic requests revision",
            "A revise verdict leads to a revised selected mechanism with revision notes.",
            {
                "title": "Fee effects on entry",
                "gap_type": "mechanism_gap",
                "description": "Within the reviewed corpus, fee effects on entry are under-theorized.",
            },
            [
                _candidate_payload(
                    "Fee mechanism needing revision",
                    source_ids=["mech-critic-requests-revision-statement-0"],
                    support_ids=["mech-critic-requests-revision-statement-0"],
                ),
            ],
            critic_verdict="revise",
            critic_issues=[
                {
                    "category": "unsupported_assumption",
                    "description": "An assumption lacks support.",
                    "severity": "medium",
                }
            ],
            revised=True,
            expected_critic_issues={
                "Fee mechanism needing revision": [
                    {"category": "unsupported_assumption", "verdict": "revise"}
                ]
            },
            expected_revision={"Fee mechanism needing revision": True},
        ),
        _mechanism_case(
            "mech-invalid-rejected-valid-survives",
            "invalid candidate rejected while valid candidates survive",
            "The generator rejects an invalid candidate while the valid one survives selection.",
            {
                "title": "Fee effects on entry",
                "gap_type": "mechanism_gap",
                "description": "Within the reviewed corpus, fee effects on entry are under-theorized.",
            },
            [
                _candidate_payload(
                    "Surviving fee mechanism",
                    source_ids=["mech-invalid-rejected-valid-survives-statement-0"],
                    support_ids=["mech-invalid-rejected-valid-survives-statement-0"],
                ),
                _candidate_payload(
                    "Doomed mechanism",
                    source_ids=["evidence-ghost"],
                    support_ids=["evidence-ghost"],
                    _valid=False,
                ),
            ],
            expected_invalid=1,
            expected_candidates={"Surviving fee mechanism": {}},
        ),
    ],
)


# ---------------------------------------------------------------------------
# equilibrium-correctness-v1
# ---------------------------------------------------------------------------


def _eq_model(
    case_id: str,
    title: str,
    actors: list[dict[str, Any]],
    variables: list[dict[str, Any]],
    parameters: list[dict[str, Any]],
    timing: list[dict[str, Any]],
    payoffs: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "title": title,
        "description": f"Fixture model for {case_id}.",
        "actors": actors,
        "variables": variables,
        "parameters": parameters,
        "timing": timing,
        "payoffs": payoffs,
    }


def _eq_case(
    case_id: str,
    name: str,
    description: str,
    model: dict[str, Any],
    reference: dict[str, Any],
    *,
    llm_fixtures: list[dict[str, Any]] | None = None,
    max_revisions: int = 2,
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "equilibrium_derivation",
            "model": model,
            "llm_fixtures": llm_fixtures or [],
            "equilibrium_config": {"max_revisions": max_revisions, "max_llm_calls": 10},
        },
        reference=reference,
        evaluation_dimensions=["equilibrium"],
        tags=["equilibrium", "offline"],
    )


def _dv(symbol: str, owner: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "meaning": symbol,
        "domain": "R_+",
        "kind": "decision_variable",
        "owner_actor_id": owner,
    }


def _param(symbol: str) -> dict[str, Any]:
    return {"symbol": symbol, "meaning": symbol, "domain": "R_+"}


def _payoff(actor_id: str, expression: str, dvs: list[str], params: list[str]) -> dict[str, Any]:
    return {
        "actor_id": actor_id,
        "objective_type": "profit",
        "expression": expression,
        "decision_variables": dvs,
        "parameters": params,
    }


def _llm_candidate(variable: str, expression: str, symbols: list[str]) -> dict[str, Any]:
    return {
        "match": "Propose a candidate equilibrium for the following game.",
        "response": {
            "expressions": [
                {"variable": variable, "expression": expression, "symbols_used": symbols}
            ]
        },
    }


def _llm_revision(variable: str, expression: str, symbols: list[str]) -> dict[str, Any]:
    return {
        "match": "The following equilibrium candidate FAILED symbolic verification.",
        "response": {
            "expressions": [
                {"variable": variable, "expression": expression, "symbols_used": symbols}
            ]
        },
    }


EQUILIBRIUM_CORRECTNESS_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="equilibrium-correctness-v1",
    version=1,
    name="Equilibrium Correctness",
    description=(
        "Offline benchmark over the real Phase 3C pipeline: fixture "
        "FormalAnalyticalModel -> real EquilibriumDeriverService (SymPy "
        "derivation + symbolic verification + bounded LLM revision) with "
        "known closed-form answers. Symbolic equivalence via SymPy, never "
        "string equality."
    ),
    category="equilibrium_correctness",
    config={"evaluators": ["evaluator.equilibrium"]},
    cases=[
        _eq_case(
            "eq-monopoly-interior-optimum",
            "monopoly / interior optimum",
            "A single-actor interior optimum is derived and verified "
            "symbolically with no LLM calls.",
            _eq_model(
                "eq-monopoly-interior-optimum",
                "Monopoly",
                [{"id": "m1", "name": "Monopolist"}],
                [_dv("q", "m1")],
                [_param("a"), _param("c")],
                [{"stage_number": 0, "name": "move", "actor_ids": ["m1"]}],
                [_payoff("m1", "q*(a - q) - c*q", ["q"], ["a", "c"])],
            ),
            {
                "expected_solution": {"q": "(a-c)/2"},
                "expected_verification": "verified",
                "expected_status": "derived",
                "expected_method": "simultaneous",
            },
        ),
        _eq_case(
            "eq-cournot-simultaneous",
            "Cournot simultaneous game",
            "A two-firm simultaneous Cournot game yields q = (a-c)/3 for both firms.",
            _eq_model(
                "eq-cournot-simultaneous",
                "Cournot duopoly",
                [{"id": "f1", "name": "Firm 1"}, {"id": "f2", "name": "Firm 2"}],
                [_dv("q1", "f1"), _dv("q2", "f2")],
                [_param("a"), _param("c")],
                [{"stage_number": 0, "name": "simultaneous", "actor_ids": ["f1", "f2"]}],
                [
                    _payoff("f1", "q1*(a - q1 - q2) - c*q1", ["q1"], ["a", "c"]),
                    _payoff("f2", "q2*(a - q1 - q2) - c*q2", ["q2"], ["a", "c"]),
                ],
            ),
            {
                "expected_solution": {"q1": "(a-c)/3", "q2": "(a-c)/3"},
                "expected_verification": "verified",
                "expected_status": "derived",
                "expected_method": "simultaneous",
            },
        ),
        _eq_case(
            "eq-sequential-leader-follower",
            "sequential leader-follower game",
            "Backward induction yields leader (a-c)/2 and follower (a-c)/4 "
            "with the correct solution order.",
            _eq_model(
                "eq-sequential-leader-follower",
                "Stackelberg duopoly",
                [{"id": "leader", "name": "Leader"}, {"id": "follower", "name": "Follower"}],
                [_dv("x", "leader"), _dv("y", "follower")],
                [_param("a"), _param("c")],
                [
                    {"stage_number": 0, "name": "leader moves", "actor_ids": ["leader"]},
                    {"stage_number": 1, "name": "follower moves", "actor_ids": ["follower"]},
                ],
                [
                    _payoff("leader", "x*(a - x - y) - c*x", ["x"], ["a", "c"]),
                    _payoff("follower", "y*(a - x - y) - c*y", ["y"], ["a", "c"]),
                ],
            ),
            {
                "expected_solution": {"x": "(a-c)/2", "y": "(a-c)/4"},
                "expected_verification": "verified",
                "expected_status": "derived",
                "expected_method": "backward_induction",
                "expected_solution_order": ["follower", "leader"],
            },
        ),
        _eq_case(
            "eq-parameter-conditioned",
            "parameter-conditioned equilibrium",
            "A demand-slope parameter yields q* = (a-c)/(2b) with the "
            "denominator condition recorded.",
            _eq_model(
                "eq-parameter-conditioned",
                "Monopoly with slope",
                [{"id": "m1", "name": "Monopolist"}],
                [_dv("q", "m1")],
                [_param("a"), _param("c"), _param("b")],
                [{"stage_number": 0, "name": "move", "actor_ids": ["m1"]}],
                [_payoff("m1", "q*(a - b*q) - c*q", ["q"], ["a", "b", "c"])],
            ),
            {
                "expected_solution": {"q": "(a-c)/(2*b)"},
                "expected_conditions": ["2*b != 0"],
                "expected_verification": "partially_verified",
                "expected_status": "partially_derived",
            },
        ),
        _eq_case(
            "eq-incorrect-llm-candidate",
            "incorrect LLM candidate rejected",
            "An incorrect LLM-proposed candidate is rejected by the symbolic "
            "verifier (no revisions allowed).",
            _eq_model(
                "eq-incorrect-llm-candidate",
                "Transcendental model",
                [{"id": "m1", "name": "Monopolist"}],
                [_dv("q", "m1")],
                [_param("a"), _param("c")],
                [{"stage_number": 0, "name": "move", "actor_ids": ["m1"]}],
                [_payoff("m1", "a*exp(q)", ["q"], ["a"])],
            ),
            {
                "expected_status": "failed",
                "expected_verification": "failed",
                "expected_rejections": 1,
            },
            llm_fixtures=[_llm_candidate("q", "a", ["a"])],
            max_revisions=0,
        ),
        _eq_case(
            "eq-zero-payoff-unsolvable",
            "zero-payoff / unsolvable model",
            "A model with zero payoffs is detected as unsolvable by the "
            "deterministic solvability gate.",
            _eq_model(
                "eq-zero-payoff-unsolvable",
                "Zero-payoff model",
                [{"id": "m1", "name": "Monopolist"}],
                [_dv("q", "m1")],
                [_param("a"), _param("c")],
                [{"stage_number": 0, "name": "move", "actor_ids": ["m1"]}],
                [_payoff("m1", "0", ["q"], ["a", "c"])],
            ),
            {
                "expected_status": "failed",
                "expected_verification": None,
            },
        ),
        _eq_case(
            "eq-invalid-foc",
            "invalid FOC",
            "A candidate that violates the model FOC is rejected with the "
            "foc_residual check failing.",
            _eq_model(
                "eq-invalid-foc",
                "Exponential payoff model",
                [{"id": "m1", "name": "Monopolist"}],
                [_dv("q", "m1")],
                [_param("a")],
                [{"stage_number": 0, "name": "move", "actor_ids": ["m1"]}],
                [_payoff("m1", "a*exp(q)", ["q"], ["a"])],
            ),
            {
                "expected_status": "failed",
                "expected_verification": "failed",
                "expected_rejections": 1,
                "expected_foc_residual_rejected": True,
            },
            llm_fixtures=[_llm_candidate("q", "1", [])],
            max_revisions=0,
        ),
        _eq_case(
            "eq-partial-symbolic-verification",
            "partial symbolic verification",
            "An equilibrium whose second-order condition cannot be proven "
            "symbolically is partially verified.",
            _eq_model(
                "eq-partial-symbolic-verification",
                "Monopoly with quadratic cost",
                [{"id": "m1", "name": "Monopolist"}],
                [_dv("q", "m1")],
                [_param("a"), _param("c")],
                [{"stage_number": 0, "name": "move", "actor_ids": ["m1"]}],
                [_payoff("m1", "q*(a - q) - c*q**2", ["q"], ["a", "c"])],
            ),
            {
                "expected_solution": {"q": "a/(2*c + 2)"},
                "expected_conditions": ["2*c + 2 != 0"],
                "expected_verification": "partially_verified",
                "expected_status": "partially_derived",
            },
        ),
        _eq_case(
            "eq-bounded-revision",
            "bounded revision",
            "Repeated incorrect LLM candidates exhaust the bounded revision "
            "budget and all are rejected.",
            _eq_model(
                "eq-bounded-revision",
                "Transcendental model",
                [{"id": "m1", "name": "Monopolist"}],
                [_dv("q", "m1")],
                [_param("a"), _param("c")],
                [{"stage_number": 0, "name": "move", "actor_ids": ["m1"]}],
                [_payoff("m1", "a*exp(q)", ["q"], ["a"])],
            ),
            {
                "expected_status": "failed",
                "expected_verification": "failed",
                "expected_rejections": 3,
                "expected_revisions": 2,
            },
            llm_fixtures=[
                _llm_candidate("q", "a", ["a"]),
                _llm_revision("q", "a", ["a"]),
            ],
            max_revisions=2,
        ),
        _eq_case(
            "eq-denominator-positivity-conditions",
            "equilibrium with denominator/positivity conditions",
            "A candidate with a parameter denominator records the required non-zero condition.",
            _eq_model(
                "eq-denominator-positivity-conditions",
                "Monopoly with scaling demand",
                [{"id": "m1", "name": "Monopolist"}],
                [_dv("q", "m1")],
                [_param("a"), _param("c"), _param("b")],
                [{"stage_number": 0, "name": "move", "actor_ids": ["m1"]}],
                [_payoff("m1", "b*q*(a - q) - c*q", ["q"], ["a", "b", "c"])],
            ),
            {
                "expected_solution": {"q": "(a*b - c)/(2*b)"},
                "expected_conditions": ["2*b != 0"],
                "expected_verification": "partially_verified",
                "expected_status": "partially_derived",
            },
        ),
    ],
)

# ---------------------------------------------------------------------------
# numerical-analysis-v1
# ---------------------------------------------------------------------------


_COURNOT_MODEL = _eq_model(
    "num-cournot",
    "Cournot duopoly",
    [{"id": "f1", "name": "Firm 1"}, {"id": "f2", "name": "Firm 2"}],
    [_dv("q1", "f1"), _dv("q2", "f2")],
    [_param("a"), _param("c")],
    [{"stage_number": 0, "name": "simultaneous", "actor_ids": ["f1", "f2"]}],
    [
        _payoff("f1", "q1*(a - q1 - q2) - c*q1", ["q1"], ["a", "c"]),
        _payoff("f2", "q2*(a - q1 - q2) - c*q2", ["q2"], ["a", "c"]),
    ],
)

_COURNOT_CANDIDATE = {
    "expressions": [
        {"variable": "q1", "expression": "(a-c)/3", "symbols_used": ["a", "c"]},
        {"variable": "q2", "expression": "(a-c)/3", "symbols_used": ["a", "c"]},
    ],
    "decision_variables": ["q1", "q2"],
    "method": "simultaneous",
}

_CONDITIONED_MODEL = _eq_model(
    "num-conditioned",
    "Monopoly with slope",
    [{"id": "m1", "name": "Monopolist"}],
    [_dv("q", "m1")],
    [
        {"symbol": "a", "meaning": "a", "domain": "R_+"},
        {"symbol": "c", "meaning": "c", "domain": "R_+"},
        {"symbol": "b", "meaning": "b", "domain": "R"},
    ],
    [{"stage_number": 0, "name": "move", "actor_ids": ["m1"]}],
    [_payoff("m1", "q*(a - b*q) - c*q", ["q"], ["a", "b", "c"])],
)

_CONDITIONED_CANDIDATE = {
    "expressions": [
        {
            "variable": "q",
            "expression": "(a-c)/(2*b)",
            "symbols_used": ["a", "c", "b"],
            "conditions": ["2*b != 0"],
        }
    ],
    "decision_variables": ["q"],
    "method": "sympy_solved",
}


def _num_case(
    case_id: str,
    name: str,
    description: str,
    model: dict[str, Any],
    candidate: dict[str, Any],
    reference: dict[str, Any],
    *,
    propositions: list[dict[str, Any]] | None = None,
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "numerical_analysis",
            "model": model,
            "candidate": candidate,
            "propositions": propositions or [],
            "numerical_config": {"max_points": 10000, "artifact_point_threshold": 500},
        },
        reference=reference,
        evaluation_dimensions=["numerical"],
        tags=["numerical", "offline"],
    )


NUMERICAL_ANALYSIS_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="numerical-analysis-v1",
    version=1,
    name="Numerical Analysis",
    description=(
        "Offline benchmark over the real Phase 3E pipeline: fixture verified "
        "equilibrium -> real NumericalAnalysisService (deterministic sweeps, "
        "grids, feasibility/condition enforcement, robustness, welfare). "
        "Floating-point comparisons use deterministic tolerances."
    ),
    category="numerical_analysis",
    config={"evaluators": ["evaluator.numerical"]},
    cases=[
        _num_case(
            "num-baseline-evaluation",
            "baseline evaluation",
            "The baseline point evaluates the verified equilibrium at the "
            "default parameters (a=10, c=1 -> q=3).",
            _COURNOT_MODEL,
            _COURNOT_CANDIDATE,
            {"expected_baseline": {"q1": 3.0, "q2": 3.0}},
        ),
        _num_case(
            "num-1d-sweep",
            "1-D sweep",
            "The one-dimensional sweep on the demand intercept is monotonic with 7 points.",
            _COURNOT_MODEL,
            _COURNOT_CANDIDATE,
            {"expected_sweep": {"parameter": "a", "points": 7, "monotonic": True}},
        ),
        _num_case(
            "num-two-parameter-grid",
            "two-parameter grid",
            "The two-parameter grid produces 16 feasible points.",
            _COURNOT_MODEL,
            _COURNOT_CANDIDATE,
            {"expected_grid_points": 16},
        ),
        _num_case(
            "num-infeasible-domain-point",
            "infeasible domain point",
            "Probe points outside the outcome domain are classified infeasible, not fabricated.",
            _COURNOT_MODEL,
            _COURNOT_CANDIDATE,
            {
                "expected_infeasible_count": 2,
                "expected_infeasible_reasons": ["parameter a violates domain"],
            },
        ),
        _num_case(
            "num-violated-equilibrium-condition",
            "violated equilibrium condition",
            "A probe point that violates the equilibrium condition "
            "(2*b != 0 at b=0) is classified infeasible.",
            _CONDITIONED_MODEL,
            _CONDITIONED_CANDIDATE,
            {
                "expected_infeasible_reasons": ["equilibrium condition violated"],
                "expected_condition_violations": 1,
            },
        ),
        _num_case(
            "num-proposition-supported",
            "proposition numerically supported",
            "A monotonicity proposition (q1 increases in a) is numerically "
            "supported across admissible points.",
            _COURNOT_MODEL,
            _COURNOT_CANDIDATE,
            {"expected_propositions": {"q1 increases in the demand intercept": "supported"}},
            propositions=[
                {
                    "statement": "q1 increases in the demand intercept",
                    "claim_type": "monotonicity",
                    "outcome_variable": "q1",
                    "parameter": "a",
                    "expected_sign": "positive",
                    "conditions": ["a > c"],
                }
            ],
        ),
        _num_case(
            "num-proposition-violated",
            "proposition numerically violated",
            "A wrong-sign monotonicity proposition is flagged violated.",
            _COURNOT_MODEL,
            _COURNOT_CANDIDATE,
            {"expected_propositions": {"q1 decreases in the demand intercept": "violated"}},
            propositions=[
                {
                    "statement": "q1 decreases in the demand intercept",
                    "claim_type": "monotonicity",
                    "outcome_variable": "q1",
                    "parameter": "a",
                    "expected_sign": "negative",
                    "conditions": ["a > c"],
                }
            ],
        ),
        _num_case(
            "num-welfare-calculation",
            "welfare calculation",
            "Welfare is computed from the model payoffs at the baseline point (2 x 9 = 18).",
            _COURNOT_MODEL,
            _COURNOT_CANDIDATE,
            {"expected_welfare": {"metrics": 2, "total": 18.0}},
        ),
        _num_case(
            "num-deterministic-rerun",
            "deterministic rerun",
            "The experiment records a deterministic engine and seed and "
            "reproduces identical values.",
            _COURNOT_MODEL,
            _COURNOT_CANDIDATE,
            {
                "expected_baseline": {"q1": 3.0, "q2": 3.0},
                "expected_reproducible": True,
            },
        ),
    ],
)


def _cs_case(
    case_id: str,
    name: str,
    description: str,
    model: dict[str, Any],
    candidate: dict[str, Any],
    reference: dict[str, Any],
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "comparative_statics",
            "model": model,
            "candidate": candidate,
        },
        reference=reference,
        evaluation_dimensions=["comparative_statics"],
        tags=["comparative_statics", "offline"],
    )


def _cs_ref(
    statics: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {"expected_statics": statics}


def _cs_static(derivative: str, sign: str, conditions: list[str] | None = None) -> dict[str, Any]:
    return {
        "derivative": derivative,
        "sign": sign,
        "conditions": conditions or [],
    }


_COMPARATIVE_MONOPOLY_MODEL = _eq_model(
    "cs-monopoly",
    "Monopoly",
    [{"id": "m1", "name": "Monopolist"}],
    [_dv("q", "m1")],
    [_param("a"), _param("c")],
    [{"stage_number": 0, "name": "move", "actor_ids": ["m1"]}],
    [_payoff("m1", "q*(a - q) - c*q", ["q"], ["a", "c"])],
)

_COMPARATIVE_MONOPOLY_CANDIDATE = {
    "expressions": [{"variable": "q", "expression": "(a-c)/2", "symbols_used": ["a", "c"]}],
    "decision_variables": ["q"],
    "method": "simultaneous",
}

_COMPARATIVE_FIXED_COST_MODEL = _eq_model(
    "cs-fixed-cost",
    "Monopoly with fixed cost",
    [{"id": "m1", "name": "Monopolist"}],
    [_dv("q", "m1")],
    [_param("a"), _param("c")],
    [{"stage_number": 0, "name": "move", "actor_ids": ["m1"]}],
    [_payoff("m1", "q*(a - q) - c", ["q"], ["a", "c"])],
)

_COMPARATIVE_FIXED_COST_CANDIDATE = {
    "expressions": [{"variable": "q", "expression": "a/2", "symbols_used": ["a"]}],
    "decision_variables": ["q"],
    "method": "simultaneous",
}

_COMPARATIVE_SLOPE_MODEL = _eq_model(
    "cs-slope",
    "Monopoly with slope",
    [{"id": "m1", "name": "Monopolist"}],
    [_dv("q", "m1")],
    [
        {"symbol": "a", "meaning": "a", "domain": "R_+"},
        {"symbol": "c", "meaning": "c", "domain": "R_+"},
        {"symbol": "b", "meaning": "b", "domain": "R"},
    ],
    [{"stage_number": 0, "name": "move", "actor_ids": ["m1"]}],
    [_payoff("m1", "q*(a - b*q) - c*q", ["q"], ["a", "b", "c"])],
)

_COMPARATIVE_SLOPE_CANDIDATE = {
    "expressions": [
        {
            "variable": "q",
            "expression": "(a-c)/(2*b)",
            "symbols_used": ["a", "c", "b"],
            "conditions": ["2*b != 0"],
        }
    ],
    "decision_variables": ["q"],
    "method": "sympy_solved",
}

_COMPARATIVE_UNUSED_PARAM_MODEL = _eq_model(
    "cs-unused-param",
    "Monopoly without cost",
    [{"id": "m1", "name": "Monopolist"}],
    [_dv("q", "m1")],
    [_param("a"), _param("c")],
    [{"stage_number": 0, "name": "move", "actor_ids": ["m1"]}],
    [_payoff("m1", "q*(a - q)", ["q"], ["a"])],
)

_COMPARATIVE_UNUSED_PARAM_CANDIDATE = {
    "expressions": [{"variable": "q", "expression": "a/2", "symbols_used": ["a"]}],
    "decision_variables": ["q"],
    "method": "simultaneous",
}


COMPARATIVE_STATICS_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="comparative-statics-v1",
    version=1,
    name="Comparative Statics",
    description=(
        "Offline benchmark over the real Phase 3D ComparativeStaticsService: "
        "fixture verified equilibrium -> real SymPy comparative statics with "
        "known closed-form derivatives and signs. Symbolic equivalence via "
        "SymPy, never string equality."
    ),
    category="comparative_statics",
    config={"evaluators": ["evaluator.comparative_statics"]},
    cases=[
        _cs_case(
            "cs-positive-derivative",
            "positive derivative",
            "dq/da = 1/2 at q* = (a-c)/2 is derived with sign positive.",
            _COMPARATIVE_MONOPOLY_MODEL,
            _COMPARATIVE_MONOPOLY_CANDIDATE,
            _cs_ref({"q/a": _cs_static("1/2", "positive")}),
        ),
        _cs_case(
            "cs-negative-derivative",
            "negative derivative",
            "dq/dc = -1/2 at q* = (a-c)/2 is derived with sign negative.",
            _COMPARATIVE_MONOPOLY_MODEL,
            _COMPARATIVE_MONOPOLY_CANDIDATE,
            _cs_ref({"q/c": _cs_static("-1/2", "negative")}),
        ),
        _cs_case(
            "cs-zero-derivative",
            "zero derivative",
            "A fixed cost drops out of q* = a/2, so dq/dc = 0 with sign zero.",
            _COMPARATIVE_FIXED_COST_MODEL,
            _COMPARATIVE_FIXED_COST_CANDIDATE,
            _cs_ref({"q/c": _cs_static("0", "zero")}),
        ),
        _cs_case(
            "cs-ambiguous-sign",
            "ambiguous sign",
            "dq/da = 1/(2*b) has sign depending on b; production must record "
            "the ambiguity rather than assert a definite sign.",
            _COMPARATIVE_SLOPE_MODEL,
            _COMPARATIVE_SLOPE_CANDIDATE,
            _cs_ref({"q/a": _cs_static("1/(2*b)", "ambiguous", ["sign of da depends on: b"])}),
        ),
        _cs_case(
            "cs-conditions-recorded",
            "derivative requiring conditions",
            "dq/db = -(a-c)/(2*b**2) requires the sign of (a-c); the condition "
            "is recorded explicitly.",
            _COMPARATIVE_SLOPE_MODEL,
            _COMPARATIVE_SLOPE_CANDIDATE,
            _cs_ref(
                {
                    "q/b": _cs_static(
                        "-(a-c)/(2*b**2)",
                        "ambiguous",
                        ["sign of db depends on: a, b, c"],
                    )
                }
            ),
        ),
        _cs_case(
            "cs-multiple-outcomes-parameters",
            "multiple outcomes and parameters",
            "The Cournot equilibrium yields four statics (2 outcomes x 2 "
            "parameters) with the correct signs.",
            _COURNOT_MODEL,
            _COURNOT_CANDIDATE,
            _cs_ref(
                {
                    "q1/a": _cs_static("1/3", "positive"),
                    "q1/c": _cs_static("-1/3", "negative"),
                    "q2/a": _cs_static("1/3", "positive"),
                    "q2/c": _cs_static("-1/3", "negative"),
                }
            ),
        ),
        _cs_case(
            "cs-incorrect-expected-derivative",
            "incorrect expected derivative",
            "The reference asserts a WRONG derivative (dq/da = 2); the "
            "evaluator must fail the case on symbolic mismatch. Fails by "
            "design.",
            _COMPARATIVE_MONOPOLY_MODEL,
            _COMPARATIVE_MONOPOLY_CANDIDATE,
            _cs_ref({"q/a": _cs_static("2", "positive")}),
        ),
        _cs_case(
            "cs-unused-parameter",
            "unused parameter",
            "A model parameter absent from the payoffs yields dq/dc = 0 with "
            "sign zero; dq/da = 1/2 remains positive.",
            _COMPARATIVE_UNUSED_PARAM_MODEL,
            _COMPARATIVE_UNUSED_PARAM_CANDIDATE,
            _cs_ref(
                {
                    "q/a": _cs_static("1/2", "positive"),
                    "q/c": _cs_static("0", "zero"),
                }
            ),
        ),
    ],
)

# ---------------------------------------------------------------------------
# proposition-correctness-v1 (Phase 6F): real Phase 3D generator/verifier
# ---------------------------------------------------------------------------


def _prop_case(
    case_id: str,
    name: str,
    description: str,
    model: dict[str, Any],
    candidate: dict[str, Any],
    expected_propositions: list[dict[str, Any]],
    llm_fixtures: list[dict[str, Any]],
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "proposition_generation",
            "model": model,
            "candidate": candidate,
            "llm_fixtures": llm_fixtures,
        },
        reference={"expected_propositions": expected_propositions},
        evaluation_dimensions=["proposition"],
        tags=["proposition", "offline"],
    )


def _prop_item(
    statement: str,
    *,
    claim_type: str = "monotonicity",
    outcome_variable: str | None = None,
    parameter: str | None = None,
    expected_sign: str | None = None,
    mathematical_form: str | None = None,
    conditions: list[str] | None = None,
    supporting_static_ids: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "statement": statement,
        "claim_type": claim_type,
        "outcome_variable": outcome_variable,
        "parameter": parameter,
        "expected_sign": expected_sign,
        "mathematical_form": mathematical_form,
        "conditions": conditions or [],
        "supporting_static_ids": supporting_static_ids or [],
    }


def _prop_fixtures(
    case_id: str,
    items: list[dict[str, Any]],
    *,
    critique: bool = True,
) -> list[dict[str, Any]]:
    fixtures: list[dict[str, Any]] = [
        {
            "match": "Propose testable propositions grounded in the verified equilibrium.",
            "response": {"propositions": items},
        }
    ]
    if critique:
        fixtures.append(
            {
                "match": "Critique the following research proposition.",
                "response": {
                    "overall_assessment": "fixture critique",
                    "verdict": "keep",
                    "recommendations": ["fixture"],
                    "issues": [],
                },
            }
        )
        fixtures.append(
            {
                "match": "Write the economic/IS interpretation of the following verified proposition.",
                "response": {
                    "mathematical_result": "fixture result",
                    "economic_interpretation": "fixture interpretation",
                    "managerial_implication": "fixture implication",
                    "is_theoretical_implication": "fixture theory",
                    "consistency_note": "fixture note",
                },
            }
        )
    return fixtures


def _prop_expectation(
    *,
    claim_type: str,
    expected_verification: str,
    outcome_variable: str | None = None,
    parameter: str | None = None,
    expected_sign: str | None = None,
    expected_conditions: list[str] | None = None,
    expected_equality: bool | None = None,
    expected_rejected: bool = False,
) -> dict[str, Any]:
    return {
        "claim_type": claim_type,
        "outcome_variable": outcome_variable,
        "parameter": parameter,
        "expected_sign": expected_sign,
        "expected_verification": expected_verification,
        "expected_conditions": expected_conditions or [],
        "expected_equality": expected_equality,
        "expected_rejected": expected_rejected,
    }


def _prop_static_ids(case_id: str, *indices: int) -> list[str]:
    return [f"{case_id}-static-{i}" for i in indices]


PROPOSITION_CORRECTNESS_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="proposition-correctness-v1",
    version=1,
    name="Proposition Correctness",
    description=(
        "Offline benchmark over the real Phase 3D proposition pipeline: "
        "fixture verified equilibrium -> real ComparativeStaticsService -> "
        "real PropositionGeneratorService + PropositionVerifierService + "
        "PropositionCriticService (scripted responses) with known-answer "
        "verification outcomes. Symbolic equivalence via SymPy, never string "
        "equality."
    ),
    category="proposition_correctness",
    config={"evaluators": ["evaluator.proposition"]},
    cases=[
        _prop_case(
            "prop-positive-monotonicity",
            "correct positive monotonicity",
            "q1 increases in the demand intercept a; the proposition is "
            "verified against the positive comparative static.",
            _COURNOT_MODEL,
            _COURNOT_CANDIDATE,
            [
                _prop_expectation(
                    claim_type="monotonicity",
                    outcome_variable="q1",
                    parameter="a",
                    expected_sign="positive",
                    expected_verification="verified",
                )
            ],
            _prop_fixtures(
                "prop-positive-monotonicity",
                [
                    _prop_item(
                        "Increasing the demand intercept a raises firm 1's equilibrium quantity.",
                        outcome_variable="q1",
                        parameter="a",
                        expected_sign="positive",
                        supporting_static_ids=_prop_static_ids("prop-positive-monotonicity", 0),
                    )
                ],
            ),
        ),
        _prop_case(
            "prop-negative-monotonicity",
            "correct negative monotonicity",
            "q1 decreases in the marginal cost c; the proposition is verified.",
            _COURNOT_MODEL,
            _COURNOT_CANDIDATE,
            [
                _prop_expectation(
                    claim_type="monotonicity",
                    outcome_variable="q1",
                    parameter="c",
                    expected_sign="negative",
                    expected_verification="verified",
                )
            ],
            _prop_fixtures(
                "prop-negative-monotonicity",
                [
                    _prop_item(
                        "Increasing the marginal cost c lowers firm 1's equilibrium quantity.",
                        outcome_variable="q1",
                        parameter="c",
                        expected_sign="negative",
                        supporting_static_ids=_prop_static_ids("prop-negative-monotonicity", 1),
                    )
                ],
            ),
        ),
        _prop_case(
            "prop-zero-effect",
            "zero-effect proposition",
            "A fixed cost drops out of q* = a/2; the zero-effect proposition is verified.",
            _COMPARATIVE_FIXED_COST_MODEL,
            _COMPARATIVE_FIXED_COST_CANDIDATE,
            [
                _prop_expectation(
                    claim_type="monotonicity",
                    outcome_variable="q",
                    parameter="c",
                    expected_sign="zero",
                    expected_verification="verified",
                )
            ],
            _prop_fixtures(
                "prop-zero-effect",
                [
                    _prop_item(
                        "The fixed cost c has no effect on the equilibrium quantity.",
                        outcome_variable="q",
                        parameter="c",
                        expected_sign="zero",
                        supporting_static_ids=_prop_static_ids("prop-zero-effect", 1),
                    )
                ],
            ),
        ),
        _prop_case(
            "prop-conditional",
            "conditional proposition",
            "q increases in a only when b > 0; the proposition declares the "
            "condition and is conditionally verified.",
            _COMPARATIVE_SLOPE_MODEL,
            _COMPARATIVE_SLOPE_CANDIDATE,
            [
                _prop_expectation(
                    claim_type="monotonicity",
                    outcome_variable="q",
                    parameter="a",
                    expected_sign="positive",
                    expected_verification="conditionally_verified",
                    expected_conditions=["b > 0"],
                )
            ],
            _prop_fixtures(
                "prop-conditional",
                [
                    _prop_item(
                        "The equilibrium quantity increases in the demand "
                        "intercept a when the slope parameter b is positive.",
                        outcome_variable="q",
                        parameter="a",
                        expected_sign="positive",
                        conditions=["b > 0"],
                        supporting_static_ids=_prop_static_ids("prop-conditional", 0),
                    )
                ],
            ),
        ),
        _prop_case(
            "prop-wrong-sign-rejected",
            "wrong-sign proposition rejected",
            "A proposition claiming q1 decreases in a contradicts the positive "
            "comparative static and is rejected by the symbolic verifier.",
            _COURNOT_MODEL,
            _COURNOT_CANDIDATE,
            [
                _prop_expectation(
                    claim_type="monotonicity",
                    outcome_variable="q1",
                    parameter="a",
                    expected_sign="negative",
                    expected_verification="failed",
                    expected_rejected=True,
                )
            ],
            _prop_fixtures(
                "prop-wrong-sign-rejected",
                [
                    _prop_item(
                        "Increasing the demand intercept a lowers firm 1's equilibrium quantity.",
                        outcome_variable="q1",
                        parameter="a",
                        expected_sign="negative",
                        supporting_static_ids=_prop_static_ids("prop-wrong-sign-rejected", 0),
                    )
                ],
                critique=False,
            ),
        ),
        _prop_case(
            "prop-missing-condition-rejected",
            "missing-condition proposition rejected",
            "A monotonicity proposition on an ambiguous comparative static "
            "without the required conditions is rejected.",
            _COMPARATIVE_SLOPE_MODEL,
            _COMPARATIVE_SLOPE_CANDIDATE,
            [
                _prop_expectation(
                    claim_type="monotonicity",
                    outcome_variable="q",
                    parameter="a",
                    expected_sign="positive",
                    expected_verification="failed",
                    expected_rejected=True,
                )
            ],
            _prop_fixtures(
                "prop-missing-condition-rejected",
                [
                    _prop_item(
                        "The equilibrium quantity increases in the demand intercept a.",
                        outcome_variable="q",
                        parameter="a",
                        expected_sign="positive",
                        supporting_static_ids=_prop_static_ids(
                            "prop-missing-condition-rejected", 0
                        ),
                    )
                ],
                critique=False,
            ),
        ),
        _prop_case(
            "prop-valid-equality",
            "valid equilibrium equality",
            "The symmetric Cournot equilibrium satisfies q1 = q2; the equality "
            "proposition is verified.",
            _COURNOT_MODEL,
            _COURNOT_CANDIDATE,
            [
                _prop_expectation(
                    claim_type="equality",
                    expected_verification="verified",
                    expected_equality=True,
                )
            ],
            _prop_fixtures(
                "prop-valid-equality",
                [
                    _prop_item(
                        "Both firms produce the same equilibrium quantity.",
                        claim_type="equality",
                        mathematical_form="q1 = q2",
                        supporting_static_ids=_prop_static_ids("prop-valid-equality", 0),
                    )
                ],
            ),
        ),
        _prop_case(
            "prop-invalid-equality",
            "invalid equality",
            "q1 = 2*q2 does not hold at the symmetric equilibrium; the proposition is rejected.",
            _COURNOT_MODEL,
            _COURNOT_CANDIDATE,
            [
                _prop_expectation(
                    claim_type="equality",
                    expected_verification="failed",
                    expected_equality=False,
                    expected_rejected=True,
                )
            ],
            _prop_fixtures(
                "prop-invalid-equality",
                [
                    _prop_item(
                        "Firm 1 produces twice firm 2's equilibrium quantity.",
                        claim_type="equality",
                        mathematical_form="q1 = 2*q2",
                        supporting_static_ids=_prop_static_ids("prop-invalid-equality", 0),
                    )
                ],
                critique=False,
            ),
        ),
        _prop_case(
            "prop-hallucinated-support",
            "hallucinated comparative static ID",
            "A proposition citing a nonexistent comparative static id is "
            "rejected on equilibrium consistency.",
            _COURNOT_MODEL,
            _COURNOT_CANDIDATE,
            [
                _prop_expectation(
                    claim_type="monotonicity",
                    outcome_variable="q1",
                    parameter="a",
                    expected_sign="positive",
                    expected_verification="failed",
                    expected_rejected=True,
                )
            ],
            _prop_fixtures(
                "prop-hallucinated-support",
                [
                    _prop_item(
                        "Increasing the demand intercept a raises firm 1's equilibrium quantity.",
                        outcome_variable="q1",
                        parameter="a",
                        expected_sign="positive",
                        supporting_static_ids=["prop-hallucinated-support-static-ghost"],
                    )
                ],
                critique=False,
            ),
        ),
        _prop_case(
            "prop-unsupported-threshold",
            "unsupported threshold proposition",
            "A threshold claim is not supported by the production verifier and is rejected.",
            _COURNOT_MODEL,
            _COURNOT_CANDIDATE,
            [
                _prop_expectation(
                    claim_type="threshold",
                    expected_verification="failed",
                    expected_rejected=True,
                )
            ],
            _prop_fixtures(
                "prop-unsupported-threshold",
                [
                    _prop_item(
                        "Firm 1's quantity exceeds firm 2's when the demand "
                        "intercept is sufficiently large.",
                        claim_type="threshold",
                        supporting_static_ids=_prop_static_ids("prop-unsupported-threshold", 0),
                    )
                ],
                critique=False,
            ),
        ),
    ],
)

# ---------------------------------------------------------------------------
# results-assembly-v1 (Phase 6G): real Phase 4A ResultsAssemblerService +
# ResultsCriticService
# ---------------------------------------------------------------------------

_RES_GAP = {
    "title": "Within the reviewed corpus, competition in digital platform "
    "markets remains under-theorized.",
    "gap_type": "mechanism_gap",
}

_RES_MECHANISM = {
    "name": "Price competition",
    "description": "Fixtures compete on price; entry barriers shape intensity.",
    "causal_logic": "Lower prices reduce margins; entry barriers soften rivalry.",
}


def _res_case(
    case_id: str,
    name: str,
    description: str,
    *,
    propositions: list[dict[str, Any]] | None = None,
    statics: list[dict[str, Any]] | None = None,
    numerical_results: list[dict[str, Any]] | None = None,
    robustness: list[dict[str, Any]] | None = None,
    llm_fixtures: list[dict[str, Any]],
    reference: dict[str, Any],
    model: dict[str, Any] | None = None,
    candidate: dict[str, Any] | None = None,
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "results_assembly",
            "model": model or _COURNOT_MODEL,
            "candidate": candidate or _COURNOT_CANDIDATE,
            "gap": _RES_GAP,
            "mechanism": _RES_MECHANISM,
            "propositions": propositions or [],
            "statics": statics or [],
            "numerical_results": numerical_results or [],
            "robustness": robustness or [],
            "llm_fixtures": llm_fixtures,
        },
        reference=reference,
        evaluation_dimensions=["results"],
        tags=["results", "offline"],
    )


def _res_prop(
    statement: str,
    *,
    outcome_variable: str,
    parameter: str,
    expected_sign: str,
    verification: str = "verified",
    conditions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "statement": statement,
        "claim_type": "monotonicity",
        "outcome_variable": outcome_variable,
        "parameter": parameter,
        "expected_sign": expected_sign,
        "verification": verification,
        "conditions": conditions or [],
    }


def _res_static(
    outcome_variable: str,
    parameter: str,
    derivative: str,
    sign: str,
    conditions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "outcome_variable": outcome_variable,
        "parameter": parameter,
        "derivative": derivative,
        "sign": sign,
        "conditions": conditions or [],
    }


def _res_result(outcomes: dict[str, float], **extra: Any) -> dict[str, Any]:
    return {"outcomes": outcomes, **extra}


def _res_finding(
    statement: str,
    *,
    prop_ids: list[str] | None = None,
    static_ids: list[str] | None = None,
    result_ids: list[str] | None = None,
    conditions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "statement": statement,
        "finding_type": "analytical_result",
        "supporting_proposition_ids": prop_ids or [],
        "supporting_comparative_static_ids": static_ids or [],
        "supporting_numerical_result_ids": result_ids or [],
        "conditions": conditions or [],
    }


def _res_assembly_response(
    findings: list[dict[str, Any]],
    contributions: list[dict[str, Any]],
    implications: list[dict[str, Any]],
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "findings": findings,
        "contributions": contributions,
        "implications": implications,
        "limitations": limitations or [],
    }


def _res_contribution(
    claim: str, finding_refs: list[str], contribution_type: str = "theoretical"
) -> dict[str, Any]:
    return {
        "claim": claim,
        "contribution_type": contribution_type,
        "finding_ids": finding_refs,
        "advances_literature": "Fixture corpus-bounded advance.",
    }


def _res_implication(
    text: str,
    finding_refs: list[str],
    claim_type: str = "interpretation",
    implication_kind: str = "theory",
) -> dict[str, Any]:
    return {
        "text": text,
        "implication_kind": implication_kind,
        "claim_type": claim_type,
        "grounded_in_finding_ids": finding_refs,
    }


def _res_reference(
    *,
    expected_novelty_normalized: int = 0,
    expected_critique_categories: list[str] | None = None,
    expected_unsupported: int = 0,
) -> dict[str, Any]:
    return {
        "expected_novelty_normalized": expected_novelty_normalized,
        "expected_critique_categories": expected_critique_categories or [],
        "expected_unsupported": expected_unsupported,
    }


def _res_assembly_fixture(
    case_id: str,
    response: dict[str, Any],
    *,
    retry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    fixtures: list[dict[str, Any]] = []
    if retry is not None:
        fixtures.append(
            {
                "match": "REJECTED by deterministic validation",
                "response": retry,
            }
        )
    fixtures.append(
        {
            "match": "Assemble findings, contribution claims, and implications from verified results.",
            "response": response,
        }
    )
    return fixtures


def _res_critic_fixture(
    *, verdict: str = "approve", issues: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "match": "Critique the following assembled research results package.",
        "response": {
            "overall_assessment": "fixture assessment",
            "verdict": verdict,
            "recommendations": ["fixture"],
            "issues": issues or [],
        },
    }


_RES_PROP_A = _res_prop(
    "Firm 1's equilibrium quantity increases in the demand intercept a.",
    outcome_variable="q1",
    parameter="a",
    expected_sign="positive",
)
_RES_PROP_C = _res_prop(
    "Firm 1's equilibrium quantity decreases in the marginal cost c.",
    outcome_variable="q1",
    parameter="c",
    expected_sign="negative",
)
_PROP_POSITIVE = _RES_PROP_A
_RES_STATIC_A = _res_static("q1", "a", "1/3", "positive")
_RES_STATIC_C = _res_static("q1", "c", "-1/3", "negative")


def _res_clean_assembly(case_id: str) -> dict[str, Any]:
    return _res_assembly_response(
        findings=[
            _res_finding(
                "Firm 1's equilibrium quantity increases in the demand intercept.",
                prop_ids=[f"{case_id}-prop-0"],
            )
        ],
        contributions=[
            _res_contribution(
                "Modeling entry barriers explains platform pricing intensity.",
                ["FINDING0"],
            )
        ],
        implications=[
            _res_implication(
                "Platforms facing stronger entry barriers price higher.",
                ["FINDING0"],
            )
        ],
        limitations=["Fixture limitation: linear demand."],
    )


RESULTS_ASSEMBLY_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="results-assembly-v1",
    version=1,
    name="Results Assembly",
    description=(
        "Offline benchmark over the real Phase 4A pipeline: fixture verified "
        "Phase 3 outputs -> real ResultsAssemblerService (deterministic "
        "validation rejects failed-proposition support, unsupported ids, and "
        "dropped conditions; normalizes global-novelty claims) -> real "
        "ResultsCriticService. Ground truth is recomputed by the evaluator "
        "from the produced artifacts."
    ),
    category="results_assembly",
    config={"evaluators": ["evaluator.results_grounding"]},
    cases=[
        _res_case(
            "res-grounded-analytical-finding",
            "correctly grounded analytical finding",
            "A finding citing a verified proposition is assembled with its support intact.",
            propositions=[_RES_PROP_A],
            statics=[_RES_STATIC_A],
            llm_fixtures=[
                *_res_assembly_fixture(
                    "res-grounded-analytical-finding",
                    _res_clean_assembly("res-grounded-analytical-finding"),
                ),
                _res_critic_fixture(),
            ],
            reference=_res_reference(),
        ),
        _res_case(
            "res-conditional-conditions-preserved",
            "conditional proposition with conditions preserved",
            "A finding citing a conditionally verified proposition carries its "
            "required conditions verbatim.",
            model=_COMPARATIVE_SLOPE_MODEL,
            candidate=_COMPARATIVE_SLOPE_CANDIDATE,
            propositions=[
                _res_prop(
                    "The equilibrium quantity increases in the demand intercept "
                    "a when the slope parameter b is positive.",
                    outcome_variable="q",
                    parameter="a",
                    expected_sign="positive",
                    verification="conditionally_verified",
                    conditions=["b > 0"],
                )
            ],
            statics=[_res_static("q", "a", "1/(2*b)", "ambiguous", ["sign of da depends on: b"])],
            llm_fixtures=[
                *_res_assembly_fixture(
                    "res-conditional-conditions-preserved",
                    _res_assembly_response(
                        findings=[
                            _res_finding(
                                "The equilibrium quantity increases in the demand "
                                "intercept when the slope is positive.",
                                prop_ids=["res-conditional-conditions-preserved-prop-0"],
                                conditions=["b > 0"],
                            )
                        ],
                        contributions=[
                            _res_contribution(
                                "A positive demand slope preserves the monotonicity.",
                                ["FINDING0"],
                            )
                        ],
                        implications=[
                            _res_implication(
                                "Demand growth is expansionary under positive slopes.",
                                ["FINDING0"],
                            )
                        ],
                        limitations=["Fixture limitation: linear demand."],
                    ),
                ),
                _res_critic_fixture(),
            ],
            reference=_res_reference(),
        ),
        _res_case(
            "res-numerical-robustness-support",
            "numerical robustness support",
            "A finding citing a numerical experiment result is assembled.",
            propositions=[_RES_PROP_A],
            statics=[_RES_STATIC_A],
            numerical_results=[_res_result({"q1": 3.0, "q2": 3.0}, scenario="baseline")],
            llm_fixtures=[
                *_res_assembly_fixture(
                    "res-numerical-robustness-support",
                    _res_assembly_response(
                        findings=[
                            _res_finding(
                                "The baseline experiment evaluates both firms at three units.",
                                result_ids=["res-numerical-robustness-support-result-0"],
                            )
                        ],
                        contributions=[
                            _res_contribution(
                                "Numerical evaluation corroborates the analytical result.",
                                ["FINDING0"],
                                contribution_type="analytical",
                            )
                        ],
                        implications=[
                            _res_implication(
                                "Baseline quantities are symmetric.",
                                ["FINDING0"],
                            )
                        ],
                        limitations=["Fixture limitation: linear demand."],
                    ),
                ),
                _res_critic_fixture(),
            ],
            reference=_res_reference(),
        ),
        _res_case(
            "res-symbolic-numerical-contradiction",
            "symbolic/numerical contradiction surfaced",
            "A finding citing a proposition whose robustness check is violated "
            "is persisted, and the critic surfaces the contradiction "
            "deterministically.",
            propositions=[_RES_PROP_A],
            statics=[_RES_STATIC_A],
            robustness=[
                {
                    "proposition_id": "res-symbolic-numerical-contradiction-prop-0",
                    "outcome": "violated",
                }
            ],
            llm_fixtures=[
                *_res_assembly_fixture(
                    "res-symbolic-numerical-contradiction",
                    _res_clean_assembly("res-symbolic-numerical-contradiction"),
                ),
                _res_critic_fixture(),
            ],
            reference=_res_reference(
                expected_critique_categories=["symbolic_numerical_contradiction"]
            ),
        ),
        _res_case(
            "res-failed-proposition-rejected",
            "failed proposition rejected as support",
            "A finding citing a failed proposition is rejected by deterministic "
            "validation and retried with a verified proposition.",
            propositions=[_RES_PROP_A, _RES_PROP_C],
            statics=[_RES_STATIC_A, _RES_STATIC_C],
            llm_fixtures=[
                *_res_assembly_fixture(
                    "res-failed-proposition-rejected",
                    _res_assembly_response(
                        findings=[
                            _res_finding(
                                "Firm 1's equilibrium quantity decreases in the marginal cost.",
                                prop_ids=["res-failed-proposition-rejected-prop-1"],
                            )
                        ],
                        contributions=[
                            _res_contribution(
                                "Cost pass-through shapes platform pricing.",
                                ["FINDING0"],
                            )
                        ],
                        implications=[
                            _res_implication(
                                "Higher costs contract platform output.",
                                ["FINDING0"],
                            )
                        ],
                        limitations=["Fixture limitation: linear demand."],
                    ),
                    retry=_res_clean_assembly("res-failed-proposition-rejected"),
                ),
                _res_critic_fixture(),
            ],
            reference=_res_reference(),
        ),
        _res_case(
            "res-unsupported-artifact-id-rejected",
            "unsupported artifact id rejected",
            "A finding citing a nonexistent comparative static id is rejected and retried.",
            propositions=[_RES_PROP_A],
            statics=[_RES_STATIC_A],
            llm_fixtures=[
                *_res_assembly_fixture(
                    "res-unsupported-artifact-id-rejected",
                    _res_assembly_response(
                        findings=[
                            _res_finding(
                                "Firm 1's quantity responds to the demand intercept.",
                                static_ids=["res-unsupported-artifact-id-rejected-static-9"],
                            )
                        ],
                        contributions=[
                            _res_contribution(
                                "Demand conditions drive equilibrium outcomes.",
                                ["FINDING0"],
                            )
                        ],
                        implications=[
                            _res_implication(
                                "Demand shocks shift market outcomes.",
                                ["FINDING0"],
                            )
                        ],
                        limitations=["Fixture limitation: linear demand."],
                    ),
                    retry=_res_clean_assembly("res-unsupported-artifact-id-rejected"),
                ),
                _res_critic_fixture(),
            ],
            reference=_res_reference(),
        ),
        _res_case(
            "res-valid-theoretical-contribution",
            "valid theoretical contribution",
            "A theoretical contribution linked to the package gap and a finding is assembled.",
            propositions=[_RES_PROP_A],
            statics=[_RES_STATIC_A],
            llm_fixtures=[
                *_res_assembly_fixture(
                    "res-valid-theoretical-contribution",
                    _res_clean_assembly("res-valid-theoretical-contribution"),
                ),
                _res_critic_fixture(),
            ],
            reference=_res_reference(),
        ),
        _res_case(
            "res-weak-gap-contribution-link",
            "weak gap/contribution link",
            "A contribution referencing a finding outside the package is "
            "rejected during assembly; the critic surfaces the weak link.",
            propositions=[_RES_PROP_A],
            statics=[_RES_STATIC_A],
            llm_fixtures=[
                *_res_assembly_fixture(
                    "res-weak-gap-contribution-link",
                    _res_assembly_response(
                        findings=[
                            _res_finding(
                                "Firm 1's equilibrium quantity increases in the demand intercept.",
                                prop_ids=["res-weak-gap-contribution-link-prop-0"],
                            )
                        ],
                        contributions=[
                            _res_contribution(
                                "Entry barriers explain pricing intensity.",
                                ["FINDING1"],
                            )
                        ],
                        implications=[
                            _res_implication(
                                "Platforms facing stronger entry barriers price higher.",
                                ["FINDING0"],
                            )
                        ],
                        limitations=["Fixture limitation: linear demand."],
                    ),
                    retry=_res_clean_assembly("res-weak-gap-contribution-link"),
                ),
                _res_critic_fixture(
                    issues=[
                        {
                            "category": "weak_gap_link",
                            "description": "contribution weakly tied to the gap",
                            "severity": "medium",
                            "location": "contribution",
                        }
                    ]
                ),
            ],
            reference=_res_reference(expected_critique_categories=["weak_gap_link"]),
        ),
        _res_case(
            "res-global-novelty-normalized",
            "global novelty claim normalized",
            "A sweeping novelty claim in a contribution is stripped during "
            "assembly and never persisted as fact.",
            propositions=[_RES_PROP_A],
            statics=[_RES_STATIC_A],
            llm_fixtures=[
                *_res_assembly_fixture(
                    "res-global-novelty-normalized",
                    _res_assembly_response(
                        findings=[
                            _res_finding(
                                "Firm 1's equilibrium quantity increases in the demand intercept.",
                                prop_ids=["res-global-novelty-normalized-prop-0"],
                            )
                        ],
                        contributions=[
                            _res_contribution(
                                "No prior study has examined entry barriers in "
                                "platform pricing; we model them explicitly.",
                                ["FINDING0"],
                            )
                        ],
                        implications=[
                            _res_implication(
                                "Platforms facing stronger entry barriers price higher.",
                                ["FINDING0"],
                            )
                        ],
                        limitations=["Fixture limitation: linear demand."],
                    ),
                ),
                _res_critic_fixture(),
            ],
            reference=_res_reference(expected_novelty_normalized=1),
        ),
        _res_case(
            "res-unsupported-managerial-implication",
            "unsupported managerial/causal implication",
            "A managerial implication with no finding support is persisted; the "
            "evaluator flags it and the critic surfaces the overstatement. "
            "Fails by design.",
            propositions=[_RES_PROP_A],
            statics=[_RES_STATIC_A],
            llm_fixtures=[
                *_res_assembly_fixture(
                    "res-unsupported-managerial-implication",
                    _res_assembly_response(
                        findings=[
                            _res_finding(
                                "Firm 1's equilibrium quantity increases in the demand intercept.",
                                prop_ids=["res-unsupported-managerial-implication-prop-0"],
                            )
                        ],
                        contributions=[
                            _res_contribution(
                                "Entry barriers explain pricing intensity.",
                                ["FINDING0"],
                            )
                        ],
                        implications=[
                            _res_implication(
                                "Platforms facing stronger entry barriers will "
                                "always raise prices.",
                                [],
                                claim_type="managerial_implication",
                                implication_kind="management",
                            )
                        ],
                        limitations=["Fixture limitation: linear demand."],
                    ),
                ),
                _res_critic_fixture(
                    issues=[
                        {
                            "category": "causal_overstatement",
                            "description": "causal claim beyond the verified result",
                            "severity": "high",
                            "location": "implication",
                        }
                    ]
                ),
            ],
            reference=_res_reference(expected_unsupported=1),
        ),
    ],
)

# ---------------------------------------------------------------------------
# manuscript-grounding-v1 (Phase 6G): real Phase 4B ManuscriptDrafterService +
# ManuscriptCriticService (+ revision)
# ---------------------------------------------------------------------------


def _ms_case(
    case_id: str,
    name: str,
    description: str,
    *,
    sections: list[str],
    llm_fixtures: list[dict[str, Any]],
    reference: dict[str, Any],
    propositions: list[dict[str, Any]] | None = None,
    contributions: list[dict[str, Any]] | None = None,
    package: dict[str, Any] | None = None,
    revise: bool = False,
    model: dict[str, Any] | None = None,
    candidate: dict[str, Any] | None = None,
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "manuscript_grounding",
            "model": model or _COURNOT_MODEL,
            "candidate": candidate or _COURNOT_CANDIDATE,
            "gap": _RES_GAP,
            "mechanism": _RES_MECHANISM,
            "propositions": propositions or [_RES_PROP_A],
            "statics": [_RES_STATIC_A],
            "numerical_results": [_res_result({"q1": 3.0}, scenario="baseline")],
            "evidence": [
                {
                    "statement": "Entry barriers shape competition in platform markets.",
                    "category": "finding",
                }
            ],
            "papers": [{"title": "Fixture paper"}],
            "synthesis": [
                {
                    "statement": "Within the reviewed corpus, entry barriers shape competition.",
                    "evidence_ids": ["ms-paper-0"],
                }
            ],
            "findings": [
                {
                    "statement": "Firm 1's equilibrium quantity increases in the demand intercept.",
                    "proposition_ids": ["ms-paper-prop-0"],
                }
            ],
            "contributions": contributions
            or [
                {
                    "claim": "Modeling entry barriers explains platform pricing intensity.",
                    "finding_ids": ["ms-paper-finding-0"],
                }
            ],
            "implications": [
                {
                    "text": "Platforms facing stronger entry barriers price higher.",
                    "finding_ids": ["ms-paper-finding-0"],
                }
            ],
            "package": package or {},
            "sections": sections,
            "revise": revise,
            "llm_fixtures": llm_fixtures,
        },
        reference=reference,
        evaluation_dimensions=["manuscript"],
        tags=["manuscript", "offline"],
    )


def _ms_section_response(
    case_id: str,
    section_title: str,
    *,
    claims: list[dict[str, Any]],
    body: str,
    citations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "title": section_title,
        "body": body,
        "claims": claims,
        "citations": citations or [],
    }


def _ms_claim(
    text: str,
    *,
    grounding_type: str | None = None,
    grounding_artifact_id: str | None = None,
    citation_id: str | None = None,
    conditions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "text": text,
        "grounding_type": grounding_type,
        "grounding_artifact_id": grounding_artifact_id,
        "citation_id": citation_id,
        "conditions": conditions or [],
    }


def _ms_citation(
    citation_id: str,
    paper_ref: str,
    evidence_ref: str,
) -> dict[str, Any]:
    return {
        "citation_id": citation_id,
        "paper_identity_id": paper_ref,
        "evidence_item_id": evidence_ref,
        "page_locator": "p. 214",
        "claim_context": "fixture claim context",
    }


def _ms_section_fixture(
    match: str,
    response: dict[str, Any],
    *,
    retry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    fixtures: list[dict[str, Any]] = []
    if retry is not None:
        fixtures.append({"match": "REJECTED by deterministic validation", "response": retry})
    fixtures.append({"match": match, "response": response})
    return fixtures


def _ms_critic_fixture(
    *, verdict: str = "approve", issues: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "match": "Critique the following manuscript draft",
        "response": {
            "overall_assessment": "fixture assessment",
            "verdict": verdict,
            "recommendations": ["fixture"],
            "issues": issues or [],
        },
    }


def _ms_reference(
    *,
    expected_sections: list[str] | None = None,
    expected_critique_categories: list[str] | None = None,
    expected_revision: bool = False,
    expected_novelty_normalized: int = 0,
) -> dict[str, Any]:
    return {
        "expected_sections": expected_sections or [],
        "expected_critique_categories": expected_critique_categories or [],
        "expected_revision": expected_revision,
        "expected_novelty_normalized": expected_novelty_normalized,
    }


def _ms_lit_review_response(case_id: str) -> dict[str, Any]:
    return _ms_section_response(
        case_id,
        "Literature Review",
        body=(
            "Prior work establishes that entry barriers shape competition "
            "in platform markets [CITE:lit-1]."
        ),
        claims=[
            _ms_claim(
                "Entry barriers shape competition in platform markets.",
                grounding_type="evidence_item",
                grounding_artifact_id=f"{case_id}-evidence-0",
                citation_id="lit-1",
            )
        ],
        citations=[_ms_citation("lit-1", f"{case_id}-paper-0", f"{case_id}-evidence-0")],
    )


def _ms_prop_section_response(case_id: str) -> dict[str, Any]:
    return _ms_section_response(
        case_id,
        "Propositions",
        body=("Proposition: firm 1's equilibrium quantity increases in the demand intercept."),
        claims=[
            _ms_claim(
                "Firm 1's equilibrium quantity increases in the demand intercept.",
                grounding_type="verified_proposition",
                grounding_artifact_id=f"{case_id}-prop-0",
            )
        ],
    )


MANUSCRIPT_GROUNDING_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="manuscript-grounding-v1",
    version=1,
    name="Manuscript Grounding",
    description=(
        "Offline benchmark over the real Phase 4B pipeline: fixture "
        "ResearchResultsPackage + literature artifacts -> real "
        "ManuscriptDrafterService (deterministic outline, scripted section "
        "drafts; validation rejects unsupported claims, missing/hallucinated "
        "citations, and failed-proposition grounding; normalizes novelty) -> "
        "real ManuscriptCriticService -> optional real revision. Ground truth "
        "is recomputed by the evaluator from the produced artifacts."
    ),
    category="manuscript_grounding",
    config={"evaluators": ["evaluator.manuscript_grounding"]},
    cases=[
        _ms_case(
            "ms-grounded-literature-claim",
            "grounded literature claim with citation",
            "A literature claim grounded in evidence with a declared citation "
            "and [CITE:...] placeholder is drafted.",
            sections=["literature_review"],
            llm_fixtures=[
                *_ms_section_fixture(
                    "Draft the 'Literature Review' section",
                    _ms_lit_review_response("ms-grounded-literature-claim"),
                ),
                _ms_critic_fixture(),
            ],
            reference=_ms_reference(expected_sections=["literature_review"]),
        ),
        _ms_case(
            "ms-grounded-mathematical-claim",
            "grounded mathematical claim",
            "A claim grounded in a verified proposition is drafted in the Propositions section.",
            sections=["propositions"],
            llm_fixtures=[
                *_ms_section_fixture(
                    "Draft the 'Propositions' section",
                    _ms_prop_section_response("ms-grounded-mathematical-claim"),
                ),
                _ms_critic_fixture(),
            ],
            reference=_ms_reference(expected_sections=["propositions"]),
        ),
        _ms_case(
            "ms-proposition-condition-preserved",
            "proposition condition preserved in prose",
            "A claim grounded in a conditional proposition carries the condition in the draft.",
            model=_COMPARATIVE_SLOPE_MODEL,
            candidate=_COMPARATIVE_SLOPE_CANDIDATE,
            propositions=[
                _res_prop(
                    "The equilibrium quantity increases in the demand intercept "
                    "a when the slope parameter b is positive.",
                    outcome_variable="q",
                    parameter="a",
                    expected_sign="positive",
                    verification="conditionally_verified",
                    conditions=["b > 0"],
                )
            ],
            sections=["propositions"],
            llm_fixtures=[
                *_ms_section_fixture(
                    "Draft the 'Propositions' section",
                    _ms_section_response(
                        "ms-proposition-condition-preserved",
                        "Propositions",
                        body=(
                            "Proposition: the equilibrium quantity increases in "
                            "the demand intercept when the slope parameter b is "
                            "positive (b > 0)."
                        ),
                        claims=[
                            _ms_claim(
                                "The equilibrium quantity increases in the demand "
                                "intercept when the slope is positive.",
                                grounding_type="verified_proposition",
                                grounding_artifact_id=("ms-proposition-condition-preserved-prop-0"),
                                conditions=["b > 0"],
                            )
                        ],
                    ),
                ),
                _ms_critic_fixture(),
            ],
            reference=_ms_reference(expected_sections=["propositions"]),
        ),
        _ms_case(
            "ms-unsupported-literature-claim",
            "unsupported literature claim",
            "A literature claim without a citation is rejected by deterministic "
            "validation and retried with a citation.",
            sections=["literature_review"],
            llm_fixtures=[
                *_ms_section_fixture(
                    "Draft the 'Literature Review' section",
                    _ms_section_response(
                        "ms-unsupported-literature-claim",
                        "Literature Review",
                        body="Entry barriers shape competition in platform markets.",
                        claims=[
                            _ms_claim(
                                "Entry barriers shape competition in platform markets.",
                                grounding_type="evidence_item",
                                grounding_artifact_id=(
                                    "ms-unsupported-literature-claim-evidence-0"
                                ),
                            )
                        ],
                    ),
                    retry=_ms_lit_review_response("ms-unsupported-literature-claim"),
                ),
                _ms_critic_fixture(),
            ],
            reference=_ms_reference(expected_sections=["literature_review"]),
        ),
        _ms_case(
            "ms-missing-citation",
            "missing citation",
            "A [CITE:...] placeholder without a declared citation is rejected and retried.",
            sections=["literature_review"],
            llm_fixtures=[
                *_ms_section_fixture(
                    "Draft the 'Literature Review' section",
                    _ms_section_response(
                        "ms-missing-citation",
                        "Literature Review",
                        body=("Entry barriers shape competition [CITE:lit-2] in platform markets."),
                        claims=[
                            _ms_claim(
                                "Entry barriers shape competition in platform markets.",
                                grounding_type="evidence_item",
                                grounding_artifact_id="ms-missing-citation-evidence-0",
                                citation_id="lit-1",
                            )
                        ],
                        citations=[
                            _ms_citation(
                                "lit-1",
                                "ms-missing-citation-paper-0",
                                "ms-missing-citation-evidence-0",
                            )
                        ],
                    ),
                    retry=_ms_lit_review_response("ms-missing-citation"),
                ),
                _ms_critic_fixture(),
            ],
            reference=_ms_reference(expected_sections=["literature_review"]),
        ),
        _ms_case(
            "ms-hallucinated-citation-id",
            "hallucinated citation id",
            "A citation referencing unknown evidence is rejected and retried.",
            sections=["literature_review"],
            llm_fixtures=[
                *_ms_section_fixture(
                    "Draft the 'Literature Review' section",
                    _ms_section_response(
                        "ms-hallucinated-citation-id",
                        "Literature Review",
                        body=("Entry barriers shape competition in platform markets [CITE:lit-1]."),
                        claims=[
                            _ms_claim(
                                "Entry barriers shape competition in platform markets.",
                                grounding_type="evidence_item",
                                grounding_artifact_id=("ms-hallucinated-citation-id-evidence-0"),
                                citation_id="lit-1",
                            )
                        ],
                        citations=[
                            _ms_citation(
                                "lit-1",
                                "ms-hallucinated-citation-id-paper-0",
                                "ghost-evidence",
                            )
                        ],
                    ),
                    retry=_ms_lit_review_response("ms-hallucinated-citation-id"),
                ),
                _ms_critic_fixture(),
            ],
            reference=_ms_reference(expected_sections=["literature_review"]),
        ),
        _ms_case(
            "ms-failed-proposition-presented",
            "failed proposition presented as result",
            "A claim grounding in a failed proposition is rejected and retried "
            "with a verified proposition.",
            propositions=[_RES_PROP_C, _RES_PROP_A],
            sections=["propositions"],
            llm_fixtures=[
                *_ms_section_fixture(
                    "Draft the 'Propositions' section",
                    _ms_section_response(
                        "ms-failed-proposition-presented",
                        "Propositions",
                        body=(
                            "Proposition: firm 1's equilibrium quantity decreases "
                            "in the marginal cost."
                        ),
                        claims=[
                            _ms_claim(
                                "Firm 1's equilibrium quantity decreases in the marginal cost.",
                                grounding_type="verified_proposition",
                                grounding_artifact_id=("ms-failed-proposition-presented-prop-0"),
                            )
                        ],
                    ),
                    retry=_ms_prop_section_response("ms-failed-proposition-presented"),
                ),
                _ms_critic_fixture(),
            ],
            reference=_ms_reference(expected_sections=["propositions"]),
        ),
        _ms_case(
            "ms-novelty-overclaim",
            "novelty overclaim",
            "A sweeping novelty phrase in the body is normalized during drafting.",
            sections=["propositions"],
            llm_fixtures=[
                *_ms_section_fixture(
                    "Draft the 'Propositions' section",
                    _ms_section_response(
                        "ms-novelty-overclaim",
                        "Propositions",
                        body=(
                            "This is the first study of Cournot competition; firm "
                            "1's equilibrium quantity increases in the demand "
                            "intercept."
                        ),
                        claims=[
                            _ms_claim(
                                "Firm 1's equilibrium quantity increases in the demand intercept.",
                                grounding_type="verified_proposition",
                                grounding_artifact_id="ms-novelty-overclaim-prop-0",
                            )
                        ],
                    ),
                ),
                _ms_critic_fixture(),
            ],
            reference=_ms_reference(
                expected_sections=["propositions"],
                expected_novelty_normalized=1,
            ),
        ),
        _ms_case(
            "ms-gap-contribution-inconsistency",
            "gap/contribution inconsistency",
            "A contribution referencing a different gap is flagged by the "
            "critic's deterministic check.",
            sections=["contributions"],
            contributions=[
                {
                    "claim": "Modeling entry barriers explains platform pricing intensity.",
                    "finding_ids": ["ms-paper-finding-0"],
                    "gap_id": "other-gap",
                }
            ],
            llm_fixtures=[
                *_ms_section_fixture(
                    "Draft the 'Contributions' section",
                    _ms_section_response(
                        "ms-gap-contribution-inconsistency",
                        "Contributions",
                        body=(
                            "This study contributes a mechanism that links entry "
                            "barriers to platform pricing."
                        ),
                        claims=[
                            _ms_claim(
                                "The mechanism links entry barriers to platform pricing.",
                                grounding_type="contribution_claim",
                                grounding_artifact_id=(
                                    "ms-gap-contribution-inconsistency-contribution-0"
                                ),
                            )
                        ],
                    ),
                ),
                _ms_critic_fixture(),
            ],
            reference=_ms_reference(
                expected_sections=["contributions"],
                expected_critique_categories=["gap_contribution_mismatch"],
            ),
        ),
        _ms_case(
            "ms-limitations-omitted",
            "limitations omitted",
            "A draft without a limitations section is flagged by the critic's deterministic check.",
            sections=["introduction", "conclusion"],
            llm_fixtures=[
                *_ms_section_fixture(
                    "Draft the 'Introduction' section",
                    _ms_section_response(
                        "ms-limitations-omitted",
                        "Introduction",
                        body=(
                            "We study competition in digital platform markets "
                            "through an analytical model."
                        ),
                        claims=[
                            _ms_claim(
                                "An analytical model links entry barriers to platform pricing.",
                                grounding_type="contribution_claim",
                                grounding_artifact_id=("ms-limitations-omitted-contribution-0"),
                            )
                        ],
                    ),
                ),
                *_ms_section_fixture(
                    "Draft the 'Conclusion' section",
                    _ms_section_response(
                        "ms-limitations-omitted",
                        "Conclusion",
                        body=("Entry barriers shape platform pricing in the reviewed model."),
                        claims=[
                            _ms_claim(
                                "Entry barriers shape platform pricing.",
                                grounding_type="research_finding",
                                grounding_artifact_id="ms-limitations-omitted-finding-0",
                            )
                        ],
                    ),
                ),
                _ms_critic_fixture(),
            ],
            reference=_ms_reference(
                expected_sections=["introduction", "conclusion"],
                expected_critique_categories=["missing_limitations"],
            ),
        ),
        _ms_case(
            "ms-revision-repairs-flagged",
            "revision fixes flagged section preserving unaffected sections",
            "A literature review section declaring a citation never used in "
            "the body is flagged by the critic's deterministic check; the "
            "revision re-drafts it (repairing the unused citation) while "
            "reusing the unaffected propositions section by id.",
            sections=["literature_review", "propositions", "limitations"],
            revise=True,
            llm_fixtures=[
                {
                    "match": "CRITIQUE FEEDBACK for this section",
                    "response": _ms_lit_review_response("ms-revision-repairs-flagged"),
                },
                *_ms_section_fixture(
                    "Draft the 'Literature Review' section",
                    _ms_section_response(
                        "ms-revision-repairs-flagged",
                        "Literature Review",
                        body=("Entry barriers shape competition in platform markets."),
                        claims=[
                            _ms_claim(
                                "Entry barriers shape competition in platform markets.",
                                grounding_type="evidence_item",
                                grounding_artifact_id=("ms-revision-repairs-flagged-evidence-0"),
                                citation_id="lit-1",
                            )
                        ],
                        citations=[
                            _ms_citation(
                                "lit-1",
                                "ms-revision-repairs-flagged-paper-0",
                                "ms-revision-repairs-flagged-evidence-0",
                            )
                        ],
                    ),
                ),
                *_ms_section_fixture(
                    "Draft the 'Propositions' section",
                    _ms_prop_section_response("ms-revision-repairs-flagged"),
                ),
                *_ms_section_fixture(
                    "Draft the 'Limitations' section",
                    _ms_section_response(
                        "ms-revision-repairs-flagged",
                        "Limitations",
                        body=(
                            "Limitations: the model assumes linear demand and static competition."
                        ),
                        claims=[
                            _ms_claim(
                                "The model assumes linear demand and static competition.",
                                grounding_type="research_gap",
                                grounding_artifact_id="ms-revision-repairs-flagged-gap",
                            )
                        ],
                    ),
                ),
                _ms_critic_fixture(),
            ],
            reference=_ms_reference(
                expected_sections=["literature_review", "propositions", "limitations"],
                expected_critique_categories=["citation_gap"],
                expected_revision=True,
            ),
        ),
    ],
)


# ---------------------------------------------------------------------------
# research-pipeline-e2e-v1 (Phase 6H): the real production chain end to end
# ---------------------------------------------------------------------------

_E2E_P0 = {
    "title": "Algorithmic Pricing and Consumer Welfare in Online Markets",
    "year": 2020,
    "venue": "Journal of Platform Studies",
    "abstract": "Algorithmic pricing raises consumer surplus when switching costs are low.",
}
_E2E_P1 = {
    "title": "Dynamic Pricing in Two-Sided Markets",
    "year": 2019,
    "venue": "Journal of Platform Studies",
    "abstract": "Rising entry costs reduce market participation and consumer surplus.",
}
_E2E_P2 = {
    "title": "A Survey of Estimation Methods for Pricing Data",
    "year": 2018,
    "venue": "Methods Review",
    "abstract": "Survey of estimation methods for pricing data.",
}

_E2E_CASE_ID = "e2e-research-pipeline"
_E2E_QUESTION = (
    "Which mechanisms explain algorithmic pricing effects on consumer "
    "welfare in platform markets? e2e-research-pipeline"
)


def _e2e_model_fixture() -> dict[str, Any]:
    return {
        "match": "Specify a formal analytical model for the selected mechanism.",
        "response": {
            "title": "Monopoly platform with linear demand slope",
            "description": (
                "A monopolist platform chooses quantity; demand depends on the slope parameter b."
            ),
            "game_type": "static complete information",
            "actors": [
                {
                    "actor_id": "m1",
                    "name": "Monopolist",
                    "role": "platform",
                    "strategic": True,
                }
            ],
            "variables": [
                {
                    "symbol": "q",
                    "name": "quantity",
                    "meaning": "platform output",
                    "domain": "R_+",
                    "kind": "decision_variable",
                    "owner_actor_id": "m1",
                }
            ],
            "parameters": [
                {
                    "symbol": "a",
                    "name": "demand intercept",
                    "meaning": "demand intercept",
                    "domain": "R_+",
                },
                {
                    "symbol": "b",
                    "name": "slope coefficient",
                    "meaning": "slope coefficient",
                    "domain": "R",
                },
                {
                    "symbol": "c",
                    "name": "marginal cost",
                    "meaning": "marginal cost",
                    "domain": "R_+",
                },
            ],
            "assumptions": [
                {
                    "statement": "Demand is linear in quantity.",
                    "mathematical_form": {
                        "expression": "a - b*q",
                        "symbols_used": ["a", "b", "q"],
                        "latex": "",
                    },
                    "knowledge_basis": "literature_supported",
                    "source_ids": [f"{_E2E_CASE_ID}-evidence-0"],
                    "purpose": "demand",
                    "restrictiveness": "medium",
                }
            ],
            "timing": [
                {
                    "stage_number": 0,
                    "name": "move",
                    "description": "The monopolist chooses quantity.",
                    "actor_ids": ["m1"],
                }
            ],
            "information_structure": {
                "items": [
                    {
                        "actor_id": "m1",
                        "variable_symbols": ["q"],
                        "available_at_stage": 0,
                        "visibility": "public",
                    }
                ],
                "uncertainty": [],
                "summary": "Complete information.",
            },
            "payoffs": [
                {
                    "actor_id": "m1",
                    "objective_type": "profit",
                    "expression": {
                        "expression": "q*(a - b*q) - c*q",
                        "symbols_used": ["q", "a", "b", "c"],
                        "latex": "",
                    },
                    "decision_variables": ["q"],
                    "parameters": ["a", "b", "c"],
                    "constraints": [],
                }
            ],
        },
    }


def _e2e_mechanism_fixtures() -> list[dict[str, Any]]:
    candidate = {
        k: v
        for k, v in _candidate_payload(
            "Price competition under entry barriers",
            basis="literature_supported",
            source_ids=[f"{_E2E_CASE_ID}-evidence-0", f"{_E2E_CASE_ID}-evidence-1"],
            support_ids=[f"{_E2E_CASE_ID}-evidence-0", f"{_E2E_CASE_ID}-evidence-1"],
        ).items()
        if not k.startswith("_")
    }
    return [
        {
            "match": "Choose ONE research gap to develop a theoretical mechanism for.",
            "response": {
                "selected_gap_id": f"{_E2E_CASE_ID}-gap",
                "selection_rationale": "fixture selection",
                "evidence_synthesis_basis": "fixture",
                "research_importance": 0.8,
                "theoretical_relevance": 0.8,
                "analytical_model_suitability": 0.8,
                "tractability": 0.8,
            },
        },
        {
            "match": "Develop structured candidate mechanisms for the selected research gap.",
            "response": {"candidates": [candidate]},
        },
        _critique_fixture("keep"),
    ]


RESEARCH_PIPELINE_E2E_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="research-pipeline-e2e-v1",
    version=1,
    name="Research Pipeline End-to-End",
    description=(
        "Offline benchmark driving the real production chain end to end: "
        "literature retrieval -> screening -> evidence extraction -> "
        "synthesis -> gap analysis -> mechanism -> analytical model -> "
        "equilibrium -> propositions -> numerical analysis -> results "
        "assembly -> manuscript grounding -> citation formatting, over a "
        "small deterministic fixture corpus with scripted model responses."
    ),
    category="pipeline_integrity",
    config={"evaluators": ["evaluator.pipeline_integrity"]},
    cases=[
        BenchmarkCaseDefinition(
            id=_E2E_CASE_ID,
            name="e2e research pipeline",
            description=(
                "One run of the full production research pipeline; the "
                "integrity evaluator verifies stage completion, provenance, "
                "grounding, conditions, citations, and bibliography fidelity."
            ),
            input={
                "workflow": "research_pipeline_e2e",
                "research_question": {"question": _E2E_QUESTION},
                "paper_order": [
                    _E2E_P0["title"],
                    _E2E_P1["title"],
                    _E2E_P2["title"],
                ],
                "included_titles": [_E2E_P0["title"], _E2E_P1["title"]],
                "providers": ["crossref", "semantic_scholar"],
                "fixture_sources": {
                    "crossref": [_E2E_P0, _E2E_P1, _E2E_P2],
                    "semantic_scholar": [_E2E_P0, _E2E_P1, _E2E_P2],
                },
                "queries": [
                    {
                        "query": "algorithmic pricing consumer welfare platform markets",
                        "purpose": "e2e benchmark",
                        "concepts": ["algorithmic pricing", "consumer welfare"],
                        "synonyms": ["dynamic pricing"],
                        "target_sources": ["crossref", "semantic_scholar"],
                        "expected_relevance": "high",
                    }
                ],
                "documents": [
                    {
                        "title": _E2E_P0["title"],
                        "pages": [
                            {
                                "page": 1,
                                "text": (
                                    "Algorithmic pricing uses demand data to "
                                    "adjust prices continuously. We find that "
                                    "algorithmic pricing raises consumer "
                                    "surplus when switching costs are low."
                                ),
                            }
                        ],
                    },
                    {
                        "title": _E2E_P1["title"],
                        "pages": [
                            {
                                "page": 1,
                                "text": (
                                    "Dynamic pricing in two-sided markets "
                                    "interacts with seller entry. Rising entry "
                                    "costs reduce market participation and "
                                    "consumer surplus."
                                ),
                            }
                        ],
                    },
                ],
                "sections": ["literature_review", "propositions", "conclusion"],
                "profile": {"name": "E2E Profile", "citation_style": "author_year"},
                "llm_fixtures": [
                    {
                        "match": _E2E_QUESTION,
                        "response": {
                            "objective": "Select studies on algorithmic pricing in digital markets.",
                            "inclusion_criteria": [
                                {
                                    "criterion_id": "I1",
                                    "description": "Studies algorithmic pricing in digital markets",
                                    "rationale": "Core focus",
                                    "required": True,
                                }
                            ],
                            "exclusion_criteria": [
                                {
                                    "criterion_id": "E1",
                                    "description": "Purely technical or non-scholarly work",
                                    "rationale": "Out of scope",
                                    "required": False,
                                }
                            ],
                            "decision_rules": "Include if I1 is satisfied.",
                        },
                    },
                    _screen_fixture(str(_E2E_P0["title"]), "include", 0.95),
                    _screen_fixture(str(_E2E_P1["title"]), "include", 0.95),
                    _screen_fixture(str(_E2E_P2["title"]), "exclude", 0.9),
                    _evidence_fixture(
                        "uses demand data to adjust prices continuously",
                        [
                            _evidence_item(
                                "result",
                                "Algorithmic pricing raises consumer surplus when switching costs are low.",
                                [1],
                            )
                        ],
                    ),
                    _evidence_fixture(
                        "interacts with seller entry",
                        [
                            _evidence_item(
                                "finding",
                                "Rising entry costs reduce market participation and consumer surplus.",
                                [1],
                            )
                        ],
                    ),
                    {
                        "match": "You are a literature synthesizer comparing research papers within a corpus.",
                        "response": {
                            "themes": [
                                {
                                    "title": "Pricing and welfare",
                                    "dimension": "welfare",
                                    "statements": [
                                        {
                                            "statement": (
                                                "Within the reviewed corpus, "
                                                "algorithmic pricing can raise "
                                                "consumer surplus when switching "
                                                "costs are low."
                                            ),
                                            "type": "consensus",
                                            "supporting_evidence_ids": [
                                                f"{_E2E_CASE_ID}-evidence-0"
                                            ],
                                            "confidence": 0.9,
                                        },
                                        {
                                            "statement": (
                                                "Within the reviewed corpus, "
                                                "rising entry costs reduce "
                                                "consumer surplus."
                                            ),
                                            "type": "consensus",
                                            "supporting_evidence_ids": [
                                                f"{_E2E_CASE_ID}-evidence-1"
                                            ],
                                            "confidence": 0.9,
                                        },
                                    ],
                                }
                            ]
                        },
                    },
                    {
                        "match": "You are analyzing a literature synthesis to identify candidate research gaps.",
                        "response": {
                            "gaps": [
                                _gap_item(
                                    "Within the reviewed corpus, the welfare effects of algorithmic pricing under entry dynamics remain under-theorized.",
                                    "mechanism_gap",
                                    "A mechanism linking algorithmic pricing to entry dynamics and welfare is missing.",
                                    stmt_ids=[
                                        f"{_E2E_CASE_ID}-stmt-0",
                                        f"{_E2E_CASE_ID}-stmt-1",
                                    ],
                                    ev_ids=[
                                        f"{_E2E_CASE_ID}-evidence-0",
                                        f"{_E2E_CASE_ID}-evidence-1",
                                    ],
                                )
                            ]
                        },
                    },
                    *_e2e_mechanism_fixtures(),
                    _e2e_model_fixture(),
                    {
                        "match": "Propose a candidate equilibrium for the following game.",
                        "response": {
                            "expressions": [
                                {
                                    "variable": "q",
                                    "expression": "(a - c)/(2*b)",
                                    "symbols_used": ["a", "b", "c"],
                                }
                            ]
                        },
                    },
                    {
                        "match": "Propose testable propositions grounded in the verified equilibrium.",
                        "response": {
                            "propositions": [
                                {
                                    "statement": (
                                        "The equilibrium quantity increases in "
                                        "the demand intercept when the slope "
                                        "parameter b is positive."
                                    ),
                                    "claim_type": "monotonicity",
                                    "outcome_variable": "q",
                                    "parameter": "a",
                                    "expected_sign": "positive",
                                    "conditions": ["b > 0"],
                                    "supporting_static_ids": [f"{_E2E_CASE_ID}-static-q-a"],
                                }
                            ]
                        },
                    },
                    {
                        "match": "Critique the following research proposition.",
                        "response": {
                            "overall_assessment": "fixture critique",
                            "verdict": "keep",
                            "recommendations": ["fixture"],
                            "issues": [],
                        },
                    },
                    {
                        "match": "Write the economic/IS interpretation of the following verified proposition.",
                        "response": {
                            "mathematical_result": "fixture result",
                            "economic_interpretation": "fixture interpretation",
                            "managerial_implication": "fixture implication",
                            "is_theoretical_implication": "fixture theory",
                            "consistency_note": "fixture note",
                        },
                    },
                    {
                        "match": "Assemble findings, contribution claims, and implications from verified results.",
                        "response": {
                            "findings": [
                                {
                                    "statement": (
                                        "The equilibrium quantity increases in "
                                        "the demand intercept when the slope is "
                                        "positive."
                                    ),
                                    "finding_type": "analytical_result",
                                    "supporting_proposition_ids": [f"{_E2E_CASE_ID}-prop-0"],
                                    "supporting_comparative_static_ids": [],
                                    "supporting_numerical_result_ids": [],
                                    "conditions": ["b > 0"],
                                    "confidence": "medium",
                                    "knowledge_basis": "research_inference",
                                }
                            ],
                            "contributions": [
                                {
                                    "claim": (
                                        "Modeling entry barriers explains "
                                        "platform pricing intensity."
                                    ),
                                    "contribution_type": "theoretical",
                                    "finding_ids": ["FINDING0"],
                                    "advances_literature": "Extends corpus-bounded pricing theory.",
                                }
                            ],
                            "implications": [
                                {
                                    "text": "Platforms facing stronger entry barriers price higher.",
                                    "implication_kind": "theory",
                                    "claim_type": "interpretation",
                                    "grounded_in_finding_ids": ["FINDING0"],
                                }
                            ],
                            "limitations": ["Fixture limitation: linear demand."],
                        },
                    },
                    {
                        "match": "Critique the following assembled research results package.",
                        "response": {
                            "overall_assessment": "fixture assessment",
                            "verdict": "approve",
                            "recommendations": ["fixture"],
                            "issues": [],
                        },
                    },
                    {
                        "match": "Draft the 'Literature Review' section",
                        "response": {
                            "title": "Literature Review",
                            "body": (
                                "Prior work shows that algorithmic pricing "
                                "raises consumer surplus when switching costs "
                                "are low [CITE:lit-1]."
                            ),
                            "claims": [
                                {
                                    "text": (
                                        "Algorithmic pricing raises consumer "
                                        "surplus when switching costs are low."
                                    ),
                                    "grounding_type": "evidence_item",
                                    "grounding_artifact_id": f"{_E2E_CASE_ID}-evidence-0",
                                    "citation_id": "lit-1",
                                    "conditions": [],
                                }
                            ],
                            "citations": [
                                {
                                    "citation_id": "lit-1",
                                    "paper_identity_id": f"{_E2E_CASE_ID}-identity-0",
                                    "evidence_item_id": f"{_E2E_CASE_ID}-evidence-0",
                                    "page_locator": "p. 1",
                                    "claim_context": "fixture claim context",
                                }
                            ],
                        },
                    },
                    {
                        "match": "Draft the 'Propositions' section",
                        "response": {
                            "title": "Propositions",
                            "body": (
                                "Proposition: the equilibrium quantity "
                                "increases in the demand intercept when the "
                                "slope parameter b is positive (b > 0)."
                            ),
                            "claims": [
                                {
                                    "text": (
                                        "The equilibrium quantity increases in "
                                        "the demand intercept when the slope is "
                                        "positive."
                                    ),
                                    "grounding_type": "verified_proposition",
                                    "grounding_artifact_id": f"{_E2E_CASE_ID}-prop-0",
                                    "citation_id": None,
                                    "conditions": ["b > 0"],
                                }
                            ],
                            "citations": [],
                        },
                    },
                    {
                        "match": "Draft the 'Conclusion' section",
                        "response": {
                            "title": "Conclusion",
                            "body": (
                                "A positive demand slope preserves the "
                                "monotonicity of the equilibrium quantity in "
                                "the demand intercept."
                            ),
                            "claims": [
                                {
                                    "text": (
                                        "The equilibrium quantity increases in "
                                        "the demand intercept when the slope is "
                                        "positive."
                                    ),
                                    "grounding_type": "research_finding",
                                    "grounding_artifact_id": f"{_E2E_CASE_ID}-finding-0",
                                    "citation_id": None,
                                    "conditions": ["b > 0"],
                                }
                            ],
                            "citations": [],
                        },
                    },
                    {
                        "match": "Critique the following manuscript draft",
                        "response": {
                            "overall_assessment": "fixture assessment",
                            "verdict": "approve",
                            "recommendations": ["fixture"],
                            "issues": [],
                        },
                    },
                ],
            },
            reference={
                "expected_stages": {
                    "retrieval": "literature_search_execution",
                    "screening": "screening_decision",
                    "evidence": "evidence_corpus",
                    "synthesis": "literature_synthesis",
                    "gap": "gap_analysis",
                    "mechanism": "selected_mechanism",
                    "model": "formal_analytical_model",
                    "equilibrium": "equilibrium_analysis",
                    "propositions": "proposition",
                    "numerical": "numerical_experiment",
                    "results": "results_package",
                    "manuscript": "manuscript_draft",
                    "formatting": "formatted_manuscript",
                },
                "expected_provenance": [
                    ["evidence_item", "synthesis_statement"],
                    ["synthesis_statement", "research_gap"],
                    ["evidence_item", "research_gap"],
                    ["research_gap", "contribution_claim"],
                    ["equilibrium_candidate", "research_finding"],
                    ["research_finding", "research_implication"],
                    ["contribution_claim", "results_package"],
                    ["results_package", "manuscript_outline"],
                    ["manuscript_outline", "manuscript_draft"],
                    ["manuscript_section", "manuscript_draft"],
                    ["manuscript_draft", "formatted_manuscript"],
                    ["paper_identity", "bibliography"],
                    ["equilibrium_candidate", "comparative_static"],
                    ["comparative_static", "proposition"],
                    ["selected_mechanism", "formal_analytical_model"],
                    ["formal_analytical_model", "equilibrium_analysis"],
                ],
                "expected_equilibrium": {"q": "(a-c)/(2*b)"},
                "expected_conditions": ["2*b != 0", "b > 0"],
                "expected_baseline": {"q": 4.5},
                "expected_citation_identity": {"lit-1": _E2E_P0["title"]},
            },
            evaluation_dimensions=["pipeline"],
            tags=["e2e", "offline"],
        )
    ],
)


# ---------------------------------------------------------------------------
# literature-synthesis-v1 (Phase 7A): real Phase 2G synthesizer
# ---------------------------------------------------------------------------
# The workflow builds a fixture EvidenceCorpus (PaperIdentity + EvidenceItem +
# PaperResearchProfile + EvidenceCorpus) and drives the REAL
# LiteratureSynthesizerService. Scripted responses reference case-scoped
# evidence ids that the workflow rewrites to run-unique ids. The synthesizer
# deterministically rejects hallucinated evidence ids and statements with no
# paper mapping, and computes support metrics (support_type, papers_supporting)
# itself.

_SYN_PROMPT_MARKER = "produce cross-paper synthesis themes and statements"


def _syn_evidence(statement: str, *, category: str = "finding") -> dict[str, Any]:
    return {"statement": statement, "category": category}


def _syn_statement(
    statement: str,
    stype: str,
    evidence_ids: list[str],
    *,
    conflicting_ids: list[str] | None = None,
    confidence: float = 0.9,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "statement": statement,
        "type": stype,
        "supporting_evidence_ids": evidence_ids,
        "confidence": confidence,
    }
    if conflicting_ids:
        item["conflicting_evidence_ids"] = conflicting_ids
    return item


def _syn_fixture(
    statements: list[dict[str, Any]], *, title: str = "synthesis theme"
) -> dict[str, Any]:
    return {
        "match": _SYN_PROMPT_MARKER,
        "response": {"themes": [{"title": title, "statements": statements}]},
    }


def _synthesis_case(
    case_id: str,
    name: str,
    description: str,
    *,
    evidence: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    fixtures: list[dict[str, Any]],
    reference: dict[str, Any],
) -> BenchmarkCaseDefinition:
    # rewrite fixture evidence ids `syn-ev-{i}` -> `{case_id}-evidence-{i}` so
    # the workflow's id map rewrites them to run-unique ids at execution time

    def _scope_ids(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: _scope_ids(v) for k, v in value.items()}
        if isinstance(value, list):
            return [_scope_ids(v) for v in value]
        if isinstance(value, str):
            return value.replace("syn-ev-", f"{case_id}-evidence-")
        return value

    scoped_fixtures = _scope_ids(fixtures)
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "literature_synthesis",
            "papers": [{"title": p.get("title", f"Paper {i}")} for i, p in enumerate(profiles)],
            "evidence": evidence,
            "profiles": profiles,
            "llm_fixtures": scoped_fixtures,
            "synthesis_config": {
                "batch_profiles": 10,
                "max_batches": 20,
                "max_model_calls": 100,
            },
        },
        reference=reference,
        evaluation_dimensions=["synthesis"],
        tags=["synthesis", "offline"],
    )


LITERATURE_SYNTHESIS_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="literature-synthesis-v1",
    version=1,
    name="Literature Synthesis",
    description=(
        "Offline benchmark over the real Phase 2G synthesizer: fixture "
        "EvidenceCorpus (papers + EvidenceItem + PaperResearchProfile) -> "
        "LiteratureSynthesizerService -> SynthesisStatement / SynthesisTheme / "
        "LiteratureSynthesis. Metrics: statement grounding, consensus/contradiction/"
        "mixed accuracy, multi-paper support, support counts, unsupported-statement "
        "rate, hallucinated references. Hallucinated evidence ids and statements "
        "with no paper mapping are deterministically rejected."
    ),
    category="literature_synthesis",
    config={"evaluators": ["evaluator.synthesis"]},
    cases=[
        _synthesis_case(
            "syn-multi-paper-consensus",
            "multi-paper consensus",
            "Three papers agree; the synthesizer produces a multi-paper consensus "
            "statement grounded in evidence from all three.",
            evidence=[
                _syn_evidence(
                    "Algorithmic pricing significantly reduces consumer welfare in platform markets."
                ),
                _syn_evidence(
                    "Empirical analysis shows algorithmic pricing lowers consumer welfare."
                ),
                _syn_evidence("Consumer welfare declines when sellers deploy algorithmic pricing."),
            ],
            profiles=[
                {"paper_index": 0, "evidence_indexes": [0]},
                {"paper_index": 1, "evidence_indexes": [1]},
                {"paper_index": 2, "evidence_indexes": [2]},
            ],
            fixtures=[
                _syn_fixture(
                    [
                        _syn_statement(
                            "Algorithmic pricing consistently reduces consumer welfare across studies.",
                            "consensus",
                            ["syn-ev-0", "syn-ev-1", "syn-ev-2"],
                        )
                    ],
                    title="algorithmic pricing and welfare",
                )
            ],
            reference={
                "expected_statements": [
                    {
                        "statement": "Algorithmic pricing consistently reduces consumer welfare across studies.",
                        "type": "consensus",
                        "support_type": "multi_paper",
                        "papers_supporting": 3,
                    }
                ],
                "expected_rejections": 0,
            },
        ),
        _synthesis_case(
            "syn-contradiction",
            "contradiction preserved with both sides",
            "Two papers disagree; the synthesizer must produce a contradiction "
            "statement citing conflicting evidence from BOTH sides.",
            evidence=[
                _syn_evidence(
                    "Dynamic pricing increases seller collusion in concentrated markets."
                ),
                _syn_evidence(
                    "Dynamic pricing reduces collusion by lowering information symmetry."
                ),
            ],
            profiles=[
                {"paper_index": 0, "evidence_indexes": [0]},
                {"paper_index": 1, "evidence_indexes": [1]},
            ],
            fixtures=[
                _syn_fixture(
                    [
                        _syn_statement(
                            "Studies disagree on whether dynamic pricing facilitates collusion.",
                            "contradiction",
                            ["syn-ev-0"],
                            conflicting_ids=["syn-ev-1"],
                        )
                    ],
                    title="dynamic pricing and collusion",
                )
            ],
            reference={
                "expected_statements": [
                    {
                        "statement": "Studies disagree on whether dynamic pricing facilitates collusion.",
                        "type": "contradiction",
                        "support_type": "single_paper",
                        "papers_supporting": 1,
                        "papers_conflicting": 1,
                    }
                ],
                "expected_rejections": 0,
            },
        ),
        _synthesis_case(
            "syn-mixed-evidence",
            "mixed evidence",
            "Two papers support and one opposes; the synthesizer produces a mixed "
            "statement citing evidence from both camps.",
            evidence=[
                _syn_evidence("Recommendation systems amplify price dispersion in e-commerce."),
                _syn_evidence("Recommendation systems increase price dispersion across sellers."),
                _syn_evidence("Recommendation systems dampen price dispersion in large markets."),
            ],
            profiles=[
                {"paper_index": 0, "evidence_indexes": [0]},
                {"paper_index": 1, "evidence_indexes": [1]},
                {"paper_index": 2, "evidence_indexes": [2]},
            ],
            fixtures=[
                _syn_fixture(
                    [
                        _syn_statement(
                            "Evidence on recommendation systems and price dispersion is mixed.",
                            "mixed",
                            ["syn-ev-0", "syn-ev-1"],
                            conflicting_ids=["syn-ev-2"],
                        )
                    ],
                    title="recommendation systems and dispersion",
                )
            ],
            reference={
                "expected_statements": [
                    {
                        "statement": "Evidence on recommendation systems and price dispersion is mixed.",
                        "type": "mixed",
                        "support_type": "multi_paper",
                        "papers_supporting": 2,
                        "papers_conflicting": 1,
                    }
                ],
                "expected_rejections": 0,
            },
        ),
        _synthesis_case(
            "syn-single-paper-not-consensus",
            "single-paper observation not treated as consensus",
            "One paper's observation must not be labeled consensus; the "
            "deterministic support type stays single_paper.",
            evidence=[
                _syn_evidence("A single study documents ad-targeting effects on content diversity.")
            ],
            profiles=[
                {"paper_index": 0, "evidence_indexes": [0]},
            ],
            fixtures=[
                _syn_fixture(
                    [
                        _syn_statement(
                            "Ad targeting reduces content diversity in news platforms.",
                            "pattern",
                            ["syn-ev-0"],
                        )
                    ],
                    title="ad targeting and content diversity",
                )
            ],
            reference={
                "expected_statements": [
                    {
                        "statement": "Ad targeting reduces content diversity in news platforms.",
                        "type": "pattern",
                        "support_type": "single_paper",
                        "papers_supporting": 1,
                    }
                ],
                "expected_not_consensus": True,
                "expected_rejections": 0,
            },
        ),
        _synthesis_case(
            "syn-boundary-condition-pattern",
            "boundary-condition pattern",
            "Two papers observe the same boundary condition; the synthesizer "
            "produces a boundary_condition statement.",
            evidence=[
                _syn_evidence("Platform pricing effects attenuate when switching costs are high."),
                _syn_evidence("The welfare effect reverses under high switching costs."),
            ],
            profiles=[
                {"paper_index": 0, "evidence_indexes": [0]},
                {"paper_index": 1, "evidence_indexes": [1]},
            ],
            fixtures=[
                _syn_fixture(
                    [
                        _syn_statement(
                            "Pricing effects depend on the level of consumer switching costs.",
                            "boundary_condition",
                            ["syn-ev-0", "syn-ev-1"],
                        )
                    ],
                    title="switching costs boundary",
                )
            ],
            reference={
                "expected_statements": [
                    {
                        "statement": "Pricing effects depend on the level of consumer switching costs.",
                        "type": "boundary_condition",
                        "support_type": "multi_paper",
                        "papers_supporting": 2,
                    }
                ],
                "expected_rejections": 0,
            },
        ),
        _synthesis_case(
            "syn-methodological-pattern",
            "methodological pattern",
            "Two papers share a method; the synthesizer produces a "
            "methodological_pattern statement.",
            evidence=[
                _syn_evidence("The field experiment measures willingness to pay directly."),
                _syn_evidence(
                    "A field experiment estimates reservation prices in the same setting."
                ),
            ],
            profiles=[
                {"paper_index": 0, "evidence_indexes": [0]},
                {"paper_index": 1, "evidence_indexes": [1]},
            ],
            fixtures=[
                _syn_fixture(
                    [
                        _syn_statement(
                            "Both studies rely on field experiments to elicit willingness to pay.",
                            "methodological_pattern",
                            ["syn-ev-0", "syn-ev-1"],
                        )
                    ],
                    title="field experiment methodology",
                )
            ],
            reference={
                "expected_statements": [
                    {
                        "statement": "Both studies rely on field experiments to elicit willingness to pay.",
                        "type": "methodological_pattern",
                        "support_type": "multi_paper",
                        "papers_supporting": 2,
                    }
                ],
                "expected_rejections": 0,
            },
        ),
        _synthesis_case(
            "syn-hallucinated-evidence-rejected",
            "hallucinated evidence id rejected",
            "A statement cites an evidence id not present in the corpus; the "
            "synthesizer must reject it while keeping valid statements.",
            evidence=[
                _syn_evidence(
                    "Subscription fatigue reduces platform switching in streaming markets."
                ),
                _syn_evidence("Platforms offset churn with loyalty bundles."),
            ],
            profiles=[
                {"paper_index": 0, "evidence_indexes": [0]},
                {"paper_index": 1, "evidence_indexes": [1]},
            ],
            fixtures=[
                _syn_fixture(
                    [
                        _syn_statement(
                            "Subscription fatigue reduces platform switching in streaming markets.",
                            "consensus",
                            ["syn-ev-0", "syn-ev-1"],
                        ),
                        _syn_statement(
                            "A fabricated claim citing no real evidence.",
                            "consensus",
                            ["syn-ev-0", "syn-ev-999"],
                        ),
                    ],
                    title="subscription fatigue",
                )
            ],
            reference={
                "expected_statements": [
                    {
                        "statement": "Subscription fatigue reduces platform switching in streaming markets.",
                        "type": "consensus",
                        "support_type": "multi_paper",
                        "papers_supporting": 2,
                    }
                ],
                "expected_absent_statements": ["A fabricated claim citing no real evidence."],
                "expected_rejections": 1,
            },
        ),
        _synthesis_case(
            "syn-unsupported-statement-rejected",
            "unsupported synthesis statement rejected",
            "A statement cites an evidence item that exists in the corpus but is "
            "attached to no paper profile; the synthesizer must reject it as "
            "having no paper mapping.",
            evidence=[
                _syn_evidence("Personalized pricing lowers consumer trust in online retail."),
                _syn_evidence("Trust rebounds when firms disclose pricing practices."),
                _syn_evidence("An orphaned evidence item attached to no profile."),
            ],
            profiles=[
                {"paper_index": 0, "evidence_indexes": [0]},
                {"paper_index": 1, "evidence_indexes": [1]},
            ],
            fixtures=[
                _syn_fixture(
                    [
                        _syn_statement(
                            "Personalized pricing lowers consumer trust in online retail.",
                            "consensus",
                            ["syn-ev-0", "syn-ev-1"],
                        ),
                        _syn_statement(
                            "An unsupported claim anchored only to orphaned evidence.",
                            "pattern",
                            ["syn-ev-2"],
                        ),
                    ],
                    title="personalized pricing and trust",
                )
            ],
            reference={
                "expected_statements": [
                    {
                        "statement": "Personalized pricing lowers consumer trust in online retail.",
                        "type": "consensus",
                        "support_type": "multi_paper",
                        "papers_supporting": 2,
                    }
                ],
                "expected_absent_statements": [
                    "An unsupported claim anchored only to orphaned evidence."
                ],
                "expected_rejections": 1,
            },
        ),
    ],
)


# ---------------------------------------------------------------------------
# analytical-model-specification-v1 (Phase 7A): real Phase 3B builder + critic
# ---------------------------------------------------------------------------
# The workflow builds a fixture SelectedMechanism with literature-supported
# grounding (real synthesis statements) and drives the REAL ModelBuilderService
# (deterministic structural validation rejects undefined/duplicate symbols,
# bad decision ownership, invalid timing, invalid information structure,
# unsupported literature-backed assumptions) plus the REAL
# ModelSpecificationCriticService. Scripted specs use case-scoped source ids
# rewritten to run-unique ids.

_MODEL_BUILD_MARKER = "Specify a formal analytical model for the selected mechanism."
_MODEL_CRITIQUE_MARKER = "Critique the following formal analytical model"


def _model_spec_case(
    case_id: str,
    name: str,
    description: str,
    *,
    spec: dict[str, Any],
    assumptions_literature: bool = False,
    expected_rejected: bool = False,
    expected_failure: str | None = None,
    expected_model_created: bool = True,
    expected_payoff_actors: list[str] | None = None,
    expected_symbols: list[str] | None = None,
    expected_decision_owners: dict[str, str] | None = None,
    expected_critique_issues: list[str] | None = None,
) -> BenchmarkCaseDefinition:
    fixtures: list[dict[str, Any]] = [
        {"match": _MODEL_BUILD_MARKER, "response": spec},
        {
            "match": _MODEL_CRITIQUE_MARKER,
            "response": {
                "issues": [
                    {
                        "category": "mechanism_model_mismatch",
                        "description": "The model's actors do not match the mechanism.",
                        "severity": "high",
                        "location": "actors",
                    }
                ],
                "overall_assessment": "fixture critique",
                "verdict": "revise",
                "revision_recommendations": ["Align the model actors with the mechanism."],
            },
        },
    ]
    reference: dict[str, Any] = {
        "expected_model_created": expected_model_created,
        "expected_rejected": expected_rejected,
    }
    if expected_failure:
        reference["expected_failure_substring"] = expected_failure
    if expected_symbols is not None:
        reference["expected_symbols"] = expected_symbols
    if expected_payoff_actors is not None:
        reference["expected_payoff_actors"] = expected_payoff_actors
    if expected_decision_owners is not None:
        reference["expected_decision_owners"] = expected_decision_owners
    if expected_critique_issues is not None:
        reference["expected_critique_issues"] = expected_critique_issues
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "analytical_model_specification",
            "spec": spec,
            "assumptions_literature": assumptions_literature,
            "mechanism": {
                "name": "Strategic Pricing Mechanism",
                "description": "Platforms set prices and consumers respond.",
                "causal_logic": "A higher price reduces demand and raises margin.",
                "actors": ["firm"],
                "strategic_interactions": ["firm price setting"],
                "information_structure": "complete information",
                "incentives": ["profit maximization"],
                "boundary_conditions": ["positive demand"],
            },
            "statements": [
                {
                    "statement": "Prior work establishes that demand decreases in price for platform markets."
                }
            ],
            "llm_fixtures": fixtures,
            "model_config": {"max_actors": 8, "max_variables": 40, "max_parameters": 40},
        },
        reference=reference,
        evaluation_dimensions=["model_specification"],
        tags=["model", "offline"],
    )


def _valid_spec() -> dict[str, Any]:
    return {
        "title": "Strategic Pricing and Welfare",
        "description": "A strategic model of algorithmic pricing and consumer welfare.",
        "game_type": "static complete information",
        "actors": [
            {"actor_id": "firm", "name": "Platform Firm", "role": "pricer", "strategic": True}
        ],
        "variables": [
            {
                "symbol": "p",
                "name": "price",
                "meaning": "platform price",
                "domain": "R_+",
                "kind": "decision_variable",
                "owner_actor_id": "firm",
            }
        ],
        "parameters": [
            {"symbol": "c", "name": "cost", "meaning": "marginal cost", "domain": "R_+"},
            {
                "symbol": "a",
                "name": "demand intercept",
                "meaning": "maximum willingness to pay",
                "domain": "R_+",
            },
        ],
        "assumptions": [
            {
                "statement": "Demand decreases linearly in price.",
                "mathematical_form": {
                    "expression": "a - p",
                    "symbols_used": ["p", "a"],
                    "latex": "a - p",
                },
                "knowledge_basis": "modeling_assumption",
                "restrictiveness": "low",
            }
        ],
        "timing": [
            {
                "stage_number": 0,
                "name": "pricing",
                "description": "the firm chooses price",
                "actor_ids": ["firm"],
            }
        ],
        "information_structure": {
            "items": [
                {
                    "actor_id": "firm",
                    "variable_symbols": [],
                    "available_at_stage": 0,
                    "visibility": "public",
                }
            ],
            "uncertainty": [],
            "summary": "complete information",
        },
        "payoffs": [
            {
                "actor_id": "firm",
                "objective_type": "profit",
                "expression": {
                    "expression": "(p - c) * (a - p)",
                    "symbols_used": ["p", "c", "a"],
                    "latex": "(p-c)(a-p)",
                },
                "decision_variables": ["p"],
                "parameters": ["c", "a"],
                "constraints": [],
            }
        ],
    }


def _adjust_spec(spec: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    updated = json_copy(spec)
    for key, value in overrides.items():
        updated[key] = value
    return updated


def json_copy(value: Any) -> Any:
    import copy

    return copy.deepcopy(value)


def _set_payoff_variables(spec: dict[str, Any], variables: list[dict[str, Any]]) -> dict[str, Any]:
    spec = json_copy(spec)
    spec["variables"] = variables
    return spec


def _set_timing(spec: dict[str, Any], timing: list[dict[str, Any]]) -> dict[str, Any]:
    spec = json_copy(spec)
    spec["timing"] = timing
    return spec


def _set_information(spec: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    spec = json_copy(spec)
    spec["information_structure"] = {
        "items": items,
        "uncertainty": [],
        "summary": "fixture information structure",
    }
    return spec


def _set_assumptions(spec: dict[str, Any], assumptions: list[dict[str, Any]]) -> dict[str, Any]:
    spec = json_copy(spec)
    spec["assumptions"] = assumptions
    return spec


ANALYTICAL_MODEL_SPECIFICATION_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="analytical-model-specification-v1",
    version=1,
    name="Analytical Model Specification",
    description=(
        "Offline benchmark over the real Phase 3B pipeline: fixture "
        "SelectedMechanism -> ModelBuilderService -> FormalAnalyticalModel -> "
        "ModelSpecificationCriticService. Deterministic structural validation "
        "rejects undefined/duplicate symbols, invalid decision ownership, "
        "invalid timing, invalid information structures, and unsupported "
        "literature-backed assumptions; the critic flags mechanism/model "
        "mismatches."
    ),
    category="analytical_model_specification",
    config={"evaluators": ["evaluator.model_specification"]},
    cases=[
        _model_spec_case(
            "model-valid-strategic",
            "valid strategic model",
            "A structurally valid model with grounded assumptions and complete payoffs is created.",
            spec=_valid_spec(),
            expected_symbols=["p", "c", "a"],
            expected_payoff_actors=["firm"],
            expected_decision_owners={"p": "firm"},
            expected_critique_issues=["mechanism_model_mismatch"],
        ),
        _model_spec_case(
            "model-undefined-symbol",
            "undefined symbol",
            "A payoff expression uses a symbol not declared in variables or "
            "parameters; the builder must reject the spec.",
            spec=_adjust_spec(
                _valid_spec(),
                payoffs=[
                    {
                        "actor_id": "firm",
                        "objective_type": "profit",
                        "expression": {
                            "expression": "(p - c) * (a - p) - t",
                            "symbols_used": ["p", "c", "a", "t"],
                            "latex": "(p-c)(a-p)-t",
                        },
                        "decision_variables": ["p"],
                        "parameters": ["c", "a", "t"],
                        "constraints": [],
                    }
                ],
            ),
            expected_rejected=True,
            expected_failure="undefined symbol",
            expected_model_created=False,
        ),
        _model_spec_case(
            "model-duplicate-symbol",
            "duplicate symbol",
            "A variable and a parameter share a symbol; the builder must reject the duplicate.",
            spec=_set_payoff_variables(
                _valid_spec(),
                [
                    {
                        "symbol": "c",
                        "name": "chosen price",
                        "meaning": "price choice",
                        "domain": "R_+",
                        "kind": "decision_variable",
                        "owner_actor_id": "firm",
                    }
                ],
            ),
            expected_rejected=True,
            expected_failure="duplicate symbol",
            expected_model_created=False,
        ),
        _model_spec_case(
            "model-invalid-decision-ownership",
            "invalid decision ownership",
            "A decision variable has no owner actor; the builder must reject it.",
            spec=_set_payoff_variables(
                _valid_spec(),
                [
                    {
                        "symbol": "p",
                        "name": "price",
                        "meaning": "platform price",
                        "domain": "R_+",
                        "kind": "decision_variable",
                        "owner_actor_id": None,
                    }
                ],
            ),
            expected_rejected=True,
            expected_failure="no owner_actor_id",
            expected_model_created=False,
        ),
        _model_spec_case(
            "model-invalid-timing",
            "invalid timing",
            "Timing stages are not sequential from 0; the builder must reject them.",
            spec=_set_timing(
                _valid_spec(),
                [
                    {
                        "stage_number": 1,
                        "name": "pricing",
                        "description": "wrong start stage",
                        "actor_ids": ["firm"],
                    }
                ],
            ),
            expected_rejected=True,
            expected_failure="sequential",
            expected_model_created=False,
        ),
        _model_spec_case(
            "model-invalid-information-structure",
            "invalid information structure",
            "An information item observes a symbol that is not defined; the "
            "builder must reject it.",
            spec=_set_information(
                _valid_spec(),
                [
                    {
                        "actor_id": "firm",
                        "variable_symbols": ["ghost"],
                        "available_at_stage": 0,
                        "visibility": "public",
                    }
                ],
            ),
            expected_rejected=True,
            expected_failure="undefined symbol",
            expected_model_created=False,
        ),
        _model_spec_case(
            "model-unsupported-literature-assumption",
            "unsupported literature-backed assumption",
            "A literature_supported assumption cites an artifact outside the "
            "mechanism's grounding; the builder must reject it.",
            spec=_set_assumptions(
                _valid_spec(),
                [
                    {
                        "statement": "Prior work establishes this demand structure.",
                        "mathematical_form": {
                            "expression": "a - p",
                            "symbols_used": ["p", "a"],
                            "latex": "a - p",
                        },
                        "knowledge_basis": "literature_supported",
                        "source_ids": ["model-unknown-source"],
                        "restrictiveness": "low",
                    }
                ],
            ),
            assumptions_literature=True,
            expected_rejected=True,
            expected_failure="unknown artifacts",
            expected_model_created=False,
        ),
        _model_spec_case(
            "model-missing-payoff",
            "missing payoff for a strategic actor",
            "A strategic actor has no payoff function; the model is structurally "
            "accepted but the payoff-completeness defect is deterministic and "
            "must be detected.",
            spec=_adjust_spec(
                _valid_spec(),
                actors=[
                    {
                        "actor_id": "firm",
                        "name": "Platform Firm",
                        "role": "pricer",
                        "strategic": True,
                    },
                    {
                        "actor_id": "rival",
                        "name": "Rival Firm",
                        "role": "pricer",
                        "strategic": True,
                    },
                ],
                variables=[
                    {
                        "symbol": "p",
                        "name": "price",
                        "meaning": "platform price",
                        "domain": "R_+",
                        "kind": "decision_variable",
                        "owner_actor_id": "firm",
                    },
                    {
                        "symbol": "r",
                        "name": "rival price",
                        "meaning": "rival price",
                        "domain": "R_+",
                        "kind": "decision_variable",
                        "owner_actor_id": "rival",
                    },
                ],
            ),
            expected_symbols=["p", "c", "a", "r"],
            expected_payoff_actors=["firm", "rival"],
            expected_decision_owners={"p": "firm", "r": "rival"},
            expected_critique_issues=["mechanism_model_mismatch"],
        ),
        _model_spec_case(
            "model-critic-detects-mismatch",
            "critic detects mechanism/model mismatch",
            "The critic identifies a mechanism/model mismatch issue on an otherwise valid model.",
            spec=_valid_spec(),
            expected_symbols=["p", "c", "a"],
            expected_payoff_actors=["firm"],
            expected_decision_owners={"p": "firm"},
            expected_critique_issues=["mechanism_model_mismatch"],
        ),
    ],
)


# ---------------------------------------------------------------------------
# document-acquisition-v1 (Phase 7A): real Phase 2E acquisition pipeline
# ---------------------------------------------------------------------------
# The workflow builds fixture PaperIdentity + PaperRecord (with OA URLs),
# a ScreenedLiteratureSet, and drives the REAL MetadataLocatorService +
# HttpFetcherService (mocked HTTP) + PypdfExtractorService +
# DocumentAcquisitionOrchestratorService -> FullTextCorpus. Case ids are
# scoped to paper indexes (paper-0, paper-1, ...).


def _acq_paper(
    title: str,
    *,
    open_access_url: str | None = None,
    pdf_url: str | None = None,
) -> dict[str, Any]:
    paper: dict[str, Any] = {"title": title}
    if open_access_url:
        paper["open_access_url"] = open_access_url
    if pdf_url:
        paper["pdf_url"] = pdf_url
    return paper


def _acq_case(
    case_id: str,
    name: str,
    description: str,
    *,
    papers: list[dict[str, Any]],
    pdf_body: str = (
        "This study documents the welfare effects of algorithmic pricing in "
        "platform markets. We measure consumer surplus, seller profits, and "
        "total welfare across a range of market conditions, and we report "
        "robustness checks against alternative demand specifications."
    ),
    http_status: int = 200,
    content_type: str = "application/pdf",
    content_length: int | None = None,
    fetch_error: str | None = None,
    routes: list[dict[str, Any]] | None = None,
    reference: dict[str, Any],
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "document_acquisition",
            "papers": papers,
            "pdf_body": pdf_body,
            "http_status": http_status,
            "content_type": content_type,
            "content_length": content_length,
            "fetch_error": fetch_error,
            "routes": routes or [],
            "acquisition_config": {
                "max_locations_per_paper": 5,
                "max_bytes": 52428800,
                "max_candidates": 100,
            },
        },
        reference=reference,
        evaluation_dimensions=["acquisition"],
        tags=["acquisition", "offline"],
    )


DOCUMENT_ACQUISITION_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="document-acquisition-v1",
    version=1,
    name="Document Acquisition",
    description=(
        "Offline benchmark over the real Phase 2E pipeline with mocked HTTP: "
        "PaperIdentity -> metadata locator -> HttpFetcherService -> BlobStore -> "
        "PypdfExtractorService -> FullTextDocument -> DocumentAcquisitionOrchestrator "
        "-> FullTextCorpus. Deterministic metrics cover acquisition success, "
        "text extraction, failure classification (HTML-as-PDF, oversized, "
        "restricted/unavailable, no location), fallback usage, duplicate-blob "
        "reuse, and corpus availability."
    ),
    category="document_acquisition",
    config={"evaluators": ["evaluator.document_acquisition"]},
    cases=[
        _acq_case(
            "acq-valid-oa-pdf",
            "valid open-access PDF",
            "A single paper with a direct OA PDF URL is located, fetched, "
            "extracted, and added to the corpus.",
            papers=[
                _acq_paper(
                    "Open Access Pricing Study",
                    open_access_url="https://repository.example.com/paper-oa.pdf",
                )
            ],
            reference={
                "expected_statuses": {"acq-paper-0": "downloaded"},
                "expected_corpus_available": ["acq-paper-0"],
                "expected_corpus_unavailable": [],
                "expected_corpus_restricted": [],
            },
        ),
        _acq_case(
            "acq-fallback-location",
            "fallback location",
            "The first location returns an invalid document; the pipeline must "
            "fall back to the second location and succeed.",
            papers=[
                _acq_paper(
                    "Fallback Study",
                    open_access_url="https://a.example.com/broken.pdf",
                    pdf_url="https://b.example.com/fallback.pdf",
                )
            ],
            routes=[{"url": "https://a.example.com/broken.pdf", "content_type": "text/html"}],
            reference={
                "expected_statuses": {"acq-paper-0": "downloaded"},
                "expected_corpus_available": ["acq-paper-0"],
                "expected_corpus_unavailable": [],
                "expected_corpus_restricted": [],
                "expected_fallback_used": True,
            },
        ),
        _acq_case(
            "acq-no-location",
            "no location",
            "A paper with no OA URL yields a not_available acquisition and no corpus entry.",
            papers=[_acq_paper("Closed Access Study")],
            reference={
                "expected_statuses": {"acq-paper-0": "not_available"},
                "expected_corpus_available": [],
                "expected_corpus_unavailable": ["acq-paper-0"],
                "expected_corpus_restricted": [],
            },
        ),
        _acq_case(
            "acq-html-masquerading-as-pdf",
            "HTML masquerading as PDF",
            "A location returns an HTML page (login wall) instead of a PDF; the "
            "fetcher must classify it invalid_content.",
            papers=[
                _acq_paper(
                    "Login Wall Study",
                    open_access_url="https://publisher.example.com/paper.pdf",
                )
            ],
            content_type="text/html",
            reference={
                "expected_statuses": {"acq-paper-0": "invalid_content"},
                "expected_corpus_available": [],
                "expected_corpus_unavailable": [],
                "expected_corpus_restricted": [],
                "expected_corpus_failed": ["acq-paper-0"],
            },
        ),
        _acq_case(
            "acq-oversized-document",
            "oversized document",
            "The server declares a Content-Length above the budget; the fetcher "
            "must classify the acquisition too_large without downloading.",
            papers=[
                _acq_paper(
                    "Oversized Study",
                    open_access_url="https://repository.example.com/huge.pdf",
                )
            ],
            content_length=90000000,
            reference={
                "expected_statuses": {"acq-paper-0": "too_large"},
                "expected_corpus_available": [],
                "expected_corpus_unavailable": [],
                "expected_corpus_restricted": [],
                "expected_corpus_failed": ["acq-paper-0"],
            },
        ),
        _acq_case(
            "acq-restricted-unavailable",
            "restricted / unavailable document",
            "The server responds 404; the fetcher must classify the acquisition not_available.",
            papers=[
                _acq_paper(
                    "Missing Study",
                    open_access_url="https://repository.example.com/missing.pdf",
                )
            ],
            http_status=404,
            reference={
                "expected_statuses": {"acq-paper-0": "not_available"},
                "expected_corpus_available": [],
                "expected_corpus_unavailable": ["acq-paper-0"],
                "expected_corpus_restricted": [],
            },
        ),
        _acq_case(
            "acq-duplicate-blob",
            "duplicate blob reuse",
            "Fetching the same location twice with identical bytes reuses the "
            "same acquisition (no duplicate blob is created).",
            papers=[
                _acq_paper(
                    "Duplicate Blob Study",
                    open_access_url="https://repository.example.com/dup.pdf",
                )
            ],
            reference={
                "expected_statuses": {"acq-paper-0": "downloaded"},
                "expected_corpus_available": ["acq-paper-0"],
                "expected_corpus_unavailable": [],
                "expected_corpus_restricted": [],
                "expected_duplicate_reuse": True,
            },
        ),
        _acq_case(
            "acq-insufficient-extracted-text",
            "insufficient extracted text",
            "A fetched PDF extracts almost no text; the extractor marks the "
            "document insufficient_text and it is excluded from the available "
            "corpus.",
            papers=[
                _acq_paper(
                    "Sparse Text Study",
                    open_access_url="https://repository.example.com/sparse.pdf",
                )
            ],
            pdf_body="Tiny",
            reference={
                "expected_statuses": {"acq-paper-0": "downloaded"},
                "expected_text_status": {"acq-paper-0": "insufficient_text"},
                "expected_corpus_available": [],
                "expected_corpus_unavailable": ["acq-paper-0"],
                "expected_corpus_restricted": [],
            },
        ),
    ],
)


# ---------------------------------------------------------------------------
# incremental-revalidation-v1 (Phase 7A): immutable downstream recomputation
# ---------------------------------------------------------------------------
# The workflow drives REAL production services twice per stage — once with a
# baseline input, once with a materially-changed upstream input — and records
# for each stage whether the downstream execution was recomputed (new artifact)
# or reused (idempotent). References declare which stages MUST recompute and
# which MUST reuse, plus the provenance versions involved.

# Stages: screening_protocol, screening_identity, evidence_config, synthesis,
# gap, equilibrium, unchanged_reuse.


def _reval_screening_fixture(decision: str = "include") -> dict[str, Any]:
    return {
        "match": "screening papers for a literature review",
        "response": {
            "decision": decision,
            "matched_inclusion_criteria": ["I1"] if decision == "include" else [],
            "matched_exclusion_criteria": [],
            "reason_codes": [],
            "rationale_summary": "fixture screening decision",
            "confidence": 0.9,
            "information_sufficiency": "sufficient",
        },
    }


def _reval_case(
    case_id: str,
    name: str,
    description: str,
    *,
    stages: list[dict[str, Any]],
    reference: dict[str, Any],
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "incremental_revalidation",
            "stages": stages,
            "llm_fixtures": [
                {
                    "match": "designing a title/abstract screening protocol",
                    "response": {
                        "objective": "Select studies on algorithmic pricing in digital markets.",
                        "inclusion_criteria": [
                            {
                                "criterion_id": "I1",
                                "description": "Studies algorithmic pricing effects in digital markets",
                                "rationale": "Core focus of the review",
                                "required": True,
                            }
                        ],
                        "exclusion_criteria": [
                            {
                                "criterion_id": "E1",
                                "description": "Purely technical or non-scholarly work",
                                "rationale": "Out of scope",
                                "required": False,
                            }
                        ],
                        "decision_rules": "Include if I1 is satisfied and no exclusion criterion matches.",
                    },
                },
                _reval_screening_fixture(),
                {
                    "match": "produce cross-paper synthesis themes and statements",
                    "response": {
                        "themes": [
                            {
                                "title": "synthesis theme",
                                "statements": [
                                    {
                                        "statement": "Algorithmic pricing affects welfare.",
                                        "type": "consensus",
                                        "supporting_evidence_ids": ["rev-ev-0", "rev-ev-1"],
                                    }
                                ],
                            }
                        ]
                    },
                },
                {
                    "match": "identify candidate research gaps",
                    "response": {
                        "gaps": [
                            {
                                "title": "No analytical model links algorithmic pricing to welfare",
                                "gap_type": "mechanism_gap",
                                "description": "No included study models how algorithmic pricing affects welfare.",
                            }
                        ]
                    },
                },
                {
                    "match": "Propose a candidate equilibrium for the following game.",
                    "response": {
                        "expressions": [
                            {"variable": "p", "expression": "(10 + c)/2", "symbols_used": ["c"]}
                        ]
                    },
                },
                {
                    "match": "The following equilibrium candidate FAILED symbolic verification.",
                    "response": {
                        "expressions": [
                            {"variable": "p", "expression": "(10 + c)/2", "symbols_used": ["c"]}
                        ]
                    },
                },
            ],
            "revalidation_config": {"max_model_calls": 500},
        },
        reference=reference,
        evaluation_dimensions=["revalidation"],
        tags=["revalidation", "offline"],
    )


INCREMENTAL_REVALIDATION_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="incremental-revalidation-v1",
    version=1,
    name="Incremental Revalidation",
    description=(
        "Offline benchmark verifying immutable downstream invalidation and "
        "recomputation when upstream artifacts change: a new ScreeningProtocol "
        "must yield new decisions, a superseding PaperIdentity must yield a new "
        "screening view, a changed model role/config must yield a new evidence "
        "execution, a changed EvidenceCorpus must yield new synthesis, a changed "
        "synthesis must yield new gap analysis, a changed model specification "
        "must yield new equilibrium analysis, and unchanged inputs must reuse "
        "deterministically. Any stale reuse of incompatible upstream state is a "
        "deterministic failure."
    ),
    category="incremental_revalidation",
    config={"evaluators": ["evaluator.revalidation"]},
    cases=[
        _reval_case(
            "rev-new-protocol-new-decisions",
            "new ScreeningProtocol -> new decisions",
            "Screening the same candidates with a different approved protocol "
            "must produce new decisions rather than reuse the old ones.",
            stages=[{"kind": "screening_protocol"}],
            reference={"expected_recomputed": ["screening_protocol"], "expected_reused": []},
        ),
        _reval_case(
            "rev-superseding-identity-new-view",
            "superseding PaperIdentity -> new screening view",
            "A PaperIdentity superseded by a new identity with different members "
            "must yield a new screening view for the current identity.",
            stages=[{"kind": "screening_identity"}],
            reference={"expected_recomputed": ["screening_identity"], "expected_reused": []},
        ),
        _reval_case(
            "rev-model-config-new-evidence",
            "model role/config change -> new evidence execution",
            "Re-running evidence extraction with a materially different model "
            "role must not reuse the prior execution.",
            stages=[{"kind": "evidence_config"}],
            reference={"expected_recomputed": ["evidence_config"], "expected_reused": []},
        ),
        _reval_case(
            "rev-changed-corpus-new-synthesis",
            "changed EvidenceCorpus -> new synthesis",
            "Synthesizing a changed EvidenceCorpus must produce a new synthesis, "
            "not reuse the prior run.",
            stages=[{"kind": "synthesis"}],
            reference={"expected_recomputed": ["synthesis"], "expected_reused": []},
        ),
        _reval_case(
            "rev-changed-synthesis-new-gap",
            "changed synthesis -> new gap analysis",
            "Analyzing a changed LiteratureSynthesis must produce a new gap "
            "analysis, not reuse the prior run.",
            stages=[{"kind": "gap"}],
            reference={"expected_recomputed": ["gap"], "expected_reused": []},
        ),
        _reval_case(
            "rev-changed-model-new-equilibrium",
            "changed model specification -> new equilibrium analysis",
            "Deriving equilibrium from a changed model specification must produce "
            "a new equilibrium analysis, not reuse the prior run.",
            stages=[{"kind": "equilibrium"}],
            reference={"expected_recomputed": ["equilibrium"], "expected_reused": []},
        ),
        _reval_case(
            "rev-unchanged-deterministic-reuse",
            "unchanged inputs -> deterministic reuse",
            "Re-running a stage with identical inputs must reuse the prior "
            "execution deterministically (no duplicate recomputation).",
            stages=[{"kind": "unchanged_reuse"}],
            reference={"expected_recomputed": [], "expected_reused": ["unchanged_reuse"]},
        ),
    ],
)


# ---------------------------------------------------------------------------
# literature-ingestion-identity-v1 (Phase 7A.1): real Phase 2B/2C ingestion +
# identity resolution
# ---------------------------------------------------------------------------
# The workflow drives the REAL LiteratureIngestor (ProviderRecordSnapshot ->
# PaperRecord -> LiteratureSearchRecord) over fixture LiteratureSources and
# then the REAL PaperIdentityResolver. References are keyed by paper index
# (`ing-paper-{i}`) and identity group index (`ing-identity-{i}`).


def _ing_paper(
    title: str,
    *,
    doi: str | None = None,
    ext_ids: list[dict[str, str]] | None = None,
    year: int = 2021,
    abstract: str | None = None,
) -> dict[str, Any]:
    return {
        "title": title,
        "year": year,
        "abstract": abstract,
        "doi": doi,
        "external_identifiers": ext_ids or [],
    }


def _ingestion_case(
    case_id: str,
    name: str,
    description: str,
    *,
    providers: dict[str, Any],
    supersede_after: bool = False,
    reference: dict[str, Any],
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "literature_ingestion_identity",
            "providers": providers,
            "supersede_after": supersede_after,
            "ingestion_config": {"max_results_per_query": 20},
        },
        reference=reference,
        evaluation_dimensions=["ingestion_identity"],
        tags=["ingestion", "identity", "offline"],
    )


LITERATURE_INGESTION_IDENTITY_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="literature-ingestion-identity-v1",
    version=1,
    name="Literature Ingestion and Identity Resolution",
    description=(
        "Offline benchmark over the real Phase 2B/2C ingestion + identity "
        "resolution: fixture ProviderRecordSnapshot -> PaperRecord -> "
        "LiteratureSearchRecord -> PaperIdentityResolver. Verifies DOI / strong-"
        "identifier dedup, content-hash dedup, similar-title separation (no "
        "semantic merges), sparse metadata, provider failure with partial "
        "ingestion, and identity supersession when a new member appears."
    ),
    category="literature_ingestion_identity",
    config={"evaluators": ["evaluator.identity_resolution"]},
    cases=[
        _ingestion_case(
            "ing-same-doi-across-providers",
            "same DOI across providers",
            "Two providers return the same paper with the same DOI; the resolver "
            "must collapse them into one identity.",
            providers={
                "crossref": [
                    _ing_paper("Algorithmic Pricing and Welfare", doi="10.6000/ing-doi"),
                ],
                "semantic_scholar": [
                    _ing_paper(
                        "Algorithmic Pricing and Welfare",
                        doi="10.6000/ing-doi",
                        abstract="Welfare effects of algorithmic pricing.",
                    ),
                ],
            },
            reference={
                "expected_identities": [
                    {"members": ["ing-paper-0", "ing-paper-1"], "method": "exact_identifier"}
                ],
            },
        ),
        _ingestion_case(
            "ing-normalized-doi-variants",
            "normalized DOI variants",
            "The same DOI written as https://doi.org/..., doi:..., and bare forms "
            "must normalize to one identity.",
            providers={
                "crossref": [
                    _ing_paper("Pricing Study", doi="10.6000/ing-var"),
                    _ing_paper("Pricing Study", doi="https://doi.org/10.6000/ing-var"),
                ],
                "semantic_scholar": [
                    _ing_paper("Pricing Study", doi="doi:10.6000/ing-var"),
                ],
            },
            reference={
                "expected_identities": [
                    {
                        "members": ["ing-paper-0", "ing-paper-1", "ing-paper-2"],
                        "method": "exact_identifier",
                        "normalized": True,
                    }
                ],
            },
        ),
        _ingestion_case(
            "ing-shared-strong-identifier",
            "shared strong identifier",
            "Two records share an arXiv identifier (but no DOI) and must merge.",
            providers={
                "crossref": [
                    _ing_paper(
                        "Platform Competition Model",
                        ext_ids=[{"scheme": "arxiv", "value": "2101.0001"}],
                    ),
                ],
                "semantic_scholar": [
                    _ing_paper(
                        "A Model of Platform Competition",
                        ext_ids=[{"scheme": "arxiv", "value": "2101.0001"}],
                    ),
                ],
            },
            reference={
                "expected_identities": [
                    {"members": ["ing-paper-0", "ing-paper-1"], "method": "exact_identifier"}
                ],
            },
        ),
        _ingestion_case(
            "ing-exact-content-duplicate",
            "exact-content duplicate",
            "Identical payloads from the same provider (no identifiers) must "
            "collapse via exact content hash.",
            providers={
                "crossref": [
                    _ing_paper(
                        "Working Note on Pricing",
                        year=2022,
                        abstract="A sparse working note.",
                    ),
                    _ing_paper(
                        "Working Note on Pricing",
                        year=2022,
                        abstract="A sparse working note.",
                    ),
                ],
            },
            reference={
                "expected_identities": [
                    {"members": ["ing-paper-0", "ing-paper-1"], "method": "exact_content"}
                ],
            },
        ),
        _ingestion_case(
            "ing-similar-title-no-strong-id",
            "similar title without strong identifier",
            "Two distinct papers with similar titles but NO shared strong "
            "identifier must remain separate identities (no semantic merge).",
            providers={
                "crossref": [
                    _ing_paper(
                        "Dynamic Pricing and Seller Collusion in Two-Sided Markets",
                        year=2019,
                        abstract="Dynamic pricing may facilitate collusion.",
                    ),
                    _ing_paper(
                        "Dynamic Pricing and Market Outcomes in Two-Sided Platforms",
                        year=2021,
                        abstract="Dynamic pricing shapes market outcomes.",
                    ),
                ],
            },
            reference={
                "expected_identities": [
                    {"members": ["ing-paper-0"], "method": "exact_identifier"},
                    {"members": ["ing-paper-1"], "method": "exact_identifier"},
                ],
            },
        ),
        _ingestion_case(
            "ing-sparse-metadata",
            "sparse metadata",
            "A paper with only a title (no identifiers) resolves as its own "
            "singleton identity without invented fields.",
            providers={
                "crossref": [
                    {"title": "Unpublished Working Paper on Rating Dynamics", "year": 2023},
                ],
            },
            reference={
                "expected_identities": [{"members": ["ing-paper-0"], "method": "exact_identifier"}],
            },
        ),
        _ingestion_case(
            "ing-provider-failure-partial",
            "provider failure with partial ingestion",
            "One provider fails entirely; papers from the surviving provider are "
            "still ingested and resolved (partial ingestion, nothing fabricated).",
            providers={
                "crossref": "fail_all",
                "semantic_scholar": [
                    _ing_paper("Surviving Study", doi="10.6000/ing-survive"),
                ],
            },
            reference={
                "expected_identities": [{"members": ["ing-paper-0"], "method": "exact_identifier"}],
                "expected_failed_providers": ["crossref"],
            },
        ),
        _ingestion_case(
            "ing-identity-supersession",
            "identity supersession when a new member appears",
            "A later provider record shares the DOI of an existing identity; "
            "resolving the union must create a superseding identity and mark the "
            "old one superseded.",
            providers={
                "crossref": [
                    _ing_paper("Platform Pricing Study", doi="10.6000/ing-sup"),
                ],
                "semantic_scholar": [
                    _ing_paper(
                        "Platform Pricing Study",
                        doi="10.6000/ing-sup",
                        abstract="A fuller record of the same work.",
                    ),
                ],
            },
            supersede_after=True,
            reference={
                "expected_identities": [
                    {"members": ["ing-paper-0", "ing-paper-1"], "method": "exact_identifier"}
                ],
                "expected_superseded": ["ing-paper-0"],
            },
        ),
    ],
)


# ---------------------------------------------------------------------------
# gap-selection-v1 (Phase 7A.1): real Phase 3A gap selection
# ---------------------------------------------------------------------------
# The workflow creates a fixture GapAnalysis + ResearchGaps and drives the REAL
# GapSelectionService (model selection or operator override + autonomy
# checkpoint). Case-scoped gap ids are rewritten to run-unique ids.

_GAP_SELECT_MARKER = "Choose ONE research gap to develop a theoretical mechanism for."


def _gs_case(
    case_id: str,
    name: str,
    description: str,
    *,
    selected_gap_id: str | None = None,
    model_choice: str = "gap-0",
    autonomy_mode: str = "high",
    approval: bool | None = None,
    expect_error: bool = False,
    reference: dict[str, Any],
) -> BenchmarkCaseDefinition:
    fixtures: list[dict[str, Any]] = [
        {
            "match": _GAP_SELECT_MARKER,
            "response": {
                "selected_gap_id": model_choice,
                "evidence_synthesis_basis": "fixture basis",
                "research_importance": 0.8,
                "theoretical_relevance": 0.8,
                "analytical_model_suitability": 0.8,
                "tractability": 0.7,
                "selection_rationale": f"fixture rationale for {model_choice}",
            },
        }
    ]
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "gap_selection",
            "selected_gap_id": selected_gap_id,
            "autonomy_mode": autonomy_mode,
            "approval": approval,
            "expect_error": expect_error,
            "llm_fixtures": fixtures,
            "gaps": [
                {
                    "id": "gap-0",
                    "title": "No analytical model of algorithmic pricing welfare",
                    "importance": 0.9,
                    "tractability": 0.9,
                },
                {
                    "id": "gap-1",
                    "title": "No model of platform governance incentives",
                    "importance": 0.7,
                    "tractability": 0.7,
                },
                {
                    "id": "gap-2",
                    "title": "No model of consumer trust under personalization",
                    "importance": 0.8,
                    "tractability": 0.6,
                },
            ],
        },
        reference=reference,
        evaluation_dimensions=["gap_selection"],
        tags=["gap-selection", "offline"],
    )


GAP_SELECTION_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="gap-selection-v1",
    version=1,
    name="Gap Selection",
    description=(
        "Offline benchmark over the real Phase 3A GapSelectionService: fixture "
        "GapAnalysis + ranked ResearchGaps -> model selection (or operator "
        "override) -> autonomy approval checkpoint -> GapSelection. Verifies "
        "selection validity, rationale grounding, alternative consideration, "
        "deterministic fallback for invalid model selections, autonomy "
        "approval/rejection, operator override, unsupported-gap-id rejection, "
        "and deterministic rerun reuse."
    ),
    category="gap_selection",
    config={"evaluators": ["evaluator.gap_selection"]},
    cases=[
        _gs_case(
            "gs-select-rank1-strongest",
            "select rank #1 when clearly strongest",
            "The model selects the top-ranked gap; the selection is valid and approved.",
            model_choice="gap-0",
            reference={
                "expected_selected_gap": "gap-0",
                "expected_status": "approved",
                "expected_selected_by": "model",
            },
        ),
        _gs_case(
            "gs-valid-nonrank1-selection",
            "valid non-rank-1 selection",
            "The model selects a lower-ranked but valid gap; the selection is accepted.",
            model_choice="gap-2",
            reference={
                "expected_selected_gap": "gap-2",
                "expected_status": "approved",
                "expected_selected_by": "model",
            },
        ),
        _gs_case(
            "gs-operator-override",
            "operator override",
            "An operator explicitly selects a gap; the selection is recorded as operator.",
            selected_gap_id="gap-1",
            reference={
                "expected_selected_gap": "gap-1",
                "expected_status": "approved",
                "expected_selected_by": "operator",
            },
        ),
        _gs_case(
            "gs-invalid-model-selection-fallback",
            "invalid model-selected gap -> deterministic fallback",
            "The model proposes an unknown gap id; the service must fall back to rank #1 deterministically.",
            model_choice="ghost-gap",
            reference={
                "expected_selected_gap": "gap-0",
                "expected_status": "approved",
                "expected_selected_by": "model",
                "expected_fallback": True,
            },
        ),
        _gs_case(
            "gs-autonomy-approval",
            "autonomy approval",
            "Interactive autonomy mode approves the selection.",
            autonomy_mode="interactive",
            approval=True,
            reference={
                "expected_selected_gap": "gap-0",
                "expected_status": "approved",
                "expected_approval_required": True,
            },
        ),
        _gs_case(
            "gs-autonomy-rejection",
            "autonomy rejection",
            "Interactive autonomy mode rejects the selection; the selection is recorded rejected.",
            autonomy_mode="interactive",
            approval=False,
            reference={
                "expected_selected_gap": "gap-0",
                "expected_status": "rejected",
                "expected_approval_required": True,
            },
        ),
        _gs_case(
            "gs-unsupported-gap-id-rejected",
            "unsupported gap id rejected",
            "An operator-supplied gap id outside the analyzed set is rejected with an error.",
            selected_gap_id="not-in-analysis",
            expect_error=True,
            reference={"expected_error": True},
        ),
        _gs_case(
            "gs-deterministic-rerun",
            "deterministic rerun",
            "Re-running selection on the same analysis reuses the existing selection artifact.",
            model_choice="gap-1",
            reference={
                "expected_selected_gap": "gap-1",
                "expected_status": "approved",
                "expected_reuse": True,
            },
        ),
    ],
)


# ---------------------------------------------------------------------------
# novelty-revalidation-v1 (Phase 7A.1): real Phase 5A/5B novelty pipeline over
# changing literature
# ---------------------------------------------------------------------------
# The workflow runs the REAL NoveltyValidationService.create_report twice —
# once against baseline fixture sources, once against changed sources — and
# records per-claim statuses, report/gate ids, supersession, and staleness.

_REV_BASE_FIXTURES = [
    {"match": "Identify novelty and contribution claims", "response": {"claims": []}},
    {"match": "Generate concrete literature-search queries", "response": {"queries": []}},
    {
        "match": "Judge whether the produced research output satisfies the reference.",
        "response": {"score": 1.0, "status": "pass", "explanation": "fixture"},
    },
    {
        "match": "grounded in the cited evidence",
        "response": {"verdict": "grounded", "explanation": "fixture"},
    },
    {
        "match": "You independently verify a prior-art assessment",
        "response": {"verdict": "concurs", "reasoning": "fixture critic concurs"},
    },
    {
        "match": "You recommend conservative rewording",
        "response": {"suggested_scope_change": "", "suggested_wording": ""},
    },
]


def _rev_claim(title: str, abstract: str) -> dict[str, Any]:
    return {
        "title": title,
        "abstract": abstract,
        "sections": {
            "introduction": (
                "Prior work examines platform pricing but not consumer welfare effects. "
                "We are the first to show that algorithmic pricing reduces consumer "
                "welfare in online markets. We study this question in a stylized model."
            ),
            "conclusion": "Our model connects pricing, welfare, and platform design.",
        },
    }


def _relevant_paper(doi: str, claim: str) -> dict[str, Any]:
    return {
        "title": "Algorithmic Pricing and Consumer Welfare in Online Markets",
        "abstract": f"We show that {claim}",
        "year": 2020,
        "doi": doi,
        "venue": "Journal of Platform Studies",
    }


def _irrelevant_paper() -> dict[str, Any]:
    return {
        "title": "Soil Microbiomes in Agricultural Systems",
        "abstract": "Microbial communities in agricultural soils.",
        "year": 2017,
        "doi": "10.6000/rev-irrelevant",
        "venue": "Agronomy",
    }


def _novelty_reval_case(
    case_id: str,
    name: str,
    description: str,
    *,
    changed_sources: list[dict[str, Any]] | str,
    reference: dict[str, Any],
    baseline_sources: list[dict[str, Any]] | str | None = None,
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "novelty_revalidation",
            "submission": _rev_claim(
                "Algorithmic Pricing and Consumer Welfare",
                "We study how algorithmic pricing shapes welfare in online markets.",
            ),
            "baseline_sources": baseline_sources if baseline_sources is not None else [],
            "changed_sources": changed_sources,
            "llm_fixtures": _REV_BASE_FIXTURES,
            "providers": ["semantic_scholar"],
            "as_of": "2026-08-01",
            "novelty_config": {
                "providers": ["semantic_scholar"],
                "max_queries_per_claim": 4,
            },
        },
        reference=reference,
        evaluation_dimensions=["novelty_revalidation"],
        tags=["novelty", "revalidation", "offline"],
    )


NOVELTY_REVALIDATION_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="novelty-revalidation-v1",
    version=1,
    name="Novelty Revalidation",
    description=(
        "Offline benchmark over the real Phase 5A/5B novelty pipeline across "
        "changing literature: the real NoveltyValidationService.create_report "
        "runs once against baseline fixture sources and again against changed "
        "sources. Verifies that new directly relevant / contradictory / "
        "mechanism-covering papers re-threaten claims, irrelevant papers do not "
        "invalidate novelty, unchanged literature keeps prior novelty reusable, "
        "stale artifacts are not silently reused, and superseding assessments "
        "preserve history."
    ),
    category="novelty_revalidation",
    config={"evaluators": ["evaluator.novelty_revalidation"]},
    cases=[
        _novelty_reval_case(
            "nvr-unchanged-literature-reusable",
            "unchanged literature -> prior novelty reusable",
            "Running validation again against unchanged literature produces the "
            "same clear verdict (the prior assessment is reusable).",
            baseline_sources=[],
            changed_sources=[],
            reference={
                "expected_overall_baseline": "clear",
                "expected_overall_changed": "clear",
                "expected_trigger": False,
            },
        ),
        _novelty_reval_case(
            "nvr-new-relevant-paper",
            "new directly relevant paper -> revalidation required",
            "A new directly relevant paper must re-threaten the novelty claim.",
            baseline_sources=[],
            changed_sources=[
                _relevant_paper(
                    "10.6000/rev-direct",
                    "algorithmic pricing reduces consumer welfare in online markets.",
                )
            ],
            reference={
                "expected_overall_baseline": "clear",
                "expected_overall_changed": "blocked",
                "expected_trigger": True,
                "expected_threatened": True,
            },
        ),
        _novelty_reval_case(
            "nvr-new-contradictory-evidence",
            "new contradictory evidence",
            "New evidence contradicting the claimed result re-threatens novelty.",
            baseline_sources=[],
            changed_sources=[
                _relevant_paper(
                    "10.6000/rev-contra",
                    "algorithmic pricing increases consumer welfare in online markets.",
                )
            ],
            reference={
                "expected_overall_baseline": "clear",
                "expected_overall_changed": "blocked",
                "expected_trigger": True,
                "expected_threatened": True,
            },
        ),
        _novelty_reval_case(
            "nvr-new-mechanism-coverage",
            "new paper covering claimed mechanism/gap",
            "A new paper covering the claimed mechanism/gap re-threatens novelty.",
            baseline_sources=[],
            changed_sources=[
                _relevant_paper(
                    "10.6000/rev-mech",
                    "algorithmic pricing reduces consumer welfare in online markets.",
                )
            ],
            reference={
                "expected_overall_baseline": "clear",
                "expected_overall_changed": "blocked",
                "expected_trigger": True,
                "expected_threatened": True,
            },
        ),
        _novelty_reval_case(
            "nvr-irrelevant-paper",
            "irrelevant new paper does not invalidate novelty",
            "Adding an unrelated paper must not invalidate the novelty claim.",
            baseline_sources=[],
            changed_sources=[_irrelevant_paper()],
            reference={
                "expected_overall_baseline": "clear",
                "expected_overall_changed": "clear",
                "expected_trigger": False,
                "expected_irrelevant": True,
            },
        ),
        _novelty_reval_case(
            "nvr-stale-not-silently-reused",
            "stale novelty artifact is not silently reused",
            "After the literature changes, the second validation must NOT reuse "
            "the first report's claim assessments; a fresh report supersedes it.",
            baseline_sources=[],
            changed_sources=[
                _relevant_paper(
                    "10.6000/rev-stale",
                    "algorithmic pricing reduces consumer welfare in online markets.",
                )
            ],
            reference={
                "expected_overall_baseline": "clear",
                "expected_overall_changed": "blocked",
                "expected_trigger": True,
                "expected_stale_reuse": False,
            },
        ),
        _novelty_reval_case(
            "nvr-supersession-preserves-history",
            "superseding assessment preserves history",
            "A new report supersedes the previous one; the old report is not "
            "mutated and provenance links both to the same package.",
            baseline_sources=[
                _relevant_paper(
                    "10.6000/rev-base",
                    "algorithmic pricing reduces consumer welfare in online markets.",
                )
            ],
            changed_sources=[
                _relevant_paper(
                    "10.6000/rev-base",
                    "algorithmic pricing reduces consumer welfare in online markets.",
                ),
                _relevant_paper(
                    "10.6000/rev-new",
                    "algorithmic pricing reduces consumer welfare in online markets.",
                ),
            ],
            reference={
                "expected_overall_baseline": "blocked",
                "expected_overall_changed": "blocked",
                "expected_trigger": False,
                "expected_supersession": True,
            },
        ),
    ],
)


# ---------------------------------------------------------------------------
# publication-packaging-v1 (Phase 7A.1): real Phase 4C formatter + exporters +
# submission package
# ---------------------------------------------------------------------------
# The workflow drives the REAL PublicationFormatterService over fixture papers
# + ManuscriptDraft sections: format -> bibliography -> validate -> exports
# (markdown/latex/docx/pdf -> BlobStore) -> SubmissionPackage. References reuse
# Phase 6B citation expectations where relevant (without duplicating that
# evaluator).


def _pkg_paper(
    paper_id: str,
    identity_id: str,
    title: str,
    *,
    authors: list[str] | None = None,
    year: int = 2021,
    venue: str = "Journal of Platform Studies",
    doi: str | None = None,
) -> dict[str, Any]:
    return {
        "id": paper_id,
        "identity_id": identity_id,
        "title": title,
        "authors": authors or ["Smith, Jane"],
        "year": year,
        "venue": venue,
        "doi": doi,
        "abstract": f"Abstract of {title}.",
    }


def _pkg_section(
    sec_id: str,
    title: str,
    body: str,
    citations: list[dict[str, Any]],
    *,
    section_id: str = "introduction",
    conditions_preserved: bool = True,
) -> dict[str, Any]:
    return {
        "id": sec_id,
        "section_id": section_id,
        "title": title,
        "body": body,
        "citations": citations,
        "conditions_preserved": conditions_preserved,
    }


def _pkg_cite(
    citation_id: str, identity_id: str, page_locator: str | None = None
) -> dict[str, Any]:
    return {
        "citation_id": citation_id,
        "paper_identity_id": identity_id,
        "page_locator": page_locator,
    }


def _packaging_case(
    case_id: str,
    name: str,
    description: str,
    *,
    papers: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    anonymous: bool = False,
    expected_status: str = "ready",
    expected_failure: bool = False,
    expected_placeholder: bool = False,
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "publication_packaging",
            "papers": papers,
            "sections": sections,
            "profile": {
                "name": "Benchmark Profile",
                "citation_style": "author_year",
                "anonymous_review": anonymous,
                "required_sections": ["introduction", "conclusion"],
                "section_order": ["introduction", "conclusion"],
            },
            "expected_package_status": expected_status,
            "expected_failure": expected_failure,
            "expected_placeholder": expected_placeholder,
        },
        reference={
            "expected_package_status": expected_status,
            "expected_failure": expected_failure,
            "expected_placeholder": expected_placeholder,
        },
        evaluation_dimensions=["publication_packaging"],
        tags=["packaging", "offline"],
    )


_PKG_PAPER_A = _pkg_paper(
    "pkg-paper-a",
    "pkg-identity-a",
    "Consumer Welfare Under Algorithmic Pricing",
    authors=["Smith, Jane"],
    year=2021,
    doi="10.6000/pkg-a",
)
_PKG_PAPER_B = _pkg_paper(
    "pkg-paper-b",
    "pkg-identity-b",
    "Dynamic Pricing and Seller Collusion",
    authors=["Doe, John"],
    year=2019,
    doi="10.6000/pkg-b",
)


def _pkg_intro(body: str, citations: list[dict[str, Any]]) -> dict[str, Any]:
    return _pkg_section("pkg-sec-1", "Introduction", body, citations, section_id="introduction")


def _pkg_conclusion(body: str) -> dict[str, Any]:
    return _pkg_section("pkg-sec-2", "Conclusion", body, [], section_id="conclusion")


PUBLICATION_PACKAGING_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="publication-packaging-v1",
    version=1,
    name="Publication Packaging",
    description=(
        "Offline benchmark over the real Phase 4C pipeline: ManuscriptDraft -> "
        "PublicationFormatterService (citation resolution + bibliography) -> "
        "deterministic validation -> exporters (Markdown/LaTeX/DOCX/PDF -> "
        "BlobStore) -> SubmissionPackage. Verifies citation resolution, "
        "unresolved-citation readiness blocking, bibliography dedup, missing-"
        "metadata non-invention, anonymous review, placeholder removal, export "
        "persistence, deterministic rerender, and invalid packages not marked "
        "publication-ready."
    ),
    category="publication_packaging",
    config={"evaluators": ["evaluator.publication_packaging"]},
    cases=[
        _packaging_case(
            "pkg-correct-citation-resolution",
            "correct citation resolution",
            "A valid manuscript formats, validates, exports, and packages as ready.",
            papers=[_PKG_PAPER_A, _PKG_PAPER_B],
            sections=[
                _pkg_intro(
                    "Prior work studies welfare effects of pricing [CITE:c1] and "
                    "collusion [CITE:c2].",
                    [_pkg_cite("c1", "pkg-identity-a"), _pkg_cite("c2", "pkg-identity-b")],
                ),
                _pkg_conclusion("We conclude with implications for platform design."),
            ],
            expected_status="ready",
        ),
        _packaging_case(
            "pkg-unresolved-citation-blocks-ready",
            "unresolved citation blocks readiness",
            "A manuscript citing a missing identity fails validation and the "
            "package is not marked ready.",
            papers=[_PKG_PAPER_A],
            sections=[
                _pkg_intro(
                    "Prior work studies welfare effects [CITE:c1].",
                    [_pkg_cite("c1", "pkg-identity-missing")],
                ),
                _pkg_conclusion("We conclude."),
            ],
            expected_status="failed",
            expected_failure=True,
        ),
        _packaging_case(
            "pkg-bibliography-dedup",
            "bibliography dedup",
            "Two citations to the same paper yield exactly one bibliography entry.",
            papers=[_PKG_PAPER_A],
            sections=[
                _pkg_intro(
                    "One view [CITE:c1] and another [CITE:c2] cite the same work.",
                    [_pkg_cite("c1", "pkg-identity-a"), _pkg_cite("c2", "pkg-identity-a")],
                ),
                _pkg_conclusion("We conclude."),
            ],
            expected_status="ready",
        ),
        _packaging_case(
            "pkg-missing-metadata-not-invented",
            "missing metadata not invented",
            "A paper with only a title renders without invented fields.",
            papers=[
                _pkg_paper("pkg-paper-s", "pkg-identity-s", "Working Paper on Quantity Games"),
            ],
            sections=[
                _pkg_intro(
                    "An unpublished note [CITE:c1] discusses quantity games.",
                    [_pkg_cite("c1", "pkg-identity-s")],
                ),
                _pkg_conclusion("We conclude."),
            ],
            expected_status="ready",
        ),
        _packaging_case(
            "pkg-anonymous-review-mode",
            "anonymous-review mode",
            "An anonymous profile renders without author front matter.",
            papers=[_PKG_PAPER_A],
            sections=[
                _pkg_intro(
                    "Prior work [CITE:c1] studies welfare effects.",
                    [_pkg_cite("c1", "pkg-identity-a")],
                ),
                _pkg_conclusion("We conclude."),
            ],
            anonymous=True,
            expected_status="ready",
        ),
        _packaging_case(
            "pkg-leftover-placeholder",
            "leftover internal placeholder",
            "An unresolved [CITE:ghost] left in the text fails validation and blocks readiness.",
            papers=[_PKG_PAPER_A],
            sections=[
                _pkg_intro(
                    "Prior work [CITE:c1] is relevant, and [CITE:ghost] is not.",
                    [_pkg_cite("c1", "pkg-identity-a")],
                ),
                _pkg_conclusion("We conclude."),
            ],
            expected_status="failed",
            expected_failure=True,
            expected_placeholder=True,
        ),
        _packaging_case(
            "pkg-markdown-latex-docx-pdf-exports",
            "Markdown/LaTeX/DOCX/PDF artifact generation",
            "All four export formats are generated and persisted to the blob store.",
            papers=[_PKG_PAPER_A],
            sections=[
                _pkg_intro(
                    "Prior work [CITE:c1] studies welfare effects.",
                    [_pkg_cite("c1", "pkg-identity-a")],
                ),
                _pkg_conclusion("We conclude."),
            ],
            expected_status="ready",
        ),
        _packaging_case(
            "pkg-invalid-not-publication-ready",
            "invalid package not marked publication-ready",
            "A manuscript failing validation yields a failed package, never ready.",
            papers=[_PKG_PAPER_A],
            sections=[
                _pkg_intro(
                    "Missing required section body.",
                    [_pkg_cite("c1", "pkg-identity-a")],
                ),
            ],
            expected_status="failed",
            expected_failure=True,
        ),
    ],
)


def _enrich_assess(relationship: str) -> dict[str, Any]:
    return {
        "dimensions": [
            {"dimension": "focal_phenomenon", "value": "match"},
            {"dimension": "setting", "value": "match"},
            {"dimension": "mechanism", "value": "match"},
            {"dimension": "theoretical_result", "value": "match"},
        ],
        "relationship": relationship,
        "assessment": f"fixture assessment: {relationship}",
    }


def _enrich_fixtures(
    titles: list[str], relationship: str = "direct_prior_art"
) -> list[dict[str, Any]]:
    fixtures: list[dict[str, Any]] = [dict(f) for f in _REV_BASE_FIXTURES]
    for title in titles:
        fixtures.append({"match": title, "response": _enrich_assess(relationship)})
    return fixtures


def _sparse_paper(title: str, doi: str) -> dict[str, Any]:
    """Title-only record: no year/venue/doi fields so the evidence basis is
    title_only; the DOI rides in external_identifiers so enrichment can still
    acquire it by identifier."""
    return {
        "title": title,
        "external_identifiers": [{"scheme": "doi", "value": doi}],
    }


def _abstract_paper(title: str, doi: str, abstract: str) -> dict[str, Any]:
    return {
        "title": title,
        "year": 2020,
        "venue": "Journal of Platform Studies",
        "doi": doi,
        "abstract": abstract,
    }


def _enrichment_case(
    case_id: str,
    name: str,
    description: str,
    *,
    source_sets: list[dict[str, Any]],
    reference: dict[str, Any],
) -> BenchmarkCaseDefinition:
    titles: list[str] = []
    for set_cfg in source_sets:
        for p in set_cfg.get("papers") or []:
            titles.append(str(p.get("title") or ""))
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "evidence_enrichment",
            "submission": _rev_claim(
                "Algorithmic Pricing and Consumer Welfare",
                "We study how algorithmic pricing shapes welfare in online markets.",
            ),
            "source_sets": source_sets,
            "llm_fixtures": _enrich_fixtures([t for t in titles if t]),
            "providers": ["semantic_scholar"],
            "as_of": "2026-08-01",
            "novelty_config": {
                "providers": ["semantic_scholar"],
                "max_queries_per_claim": 4,
                "acquire_abstract": True,
                "acquire_full_text": False,
            },
        },
        reference=reference,
        evaluation_dimensions=["evidence_enrichment"],
        tags=["evidence", "enrichment", "offline"],
    )


EVIDENCE_ENRICHMENT_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="evidence-enrichment-v1",
    version=1,
    name="Evidence Enrichment (Phase 5C-5D)",
    description=(
        "Offline benchmark over the real Phase 5C-5D evidence enrichment and "
        "pre-acquisition pipeline: the real NoveltyValidationService runs "
        "create_report with enrichment_enabled=True and preacquisition_enabled=True "
        "over fixture sources whose get() serves acquired abstracts. Verifies "
        "enrichment grounding, source preservation, unsupported-enrichment "
        "rejection (never fabricated evidence), stale-enrichment handling across "
        "a changed source set, and provenance/version correctness."
    ),
    category="evidence_enrichment",
    config={"evaluators": ["evaluator.evidence_enrichment"]},
    cases=[
        _enrichment_case(
            "enc-title-only-enriched",
            "title-only candidate enriched to abstract",
            "A sparse title-only candidate with a DOI is enriched to abstract "
            "evidence via the real provider_get_abstract path.",
            source_sets=[
                {
                    "label": "baseline",
                    "papers": [
                        _sparse_paper("Demand Dynamics in Platform Markets", "10.7000/enc-title")
                    ],
                    "get_hits": {
                        "10.7000/enc-title": _abstract_paper(
                            "Demand Dynamics in Platform Markets",
                            "10.7000/enc-title",
                            "We model demand-driven platform dynamics and characterize "
                            "the equilibrium quantities under demand uncertainty.",
                        )
                    },
                }
            ],
            reference={
                "expected_run_count": 1,
                "expected_outcomes": ["enriched"],
                "expected_before_basis": ["title_only"],
                "expected_after_basis": ["abstract"],
                "expected_attempt_statuses": ["success"],
                "expected_grounded": True,
                "expected_source_preserved": True,
                "expected_no_invented_evidence": False,
            },
        ),
        _enrichment_case(
            "enc-indexed-metadata-enriched",
            "indexed-metadata candidate enriched to abstract",
            "A candidate with indexed metadata (year/venue) but no abstract is "
            "enriched to abstract evidence.",
            source_sets=[
                {
                    "label": "baseline",
                    "papers": [
                        {
                            "title": "Platforms and Consumer Surplus",
                            "year": 2019,
                            "venue": "Journal of Platform Studies",
                            "doi": "10.7000/enc-meta",
                        }
                    ],
                    "get_hits": {
                        "10.7000/enc-meta": _abstract_paper(
                            "Platforms and Consumer Surplus",
                            "10.7000/enc-meta",
                            "We show that platform governance shapes consumer surplus.",
                        )
                    },
                }
            ],
            reference={
                "expected_run_count": 1,
                "expected_outcomes": ["enriched"],
                "expected_before_basis": ["indexed_metadata"],
                "expected_after_basis": ["abstract"],
                "expected_attempt_statuses": ["success"],
                "expected_grounded": True,
                "expected_no_invented_evidence": False,
            },
        ),
        _enrichment_case(
            "enc-unsupported-rejected",
            "unsupported enrichment rejected without invented evidence",
            "The source has no record for the DOI; enrichment fails and no "
            "fabricated evidence is produced.",
            source_sets=[
                {
                    "label": "baseline",
                    "papers": [
                        _sparse_paper("Missing Prior Platform Study", "10.7000/enc-missing")
                    ],
                    "get_hits": {},
                }
            ],
            reference={
                "expected_run_count": 1,
                "expected_outcomes": ["failed"],
                "expected_before_basis": ["title_only"],
                "expected_after_basis": ["title_only"],
                "expected_attempt_statuses": ["not_found"],
                "expected_no_invented_evidence": True,
                "expected_grounded": True,
            },
        ),
        _enrichment_case(
            "enc-restricted-no-invention",
            "restricted source rejected without invented evidence",
            "A rate-limited source never yields fabricated evidence.",
            source_sets=[
                {
                    "label": "baseline",
                    "papers": [
                        _sparse_paper("Restricted Platform Study", "10.7000/enc-restricted")
                    ],
                    "get_hits": {},
                    "get_errors": {"10.7000/enc-restricted": "rate_limited"},
                }
            ],
            reference={
                "expected_run_count": 1,
                "expected_outcomes": ["failed"],
                "expected_before_basis": ["title_only"],
                "expected_after_basis": ["title_only"],
                "expected_attempt_statuses": ["rate_limited"],
                "expected_no_invented_evidence": True,
            },
        ),
        _enrichment_case(
            "enc-preacquisition-upgrades",
            "pre-acquisition selects and upgrades sparse candidates",
            "Phase 5D pre-acquisition runs for the high-risk claim, selects "
            "sparse candidates and upgrades them with acquired abstracts.",
            source_sets=[
                {
                    "label": "baseline",
                    "papers": [
                        _sparse_paper("Preacquisition Platform Study A", "10.7000/enc-pa-a"),
                        _sparse_paper("Preacquisition Platform Study B", "10.7000/enc-pa-b"),
                    ],
                    "get_hits": {
                        "10.7000/enc-pa-a": _abstract_paper(
                            "Preacquisition Platform Study A",
                            "10.7000/enc-pa-a",
                            "We characterize equilibrium platform pricing.",
                        ),
                        "10.7000/enc-pa-b": _abstract_paper(
                            "Preacquisition Platform Study B",
                            "10.7000/enc-pa-b",
                            "We analyze platform competition and welfare.",
                        ),
                    },
                }
            ],
            reference={
                "expected_run_count": 1,
                "expected_outcomes": ["enriched"],
                "expected_attempt_statuses": ["success"],
                "expected_grounded": True,
                "expected_preacquisition": True,
                "expected_no_invented_evidence": False,
            },
        ),
        _enrichment_case(
            "enc-source-preservation",
            "enrichment preserves the original sparse source",
            "The original sparse paper record remains intact alongside the "
            "acquired abstract record; provenance links the execution to its "
            "plan/attempts/identity.",
            source_sets=[
                {
                    "label": "baseline",
                    "papers": [_sparse_paper("Preserved Sparse Study", "10.7000/enc-preserve")],
                    "get_hits": {
                        "10.7000/enc-preserve": _abstract_paper(
                            "Preserved Sparse Study",
                            "10.7000/enc-preserve",
                            "We preserve the original record while enriching it.",
                        )
                    },
                }
            ],
            reference={
                "expected_run_count": 1,
                "expected_outcomes": ["enriched"],
                "expected_attempt_statuses": ["success"],
                "expected_grounded": True,
                "expected_source_preserved": True,
            },
        ),
        _enrichment_case(
            "enc-stale-not-reused",
            "changed source set does not reuse stale enrichment",
            "When the source set changes so enrichment can no longer succeed, "
            "a fresh enrichment run fails instead of reusing the previous run's "
            "enriched execution.",
            source_sets=[
                {
                    "label": "baseline",
                    "papers": [_sparse_paper("Stale Enrichment Study", "10.7000/enc-stale-a")],
                    "get_hits": {
                        "10.7000/enc-stale-a": _abstract_paper(
                            "Stale Enrichment Study",
                            "10.7000/enc-stale-a",
                            "We characterize equilibrium platform pricing.",
                        )
                    },
                },
                {
                    "label": "changed",
                    "papers": [_sparse_paper("Stale Enrichment Study", "10.7000/enc-stale-b")],
                    "get_hits": {},
                },
            ],
            reference={
                "expected_run_count": 2,
                "expected_outcomes": ["enriched", "failed"],
                "expected_attempt_statuses": ["success", "not_found"],
                "expected_grounded": True,
                "expected_executions_differ": True,
                "expected_no_invented_evidence": False,
            },
        ),
    ],
)


def _routing_entry(
    candidate_id: str,
    *,
    det: float | None = 1.0,
    benchmark: float = 1.0,
    cost: float | None = None,
    cost_per: float | None = None,
    latency: float | None = None,
    structured: float | None = 1.0,
    error: float | None = 0.0,
    eligibility: str = "eligible",
    provider: str = "openrouter",
) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "model": {
            "candidate_id": candidate_id,
            "provider": provider,
            "requested_model": f"m-{candidate_id}",
            "structured_output": True,
        },
        "resolved_model": f"m-{candidate_id}",
        "rank": 0,
        "eligibility": eligibility,
        "deterministic_pass_rate": det,
        "benchmark_pass_rate": benchmark,
        "case_pass_rate": det,
        "structured_output_success_rate": structured,
        "model_error_rate": error,
        "retry_rate": 0.0,
        "latency_ms_p50": latency,
        "latency_ms_mean": latency,
        "latency_ms_p95": latency,
        "estimated_cost": cost,
        "cost_per_successful_case": cost_per,
        "cost_per_successful_benchmark": cost_per,
        "advisory_score": None,
        "caveats": [],
    }


def _routing_leaderboard(
    role: str,
    entries: list[dict[str, Any]],
    *,
    repetitions: int = 1,
    age_seconds: float | None = None,
) -> dict[str, Any]:
    created = (
        datetime.now(UTC) - timedelta(seconds=age_seconds) if age_seconds else datetime.now(UTC)
    )
    return {
        "id": None,
        "role": role,
        "plan_id": "routing-plan",
        "tournament_run_id": "routing-run",
        "plan_hash": "routing-hash",
        "ranking_rules": {},
        "entries": entries,
        "created_at": created.isoformat(),
        "metadata": {"repetitions": repetitions},
    }


def _routing_case(
    case_id: str,
    name: str,
    description: str,
    *,
    role: str,
    policy: str,
    leaderboards: list[dict[str, Any]],
    reference: dict[str, Any],
    request: dict[str, Any] | None = None,
    capabilities: dict[str, Any] | None = None,
    current_roles: dict[str, Any] | None = None,
    use_fallback: bool = False,
    shadow: bool = False,
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "model_routing",
            "role": role,
            "policy": policy,
            "use_fallback": use_fallback,
            "shadow": shadow,
            "request": request or {},
            "leaderboards": leaderboards,
            "capabilities": capabilities
            or {"openrouter": {"structured_output": True, "context_length": None}},
            "current_roles": current_roles or {},
        },
        reference=reference,
        evaluation_dimensions=["model_routing"],
        tags=["routing", "policy", "offline"],
    )


MODEL_ROUTING_POLICY_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="model-routing-policy-v1",
    version=1,
    name="Model Routing Policy (Phase 7C)",
    description=(
        "Offline benchmark over the real policy-constrained router (shadow mode "
        "only). Synthetic RoleLeaderboard fixtures drive decide()/shadow() under "
        "quality_first / balanced / cost_constrained / latency_constrained "
        "policies. Verifies eligibility gates, capability compatibility, "
        "constraint satisfaction, stale/insufficient-evidence handling, "
        "fallbacks, deterministic tie-breaking, role isolation, and that "
        "unsafe (ineligible) selections never happen."
    ),
    category="model_routing",
    config={"evaluators": ["evaluator.model_routing"]},
    cases=[
        _routing_case(
            "route-quality-first",
            "quality-first chooses highest eligible correctness",
            "Among eligible candidates the quality-first policy selects the "
            "highest deterministic quality, ignoring a cheaper lower-quality model.",
            role="reasoning",
            policy="quality_first",
            leaderboards=[
                _routing_leaderboard(
                    "reasoning",
                    [
                        _routing_entry("good", det=0.98, latency=120.0, cost=0.01, cost_per=0.02),
                        _routing_entry(
                            "cheap", det=0.86, latency=5.0, cost=0.0001, cost_per=0.0002
                        ),
                    ],
                )
            ],
            reference={
                "expected_status": "selected",
                "expected_selected": "good",
                "expected_role": "reasoning",
                "expected_policy": "quality_first",
                "expected_eligible_count": 2,
                "expected_no_unsafe": True,
            },
        ),
        _routing_case(
            "route-cheap-failing-rejected",
            "cheaper failing model rejected",
            "A cheap model below the required deterministic quality is rejected "
            "even though it is far cheaper.",
            role="reasoning",
            policy="cost_constrained",
            request={"required_deterministic_pass_rate": 0.9},
            leaderboards=[
                _routing_leaderboard(
                    "reasoning",
                    [
                        _routing_entry("good", det=0.95, latency=100.0, cost=0.05, cost_per=0.06),
                        _routing_entry(
                            "failing", det=0.7, latency=3.0, cost=0.0001, cost_per=0.0002
                        ),
                    ],
                )
            ],
            reference={
                "expected_status": "selected",
                "expected_selected": "good",
                "expected_role": "reasoning",
                "expected_rejected": ["failing"],
                "expected_eligible_count": 1,
            },
        ),
        _routing_case(
            "route-cost-constrained",
            "cost-constrained chooses cheapest quality-qualified model",
            "With cost_constrained, the cheapest model that still clears the "
            "minimum quality gate is selected.",
            role="reasoning",
            policy="cost_constrained",
            request={"required_deterministic_pass_rate": 0.85},
            leaderboards=[
                _routing_leaderboard(
                    "reasoning",
                    [
                        _routing_entry("premium", det=0.99, latency=200.0, cost=0.1, cost_per=0.12),
                        _routing_entry("mid", det=0.9, latency=80.0, cost=0.01, cost_per=0.02),
                        _routing_entry(
                            "cheap-low", det=0.82, latency=10.0, cost=0.001, cost_per=0.002
                        ),
                    ],
                )
            ],
            reference={
                "expected_status": "selected",
                "expected_selected": "mid",
                "expected_rejected": ["cheap-low"],
                "expected_eligible_count": 2,
            },
        ),
        _routing_case(
            "route-latency-constrained",
            "latency-constrained chooses fastest eligible model",
            "With latency_constrained, the fastest model that clears the quality gate is selected.",
            role="reasoning",
            policy="latency_constrained",
            request={"required_deterministic_pass_rate": 0.85},
            leaderboards=[
                _routing_leaderboard(
                    "reasoning",
                    [
                        _routing_entry("fast", det=0.87, latency=12.0, cost=0.02, cost_per=0.03),
                        _routing_entry("slow", det=0.99, latency=400.0, cost=0.1, cost_per=0.12),
                    ],
                )
            ],
            reference={
                "expected_status": "selected",
                "expected_selected": "fast",
                "expected_eligible_count": 2,
            },
        ),
        _routing_case(
            "route-structured-output-rejected",
            "model without structured-output capability rejected",
            "A candidate whose provider lacks structured-output capability is "
            "rejected when the request requires structured output.",
            role="reasoning",
            policy="quality_first",
            leaderboards=[
                _routing_leaderboard(
                    "reasoning",
                    [
                        _routing_entry("good", det=0.97, latency=50.0, cost=0.02, cost_per=0.03),
                        _routing_entry(
                            "no-so",
                            det=0.99,
                            latency=1.0,
                            cost=0.0001,
                            cost_per=0.0002,
                            provider="legacy",
                        ),
                    ],
                )
            ],
            capabilities={
                "openrouter": {"structured_output": True, "context_length": None},
                "legacy": {"structured_output": False, "context_length": None},
            },
            reference={
                "expected_status": "selected",
                "expected_selected": "good",
                "expected_rejected": ["no-so"],
                "expected_eligible_count": 1,
            },
        ),
        _routing_case(
            "route-missing-cost",
            "missing cost handled without invention",
            "A candidate with unknown cost cannot satisfy a max-cost constraint "
            "and is rejected (cost is never invented); a priced eligible model "
            "is selected.",
            role="reasoning",
            policy="cost_constrained",
            request={"max_estimated_cost": 0.05},
            leaderboards=[
                _routing_leaderboard(
                    "reasoning",
                    [
                        _routing_entry("priced", det=0.9, latency=60.0, cost=0.01, cost_per=0.02),
                        _routing_entry(
                            "unknown-cost", det=0.98, latency=30.0, cost=None, cost_per=None
                        ),
                    ],
                )
            ],
            reference={
                "expected_status": "selected",
                "expected_selected": "priced",
                "expected_rejected": ["unknown-cost"],
                "expected_eligible_count": 1,
            },
        ),
        _routing_case(
            "route-stale-leaderboard",
            "stale leaderboard rejected",
            "Evidence older than the configured freshness threshold is treated "
            "as insufficient evidence, never a silent choice.",
            role="reasoning",
            policy="quality_first",
            request={"leaderboard_max_age_seconds": 100},
            leaderboards=[
                _routing_leaderboard(
                    "reasoning",
                    [_routing_entry("old", det=0.98, latency=50.0)],
                    age_seconds=5000,
                )
            ],
            reference={
                "expected_status": "insufficient_evidence",
                "expected_selected": None,
                "expected_eligible_count": 0,
            },
        ),
        _routing_case(
            "route-insufficient-repetitions",
            "insufficient repetitions rejected",
            "Evidence built from too few repetitions is insufficient evidence.",
            role="reasoning",
            policy="quality_first",
            request={"min_repetitions": 3},
            leaderboards=[
                _routing_leaderboard(
                    "reasoning",
                    [_routing_entry("only-one", det=0.98, latency=50.0)],
                    repetitions=1,
                )
            ],
            reference={
                "expected_status": "insufficient_evidence",
                "expected_selected": None,
                "expected_eligible_count": 0,
            },
        ),
        _routing_case(
            "route-no-eligible",
            "no eligible candidate",
            "When every candidate is below the required quality threshold, no model is selected.",
            role="reasoning",
            policy="quality_first",
            request={"required_deterministic_pass_rate": 0.95},
            leaderboards=[
                _routing_leaderboard(
                    "reasoning",
                    [
                        _routing_entry("a", det=0.6, latency=10.0),
                        _routing_entry("b", det=0.7, latency=20.0),
                    ],
                )
            ],
            reference={
                "expected_status": "no_eligible_model",
                "expected_selected": None,
                "expected_eligible_count": 0,
                "expected_rejected": ["a", "b"],
            },
        ),
        _routing_case(
            "route-deterministic-tiebreak",
            "deterministic tie-breaking",
            "Identical quality/latency/cost candidates break ties "
            "deterministically by candidate_id.",
            role="reasoning",
            policy="quality_first",
            leaderboards=[
                _routing_leaderboard(
                    "reasoning",
                    [
                        _routing_entry("beta", det=1.0, latency=10.0, cost=0.01, cost_per=0.02),
                        _routing_entry("alpha", det=1.0, latency=10.0, cost=0.01, cost_per=0.02),
                    ],
                )
            ],
            reference={
                "expected_status": "selected",
                "expected_selected": "alpha",
                "expected_tiebreak": "alpha",
                "expected_eligible_count": 2,
            },
        ),
        _routing_case(
            "route-fallback",
            "fallback selection",
            "With use_fallback, the second-best eligible model is returned as "
            "the approved fallback and the primary is preserved.",
            role="reasoning",
            policy="quality_first",
            use_fallback=True,
            leaderboards=[
                _routing_leaderboard(
                    "reasoning",
                    [
                        _routing_entry("best", det=0.99, latency=100.0, cost=0.1, cost_per=0.12),
                        _routing_entry("second", det=0.95, latency=50.0, cost=0.02, cost_per=0.03),
                        _routing_entry("worst", det=0.5, latency=5.0, cost=0.001, cost_per=0.002),
                    ],
                )
            ],
            reference={
                "expected_status": "fallback",
                "expected_selected": "second",
                "expected_fallback": "best",
                "expected_eligible_count": 2,
            },
        ),
        _routing_case(
            "route-role-isolation",
            "role isolation: reasoning evidence cannot route a critic task",
            "A critic routing request must never use reasoning-leaderboard "
            "evidence; with no critic leaderboard the decision is "
            "insufficient_evidence.",
            role="critic",
            policy="quality_first",
            leaderboards=[
                _routing_leaderboard(
                    "reasoning",
                    [_routing_entry("reasoning-win", det=0.99, latency=50.0)],
                )
            ],
            reference={
                "expected_status": "insufficient_evidence",
                "expected_selected": None,
                "expected_role": "critic",
                "expected_eligible_count": 0,
            },
        ),
    ],
)


def _lq_paper(title: str, doi: str, abstract: str) -> dict[str, Any]:
    return {
        "title": title,
        "abstract": abstract,
        "year": 2020,
        "venue": "Journal of Platform Studies",
        "doi": doi,
    }


def _lq_case(
    case_id: str,
    name: str,
    description: str,
    *,
    workflow: str,
    task: str,
    input_extra: dict[str, Any],
    reference: dict[str, Any],
    evaluator: str,
    tags: list[str],
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": workflow,
            "task": task,
            **input_extra,
        },
        reference=reference,
        evaluation_dimensions=[task],
        tags=tags,
    )


def _lq_evidence_statement(statement: str) -> dict[str, Any]:
    return {"statement": statement, "category": "result"}


def _lq_statement(statement: str, *, type_: str = "consensus") -> dict[str, Any]:
    return {
        "statement": statement,
        "type": type_,
        "evidence_ids": [],
        "paper_ids": [],
        "conflicting_evidence_ids": [],
        "conflicting_paper_ids": [],
        "support_type": "single_paper",
        "confidence": 0.9,
    }


LIVE_QUALITY_REASONING_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="live-quality-reasoning-v1",
    version=1,
    name="Live Quality — Reasoning",
    description=(
        "Live-quality benchmark over the REAL reasoning production pipelines "
        "(evidence extraction, synthesis, gap analysis, mechanism generation, "
        "model specification, proposition generation). Model-agnostic inputs; "
        "structural/semantic references; deterministic evaluator. Runs against "
        "a real model via the live-quality service; not a scripted-fixture proxy."
    ),
    category="live_quality",
    config={"evaluators": ["evaluator.live_quality_reasoning"]},
    cases=[
        _lq_case(
            "lq-evidence-extraction",
            "evidence extraction",
            "Real evidence extractor over a realistic algorithmic-pricing document.",
            workflow="evidence_extraction",
            task="evidence_extraction",
            input_extra={
                "documents": [
                    {
                        "title": "Algorithmic Pricing and Consumer Welfare in Online Markets",
                        "year": 2020,
                        "venue": "Journal of Platform Studies",
                        "abstract": "We study how algorithmic pricing shapes consumer welfare.",
                        "text_status": "extracted",
                        "pages": [
                            {
                                "page": 1,
                                "text": "Sellers increasingly use algorithmic pricing, setting "
                                "prices from demand data. Prior work examines platform pricing "
                                "but not the resulting consumer welfare effects in online markets.",
                            },
                            {
                                "page": 2,
                                "text": "Our model shows that algorithmic pricing can reduce "
                                "consumer welfare when demand is uncertain, because personalized "
                                "price discrimination extracts consumer surplus.",
                            },
                        ],
                    }
                ],
                "evidence_config": {
                    "pages_per_chunk": 4,
                    "max_chunks_per_document": 10,
                    "max_model_calls": 20,
                },
            },
            reference={
                "task": "evidence_extraction",
                "required_concepts": ["algorithmic pricing", "consumer welfare"],
            },
            evaluator="evaluator.live_quality_reasoning",
            tags=["live_quality", "reasoning", "offline"],
        ),
        _lq_case(
            "lq-literature-synthesis",
            "literature synthesis",
            "Real synthesizer over realistic evidence statements.",
            workflow="literature_synthesis",
            task="literature_synthesis",
            input_extra={
                "papers": [
                    _lq_paper(
                        "Algorithmic Pricing and Consumer Welfare",
                        "10.7000/lq-syn-a",
                        "We show algorithmic pricing reduces consumer welfare.",
                    ),
                    _lq_paper(
                        "Data-Driven Pricing and Competition",
                        "10.7000/lq-syn-b",
                        "Data-driven pricing intensifies seller competition.",
                    ),
                ],
                "evidence": [
                    _lq_evidence_statement(
                        "Algorithmic pricing can reduce consumer welfare in online markets."
                    ),
                    _lq_evidence_statement(
                        "Data-driven pricing intensifies competition among sellers."
                    ),
                ],
                "profiles": [
                    {"paper_index": 0, "evidence_indexes": [0]},
                    {"paper_index": 1, "evidence_indexes": [1]},
                ],
                "synthesis_config": {"batch_profiles": 2, "max_batches": 4, "max_model_calls": 20},
            },
            reference={
                "task": "literature_synthesis",
                "required_concepts": ["algorithmic pricing", "welfare"],
            },
            evaluator="evaluator.live_quality_reasoning",
            tags=["live_quality", "reasoning", "offline"],
        ),
        _lq_case(
            "lq-gap-analysis",
            "gap analysis",
            "Real gap analyzer over realistic synthesis statements.",
            workflow="gap_analysis",
            task="gap_analysis",
            input_extra={
                "evidence": [
                    _lq_evidence_statement("Algorithmic pricing can reduce consumer welfare.")
                ],
                "profiles": [{"paper_index": 0, "evidence_indexes": [0]}],
                "statements": [
                    _lq_statement(
                        "Algorithmic pricing affects consumer welfare but the welfare mechanism "
                        "is not fully understood."
                    )
                ],
                "themes": [
                    {
                        "title": "Algorithmic pricing and welfare",
                        "dimension": "welfare",
                        "statement_indexes": [0],
                    }
                ],
                "documents_without_evidence": [],
                "gap_config": {"max_statements": 10, "max_gaps": 5, "max_model_calls": 10},
            },
            reference={
                "task": "gap_analysis",
                "allowed_gap_types": [
                    "mechanism_gap",
                    "empirical_gap",
                    "theoretical_gap",
                    "context_gap",
                ],
                "required_concepts": ["algorithmic pricing"],
            },
            evaluator="evaluator.live_quality_reasoning",
            tags=["live_quality", "reasoning", "offline"],
        ),
        _lq_case(
            "lq-mechanism-development",
            "mechanism generation",
            "Real mechanism generator over a realistic research gap.",
            workflow="mechanism_development",
            task="mechanism_development",
            input_extra={
                "evidence": [
                    _lq_evidence_statement("Algorithmic pricing can reduce consumer welfare.")
                ],
                "statements": [
                    _lq_statement(
                        "Algorithmic pricing may reduce welfare through extraction of consumer surplus."
                    )
                ],
                "gap": {
                    "title": "Mechanism linking algorithmic pricing to lower welfare",
                    "gap_type": "mechanism_gap",
                    "description": "The mechanism by which algorithmic pricing reduces welfare is unclear.",
                    "statement_ids": [],
                    "evidence_ids": [],
                    "contradiction_statement_ids": [],
                    "strength": "tentative",
                    "confidence": 0.8,
                },
            },
            reference={
                "task": "mechanism_development",
                "required_concepts": ["algorithmic pricing"],
            },
            evaluator="evaluator.live_quality_reasoning",
            tags=["live_quality", "reasoning", "offline"],
        ),
        _lq_case(
            "lq-model-specification",
            "model specification",
            "Real model builder over a realistic mechanism.",
            workflow="analytical_model_specification",
            task="analytical_model_specification",
            input_extra={
                "statements": [
                    _lq_statement(
                        "Algorithmic pricing can reduce consumer welfare when demand is uncertain."
                    )
                ],
                "mechanism": {
                    "name": "Surplus extraction under algorithmic pricing",
                    "description": "Sellers use demand data to price-discriminate, extracting consumer surplus.",
                    "causal_logic": "More data enables finer price discrimination, transferring surplus from consumers to sellers.",
                    "actors": ["seller", "consumer"],
                    "strategic_interactions": "seller sets price, consumer buys",
                    "information_structure": "seller observes demand signals",
                    "incentives": "seller maximizes profit",
                    "boundary_conditions": "uncertain demand",
                },
                "model_config": {"max_actors": 8, "max_variables": 40},
            },
            reference={
                "task": "analytical_model_specification",
                "required_model_structure": [
                    "actors",
                    "variables",
                    "parameters",
                    "payoffs",
                    "timing",
                ],
            },
            evaluator="evaluator.live_quality_reasoning",
            tags=["live_quality", "reasoning", "offline"],
        ),
        _lq_case(
            "lq-proposition-generation",
            "proposition generation",
            "Real proposition generator over a verified equilibrium.",
            workflow="proposition_generation",
            task="proposition_generation",
            input_extra={
                "model": {
                    "title": "Algorithmic Pricing and Consumer Surplus",
                    "actors": [{"id": "seller", "name": "Seller"}],
                    "variables": [
                        {
                            "symbol": "p",
                            "name": "price",
                            "meaning": "retail price",
                            "domain": "R_+",
                            "kind": "decision_variable",
                            "owner_actor_id": "seller",
                        }
                    ],
                    "parameters": [
                        {"symbol": "c", "name": "cost", "meaning": "marginal cost", "domain": "R_+"}
                    ],
                    "timing": [
                        {
                            "stage_number": 0,
                            "name": "pricing",
                            "description": "seller sets price",
                            "actor_ids": ["seller"],
                        }
                    ],
                    "payoffs": [
                        {
                            "actor_id": "seller",
                            "expression": {
                                "expression": "(p - c) * (10 - p)",
                                "symbols_used": ["p", "c"],
                            },
                            "decision_variables": ["p"],
                            "parameters": ["c"],
                        }
                    ],
                },
                "candidate": {
                    "expressions": [
                        {
                            "label": "price",
                            "expression": "(p - c) * (10 - p)",
                            "decision_variables": ["p"],
                            "parameters": ["c"],
                        }
                    ],
                    "decision_variables": ["p"],
                    "method": "symbolic",
                    "solution_order": ["price"],
                },
            },
            reference={
                "task": "proposition_generation",
                "required_concepts": ["algorithmic pricing"],
            },
            evaluator="evaluator.live_quality_reasoning",
            tags=["live_quality", "reasoning", "offline"],
        ),
    ],
)


def _lq_critic_fixtures(task: str) -> dict[str, list[dict[str, Any]]]:
    if task == "mechanism_critique":
        return {
            "research_gap": [
                {
                    "title": "Mechanism linking algorithmic pricing to lower welfare",
                    "gap_type": "mechanism_gap",
                    "description": "The mechanism by which algorithmic pricing reduces welfare is unclear.",
                }
            ],
            "mechanism_candidate": [
                {
                    "gap_id": "{research_gap#0}",
                    "gap_selection_id": "lq-gap-selection",
                    "name": "Surplus extraction mechanism",
                    "description": "Sellers use demand data to price-discriminate.",
                    "causal_logic": "The mechanism's direction is ambiguous and it omits the consumer's strategic incentives.",
                    "actors": ["seller"],
                    "literature_support_ids": [],
                }
            ],
        }
    if task == "model_critique":
        return {
            "selected_mechanism": [
                {
                    "gap_id": "lq-gap",
                    "gap_selection_id": "lq-gap-selection",
                    "mechanism_candidate_id": "lq-mech",
                    "name": "Surplus extraction",
                    "description": "Sellers extract surplus.",
                    "causal_logic": "Data enables price discrimination.",
                }
            ],
            "formal_analytical_model": [
                {
                    "selected_mechanism_id": "{selected_mechanism#0}",
                    "title": "Algorithmic Pricing Model",
                    "description": "A model of surplus extraction. The seller's payoff is inconsistent with its price setting and the consumer is not a strategic actor.",
                    "actors": [
                        {"actor_id": "seller", "name": "Seller", "strategic": True},
                        {"actor_id": "consumer", "name": "Consumer", "strategic": False},
                    ],
                    "variables": [
                        {
                            "symbol": "p",
                            "name": "price",
                            "meaning": "retail price",
                            "domain": "R_+",
                            "kind": "decision_variable",
                            "owner_actor_id": "seller",
                        }
                    ],
                    "parameters": [
                        {"symbol": "c", "name": "cost", "meaning": "marginal cost", "domain": "R_+"}
                    ],
                    "timing": [
                        {
                            "stage_number": 0,
                            "name": "pricing",
                            "description": "seller sets price",
                            "actor_ids": ["seller"],
                        }
                    ],
                    "payoffs": [
                        {
                            "actor_id": "seller",
                            "expression": {"expression": "10 - p", "symbols_used": ["p"]},
                            "decision_variables": ["p"],
                            "parameters": [],
                        }
                    ],
                }
            ],
        }
    if task == "proposition_critique":
        return {
            "formal_analytical_model": [
                {
                    "selected_mechanism_id": "lq-mech",
                    "title": "Algorithmic Pricing Model",
                    "description": "A model of surplus extraction.",
                    "actors": [{"actor_id": "seller", "name": "Seller", "strategic": True}],
                    "variables": [
                        {
                            "symbol": "p",
                            "name": "price",
                            "meaning": "retail price",
                            "domain": "R_+",
                            "kind": "decision_variable",
                            "owner_actor_id": "seller",
                        }
                    ],
                    "parameters": [
                        {"symbol": "c", "name": "cost", "meaning": "marginal cost", "domain": "R_+"}
                    ],
                    "timing": [
                        {
                            "stage_number": 0,
                            "name": "pricing",
                            "description": "seller sets price",
                            "actor_ids": ["seller"],
                        }
                    ],
                    "payoffs": [
                        {
                            "actor_id": "seller",
                            "expression": {
                                "expression": "(p - c) * (10 - p)",
                                "symbols_used": ["p", "c"],
                            },
                            "decision_variables": ["p"],
                            "parameters": ["c"],
                        }
                    ],
                }
            ],
            "proposition": [
                {
                    "model_id": "{formal_analytical_model#0}",
                    "equilibrium_candidate_id": "lq-eq",
                    "comparative_statics_analysis_id": "lq-cs",
                    "statement": "Algorithmic pricing ALWAYS increases consumer welfare in every market.",
                    "claim_type": "monotonicity",
                    "conditions": [],
                }
            ],
        }
    if task == "results_critique":
        return {
            "research_finding": [
                {
                    "model_id": "lq-model",
                    "equilibrium_candidate_id": "lq-eq",
                    "statement": "Algorithmic pricing reduces consumer welfare.",
                    "finding_type": "analytical_result",
                    "supporting_proposition_ids": ["lq-prop"],
                    "conditions": [],
                }
            ],
            "contribution_claim": [
                {
                    "gap_id": "lq-gap",
                    "finding_ids": ["lq-finding"],
                    "claim": "We prove algorithmic pricing causes large welfare losses.",
                    "contribution_type": "theoretical",
                    "novelty_claim": "this is a definitive welfare statement",
                    "novelty_normalized": False,
                }
            ],
            "research_results_package": [
                {
                    "research_question_id": "lq-rq",
                    "gap_id": "lq-gap",
                    "selected_mechanism_id": "lq-mech",
                    "model_id": "lq-model",
                    "equilibrium_analysis_id": "lq-ea",
                    "equilibrium_candidate_id": "lq-eq",
                    "finding_ids": ["{research_finding#0}"],
                    "contribution_claim_ids": ["{contribution_claim#0}"],
                    "implication_ids": [],
                    "limitations": [],
                    "status": "assembled",
                    "summary": "Results package.",
                }
            ],
        }
    # manuscript_critique
    return {
        "contribution_claim": [
            {
                "gap_id": "lq-gap",
                "finding_ids": ["lq-finding"],
                "claim": "Algorithmic pricing reduces consumer welfare.",
                "contribution_type": "theoretical",
                "novelty_claim": "",
                "novelty_normalized": True,
            }
        ],
        "research_results_package": [
            {
                "research_question_id": "lq-rq",
                "gap_id": "lq-gap",
                "selected_mechanism_id": "lq-mech",
                "model_id": "lq-model",
                "equilibrium_analysis_id": "lq-ea",
                "equilibrium_candidate_id": "lq-eq",
                "finding_ids": ["lq-finding"],
                "contribution_claim_ids": ["{contribution_claim#0}"],
                "implication_ids": [],
                "limitations": ["small model"],
                "status": "assembled",
                "summary": "Results package.",
            }
        ],
        "manuscript_draft": [
            {
                "outline_id": "lq-outline",
                "results_package_id": "{research_results_package#0}",
                "title": "Algorithmic Pricing and Consumer Welfare",
                "section_ids": ["{manuscript_section#0}", "{manuscript_section#1}"],
            }
        ],
        "manuscript_section": [
            {
                "outline_id": "lq-outline",
                "section_id": "introduction",
                "title": "Introduction",
                "body": "We study algorithmic pricing. Our model shows prices increase [CITE:smith2019]. "
                "Algorithmic pricing increases consumer welfare in our benchmark setting.",
                "claims": [
                    {
                        "text": "Algorithmic pricing increases consumer welfare.",
                        "grounding_artifact_id": "lq-finding",
                    }
                ],
                "citations": [
                    {
                        "citation_id": "smith2019",
                        "paper_identity_id": "lq-paper",
                        "evidence_item_id": "lq-evidence",
                    }
                ],
            },
            {
                "outline_id": "lq-outline",
                "section_id": "limitations",
                "title": "Limitations",
                "body": "Our analysis abstracts from entry dynamics.",
                "claims": [],
                "citations": [],
            },
        ],
    }


LIVE_QUALITY_CRITIC_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="live-quality-critic-v1",
    version=1,
    name="Live Quality — Critic",
    description=(
        "Live-quality benchmark over the REAL production critic services "
        "(mechanism, model, proposition, results, manuscript). Reference cases "
        "contain known INJECTED DEFECTS; the evaluator measures how well the "
        "critic's model output detects them (defect_recall, false_positive_rate, "
        "severity_accuracy, coverage, actionable revisions)."
    ),
    category="live_quality",
    config={"evaluators": ["evaluator.live_quality_critic"]},
    cases=[
        _lq_case(
            "lq-mechanism-critique",
            "mechanism critique with injected defects",
            "Real mechanism critic against a candidate with an unclear causal "
            "direction and a missing strategic actor.",
            workflow="lq_critique",
            task="mechanism_critique",
            input_extra={
                "target_artifact_type": "mechanism_candidate",
                "fixtures": _lq_critic_fixtures("mechanism_critique"),
            },
            reference={
                "task": "mechanism_critique",
                "injected_defects": [
                    {"category": "unclear_causal_direction", "severity": "high"},
                    {"category": "missing_actor_or_incentive", "severity": "medium"},
                ],
            },
            evaluator="evaluator.live_quality_critic",
            tags=["live_quality", "critic", "offline"],
        ),
        _lq_case(
            "lq-model-critique",
            "model-specification critique with injected defects",
            "Real model-specification critic against a model with a payoff "
            "inconsistency and a missing strategic actor.",
            workflow="lq_critique",
            task="model_critique",
            input_extra={
                "target_artifact_type": "formal_analytical_model",
                "fixtures": _lq_critic_fixtures("model_critique"),
            },
            reference={
                "task": "model_critique",
                "injected_defects": [
                    {"category": "payoff_inconsistency", "severity": "high"},
                    {"category": "missing_strategic_actor", "severity": "medium"},
                ],
            },
            evaluator="evaluator.live_quality_critic",
            tags=["live_quality", "critic", "offline"],
        ),
        _lq_case(
            "lq-proposition-critique",
            "proposition critique with injected defects",
            "Real proposition critic against an overclaiming proposition with missing conditions.",
            workflow="lq_critique",
            task="proposition_critique",
            input_extra={
                "target_artifact_type": "proposition",
                "fixtures": _lq_critic_fixtures("proposition_critique"),
            },
            reference={
                "task": "proposition_critique",
                "injected_defects": [
                    {"category": "overclaiming", "severity": "high"},
                    {"category": "missing_conditions", "severity": "medium"},
                ],
            },
            evaluator="evaluator.live_quality_critic",
            tags=["live_quality", "critic", "offline"],
        ),
        _lq_case(
            "lq-results-critique",
            "results critique with injected defects",
            "Real results critic against a package with a causal overstatement.",
            workflow="lq_critique",
            task="results_critique",
            input_extra={
                "target_artifact_type": "research_results_package",
                "fixtures": _lq_critic_fixtures("results_critique"),
            },
            reference={
                "task": "results_critique",
                "injected_defects": [{"category": "causal_overstatement", "severity": "high"}],
            },
            evaluator="evaluator.live_quality_critic",
            tags=["live_quality", "critic", "offline"],
        ),
        _lq_case(
            "lq-manuscript-critique",
            "manuscript critique with injected defects",
            "Real manuscript critic against a draft with a cross-section "
            "inconsistency (intro vs conclusion) that no deterministic check "
            "catches.",
            workflow="lq_critique",
            task="manuscript_critique",
            input_extra={
                "target_artifact_type": "manuscript_draft",
                "fixtures": _lq_critic_fixtures("manuscript_critique"),
            },
            reference={
                "task": "manuscript_critique",
                "injected_defects": [
                    {"category": "cross_section_inconsistency", "severity": "high"}
                ],
            },
            evaluator="evaluator.live_quality_critic",
            tags=["live_quality", "critic", "offline"],
        ),
    ],
)


LIVE_QUALITY_FAST_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="live-quality-fast-v1",
    version=1,
    name="Live Quality — Fast (screening)",
    description=(
        "Live-quality benchmark over the REAL screening pipeline's fast-role "
        "screener decisions on realistic papers. References are expected "
        "decision classes (deterministic from the inclusion criteria). "
        "Decision accuracy, uncertain-case handling, false-exclusion rate, "
        "structured-output success."
    ),
    category="live_quality",
    config={"evaluators": ["evaluator.live_quality_fast"]},
    cases=[
        _lq_case(
            "lq-fast-screening-clear",
            "fast screening — clear relevance",
            "Two clearly relevant papers (include) and one clearly irrelevant "
            "paper (exclude); the fast-role screener must not exclude relevant work.",
            workflow="literature_screening",
            task="screening",
            input_extra={
                "research_question": {
                    "question": "Which studies examine algorithmic pricing effects on consumer welfare in online markets?"
                },
                "papers": [
                    _lq_paper(
                        "Algorithmic Pricing and Consumer Welfare in Online Markets",
                        "10.7000/lq-f1",
                        "We study how algorithmic pricing reduces consumer welfare.",
                    ),
                    _lq_paper(
                        "Data-Driven Pricing and Market Outcomes",
                        "10.7000/lq-f2",
                        "Data-driven pricing shapes market outcomes and consumer welfare.",
                    ),
                    _lq_paper(
                        "Soil Microbiomes in Agricultural Systems",
                        "10.7000/lq-f3",
                        "Microbial communities in agricultural soils.",
                    ),
                ],
                "identities": [
                    {"member_indexes": [0]},
                    {"member_indexes": [1]},
                    {"member_indexes": [2]},
                ],
                "screening_config": {"max_candidates": 100, "max_model_calls": 500},
            },
            reference={
                "task": "screening",
                "expected_decisions": {
                    "Algorithmic Pricing and Consumer Welfare in Online Markets": "include",
                    "Data-Driven Pricing and Market Outcomes": "include",
                    "Soil Microbiomes in Agricultural Systems": "exclude",
                },
                "required_decision_accuracy": 0.8,
            },
            evaluator="evaluator.live_quality_fast",
            tags=["live_quality", "fast", "offline"],
        ),
        _lq_case(
            "lq-fast-screening-uncertain",
            "fast screening — uncertain handling",
            "An ambiguous paper must not be force-excluded; it should stay "
            "uncertain or be reviewed.",
            workflow="literature_screening",
            task="screening",
            input_extra={
                "research_question": {
                    "question": "Which studies examine algorithmic pricing effects on consumer welfare in online markets?"
                },
                "papers": [
                    _lq_paper(
                        "Pricing and Consumer Behavior in Digital Markets",
                        "10.7000/lq-u1",
                        "We study pricing and consumer behavior in digital markets.",
                    ),
                    _lq_paper(
                        "Consumer Welfare in Online Retail",
                        "10.7000/lq-u2",
                        "An overview of consumer welfare in online retail.",
                    ),
                ],
                "identities": [{"member_indexes": [0]}, {"member_indexes": [1]}],
                "screening_config": {"max_candidates": 100, "max_model_calls": 500},
            },
            reference={
                "task": "screening",
                "expected_decisions": {
                    "Pricing and Consumer Behavior in Digital Markets": "uncertain",
                    "Consumer Welfare in Online Retail": "uncertain",
                },
                "required_decision_accuracy": 0.8,
            },
            evaluator="evaluator.live_quality_fast",
            tags=["live_quality", "fast", "offline"],
        ),
    ],
)


def _lq_sanity_case(
    case_id: str,
    name: str,
    description: str,
    *,
    task: str,
    produced: dict[str, list[dict[str, Any]]],
    reference: dict[str, Any],
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={"workflow": "evaluator_sanity", "task": task, "produced": produced},
        reference={"task": task, **reference},
        evaluation_dimensions=["evaluator_sanity"],
        tags=["live_quality", "evaluator_sanity", "offline"],
    )


LIVE_QUALITY_EVALUATOR_SANITY_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="live-quality-evaluator-sanity-v1",
    version=1,
    name="Live Quality — Evaluator Sanity (Phase 7D.3B)",
    description=(
        "Offline audit of the live-quality evaluators before qualification "
        "results are trusted (Phase 7D.3A found a genuine evaluator bug). Each "
        "case feeds a synthetic model-shaped response to the REAL evaluator and "
        "verifies: known-good responses pass, known-bad responses fail, scalar "
        "reference ids vs list reference ids are handled correctly, denominators "
        "include all legitimately exercised cases, and provider errors (no "
        "produced artifacts) are never counted as successes. Never relaxes the "
        "expected quality."
    ),
    category="live_quality",
    config={"evaluators": ["evaluator.evaluator_sanity"]},
    cases=[
        _lq_sanity_case(
            "lq-sanity-gap-known-good",
            "gap analysis known-good response passes",
            "A gap grounded in a produced synthesis statement with a valid gap "
            "type passes the reasoning evaluator.",
            task="gap_analysis",
            produced={
                "evidence_item": [
                    {
                        "source_artifact_id": "lq-paper",
                        "statement": "Algorithmic pricing affects consumer welfare.",
                        "category": "result",
                    }
                ],
                "synthesis_statement": [
                    {
                        "statement": "Algorithmic pricing affects consumer welfare in online markets.",
                        "type": "consensus",
                        "supporting_evidence_ids": ["{evidence_item#0}"],
                        "conflicting_evidence_ids": [],
                        "supporting_paper_identity_ids": [],
                        "conflicting_paper_identity_ids": [],
                    }
                ],
                "research_gap": [
                    {
                        "title": "Mechanism linking algorithmic pricing to welfare",
                        "gap_type": "mechanism_gap",
                        "description": "The mechanism by which algorithmic pricing reduces welfare is unclear.",
                        "supporting_synthesis_statement_ids": ["{synthesis_statement#0}"],
                        "supporting_evidence_ids": [],
                        "contradiction_statement_ids": [],
                        "relevant_paper_identity_ids": [],
                        "supporting_papers": 0,
                        "supporting_evidence_items": 0,
                    }
                ],
            },
            reference={
                "allowed_gap_types": [
                    "mechanism_gap",
                    "empirical_gap",
                    "theoretical_gap",
                    "context_gap",
                ],
                "required_concepts": ["algorithmic pricing"],
                "expected_evaluator_status": "passed",
                "expect_task_diagnostics": [
                    "hallucinated_synthesis_evidence_refs",
                    "unsupported_gap",
                    "incorrect_gap_type",
                    "sweeping_novelty_claim",
                    "support_count_mismatch",
                    "structured_output_failure",
                ],
            },
        ),
        _lq_sanity_case(
            "lq-sanity-gap-known-bad",
            "gap analysis known-bad response fails",
            "A gap referencing a non-produced synthesis statement id and an "
            "invalid gap type fails the reasoning evaluator.",
            task="gap_analysis",
            produced={
                "research_gap": [
                    {
                        "title": "Mechanism linking algorithmic pricing to welfare",
                        "gap_type": "methodological_gap",
                        "description": "The mechanism is unclear.",
                        "supporting_synthesis_statement_ids": ["lq-nonexistent"],
                        "supporting_evidence_ids": [],
                        "contradiction_statement_ids": [],
                        "relevant_paper_identity_ids": [],
                        "supporting_papers": 0,
                        "supporting_evidence_items": 0,
                    }
                ],
            },
            reference={
                "allowed_gap_types": [
                    "mechanism_gap",
                    "empirical_gap",
                    "theoretical_gap",
                    "context_gap",
                ],
                "expected_evaluator_status": "failed",
                "expect_task_diagnostics_positive": ["hallucinated_synthesis_evidence_refs"],
            },
        ),
        _lq_sanity_case(
            "lq-sanity-evidence-scalar-ref-good",
            "evidence scalar reference id handled correctly",
            "An evidence item whose source_artifact_id is a single scalar id "
            "referencing a produced document passes (regression for the 7D.3A "
            "char-by-char scalar bug).",
            task="evidence_extraction",
            produced={
                "paper_record": [
                    {"title": "Algorithmic Pricing and Consumer Welfare in Online Markets"}
                ],
                "evidence_item": [
                    {
                        "source_artifact_id": "{paper_record#0}",
                        "locator": {"pages": [1]},
                        "statement": "Algorithmic pricing can reduce consumer welfare.",
                        "category": "result",
                    }
                ],
            },
            reference={
                "required_concepts": ["algorithmic pricing", "consumer welfare"],
                "expected_evaluator_status": "passed",
            },
        ),
        _lq_sanity_case(
            "lq-sanity-evidence-scalar-ref-bad",
            "evidence scalar reference id unsupported fails",
            "An evidence item whose scalar source_artifact_id points to a "
            "non-produced id fails the reasoning evaluator (genuine unsupported "
            "scalar reference).",
            task="evidence_extraction",
            produced={
                "evidence_item": [
                    {
                        "source_artifact_id": "lq-ghost-doc",
                        "locator": {"pages": [1]},
                        "statement": "Algorithmic pricing can reduce consumer welfare.",
                        "category": "result",
                    }
                ],
            },
            reference={
                "expected_evaluator_status": "failed",
                "expect_task_diagnostics_positive": [],
            },
        ),
        _lq_sanity_case(
            "lq-sanity-synthesis-list-ref-bad",
            "synthesis list reference id unsupported fails",
            "A synthesis statement referencing a non-produced evidence id in a "
            "LIST field fails the reasoning evaluator.",
            task="literature_synthesis",
            produced={
                "synthesis_statement": [
                    {
                        "statement": "Algorithmic pricing reduces consumer welfare.",
                        "type": "consensus",
                        "supporting_evidence_ids": ["lq-ghost-evidence"],
                        "conflicting_evidence_ids": [],
                        "supporting_paper_identity_ids": [],
                        "conflicting_paper_identity_ids": [],
                    }
                ],
            },
            reference={
                "required_concepts": ["algorithmic pricing", "welfare"],
                "expected_evaluator_status": "failed",
            },
        ),
        _lq_sanity_case(
            "lq-sanity-proposition-known-good",
            "proposition generation known-good response passes",
            "A proposition grounded in a produced model with a passing "
            "deterministic verification passes the reasoning evaluator.",
            task="proposition_generation",
            produced={
                "formal_analytical_model": [
                    {
                        "selected_mechanism_id": "lq-mech",
                        "title": "Model",
                        "description": "A model of surplus extraction.",
                    }
                ],
                "equilibrium_candidate": [
                    {
                        "model_id": "{formal_analytical_model#0}",
                        "expressions": [],
                        "decision_variables": ["p"],
                    }
                ],
                "comparative_statics_analysis": [
                    {
                        "model_id": "{formal_analytical_model#0}",
                        "equilibrium_candidate_id": "{equilibrium_candidate#0}",
                        "static_ids": [],
                    }
                ],
                "proposition": [
                    {
                        "model_id": "{formal_analytical_model#0}",
                        "equilibrium_candidate_id": "{equilibrium_candidate#0}",
                        "comparative_statics_analysis_id": "{comparative_statics_analysis#0}",
                        "statement": "Algorithmic pricing reduces consumer welfare.",
                        "claim_type": "monotonicity",
                        "conditions": ["demand uncertainty"],
                        "expected_sign": "negative",
                        "supporting_static_ids": [],
                    }
                ],
                "proposition_verification": [
                    {
                        "proposition_id": "lq-prop",
                        "model_id": "{formal_analytical_model#0}",
                        "status": "verified",
                    }
                ],
            },
            reference={
                "required_concepts": ["algorithmic pricing"],
                "expected_evaluator_status": "passed",
                "expect_task_diagnostics": [
                    "hallucinated_static_id",
                    "incorrect_expected_sign",
                    "missing_conditions",
                    "invalid_equality",
                    "unsupported_proposition",
                    "structured_output_failure",
                ],
            },
        ),
        _lq_sanity_case(
            "lq-sanity-proposition-provider-not-success",
            "provider error (no artifacts) is never a success",
            "When the provider returns nothing, the proposition evaluator must "
            "FAIL — a provider error can never count as a success.",
            task="proposition_generation",
            produced={},
            reference={
                "expected_evaluator_status": "failed",
                "expect_provider_not_success": True,
                "expect_task_diagnostics_positive": ["structured_output_failure"],
            },
        ),
        _lq_sanity_case(
            "lq-sanity-mechanism-known-bad",
            "mechanism generation hallucinated source ids fail",
            "A mechanism referencing a non-produced literature support id fails "
            "the reasoning evaluator and its diagnostics are positive.",
            task="mechanism_development",
            produced={
                "mechanism_candidate": [
                    {
                        "gap_id": "lq-gap",
                        "name": "Surplus extraction",
                        "description": "Sellers price discriminate using demand data.",
                        "causal_logic": "Data enables finer price discrimination.",
                        "actors": ["seller"],
                        "incentives": ["profit"],
                        "literature_support_ids": ["lq-ghost-statement"],
                    }
                ],
            },
            reference={
                "required_concepts": ["algorithmic pricing"],
                "expected_evaluator_status": "failed",
                "expect_task_diagnostics_positive": [
                    "invalid_literature_support",
                    "unsupported_source_ids",
                ],
            },
        ),
        _lq_sanity_case(
            "lq-sanity-model-known-bad",
            "model specification undefined symbol fails",
            "A payoff expression referencing an undefined symbol fails the "
            "reasoning evaluator with positive diagnostics.",
            task="analytical_model_specification",
            produced={
                "formal_analytical_model": [
                    {
                        "selected_mechanism_id": "lq-mech",
                        "title": "Model",
                        "description": "A model of surplus extraction.",
                        "actors": [{"actor_id": "seller", "name": "Seller", "strategic": True}],
                        "variables": [
                            {
                                "symbol": "p",
                                "name": "price",
                                "meaning": "retail price",
                                "domain": "R_+",
                                "kind": "decision_variable",
                                "owner_actor_id": "seller",
                            }
                        ],
                        "parameters": [],
                        "timing": [
                            {
                                "stage_number": 0,
                                "name": "pricing",
                                "description": "seller sets price",
                                "actor_ids": ["seller"],
                            }
                        ],
                        "payoffs": [
                            {
                                "actor_id": "seller",
                                "expression": {"expression": "p * q", "symbols_used": ["p", "q"]},
                                "decision_variables": ["p"],
                                "parameters": [],
                            }
                        ],
                    }
                ],
            },
            reference={
                "expected_evaluator_status": "failed",
                "expect_task_diagnostics_positive": ["undefined_symbols"],
            },
        ),
        _lq_sanity_case(
            "lq-sanity-model-malformed-expression-fails",
            "production-prompt repair never weakens the model-spec gate",
            "A malformed mathematical expression (unbalanced parentheses) must "
            "STILL fail the model-specification evaluator after any production "
            "prompt/schema improvement — the deterministic gate is never "
            "weakened.",
            task="analytical_model_specification",
            produced={
                "formal_analytical_model": [
                    {
                        "selected_mechanism_id": "lq-mech",
                        "title": "Model",
                        "description": "A model of surplus extraction.",
                        "actors": [{"actor_id": "seller", "name": "Seller", "strategic": True}],
                        "variables": [
                            {
                                "symbol": "p",
                                "name": "price",
                                "meaning": "retail price",
                                "domain": "R_+",
                                "kind": "decision_variable",
                                "owner_actor_id": "seller",
                            }
                        ],
                        "parameters": [],
                        "timing": [
                            {
                                "stage_number": 0,
                                "name": "pricing",
                                "description": "seller sets price",
                                "actor_ids": ["seller"],
                            }
                        ],
                        "payoffs": [
                            {
                                "actor_id": "seller",
                                "expression": {
                                    "expression": "(p - c * (10 - p",
                                    "symbols_used": ["p"],
                                },
                                "decision_variables": ["p"],
                                "parameters": [],
                            }
                        ],
                    }
                ],
            },
            reference={
                "expected_evaluator_status": "failed",
                "expect_task_diagnostics_positive": ["malformed_mathematical_expression"],
            },
        ),
        _lq_sanity_case(
            "lq-sanity-critic-known-good",
            "critic known-good response passes",
            "A mechanism critique that detects the injected defect with an "
            "assessment and actionable revisions passes the critic evaluator.",
            task="mechanism_critique",
            produced={
                "mechanism_critique": [
                    {
                        "mechanism_candidate_id": "lq-mech",
                        "issues": [
                            {
                                "category": "unclear_causal_direction",
                                "description": "The causal direction is ambiguous.",
                                "severity": "high",
                            }
                        ],
                        "overall_assessment": "Needs revision.",
                        "verdict": "revise",
                        "revision_recommendations": ["Specify the causal direction."],
                    }
                ],
            },
            reference={
                "injected_defects": [{"category": "unclear_causal_direction", "severity": "high"}],
                "expected_evaluator_status": "passed",
                "expect_task_diagnostics": [
                    "defect_recall_missed",
                    "false_positive_issues",
                    "severity_mismatch",
                    "required_category_missed",
                    "missing_actionable_revision",
                    "structured_output_failure",
                ],
            },
        ),
        _lq_sanity_case(
            "lq-sanity-critic-known-bad",
            "critic known-bad response fails",
            "A mechanism critique that detects none of the injected defects "
            "fails the critic evaluator.",
            task="mechanism_critique",
            produced={
                "mechanism_critique": [
                    {
                        "mechanism_candidate_id": "lq-mech",
                        "issues": [],
                        "overall_assessment": "Looks fine.",
                        "verdict": "keep",
                        "revision_recommendations": [],
                    }
                ],
            },
            reference={
                "injected_defects": [{"category": "unclear_causal_direction", "severity": "high"}],
                "expected_evaluator_status": "failed",
                "expect_task_diagnostics_positive": ["defect_recall_missed"],
            },
        ),
        _lq_sanity_case(
            "lq-sanity-fast-known-good",
            "screening known-good decisions pass",
            "Correct include/exclude screening decisions pass the fast evaluator.",
            task="screening",
            produced={
                "paper_record": [{"title": "Algorithmic Pricing and Consumer Welfare"}],
                "paper_identity": [
                    {
                        "member_paper_artifact_ids": ["{paper_record#0}"],
                        "resolution_method": "exact_identifier",
                    }
                ],
                "screening_decision": [
                    {
                        "paper_identity_id": "{paper_identity#0}",
                        "screening_view_id": "lq-view",
                        "screening_protocol_id": "lq-protocol",
                        "decision": "include",
                        "rationale_summary": "Relevant to algorithmic pricing and welfare.",
                        "confidence": 0.9,
                    }
                ],
            },
            reference={
                "expected_decisions": {"Algorithmic Pricing and Consumer Welfare": "include"},
                "required_decision_accuracy": 0.8,
                "expected_evaluator_status": "passed",
                "expect_task_diagnostics": [
                    "include_mismatch",
                    "exclude_mismatch",
                    "uncertain_mismatch",
                    "false_exclusion",
                    "false_inclusion",
                    "structured_output_failure",
                    "provider_error",
                ],
            },
        ),
        _lq_sanity_case(
            "lq-sanity-fast-known-bad",
            "screening false exclusion fails",
            "Excluding a clearly relevant paper is a critical false exclusion "
            "and fails the fast evaluator.",
            task="screening",
            produced={
                "paper_record": [{"title": "Algorithmic Pricing and Consumer Welfare"}],
                "paper_identity": [
                    {
                        "member_paper_artifact_ids": ["{paper_record#0}"],
                        "resolution_method": "exact_identifier",
                    }
                ],
                "screening_decision": [
                    {
                        "paper_identity_id": "{paper_identity#0}",
                        "screening_view_id": "lq-view",
                        "screening_protocol_id": "lq-protocol",
                        "decision": "exclude",
                        "rationale_summary": "Relevant to algorithmic pricing and welfare.",
                        "confidence": 0.9,
                    }
                ],
            },
            reference={
                "expected_decisions": {"Algorithmic Pricing and Consumer Welfare": "include"},
                "required_decision_accuracy": 0.8,
                "expected_evaluator_status": "failed",
                "expect_task_diagnostics_positive": ["false_exclusion"],
            },
        ),
    ],
)


def _lq_result(
    candidate_id: str,
    *,
    det: float = 0.9,
    structured: float = 0.9,
    provider_error: float = 0.0,
    grounding_failures: int = 0,
    repetitions: int = 3,
    age_seconds: float | None = None,
    cost: float | None = None,
    role: str = "reasoning",
    repetition_rates: list[float] | None = None,
    stability: str | None = None,
    failure_attribution: dict[str, int] | None = None,
    excluded_failure_attribution: dict[str, int] | None = None,
    task_performance: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    created = (
        datetime.now(UTC) - timedelta(seconds=age_seconds) if age_seconds else datetime.now(UTC)
    )
    # An empty list is not the same as None: None means "no repetition data",
    # whereas [] would divide by zero.
    if repetition_rates:
        det_mean = sum(repetition_rates) / len(repetition_rates)
        det_worst = min(repetition_rates)
        det_var = (
            sum((r - det_mean) ** 2 for r in repetition_rates) / len(repetition_rates)
            if len(repetition_rates) > 1
            else 0.0
        )
        reps = len(repetition_rates)
    else:
        det_mean, det_worst, det_var, reps = det, det, 0.0, repetitions
    return {
        "candidate_id": candidate_id,
        "model": {"candidate_id": candidate_id, "requested_model": f"m-{candidate_id}"},
        "resolved_model": f"m-{candidate_id}",
        "role": role,
        "benchmark_id": "live-quality-reasoning-v1",
        "repetitions": reps,
        "deterministic_pass_rate_mean": det_mean,
        "deterministic_pass_rate_worst": det_worst,
        "deterministic_pass_rate_variance": det_var,
        "structured_output_success_rate": structured,
        "provider_error_frequency": provider_error,
        "critical_grounding_failures": grounding_failures,
        "estimated_cost": cost,
        "stability": stability,
        "failure_attribution": failure_attribution or {},
        "excluded_failure_attribution": excluded_failure_attribution or {},
        "task_performance": task_performance or [],
        "task_results": [
            {
                "repetition": i,
                "run_id": f"run-{i}",
                "report_id": f"rep-{i}",
                "report_status": "passed",
                "cases_total": 1,
                "cases_passed": 1,
                "cases_failed": 0,
                "cases_error": 0,
                "task_pass_rate": 1.0,
                "task_completed": True,
            }
            for i in range(reps)
        ],
        "evidence_timestamp": created.isoformat(),
    }


def _lq_task_perf(
    task_id: str,
    *,
    det: float = 0.9,
    worst: float | None = None,
    variance: float = 0.0,
    reps: int = 3,
    structured: float = 0.9,
    provider: float = 0.0,
    grounding: int = 0,
    pass_rates: list[float] | None = None,
    evidence_diagnostics: dict[str, int] | None = None,
) -> dict[str, Any]:
    if pass_rates is None:
        rates = [det] * reps
    else:
        rates = list(pass_rates)
        reps = len(rates)
    from statistics import fmean, pvariance

    return {
        "task_id": task_id,
        "task_name": task_id,
        "repetitions": reps,
        "pass_rate_mean": fmean(rates) if rates else None,
        "pass_rate_worst": min(rates) if rates else (worst if worst is not None else det),
        "pass_rate_variance": pvariance(rates) if len(rates) > 1 else variance,
        "pass_rates": rates,
        "structured_output_success_rate": structured,
        "provider_error_frequency": provider,
        "critical_grounding_failures": grounding,
        "failure_attribution": {},
        "excluded_failure_attribution": {},
        "evidence_diagnostics": evidence_diagnostics or {},
    }


# canonical case ids per role for synthetic task performance
_REASONING_CASES = [
    "lq-evidence-extraction",
    "lq-literature-synthesis",
    "lq-gap-analysis",
    "lq-mechanism-development",
    "lq-model-specification",
    "lq-proposition-generation",
]
_CRITIC_CASES = [
    "lq-mechanism-critique",
    "lq-model-critique",
    "lq-proposition-critique",
    "lq-results-critique",
    "lq-manuscript-critique",
]
_FAST_CASES = ["lq-fast-screening-clear", "lq-fast-screening-uncertain"]


def _lq_tasks_result(
    candidate_id: str,
    *,
    role: str,
    tasks: dict[str, dict[str, Any]],
    benchmark_id: str = "live-quality-reasoning-v1",
    repetitions: int = 3,
    cost: float | None = None,
    age_seconds: float | None = None,
) -> dict[str, Any]:
    """Build a synthetic LiveQualityModelResult with per-task performance whose
    overall deterministic pass rate equals the mean of the per-task rates."""
    from statistics import fmean

    perfs = []
    all_rates: list[float] = []
    total_grounding = 0
    for case_id, spec in tasks.items():
        rates = spec.get("pass_rates") or [spec.get("det", 0.9)] * repetitions
        all_rates.extend(rates)
        total_grounding += int(spec.get("grounding", 0))
        perfs.append(
            _lq_task_perf(
                case_id,
                det=spec.get("det", 0.9),
                worst=spec.get("worst"),
                variance=spec.get("variance", 0.0),
                reps=repetitions,
                structured=spec.get("structured", 0.9),
                provider=spec.get("provider", 0.0),
                grounding=spec.get("grounding", 0),
                pass_rates=spec.get("pass_rates"),
                evidence_diagnostics=spec.get("evidence_diagnostics"),
            )
        )
    return _lq_result(
        candidate_id,
        det=fmean(all_rates) if all_rates else 0.0,
        role=role,
        repetitions=repetitions,
        grounding_failures=total_grounding,
        cost=cost,
        age_seconds=age_seconds,
        task_performance=perfs,
    )


def _task_qualification_case(
    case_id: str,
    name: str,
    description: str,
    *,
    role: str,
    reference: dict[str, Any],
    live_results: dict[str, dict[str, Any]],
    criteria: dict[str, Any] | None = None,
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "task_qualification",
            "role": role,
            "live_results": live_results,
            "criteria": criteria or {},
        },
        reference=reference,
        evaluation_dimensions=["task_model_qualification"],
        tags=["task_qualification", "offline"],
    )


def _task_aware_routing_case(
    case_id: str,
    name: str,
    description: str,
    *,
    role: str,
    reference: dict[str, Any],
    live_results: dict[str, dict[str, Any]],
    task: str | None = None,
    tasks: list[str] | None = None,
    static_model: str = "static/model",
    static_provider: str = "openrouter",
    criteria: dict[str, Any] | None = None,
    max_qualification_age_seconds: float | None = None,
    matrix_age_seconds: float | None = None,
) -> BenchmarkCaseDefinition:
    case_input: dict[str, Any] = {
        "workflow": "task_aware_routing",
        "role": role,
        "live_results": live_results,
        "static_model": static_model,
        "static_provider": static_provider,
        "criteria": criteria or {},
    }
    if tasks is not None:
        case_input["tasks"] = list(tasks)
    if task is not None:
        case_input["task"] = task
    if max_qualification_age_seconds is not None:
        case_input["max_qualification_age_seconds"] = max_qualification_age_seconds
    if matrix_age_seconds is not None:
        case_input["matrix_age_seconds"] = matrix_age_seconds
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input=case_input,
        reference=reference,
        evaluation_dimensions=["task_aware_routing"],
        tags=["task_aware_routing", "offline"],
    )


def _readiness_case(
    case_id: str,
    name: str,
    description: str,
    *,
    role: str,
    configured_model: str | None,
    reference: dict[str, Any],
    live_results: dict[str, dict[str, Any]] | None = None,
    require_fallback: bool = False,
    criteria: dict[str, Any] | None = None,
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": "routing_readiness",
            "role": role,
            "configured_model": configured_model,
            "require_fallback": require_fallback,
            "live_results": live_results or {},
            "criteria": criteria or {},
        },
        reference=reference,
        evaluation_dimensions=["routing_readiness"],
        tags=["routing", "readiness", "offline"],
    )


PRODUCTION_ROUTING_READINESS_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="production-routing-readiness-v1",
    version=1,
    name="Production Routing Readiness (Phase 7D.0)",
    description=(
        "Offline benchmark over the real deterministic production-routing "
        "readiness logic. Verifies that live-quality evidence is required for "
        "qualification, thresholds/repetitions/grounding/provider-error gates "
        "work, role evidence is isolated, fallbacks qualify, and "
        "unsafe_production_qualification_rate stays 0."
    ),
    category="routing_readiness",
    config={"evaluators": ["evaluator.routing_readiness"]},
    cases=[
        _readiness_case(
            "prr-no-live-evidence",
            "high offline score but no live evidence -> not qualified",
            "Offline fixture tournaments alone can never authorize routing.",
            role="reasoning",
            configured_model="m-offline-best",
            reference={
                "expected_qualified": False,
                "expected_role": "reasoning",
                "expected_reason_substring": "no live-quality evidence",
                "expected_qualified_models": [],
            },
        ),
        _readiness_case(
            "prr-below-threshold",
            "live quality below threshold -> rejected",
            "A model whose live deterministic quality is below the role "
            "threshold is not qualified.",
            role="reasoning",
            configured_model="m-configured",
            live_results={
                "m-configured": _lq_result("m-configured", det=0.6),
                "m-fallback": _lq_result("m-fallback", det=0.95),
            },
            require_fallback=True,
            reference={
                "expected_qualified": False,
                "expected_reason_substring": "deterministic_pass_rate",
                "expected_qualified_models": ["m-fallback"],
            },
        ),
        _readiness_case(
            "prr-qualified",
            "sufficient repetitions + quality -> qualified",
            "A model with adequate live evidence and a qualified fallback qualifies the role.",
            role="reasoning",
            configured_model="m-configured",
            live_results={
                "m-configured": _lq_result("m-configured", det=0.92),
                "m-fallback": _lq_result("m-fallback", det=0.9),
            },
            require_fallback=True,
            reference={
                "expected_qualified": True,
                "expected_qualified_models": ["m-configured", "m-fallback"],
                "expected_fallback_qualified": True,
                "expected_fallback_model": "m-fallback",
            },
        ),
        _readiness_case(
            "prr-critical-grounding",
            "grounding critical failure -> rejected",
            "A model with any critical grounding failure is never qualified.",
            role="reasoning",
            configured_model="m-configured",
            live_results={
                "m-configured": _lq_result("m-configured", det=0.95, grounding_failures=1)
            },
            require_fallback=True,
            reference={
                "expected_qualified": False,
                "expected_reason_substring": "grounding",
                "expected_qualified_models": [],
            },
        ),
        _readiness_case(
            "prr-high-provider-error",
            "high provider-error rate -> rejected",
            "A model with a provider-failure rate above the role maximum is not qualified.",
            role="reasoning",
            configured_model="m-configured",
            live_results={"m-configured": _lq_result("m-configured", det=0.95, provider_error=0.5)},
            require_fallback=True,
            reference={
                "expected_qualified": False,
                "expected_reason_substring": "provider_error",
                "expected_qualified_models": [],
            },
        ),
        _readiness_case(
            "prr-stale-evidence",
            "stale live evidence -> rejected",
            "Live evidence older than the freshness threshold is not qualified.",
            role="reasoning",
            configured_model="m-configured",
            live_results={"m-configured": _lq_result("m-configured", det=0.95, age_seconds=5000)},
            require_fallback=True,
            criteria={"role": "reasoning", "leaderboard_max_age_seconds": 100},
            reference={
                "expected_qualified": False,
                "expected_reason_substring": "stale",
                "expected_qualified_models": [],
            },
        ),
        _readiness_case(
            "prr-role-evidence-mismatch",
            "role evidence mismatch -> rejected",
            "Live evidence for a different model than the configured production "
            "model cannot qualify the role.",
            role="reasoning",
            configured_model="m-production",
            live_results={"m-other": _lq_result("m-other", det=0.95)},
            require_fallback=True,
            reference={
                "expected_qualified": False,
                "expected_reason_substring": "configured model",
                "expected_qualified_models": ["m-other"],
            },
        ),
        _readiness_case(
            "prr-no-qualified-fallback",
            "no qualified fallback when policy requires one -> not ready",
            "A qualified primary with no qualified fallback is not ready.",
            role="reasoning",
            configured_model="m-configured",
            live_results={"m-configured": _lq_result("m-configured", det=0.95)},
            require_fallback=True,
            reference={
                "expected_qualified": False,
                "expected_reason_substring": "fallback",
                "expected_qualified_models": ["m-configured"],
            },
        ),
        _readiness_case(
            "prr-fallback-not-required",
            "qualified primary without required fallback -> ready",
            "When the policy does not require a fallback, a qualified primary alone is ready.",
            role="reasoning",
            configured_model="m-configured",
            live_results={"m-configured": _lq_result("m-configured", det=0.95)},
            require_fallback=False,
            reference={
                "expected_qualified": True,
                "expected_qualified_models": ["m-configured"],
            },
        ),
    ],
)


def _qualification_case(
    case_id: str,
    name: str,
    description: str,
    *,
    role: str,
    reference: dict[str, Any],
    live_results: dict[str, Any],
    criteria: dict[str, Any] | None = None,
    workflow: str = "qualification_policy",
) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id=case_id,
        name=name,
        description=description,
        input={
            "workflow": workflow,
            "role": role,
            "live_results": live_results,
            "criteria": criteria or {},
        },
        reference=reference,
        evaluation_dimensions=["model_qualification"],
        tags=["qualification", "offline"],
    )


MODEL_QUALIFICATION_POLICY_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="model-qualification-policy-v1",
    version=1,
    name="Model Qualification Policy (Phase 7D.1)",
    description=(
        "Offline benchmark over the real deterministic live-model qualification "
        "algorithm. Verifies primary/fallback selection among qualified models, "
        "structured rejection kinds, role isolation, deterministic ties, "
        "borderline rejection, and that unsafe_model_qualification_rate stays 0."
    ),
    category="model_qualification",
    config={"evaluators": ["evaluator.model_qualification"]},
    cases=[
        _qualification_case(
            "mq-qualified-with-fallback",
            "qualified primary + fallback",
            "Two independently qualified models produce a primary and a fallback.",
            role="reasoning",
            live_results={
                "m-a": _lq_result("m-a", det=0.92),
                "m-b": _lq_result("m-b", det=0.9),
            },
            reference={
                "expected_status": "qualified",
                "expected_primary": "m-a",
                "expected_fallback": "m-b",
                "expected_qualified_models": ["m-a", "m-b"],
                "expected_role": "reasoning",
            },
        ),
        _qualification_case(
            "mq-primary-no-fallback",
            "primary qualifies, fallback does not",
            "Only one model qualifies; status is qualified_without_fallback.",
            role="reasoning",
            live_results={
                "m-a": _lq_result("m-a", det=0.92),
                "m-bad": _lq_result("m-bad", det=0.6),
            },
            reference={
                "expected_status": "qualified_without_fallback",
                "expected_primary": "m-a",
                "expected_fallback": None,
                "expected_qualified_models": ["m-a"],
            },
        ),
        _qualification_case(
            "mq-none-qualified",
            "no model qualifies",
            "All candidates fall below the role threshold.",
            role="reasoning",
            live_results={
                "m-a": _lq_result("m-a", det=0.6),
                "m-b": _lq_result("m-b", det=0.7),
            },
            reference={
                "expected_status": "no_qualified_model",
                "expected_primary": None,
                "expected_fallback": None,
                "expected_qualified_models": [],
                "expected_rejection_kinds": {"m-a": ["below_quality_threshold"]},
            },
        ),
        _qualification_case(
            "mq-critical-grounding",
            "candidate passes mean but has a critical grounding failure",
            "A strong mean pass rate never overrides a critical grounding failure.",
            role="reasoning",
            live_results={"m-a": _lq_result("m-a", det=0.95, grounding_failures=1)},
            reference={
                "expected_status": "no_qualified_model",
                "expected_primary": None,
                "expected_qualified_models": [],
                "expected_rejection_kinds": {"m-a": ["critical_grounding_failure"]},
            },
        ),
        _qualification_case(
            "mq-insufficient-repetitions",
            "insufficient repetitions",
            "A single lucky run never qualifies.",
            role="reasoning",
            live_results={"m-a": _lq_result("m-a", det=0.95, repetitions=1)},
            reference={
                "expected_status": "no_qualified_model",
                "expected_rejection_kinds": {"m-a": ["insufficient_repetitions"]},
            },
        ),
        _qualification_case(
            "mq-stale-evidence",
            "stale live evidence",
            "Live evidence older than the freshness limit is rejected.",
            role="reasoning",
            live_results={"m-a": _lq_result("m-a", det=0.95, age_seconds=5000)},
            criteria={"role": "reasoning", "leaderboard_max_age_seconds": 100},
            reference={
                "expected_status": "no_qualified_model",
                "expected_rejection_kinds": {"m-a": ["stale_evidence"]},
            },
        ),
        _qualification_case(
            "mq-role-mismatch",
            "role mismatch",
            "Reasoning evidence can never qualify a critic role.",
            role="critic",
            live_results={"m-a": _lq_result("m-a", det=0.95)},
            reference={
                "expected_status": "no_qualified_model",
                "expected_primary": None,
                "expected_qualified_models": [],
                "expected_role": "critic",
            },
        ),
        _qualification_case(
            "mq-cheap-unqualified-loses",
            "cheaper unqualified candidate loses",
            "An unqualified cheap model is never selected over a qualified one.",
            role="reasoning",
            live_results={
                "m-good": _lq_result("m-good", det=0.9, cost=0.05),
                "m-cheap-bad": _lq_result("m-cheap-bad", det=0.6, cost=0.0001),
            },
            reference={
                "expected_status": "qualified_without_fallback",
                "expected_primary": "m-good",
                "expected_qualified_models": ["m-good"],
            },
        ),
        _qualification_case(
            "mq-deterministic-tie",
            "deterministic tie between qualified models",
            "Identical qualified metrics break ties deterministically by candidate_id.",
            role="reasoning",
            live_results={
                "m-beta": _lq_result("m-beta", det=0.9),
                "m-alpha": _lq_result("m-alpha", det=0.9),
            },
            reference={
                "expected_status": "qualified",
                "expected_primary": "m-alpha",
                "expected_fallback": "m-beta",
                "expected_tiebreak": "m-alpha",
                "expected_qualified_models": ["m-alpha", "m-beta"],
            },
        ),
        _qualification_case(
            "mq-borderline-unqualified",
            "borderline candidate remains unqualified",
            "A candidate just below the threshold is rejected (never loosened to obtain a winner).",
            role="reasoning",
            live_results={"m-border": _lq_result("m-border", det=0.849)},
            reference={
                "expected_status": "no_qualified_model",
                "expected_rejection_kinds": {"m-border": ["below_quality_threshold"]},
            },
        ),
        _qualification_case(
            "mq-benchmark-defect-excluded",
            "benchmark defect excluded from model failure",
            "A confirmed benchmark-reference defect is not counted against the model: "
            "the candidate's only grounding failures are excluded and it qualifies.",
            role="reasoning",
            live_results={
                "m-a": _lq_result(
                    "m-a",
                    det=0.9,
                    grounding_failures=1,
                    excluded_failure_attribution={"benchmark_reference_defect": 1},
                    failure_attribution={"benchmark_reference_defect": 1},
                )
            },
            reference={
                "expected_status": "qualified_without_fallback",
                "expected_primary": "m-a",
                "expected_qualified_models": ["m-a"],
            },
        ),
        _qualification_case(
            "mq-evaluator-defect-excluded",
            "evaluator defect excluded from model failure",
            "A confirmed evaluator defect is not counted against the model: inflated "
            "critical-grounding counts are excluded and the candidate qualifies.",
            role="reasoning",
            live_results={
                "m-a": _lq_result(
                    "m-a",
                    det=0.9,
                    grounding_failures=2,
                    excluded_failure_attribution={"evaluator_defect": 2},
                    failure_attribution={"evaluator_defect": 2},
                )
            },
            reference={
                "expected_status": "qualified_without_fallback",
                "expected_primary": "m-a",
                "expected_qualified_models": ["m-a"],
            },
        ),
        _qualification_case(
            "mq-borderline-extra-repetitions",
            "borderline candidate requiring extra repetitions",
            "A borderline candidate evaluated over 5 repetitions (worst just above the "
            "threshold) qualifies with stability=borderline; extra repetitions are "
            "supported and recorded.",
            role="reasoning",
            live_results={
                "m-a": _lq_result(
                    "m-a", repetition_rates=[0.86, 0.86, 0.86, 0.86, 0.86], structured=0.9
                )
            },
            reference={
                "expected_status": "qualified_without_fallback",
                "expected_primary": "m-a",
                "expected_qualified_models": ["m-a"],
                "expected_stability": {"m-a": "borderline"},
                "expected_primary_eligible": {"m-a": True},
                "expected_repetitions": {"m-a": 5},
            },
        ),
        _qualification_case(
            "mq-unstable-rejected",
            "unstable candidate rejected",
            "A candidate whose mean passes but a repetition falls below the threshold is "
            "qualified by the existing criteria yet marked unstable and NOT eligible "
            "for primary/fallback (activation).",
            role="reasoning",
            live_results={
                "m-a": _lq_result("m-a", repetition_rates=[1.0, 1.0, 0.7], structured=0.9)
            },
            reference={
                "expected_status": "qualified_without_fallback",
                "expected_primary": "m-a",
                "expected_qualified_models": ["m-a"],
                "expected_stability": {"m-a": "unstable"},
                "expected_primary_eligible": {"m-a": False},
                "expected_fallback_eligible": {"m-a": False},
            },
        ),
        _qualification_case(
            "mq-qualified-primary-fallback",
            "qualified primary + qualified fallback",
            "Two independently qualified, stable candidates produce a primary and a "
            "qualified fallback, both eligible.",
            role="reasoning",
            live_results={
                "m-a": _lq_result("m-a", det=0.92),
                "m-b": _lq_result("m-b", det=0.9),
            },
            reference={
                "expected_status": "qualified",
                "expected_primary": "m-a",
                "expected_fallback": "m-b",
                "expected_qualified_models": ["m-a", "m-b"],
                "expected_stability": {"m-a": "stable", "m-b": "stable"},
                "expected_primary_eligible": {"m-a": True},
                "expected_fallback_eligible": {"m-b": True},
            },
        ),
        _qualification_case(
            "mq-role-partial-qualification",
            "role-specific partial qualification",
            "One role fully qualifies (primary + fallback) while another only has a "
            "qualified primary; the matrix records the per-role partial state.",
            role="reasoning",
            workflow="qualification_matrix",
            live_results={
                "reasoning": {
                    "m-a": _lq_result("m-a", det=0.92, role="reasoning"),
                    "m-b": _lq_result("m-b", det=0.9, role="reasoning"),
                },
                "critic": {"m-c": _lq_result("m-c", det=0.95, role="critic")},
            },
            reference={
                "expected_matrix_rows": [
                    {
                        "role": "reasoning",
                        "candidate": "m-a",
                        "qualified": True,
                        "stability": "stable",
                        "primary_eligible": True,
                        "fallback_eligible": False,
                    },
                    {
                        "role": "reasoning",
                        "candidate": "m-b",
                        "qualified": True,
                        "stability": "stable",
                        "primary_eligible": True,
                        "fallback_eligible": True,
                    },
                    {
                        "role": "critic",
                        "candidate": "m-c",
                        "qualified": True,
                        "stability": "stable",
                        "primary_eligible": True,
                        "fallback_eligible": False,
                    },
                ],
                "expected_matrix_status": {
                    "reasoning": "qualified",
                    "critic": "qualified_without_fallback",
                },
            },
        ),
    ],
)


TASK_SPECIFIC_MODEL_QUALIFICATION_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="task-specific-model-qualification-v1",
    version=1,
    name="Task-Specific Model Qualification (Phase 7D.3)",
    description=(
        "Offline benchmark over the real deterministic task-specific "
        "qualification logic (same thresholds as the role criteria, never "
        "relaxed). Verifies per-task qualified model sets, per-task ranking "
        "among qualified models only, role vs task separation, structured "
        "rejection reasons (grounding/provider/stale/insufficient repetitions), "
        "deterministic tie-breaks, and that unsafe_task_qualification_rate "
        "stays 0."
    ),
    category="task_model_qualification",
    config={"evaluators": ["evaluator.task_model_qualification"]},
    cases=[
        _task_qualification_case(
            "tq-one-task-qualified-other-fails",
            "model qualifies for one reasoning task but fails another",
            "A model passes synthesis but fails evidence extraction; only the "
            "passing task is qualified, and the role is not.",
            role="reasoning",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="reasoning",
                    tasks={
                        "lq-evidence-extraction": {"det": 0.5},
                        "lq-literature-synthesis": {"det": 0.9},
                        "lq-gap-analysis": {"det": 0.5},
                        "lq-mechanism-development": {"det": 0.5},
                        "lq-model-specification": {"det": 0.5},
                        "lq-proposition-generation": {"det": 0.5},
                    },
                ),
            },
            reference={
                "expected_role": "reasoning",
                "expected_qualified_by_task": {
                    "synthesis": ["m-a"],
                    "evidence_extraction": [],
                },
                "expected_ranked_by_task": {"synthesis": ["m-a"]},
                "expected_role_qualified_models": [],
            },
        ),
        _task_qualification_case(
            "tq-task-not-role",
            "task qualification does not imply role qualification",
            "A model qualified for some tasks is still not role-qualified "
            "because other tasks fail.",
            role="reasoning",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="reasoning",
                    tasks={
                        "lq-evidence-extraction": {"det": 0.9},
                        "lq-literature-synthesis": {"det": 0.9},
                        "lq-gap-analysis": {"det": 0.5},
                        "lq-mechanism-development": {"det": 0.5},
                        "lq-model-specification": {"det": 0.5},
                        "lq-proposition-generation": {"det": 0.5},
                    },
                ),
            },
            reference={
                "expected_qualified_by_task": {
                    "evidence_extraction": ["m-a"],
                    "synthesis": ["m-a"],
                },
                "expected_role_qualified_models": [],
            },
        ),
        _task_qualification_case(
            "tq-critical-grounding-blocks-task",
            "critical grounding failure blocks task qualification",
            "A strong task pass rate never overrides a critical grounding "
            "failure at the task level.",
            role="reasoning",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="reasoning",
                    tasks={
                        "lq-literature-synthesis": {"det": 0.9, "grounding": 1},
                        "lq-evidence-extraction": {"det": 0.9},
                        "lq-gap-analysis": {"det": 0.9},
                        "lq-mechanism-development": {"det": 0.9},
                        "lq-model-specification": {"det": 0.9},
                        "lq-proposition-generation": {"det": 0.9},
                    },
                ),
            },
            reference={
                "expected_qualified_by_task": {
                    "synthesis": [],
                    "evidence_extraction": ["m-a"],
                },
                "expected_rejections": {"m-a/synthesis": "grounding"},
            },
        ),
        _task_qualification_case(
            "tq-provider-error-blocks",
            "provider error blocks task qualification",
            "A task with a provider-error frequency above the cap is not "
            "qualified regardless of pass rate.",
            role="reasoning",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="reasoning",
                    tasks={
                        "lq-literature-synthesis": {"det": 0.9, "provider": 0.5},
                        "lq-evidence-extraction": {"det": 0.5},
                        "lq-gap-analysis": {"det": 0.5},
                        "lq-mechanism-development": {"det": 0.5},
                        "lq-model-specification": {"det": 0.5},
                        "lq-proposition-generation": {"det": 0.5},
                    },
                ),
            },
            reference={
                "expected_qualified_by_task": {"synthesis": []},
                "expected_rejections": {"m-a/synthesis": "provider_error"},
            },
        ),
        _task_qualification_case(
            "tq-strong-expensive-beats-cheap-failing",
            "stronger expensive qualified model beats cheap failing model",
            "An unqualified cheap model is never ranked for a task over a "
            "qualified (more expensive) model.",
            role="reasoning",
            live_results={
                "m-good": _lq_tasks_result(
                    "m-good",
                    role="reasoning",
                    cost=0.05,
                    tasks={
                        "lq-literature-synthesis": {"det": 0.9},
                        "lq-evidence-extraction": {"det": 0.5},
                        "lq-gap-analysis": {"det": 0.5},
                        "lq-mechanism-development": {"det": 0.5},
                        "lq-model-specification": {"det": 0.5},
                        "lq-proposition-generation": {"det": 0.5},
                    },
                ),
                "m-cheap-bad": _lq_tasks_result(
                    "m-cheap-bad",
                    role="reasoning",
                    cost=0.0001,
                    tasks={
                        "lq-literature-synthesis": {"det": 0.5},
                        "lq-evidence-extraction": {"det": 0.5},
                        "lq-gap-analysis": {"det": 0.5},
                        "lq-mechanism-development": {"det": 0.5},
                        "lq-model-specification": {"det": 0.5},
                        "lq-proposition-generation": {"det": 0.5},
                    },
                ),
            },
            reference={
                "expected_qualified_by_task": {"synthesis": ["m-good"]},
                "expected_ranked_by_task": {"synthesis": ["m-good"]},
            },
        ),
        _task_qualification_case(
            "tq-qualified-critic-fallback",
            "qualified critic fallback",
            "Two independent candidates qualified for every critic task give "
            "the critic role a primary and a qualified fallback.",
            role="critic",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="critic",
                    benchmark_id="live-quality-critic-v1",
                    tasks={case_id: {"det": 0.9} for case_id in _CRITIC_CASES},
                ),
                "m-b": _lq_tasks_result(
                    "m-b",
                    role="critic",
                    benchmark_id="live-quality-critic-v1",
                    tasks={case_id: {"det": 0.9} for case_id in _CRITIC_CASES},
                ),
            },
            reference={
                "expected_role": "critic",
                "expected_qualified_by_task": {
                    "mechanism_critique": ["m-a", "m-b"],
                    "results_critique": ["m-a", "m-b"],
                },
                "expected_role_qualified_models": ["m-a", "m-b"],
            },
        ),
        _task_qualification_case(
            "tq-deterministic-tiebreak",
            "deterministic task-specific tie break",
            "Identical per-task metrics break ties deterministically by candidate_id.",
            role="reasoning",
            live_results={
                "m-beta": _lq_tasks_result(
                    "m-beta",
                    role="reasoning",
                    tasks={
                        "lq-literature-synthesis": {"det": 0.9},
                        "lq-evidence-extraction": {"det": 0.5},
                        "lq-gap-analysis": {"det": 0.5},
                        "lq-mechanism-development": {"det": 0.5},
                        "lq-model-specification": {"det": 0.5},
                        "lq-proposition-generation": {"det": 0.5},
                    },
                ),
                "m-alpha": _lq_tasks_result(
                    "m-alpha",
                    role="reasoning",
                    tasks={
                        "lq-literature-synthesis": {"det": 0.9},
                        "lq-evidence-extraction": {"det": 0.5},
                        "lq-gap-analysis": {"det": 0.5},
                        "lq-mechanism-development": {"det": 0.5},
                        "lq-model-specification": {"det": 0.5},
                        "lq-proposition-generation": {"det": 0.5},
                    },
                ),
            },
            reference={
                "expected_ranked_by_task": {"synthesis": ["m-alpha", "m-beta"]},
                "expected_qualified_by_task": {"synthesis": ["m-alpha", "m-beta"]},
            },
        ),
        _task_qualification_case(
            "tq-insufficient-repetitions",
            "insufficient repetitions",
            "A task evaluated over fewer repetitions than required never qualifies.",
            role="reasoning",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="reasoning",
                    repetitions=1,
                    tasks={
                        "lq-literature-synthesis": {"det": 0.9},
                        "lq-evidence-extraction": {"det": 0.5},
                        "lq-gap-analysis": {"det": 0.5},
                        "lq-mechanism-development": {"det": 0.5},
                        "lq-model-specification": {"det": 0.5},
                        "lq-proposition-generation": {"det": 0.5},
                    },
                ),
            },
            reference={
                "expected_qualified_by_task": {"synthesis": []},
                "expected_rejections": {"m-a/synthesis": "insufficient repetitions"},
            },
        ),
        _task_qualification_case(
            "tq-stale-evidence",
            "stale live evidence",
            "Task evidence older than the freshness limit is rejected.",
            role="reasoning",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="reasoning",
                    age_seconds=5000,
                    tasks={
                        "lq-literature-synthesis": {"det": 0.9},
                        "lq-evidence-extraction": {"det": 0.5},
                        "lq-gap-analysis": {"det": 0.5},
                        "lq-mechanism-development": {"det": 0.5},
                        "lq-model-specification": {"det": 0.5},
                        "lq-proposition-generation": {"det": 0.5},
                    },
                ),
            },
            criteria={"role": "reasoning", "leaderboard_max_age_seconds": 100},
            reference={
                "expected_qualified_by_task": {"synthesis": []},
                "expected_rejections": {"m-a/synthesis": "stale"},
            },
        ),
        _task_qualification_case(
            "tq-no-qualified",
            "no qualified candidate",
            "When no candidate qualifies for any task, every task has an empty "
            "qualified set and the role is not qualified.",
            role="fast",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="fast",
                    benchmark_id="live-quality-fast-v1",
                    tasks={case_id: {"det": 0.5} for case_id in _FAST_CASES},
                ),
                "m-b": _lq_tasks_result(
                    "m-b",
                    role="fast",
                    benchmark_id="live-quality-fast-v1",
                    tasks={case_id: {"det": 0.6} for case_id in _FAST_CASES},
                ),
            },
            reference={
                "expected_role": "fast",
                "expected_qualified_by_task": {"screening": []},
                "expected_role_qualified_models": [],
            },
        ),
        _task_qualification_case(
            "tq-screening-denominator-includes-exercised",
            "screening denominator includes every exercised case",
            "Both fast screening cases map to the screening task; a failing case "
            "can never be silently excluded from the denominator, so screening "
            "is not qualified.",
            role="fast",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="fast",
                    benchmark_id="live-quality-fast-v1",
                    tasks={
                        "lq-fast-screening-clear": {"det": 0.9},
                        "lq-fast-screening-uncertain": {"det": 0.0},
                    },
                ),
            },
            reference={
                "expected_role": "fast",
                "expected_qualified_by_task": {"screening": []},
                "expected_rejections": {"m-a/screening": "deterministic_pass_rate"},
            },
        ),
        _task_qualification_case(
            "tq-unexercised-task-not-qualified",
            "unexercised task cannot be silently qualified",
            "A model with no exercised cases for a task is never qualified for "
            "it — the task cannot be silently excluded from qualification.",
            role="reasoning",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="reasoning",
                    tasks={
                        "lq-evidence-extraction": {"det": 0.9},
                        "lq-literature-synthesis": {"det": 0.9},
                    },
                ),
            },
            reference={
                "expected_qualified_by_task": {
                    "evidence_extraction": ["m-a"],
                    "synthesis": ["m-a"],
                    "gap_analysis": [],
                    "mechanism_generation": [],
                    "model_specification": [],
                    "proposition_generation": [],
                },
            },
        ),
        _task_qualification_case(
            "tq-provider-unavailable-not-model-quality",
            "provider unavailable is not a model-quality failure",
            "A task with a provider-error frequency above the cap is not "
            "qualified and the structured rejection is attributed to the "
            "provider, never to model reasoning quality.",
            role="reasoning",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="reasoning",
                    tasks={
                        "lq-gap-analysis": {"det": 0.9, "provider": 0.5},
                        "lq-evidence-extraction": {"det": 0.5},
                        "lq-literature-synthesis": {"det": 0.5},
                        "lq-mechanism-development": {"det": 0.5},
                        "lq-model-specification": {"det": 0.5},
                        "lq-proposition-generation": {"det": 0.5},
                    },
                ),
            },
            reference={
                "expected_qualified_by_task": {"gap_analysis": []},
                "expected_rejections": {"m-a/gap_analysis": "provider_error"},
            },
        ),
        _task_qualification_case(
            "tq-task-primary-and-fallback",
            "per-task primary and fallback from qualified models only",
            "Two qualified models for a critic task give a ranked primary and "
            "fallback; a cheaper unqualified model is never ranked.",
            role="critic",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="critic",
                    benchmark_id="live-quality-critic-v1",
                    cost=0.05,
                    tasks={case_id: {"det": 0.9} for case_id in _CRITIC_CASES},
                ),
                "m-b": _lq_tasks_result(
                    "m-b",
                    role="critic",
                    benchmark_id="live-quality-critic-v1",
                    cost=0.06,
                    tasks={case_id: {"det": 0.9} for case_id in _CRITIC_CASES},
                ),
                "m-cheap-bad": _lq_tasks_result(
                    "m-cheap-bad",
                    role="critic",
                    benchmark_id="live-quality-critic-v1",
                    cost=0.0001,
                    tasks={case_id: {"det": 0.5} for case_id in _CRITIC_CASES},
                ),
            },
            reference={
                "expected_role": "critic",
                "expected_qualified_by_task": {
                    "mechanism_critique": ["m-a", "m-b"],
                    "results_critique": ["m-a", "m-b"],
                },
                "expected_ranked_by_task": {"mechanism_critique": ["m-a", "m-b"]},
                "expected_role_qualified_models": ["m-a", "m-b"],
            },
        ),
        _task_qualification_case(
            "tq-role-task-isolation-across-roles",
            "role/task isolation across roles",
            "A reasoning-qualified model is never considered for critic tasks; "
            "role isolation keeps the critic matrix free of reasoning results.",
            role="critic",
            live_results={
                "m-reasoning": _lq_tasks_result(
                    "m-reasoning",
                    role="reasoning",
                    tasks={
                        "lq-evidence-extraction": {"det": 0.9},
                        "lq-literature-synthesis": {"det": 0.9},
                        "lq-gap-analysis": {"det": 0.9},
                        "lq-mechanism-development": {"det": 0.9},
                        "lq-model-specification": {"det": 0.9},
                        "lq-proposition-generation": {"det": 0.9},
                    },
                ),
                "m-critic": _lq_tasks_result(
                    "m-critic",
                    role="critic",
                    benchmark_id="live-quality-critic-v1",
                    tasks={case_id: {"det": 0.5} for case_id in _CRITIC_CASES},
                ),
            },
            reference={
                "expected_role": "critic",
                "expected_qualified_by_task": {
                    "mechanism_critique": [],
                    "results_critique": [],
                },
                "expected_role_qualified_models": [],
            },
        ),
        _task_qualification_case(
            "tq-task-primary-without-fallback",
            "per-task primary without fallback",
            "A task with exactly one qualified model gets a primary but no "
            "fallback; fallback eligibility is never invented.",
            role="critic",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="critic",
                    benchmark_id="live-quality-critic-v1",
                    tasks={case_id: {"det": 0.9} for case_id in _CRITIC_CASES},
                ),
                "m-b": _lq_tasks_result(
                    "m-b",
                    role="critic",
                    benchmark_id="live-quality-critic-v1",
                    tasks={case_id: {"det": 0.5} for case_id in _CRITIC_CASES},
                ),
            },
            reference={
                "expected_role": "critic",
                "expected_qualified_by_task": {
                    "mechanism_critique": ["m-a"],
                    "results_critique": ["m-a"],
                },
                "expected_ranked_by_task": {"mechanism_critique": ["m-a"]},
                "expected_role_qualified_models": ["m-a"],
            },
        ),
        _task_qualification_case(
            "tq-task-pool-model-covers-role-default-failure",
            "stronger task-pool model covers a task the role default fails",
            "A model that is not role-qualified can still qualify for an "
            "individual task, so a stronger task-specific candidate pool can "
            "cover a task the role's default cannot.",
            role="reasoning",
            live_results={
                "m-role-default": _lq_tasks_result(
                    "m-role-default",
                    role="reasoning",
                    tasks={
                        "lq-evidence-extraction": {"det": 0.5},
                        "lq-literature-synthesis": {"det": 0.5},
                        "lq-gap-analysis": {"det": 0.9},
                        "lq-mechanism-development": {"det": 0.5},
                        "lq-model-specification": {"det": 0.5},
                        "lq-proposition-generation": {"det": 0.5},
                    },
                ),
                "m-task-pool": _lq_tasks_result(
                    "m-task-pool",
                    role="reasoning",
                    tasks={
                        "lq-evidence-extraction": {"det": 0.5},
                        "lq-literature-synthesis": {"det": 0.5},
                        "lq-gap-analysis": {"det": 0.5},
                        "lq-mechanism-development": {"det": 0.9},
                        "lq-model-specification": {"det": 0.5},
                        "lq-proposition-generation": {"det": 0.5},
                    },
                ),
            },
            reference={
                "expected_qualified_by_task": {
                    "gap_analysis": ["m-role-default"],
                    "mechanism_generation": ["m-task-pool"],
                },
                "expected_role_qualified_models": [],
            },
        ),
    ],
)


TASK_AWARE_SHADOW_ROUTING_V1: BenchmarkDefinition = BenchmarkDefinition(
    benchmark_id="task-aware-shadow-routing-v1",
    version=1,
    name="Task-Aware Shadow Routing (Phase 7D.4)",
    description=(
        "Offline benchmark over the real deterministic task-aware shadow "
        "routing logic. Verifies exact-task selection (qualified models only, "
        "never transferred across tasks), task specialization, uncovered-task "
        "static fallback (no_qualified_task_model), unqualified-cheaper-model "
        "non-selection, qualified primary/fallback behavior, primary-without-"
        "qualified-fallback, stale-qualification rejection, role/task mismatch "
        "rejection, deterministic tie-breaks, and that unsafe_task_route_rate "
        "stays 0. Shadow only — production never switches."
    ),
    category="task_aware_routing",
    config={"evaluators": ["evaluator.task_aware_routing"]},
    cases=[
        _task_aware_routing_case(
            "tar-qualified-task-selects",
            "qualified task selects qualified model",
            "A model qualified for evidence_extraction is selected for that task.",
            role="reasoning",
            task="evidence_extraction",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="reasoning",
                    tasks={"lq-evidence-extraction": {"det": 0.9}},
                ),
            },
            reference={
                "expected_status": "selected",
                "expected_primary": "m-a",
                "expected_shadow_selected": "m-a",
                "expected_would_switch": True,
                "expected_reason": "",
            },
        ),
        _task_aware_routing_case(
            "tar-different-tasks-different-models",
            "different tasks select different models",
            "Two tasks specialize on different qualified models.",
            role="reasoning",
            tasks=["evidence_extraction", "gap_analysis"],
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="reasoning",
                    tasks={"lq-evidence-extraction": {"det": 0.95}},
                ),
                "m-b": _lq_tasks_result(
                    "m-b",
                    role="reasoning",
                    tasks={"lq-gap-analysis": {"det": 0.95}},
                ),
            },
            reference={
                "expected_decisions": {
                    "evidence_extraction": {
                        "expected_status": "selected",
                        "expected_primary": "m-a",
                        "expected_shadow_selected": "m-a",
                        "expected_would_switch": True,
                    },
                    "gap_analysis": {
                        "expected_status": "selected",
                        "expected_primary": "m-b",
                        "expected_shadow_selected": "m-b",
                        "expected_would_switch": True,
                    },
                }
            },
        ),
        _task_aware_routing_case(
            "tar-no-qualification-transfer",
            "qualification is not transferred between tasks",
            "A model qualified for evidence_extraction is NOT used for mechanism_generation.",
            role="reasoning",
            task="mechanism_generation",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="reasoning",
                    tasks={"lq-evidence-extraction": {"det": 0.95}},
                ),
            },
            reference={
                "expected_status": "static_fallback",
                "expected_reason": "no_qualified_task_model",
                "expected_primary": None,
                "expected_shadow_selected": None,
                "expected_would_switch": False,
            },
        ),
        _task_aware_routing_case(
            "tar-uncovered-task-stays-static",
            "uncovered task stays static",
            "A task with no qualified model keeps the configured static model.",
            role="reasoning",
            task="model_specification",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="reasoning",
                    tasks={"lq-model-specification": {"det": 0.5}},
                ),
            },
            reference={
                "expected_status": "static_fallback",
                "expected_reason": "no_qualified_task_model",
                "expected_primary": None,
                "expected_shadow_selected": None,
                "expected_would_switch": False,
                "expected_fallback": "static/model",
                "expected_fallback_not_live_qualified": True,
            },
        ),
        _task_aware_routing_case(
            "tar-unqualified-cheaper-never-selected",
            "unqualified cheaper model never selected",
            "A cheaper but unqualified model is never chosen over a qualified one.",
            role="reasoning",
            task="evidence_extraction",
            live_results={
                "m-cheap": _lq_tasks_result(
                    "m-cheap",
                    role="reasoning",
                    tasks={"lq-evidence-extraction": {"det": 0.5}},
                    cost=0.0,
                ),
                "m-good": _lq_tasks_result(
                    "m-good",
                    role="reasoning",
                    tasks={"lq-evidence-extraction": {"det": 0.9}},
                    cost=1.0,
                ),
            },
            reference={
                "expected_status": "selected",
                "expected_primary": "m-good",
                "expected_shadow_selected": "m-good",
            },
        ),
        _task_aware_routing_case(
            "tar-qualified-primary-plus-fallback",
            "qualified primary + fallback",
            "Two qualified models: primary and a live-qualified fallback are recorded.",
            role="reasoning",
            task="evidence_extraction",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="reasoning",
                    tasks={"lq-evidence-extraction": {"det": 0.95}},
                ),
                "m-b": _lq_tasks_result(
                    "m-b",
                    role="reasoning",
                    tasks={"lq-evidence-extraction": {"det": 0.9}},
                ),
            },
            reference={
                "expected_status": "selected",
                "expected_primary": "m-a",
                "expected_fallback": "m-b",
                "expected_fallback_is_qualified": True,
                "expected_fallback_not_live_qualified": False,
                "expected_would_switch": True,
            },
        ),
        _task_aware_routing_case(
            "tar-primary-without-qualified-fallback",
            "primary without qualified fallback",
            "Only a primary is qualified; the static model is the fallback, "
            "explicitly marked not live qualified.",
            role="reasoning",
            task="evidence_extraction",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="reasoning",
                    tasks={"lq-evidence-extraction": {"det": 0.9}},
                ),
            },
            reference={
                "expected_status": "selected",
                "expected_primary": "m-a",
                "expected_fallback": "static/model",
                "expected_fallback_is_qualified": False,
                "expected_fallback_not_live_qualified": True,
                "expected_would_switch": True,
            },
        ),
        _task_aware_routing_case(
            "tar-stale-qualification-rejected",
            "stale qualification rejected",
            "Qualification evidence older than the max age is rejected; the task stays static.",
            role="reasoning",
            task="evidence_extraction",
            max_qualification_age_seconds=60,
            matrix_age_seconds=3600,
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="reasoning",
                    tasks={"lq-evidence-extraction": {"det": 0.9}},
                ),
            },
            reference={
                "expected_status": "static_fallback",
                "expected_reason": "stale_qualification",
                "expected_primary": None,
                "expected_shadow_selected": None,
                "expected_would_switch": False,
            },
        ),
        _task_aware_routing_case(
            "tar-role-task-mismatch-rejected",
            "role/task mismatch rejected",
            "A task that does not belong to the role is rejected (never routed).",
            role="reasoning",
            task="screening",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="reasoning",
                    tasks={"lq-evidence-extraction": {"det": 0.9}},
                ),
            },
            reference={
                "expected_status": "static_fallback",
                "expected_reason": "role_task_mismatch",
                "expected_primary": None,
                "expected_shadow_selected": None,
                "expected_would_switch": False,
            },
        ),
        _task_aware_routing_case(
            "tar-deterministic-tie-break",
            "deterministic tie break",
            "Identical qualified candidates resolve deterministically by candidate_id.",
            role="reasoning",
            task="evidence_extraction",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="reasoning",
                    tasks={"lq-evidence-extraction": {"det": 0.9}},
                ),
                "m-b": _lq_tasks_result(
                    "m-b",
                    role="reasoning",
                    tasks={"lq-evidence-extraction": {"det": 0.9}},
                ),
            },
            reference={
                "expected_status": "selected",
                "expected_primary": "m-a",
                "expected_shadow_selected": "m-a",
                "expected_fallback": "m-b",
                "expected_fallback_is_qualified": True,
            },
        ),
        _task_aware_routing_case(
            "tar-static-model-kept-when-current",
            "static model kept when already qualified",
            "When the static configured model is itself the top qualified model, "
            "would_switch is False.",
            role="reasoning",
            task="evidence_extraction",
            static_model="m-a",
            live_results={
                "m-a": _lq_tasks_result(
                    "m-a",
                    role="reasoning",
                    tasks={"lq-evidence-extraction": {"det": 0.9}},
                ),
            },
            reference={
                "expected_status": "selected",
                "expected_primary": "m-a",
                "expected_shadow_selected": "m-a",
                "expected_would_switch": False,
            },
        ),
    ],
)


BUILTIN_BENCHMARKS: dict[str, BenchmarkDefinition] = {
    NOVELTY_THREAT_V1.benchmark_id: NOVELTY_THREAT_V1,
    LITERATURE_RETRIEVAL_V1.benchmark_id: LITERATURE_RETRIEVAL_V1,
    CITATION_CORRECTNESS_V1.benchmark_id: CITATION_CORRECTNESS_V1,
    LITERATURE_SCREENING_V1.benchmark_id: LITERATURE_SCREENING_V1,
    EVIDENCE_EXTRACTION_V1.benchmark_id: EVIDENCE_EXTRACTION_V1,
    RESEARCH_GAP_ANALYSIS_V1.benchmark_id: RESEARCH_GAP_ANALYSIS_V1,
    MECHANISM_DEVELOPMENT_V1.benchmark_id: MECHANISM_DEVELOPMENT_V1,
    EQUILIBRIUM_CORRECTNESS_V1.benchmark_id: EQUILIBRIUM_CORRECTNESS_V1,
    NUMERICAL_ANALYSIS_V1.benchmark_id: NUMERICAL_ANALYSIS_V1,
    COMPARATIVE_STATICS_V1.benchmark_id: COMPARATIVE_STATICS_V1,
    PROPOSITION_CORRECTNESS_V1.benchmark_id: PROPOSITION_CORRECTNESS_V1,
    RESULTS_ASSEMBLY_V1.benchmark_id: RESULTS_ASSEMBLY_V1,
    MANUSCRIPT_GROUNDING_V1.benchmark_id: MANUSCRIPT_GROUNDING_V1,
    RESEARCH_PIPELINE_E2E_V1.benchmark_id: RESEARCH_PIPELINE_E2E_V1,
    LITERATURE_SYNTHESIS_V1.benchmark_id: LITERATURE_SYNTHESIS_V1,
    ANALYTICAL_MODEL_SPECIFICATION_V1.benchmark_id: ANALYTICAL_MODEL_SPECIFICATION_V1,
    DOCUMENT_ACQUISITION_V1.benchmark_id: DOCUMENT_ACQUISITION_V1,
    INCREMENTAL_REVALIDATION_V1.benchmark_id: INCREMENTAL_REVALIDATION_V1,
    LITERATURE_INGESTION_IDENTITY_V1.benchmark_id: LITERATURE_INGESTION_IDENTITY_V1,
    GAP_SELECTION_V1.benchmark_id: GAP_SELECTION_V1,
    NOVELTY_REVALIDATION_V1.benchmark_id: NOVELTY_REVALIDATION_V1,
    PUBLICATION_PACKAGING_V1.benchmark_id: PUBLICATION_PACKAGING_V1,
    EVIDENCE_ENRICHMENT_V1.benchmark_id: EVIDENCE_ENRICHMENT_V1,
    MODEL_ROUTING_POLICY_V1.benchmark_id: MODEL_ROUTING_POLICY_V1,
    LIVE_QUALITY_REASONING_V1.benchmark_id: LIVE_QUALITY_REASONING_V1,
    LIVE_QUALITY_CRITIC_V1.benchmark_id: LIVE_QUALITY_CRITIC_V1,
    LIVE_QUALITY_FAST_V1.benchmark_id: LIVE_QUALITY_FAST_V1,
    LIVE_QUALITY_EVALUATOR_SANITY_V1.benchmark_id: LIVE_QUALITY_EVALUATOR_SANITY_V1,
    PRODUCTION_ROUTING_READINESS_V1.benchmark_id: PRODUCTION_ROUTING_READINESS_V1,
    MODEL_QUALIFICATION_POLICY_V1.benchmark_id: MODEL_QUALIFICATION_POLICY_V1,
    TASK_SPECIFIC_MODEL_QUALIFICATION_V1.benchmark_id: TASK_SPECIFIC_MODEL_QUALIFICATION_V1,
    TASK_AWARE_SHADOW_ROUTING_V1.benchmark_id: TASK_AWARE_SHADOW_ROUTING_V1,
}
