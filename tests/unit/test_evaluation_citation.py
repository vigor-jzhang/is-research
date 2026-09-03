"""Phase 6B unit tests — manuscript_citation evaluator mode.

Covers: citation resolution, bibliography deduplication, wrong identity
mapping, unresolved citations, leftover placeholders, sparse metadata
without invention, and invented bibliographic fields.
"""

from __future__ import annotations

import pytest

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_citation_correctness.plugin import (
    CitationCorrectnessEvaluator,
)
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus
from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
from research_harness.research.schemas.paper import PaperRecord
from research_harness.research.schemas.publication import (
    Bibliography,
    BibliographyEntry,
    FormattedManuscript,
    FormattedSection,
    FrontMatter,
)

PAPER_A = {
    "title": "Consumer Welfare Under Algorithmic Pricing",
    "authors": ["Smith, Jane"],
    "year": 2021,
    "venue": "Journal of Platform Studies",
    "doi": "10.6000/cit-a",
}


def _paper_env(paper_id: str, paper: dict, *, identity_id: str) -> list[ArtifactEnvelope]:
    rec = ArtifactEnvelope.create(
        payload=PaperRecord(
            title=paper["title"],
            authors=[{"name": a} for a in paper.get("authors", [])],
            year=paper.get("year"),
            venue=paper.get("venue"),
            doi=paper.get("doi"),
        ),
        artifact_type="paper_record",
        producer="test",
        artifact_id=paper_id,
    )
    identity = ArtifactEnvelope.create(
        payload=PaperIdentity(
            member_paper_artifact_ids=[paper_id],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.manual,
            resolution_evidence=[],
        ),
        artifact_type="paper_identity",
        producer="test",
        artifact_id=identity_id,
    )
    return [rec, identity]


def _manuscript(
    bodies: list[str],
    citation_map: dict[str, str],
    entries: list[BibliographyEntry],
    *,
    anonymous: bool = False,
) -> ArtifactEnvelope:
    manuscript = FormattedManuscript(
        draft_id="d",
        results_package_id="r",
        profile_id="p",
        profile_name="profile",
        front_matter=FrontMatter(title="T", authors=[] if anonymous else ["Smith, Jane"]),
        sections=[
            FormattedSection(section_id=f"s{i}", title=f"S{i}", body=body)
            for i, body in enumerate(bodies)
        ],
        bibliography=Bibliography(
            profile_name="profile",
            entries=entries,
        ),
        anonymous_review=anonymous,
        citation_map=citation_map,
    )
    return ArtifactEnvelope.create(
        payload=manuscript,
        artifact_type="formatted_manuscript",
        producer="test",
    )


def _entry(
    identity_id: str,
    citation_ids: list[str],
    *,
    title: str = "Consumer Welfare Under Algorithmic Pricing",
    authors: list[str] | None = None,
    year: int | None = 2021,
    venue: str | None = "Journal of Platform Studies",
    doi: str | None = "10.6000/cit-a",
) -> BibliographyEntry:
    return BibliographyEntry(
        paper_identity_id=identity_id,
        citation_ids=citation_ids,
        title=title,
        authors=["Smith, Jane"] if authors is None else authors,
        year=year,
        venue=venue,
        doi=doi,
        rendered="entry",
    )


def _case(reference: dict) -> BenchmarkCase:
    return BenchmarkCase(
        id="c1",
        benchmark_id="b",
        version=1,
        name="case",
        input={},
        reference=reference,
        evaluation_dimensions=["citation"],
        tags=[],
    )


def _ctx(case: BenchmarkCase, produced: list) -> EvaluatorContext:
    return EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=produced,
        config={"citation_mode": "manuscript_citation"},
    )


async def test_valid_citation_passes():
    papers = _paper_env("paper-a", PAPER_A, identity_id="identity-a")
    ms = _manuscript(
        ["Prior work (Smith, Jane, 2021) studies welfare."],
        {"c1": "identity-a"},
        [_entry("identity-a", ["c1"])],
    )
    case = _case(
        {
            "expected_identity_map": {"c1": "identity-a"},
            "expected_bibliography_citation_ids": {"identity-a": ["c1"]},
            "expected_leftover_placeholders": [],
        }
    )
    result = await CitationCorrectnessEvaluator().evaluate(_ctx(case, papers + [ms]))
    assert result.status == EvaluatorStatus.passed
    assert result.value["leftover_placeholders"] == []
    assert result.value["unresolved_citations"] == []
    assert result.value["invented_fields"] == []
    assert result.value["metrics"]["citation_resolution_accuracy"]["value"] == 1.0


