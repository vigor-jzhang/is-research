"""Phase 7A.1 unit tests — publication-packaging evaluator."""

from __future__ import annotations

import pathlib

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_publication_packaging.plugin import (
    PublicationPackagingEvaluator,
)
from research_harness.research.benchmarks.workflows import PackagingReport
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus
from research_harness.research.schemas.publication import (
    Bibliography,
    BibliographyEntry,
    FormattedManuscript,
    FormattedManuscriptStatus,
    FormattedSection,
    FrontMatter,
    SubmissionPackage,
    SubmissionPackageStatus,
)


def _package_env(status: str, export_formats: list[str]) -> ArtifactEnvelope:
    from research_harness.research.schemas.publication import ExportRecord

    exports = [
        ExportRecord(
            format=f,
            renderer="test",
            renderer_version="1",
            blob_ref={
                "algorithm": "sha256",
                "digest": "x",
                "size_bytes": 1,
                "storage_key": "ab/x",
                "media_type": "application/octet-stream",
            },
            content_hash="hash",
            size_bytes=1,
            source_draft_id="d",
            profile_id="p",
        )
        for f in export_formats
    ]
    return ArtifactEnvelope.create(
        payload=SubmissionPackage(
            formatted_manuscript_id="fm",
            draft_id="d",
            profile_id="p",
            export_records=exports,
            status=SubmissionPackageStatus(status),
        ),
        artifact_type="submission_package",
        producer="test",
    )


def _export_env(fmt: str) -> ArtifactEnvelope:
    from research_harness.research.schemas.publication import ExportRecord

    return ArtifactEnvelope.create(
        payload=ExportRecord(
            format=fmt,
            renderer="test",
            renderer_version="1",
            blob_ref={
                "algorithm": "sha256",
                "digest": "x",
                "size_bytes": 1,
                "storage_key": "ab/x",
                "media_type": "application/octet-stream",
            },
            content_hash="hash",
            size_bytes=1,
            source_draft_id="d",
            profile_id="p",
        ),
        artifact_type="export_record",
        producer="test",
    )


def _manuscript_env(
    citation_map: dict[str, str], *, anonymous: bool = False, body: str = "Prior work is relevant."
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=FormattedManuscript(
            draft_id="d",
            results_package_id="r",
            profile_id="p",
            profile_name="P",
            citation_style="author_year",
            front_matter=FrontMatter(title="T", authors=[] if anonymous else ["Smith, Jane"]),
            sections=[
                FormattedSection(
                    section_id="introduction", title="Introduction", body=body, word_count=3
                )
            ],
            validation_status=FormattedManuscriptStatus.validated,
            citation_map=citation_map,
            bibliography=None,
            anonymous_review=anonymous,
        ),
        artifact_type="formatted_manuscript",
        producer="test",
    )


def _bib_env(entries: list[tuple[str, str]]) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=Bibliography(
            profile_name="P",
            citation_style="author_year",
            entries=[
                BibliographyEntry(
                    paper_identity_id=iid,
                    citation_ids=[cid],
                    title="t",
                    rendered="r",
                )
                for iid, cid in entries
            ],
        ),
        artifact_type="bibliography",
        producer="test",
    )


def _profile_env(anonymous: bool) -> ArtifactEnvelope:
    from research_harness.research.schemas.publication import PublicationProfile

    return ArtifactEnvelope.create(
        payload=PublicationProfile(
            name="P",
            anonymous_review=anonymous,
            abstract_required=False,
        ),
        artifact_type="publication_profile",
        producer="test",
    )


def _report(
    export_ids: list[str] | None = None,
    reexport_ids: list[str] | None = None,
    error: str | None = None,
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=PackagingReport(
            benchmark_case_id="c1",
            formatted_manuscript_id="fm",
            package_id="pkg",
            export_ids=list(export_ids or []),
            reexport_ids=list(reexport_ids or []),
            error=error,
        ),
        artifact_type="packaging_report",
        producer="test",
    )


def _case(reference: dict) -> BenchmarkCase:
    return BenchmarkCase(
        id="c1",
        benchmark_id="b",
        version=1,
        name="case",
        input={},
        reference=reference,
        evaluation_dimensions=["publication_packaging"],
        tags=[],
    )


