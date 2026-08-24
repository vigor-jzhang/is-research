"""Built-in benchmark definitions — Phase 6A.

Benchmarks are immutable, versioned definitions. A change to a definition
requires a new benchmark version (the harness refuses to silently overwrite a
registered benchmark whose content differs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
}