async def test_bibliography_deduplication_two_citation_ids():
    papers = _paper_env("paper-a", PAPER_A, identity_id="identity-a")
    ms = _manuscript(
        ["(Smith, Jane, 2021) and (Smith, Jane, 2021) cite the same work."],
        {"c1": "identity-a", "c2": "identity-a"},
        [_entry("identity-a", ["c1", "c2"])],
    )
    case = _case(
        {
            "expected_identity_map": {"c1": "identity-a", "c2": "identity-a"},
            "expected_bibliography_citation_ids": {"identity-a": ["c1", "c2"]},
            "expected_leftover_placeholders": [],
        }
    )
    result = await CitationCorrectnessEvaluator().evaluate(_ctx(case, papers + [ms]))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["bibliography_deduplication_accuracy"]["value"] == 1.0


async def test_unresolved_citation_fails():
    papers = _paper_env("paper-a", PAPER_A, identity_id="identity-a")
    # c2 declared in the map but absent from the bibliography
    ms = _manuscript(
        ["Rendered text only."],
        {"c1": "identity-a", "c2": "identity-a"},
        [_entry("identity-a", ["c1"])],
    )
    case = _case(
        {
            "expected_identity_map": {"c1": "identity-a", "c2": "identity-a"},
            "expected_bibliography_citation_ids": {"identity-a": ["c1"]},
            "expected_leftover_placeholders": [],
        }
    )
    result = await CitationCorrectnessEvaluator().evaluate(_ctx(case, papers + [ms]))
    assert result.status == EvaluatorStatus.failed
    assert result.value["unresolved_citations"] == ["c2"]
    assert result.value["metrics"]["unresolved_citation_count"]["value"] == 1.0
    assert result.value["metrics"]["citation_resolution_accuracy"]["value"] / result.value[
        "metrics"
    ]["citation_resolution_accuracy"]["count"] == pytest.approx(0.5)


async def test_wrong_identity_mapping_fails():
    papers = _paper_env("paper-a", PAPER_A, identity_id="identity-a")
    # c1 wired to identity-b but expected to be identity-a
    ms = _manuscript(
        ["Rendered text."],
        {"c1": "identity-b"},
        [_entry("identity-b", ["c1"], title="Other Paper", year=2019)],
    )
    case = _case(
        {
            "expected_identity_map": {"c1": "identity-a"},
            "expected_bibliography_citation_ids": {"identity-a": ["c1"]},
            "expected_leftover_placeholders": [],
        }
    )
    result = await CitationCorrectnessEvaluator().evaluate(_ctx(case, papers + [ms]))
    assert result.status == EvaluatorStatus.failed
    assert result.value["metrics"]["citation_map_accuracy"]["value"] == 0.0
    assert "citation_map" in result.explanation


async def test_leftover_placeholder_fails():
    papers = _paper_env("paper-a", PAPER_A, identity_id="identity-a")
    ms = _manuscript(
        ["Rendered text and a dangling [CITE:ghost]."],
        {"c1": "identity-a"},
        [_entry("identity-a", ["c1"])],
    )
    case = _case(
        {
            "expected_identity_map": {"c1": "identity-a"},
            "expected_bibliography_citation_ids": {"identity-a": ["c1"]},
            "expected_leftover_placeholders": [],
        }
    )
    result = await CitationCorrectnessEvaluator().evaluate(_ctx(case, papers + [ms]))
    assert result.status == EvaluatorStatus.failed
    assert result.value["leftover_placeholders"] == ["ghost"]
    assert result.value["metrics"]["leftover_placeholder_count"]["value"] == 1.0


async def test_sparse_metadata_without_invention():
    sparse = {
        "title": "Working Paper on Quantity Games",
    }
    papers = _paper_env("paper-s", sparse, identity_id="identity-s")
    ms = _manuscript(
        ['An unpublished note ("Working Paper on Quantity Games") discusses quantity games.'],
        {"c1": "identity-s"},
        [
            _entry(
                "identity-s",
                ["c1"],
                title="Working Paper on Quantity Games",
                authors=[],
                year=None,
                venue=None,
                doi=None,
            )
        ],
    )
    case = _case(
        {
            "expected_identity_map": {"c1": "identity-s"},
            "expected_bibliography_citation_ids": {"identity-s": ["c1"]},
            "expected_leftover_placeholders": [],
        }
    )
    result = await CitationCorrectnessEvaluator().evaluate(_ctx(case, papers + [ms]))
    assert result.status == EvaluatorStatus.passed
    assert result.value["invented_fields"] == []