def _ctx(case: BenchmarkCase, produced: list) -> EvaluatorContext:
    return EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=produced,
        config={},
    )


async def test_ready_package_passes(tmp_path: pathlib.Path):
    class _FakeBlobStore:
        def __init__(self) -> None:
            self._existing = {"ab/x"}

        async def exists(self, ref: object) -> bool:
            return getattr(ref, "storage_key", None) in self._existing

    ref = {"expected_package_status": "ready", "expected_failure": False}
    produced = [
        _report(["e1", "e2", "e3", "e4"], ["e1", "e2", "e3", "e4"]),
        _package_env("ready", ["markdown", "latex", "docx", "pdf"]),
        _export_env("markdown"),
        _export_env("latex"),
        _export_env("docx"),
        _export_env("pdf"),
        _manuscript_env({"c1": "id-a"}),
        _bib_env([("id-a", "c1")]),
        _profile_env(False),
    ]
    ctx = _ctx(_case(ref), produced)
    ctx = EvaluatorContext(
        case=ctx.case,
        case_envelope=ctx.case_envelope,
        produced_artifacts=produced,
        config={},
        blob_store=_FakeBlobStore(),
    )
    result = await PublicationPackagingEvaluator().evaluate(ctx)
    assert result.status == EvaluatorStatus.passed
    m = result.value["metrics"]
    assert m["package_validation_accuracy"]["value"] == 1.0
    assert m["export_success_accuracy"]["value"] == 4.0
    assert m["bibliography_integrity"]["value"] == 1.0
    assert m["placeholder_removal_accuracy"]["value"] == 1.0
    assert m["blob_persistence_accuracy"]["value"] == 1.0
    assert m["deterministic_render_accuracy"]["value"] == 1.0


async def test_failed_package_matches_reference():
    ref = {"expected_package_status": "failed", "expected_failure": True}
    produced = [
        _report([], []),
        _package_env("failed", []),
        _manuscript_env({"c1": "id-a"}),
        _bib_env([]),
        _profile_env(False),
    ]
    result = await PublicationPackagingEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["metrics"]["package_validation_accuracy"]["value"] == 1.0


async def test_ready_but_failed_package_fails():
    ref = {"expected_package_status": "ready"}
    produced = [
        _report([], []),
        _package_env("failed", ["markdown"]),
        _manuscript_env({"c1": "id-a"}),
        _bib_env([]),
        _profile_env(False),
    ]
    result = await PublicationPackagingEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.failed
    assert "PACKAGE STATUS MISMATCH" in result.explanation


async def test_leftover_placeholder_fails():
    ref = {"expected_package_status": "ready", "expected_placeholder": False}
    produced = [
        _report([], []),
        _package_env("ready", ["markdown", "latex", "docx", "pdf"]),
        _manuscript_env({"c1": "id-a"}, body="Prior work [CITE:c1]."),
        _bib_env([("id-a", "c1")]),
        _profile_env(False),
    ]
    result = await PublicationPackagingEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.failed
    assert "LEFTOVER PLACEHOLDERS" in result.explanation


async def test_missing_export_fails():
    ref = {"expected_package_status": "ready"}
    produced = [
        _report(["e1", "e2"], ["e1", "e2"]),
        _package_env("ready", ["markdown", "latex"]),
        _export_env("markdown"),
        _export_env("latex"),
        _manuscript_env({"c1": "id-a"}),
        _bib_env([("id-a", "c1")]),
        _profile_env(False),
    ]
    result = await PublicationPackagingEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.failed
    assert "EXPORT MISSING" in result.explanation


async def test_nondeterministic_render_fails():
    ref = {"expected_package_status": "ready"}
    produced = [
        _report(["e1", "e2", "e3", "e4"], ["e9", "e2", "e3", "e4"]),
        _package_env("ready", ["markdown", "latex", "docx", "pdf"]),
        _manuscript_env({"c1": "id-a"}),
        _bib_env([("id-a", "c1")]),
        _profile_env(False),
    ]
    result = await PublicationPackagingEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.failed
    assert "DETERMINISTIC RERENDER" in result.explanation


async def test_no_package_artifacts_fails():
    result = await PublicationPackagingEvaluator().evaluate(_ctx(_case({}), []))
    assert result.status == EvaluatorStatus.failed
    assert result.score is None