async def test_invented_bibliographic_field_fails():
    papers = _paper_env("paper-a", PAPER_A, identity_id="identity-a")
    # bibliography claims year 1999 though the record says 2021
    ms = _manuscript(
        ["Rendered text."],
        {"c1": "identity-a"},
        [_entry("identity-a", ["c1"], year=1999)],
    )
    case = _case(
        {
            "expected_identity_map": {"c1": "identity-a"},
            "expected_bibliography_citation_ids": {"identity-a": ["c1"]},
            "expected_leftover_placeholders": [],
        }
    )
    result = await CitationCorrectnessEvaluator().evaluate(_ctx(case, papers + [ms]))
    assert result.status == EvaluatorStatus.failed
    assert "identity-a:year" in result.value["invented_fields"]
    assert result.value["metrics"]["invented_bibliographic_field_count"]["value"] == 1.0
    assert "INVENTED" in result.explanation


async def test_unsupported_bibliography_entry_fails():
    papers = _paper_env("paper-a", PAPER_A, identity_id="identity-a")
    # an entry for an identity that is never cited by the manuscript
    ms = _manuscript(
        ["Rendered text."],
        {"c1": "identity-a"},
        [
            _entry("identity-a", ["c1"]),
            _entry("identity-ghost", ["c9"], title="Never Cited", year=2000),
        ],
    )
    case = _case(
        {
            "expected_identity_map": {"c1": "identity-a"},
            "expected_bibliography_citation_ids": {"identity-a": ["c1"]},
            "expected_leftover_placeholders": [],
        }
    )
    result = await CitationCorrectnessEvaluator().evaluate(_ctx(case, papers + [ms]))
    assert result.status == EvaluatorStatus.failed
    assert result.value["unsupported_entries"] == ["identity-ghost"]
    assert result.value["metrics"]["unsupported_bibliography_entry_count"]["value"] == 1.0


async def test_expected_formatter_failure_passes():
    case = _case(
        {
            "expected_formatter_failure": True,
            "expected_identity_map": {},
            "expected_bibliography_citation_ids": {},
            "expected_leftover_placeholders": [],
        }
    )
    result = await CitationCorrectnessEvaluator().evaluate(_ctx(case, []))
    assert result.status == EvaluatorStatus.passed
    assert result.value["formatter_failed"] is True


async def test_inline_rendering_check():
    papers = _paper_env("paper-a", PAPER_A, identity_id="identity-a")
    ms = _manuscript(
        ["The result appears in (Smith, Jane, 2021)."],
        {"c1": "identity-a"},
        [_entry("identity-a", ["c1"])],
    )
    case = _case(
        {
            "expected_identity_map": {"c1": "identity-a"},
            "expected_bibliography_citation_ids": {"identity-a": ["c1"]},
            "expected_leftover_placeholders": [],
            "expected_inline": {"c1": "(Smith, Jane, 2021)"},
        }
    )
    result = await CitationCorrectnessEvaluator().evaluate(_ctx(case, papers + [ms]))
    assert result.status == EvaluatorStatus.passed
    assert result.value["inline_matches"] == 1


# --- regression: placeholder_check mode must contribute metrics -----------
#
# ``_placeholder_check`` returned a value dict with no ``metrics`` and no
# ``dimension_scores``. Because the harness only aggregates metrics from
# deterministic evaluators, any benchmark wiring this evaluator without an
# explicit ``citation_mode`` (it defaults to placeholder_check, e.g.
# novelty-threat-v1) silently contributed nothing to the report.


def _ctx_default_mode(case: BenchmarkCase, produced: list) -> EvaluatorContext:
    """No citation_mode -> placeholder_check, as novelty-threat-v1 does."""
    return EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=produced,
        config={},
    )


async def test_placeholder_check_emits_metrics():
    ms = _manuscript(
        ["Prior work [CITE:c1] studies welfare."],
        {"c1": "identity-a"},
        [_entry("identity-a", ["c1"])],
    )
    result = await CitationCorrectnessEvaluator().evaluate(
        _ctx_default_mode(_case({}), [ms])
    )

    value = result.value or {}
    assert value.get("metrics"), "placeholder_check produced no aggregate metrics"
    metric = value["metrics"]["citation_resolution_accuracy"]
    assert metric["count"] == 1
    assert metric["value"] == 1.0
    assert "citation_resolution_accuracy" in (value.get("dimension_scores") or {})


async def test_placeholder_check_metrics_reflect_unresolved_citations():
    # Marker with no matching bibliography entry.
    ms = _manuscript(["Prior work [CITE:missing] studies welfare."], {}, [])
    result = await CitationCorrectnessEvaluator().evaluate(
        _ctx_default_mode(_case({}), [ms])
    )

    metric = result.value["metrics"]["citation_resolution_accuracy"]
    assert metric["count"] == 1
    assert metric["value"] == 0.0
