"""evaluator.publication_packaging — deterministic publication-packaging
evaluator (Phase 7A.1).

Evaluates produced Phase 4C artifacts (FormattedManuscript, Bibliography,
ExportRecord, SubmissionPackage) against known-answer expectations. Reuses
Phase 6B citation expectations conceptually (bibliography coverage, leftover
placeholders) without duplicating that evaluator.
"""

from __future__ import annotations

import re
from typing import Any

from research_harness.contracts.evaluator import EvaluatorContext, envelope_payload_dict
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.schemas.evaluation import (
    EvaluatorCategory,
    EvaluatorResult,
    EvaluatorStatus,
)

_CITE_RE = re.compile(r"\[CITE:[^\]]*\]")


class PublicationPackagingEvaluator:
    evaluator_id = "evaluator.publication_packaging"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        reports = [e for e in ctx.produced_artifacts if e.artifact_type == "packaging_report"]
        packages = [
            envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "submission_package"
        ]
        manuscripts = [
            envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "formatted_manuscript"
        ]
        bibliographies = [
            envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "bibliography"
        ]
        exports = [
            envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "export_record"
        ]
        if not reports and not packages:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no packaging artifacts produced for the case",
            )
        report = envelope_payload_dict(max(reports, key=lambda e: e.created_at)) if reports else {}
        package = max(packages, key=lambda p: p.get("created_at") or 0) if packages else {}
        manuscript = max(manuscripts, key=lambda m: m.get("created_at") or 0) if manuscripts else {}
        report_error = report.get("error")
        _ = report_error

        reference = ctx.case.reference or {}
        expected_status = str(reference.get("expected_package_status") or "")
        expected_placeholder = bool(reference.get("expected_placeholder") or False)

        failures_detail: list[str] = []

        # ---- package validation --------------------------------------------
        produced_status = str(package.get("status") or "")
        if expected_status == "failed" and not package:
            # the formatter refused (e.g. missing identity); the refusal blocks
            # readiness and is the correct deterministic outcome
            validation_ok = True
        else:
            validation_ok = not expected_status or produced_status == expected_status
        if not validation_ok:
            failures_detail.append(
                f"PACKAGE STATUS MISMATCH: expected {expected_status!r}, produced {produced_status!r}"
            )

        # ---- export success -----------------------------------------------
        expected_formats = {"markdown", "latex", "docx", "pdf"}
        produced_formats = {str(e.get("format") or "") for e in exports}
        export_missing = expected_formats - produced_formats
        if export_missing and expected_status == "ready":
            failures_detail.append(f"EXPORT MISSING: {sorted(export_missing)}")

        # ---- bibliography integrity ---------------------------------------
        bib_entries = [
            str(e.get("paper_identity_id") or "")
            for b in bibliographies
            for e in (b.get("entries") or [])
        ]
        dedup_ok = len(bib_entries) == len(set(bib_entries))
        if not dedup_ok:
            failures_detail.append("BIBLIOGRAPHY NOT DEDUPLICATED")
        citation_map = dict(manuscript.get("citation_map") or {})
        cited = set(citation_map.values())
        bib_covered = set(bib_entries)
        uncovered = cited - bib_covered
        # only ready packages must cover every cited identity; a failed package
        # may be incomplete by design (unused citations, missing sections)
        bib_integrity_ok = dedup_ok and (not uncovered or expected_status != "ready")
        if uncovered and expected_status == "ready":
            failures_detail.append(f"BIBLIOGRAPHY MISSING ENTRIES: {sorted(uncovered)}")

        # ---- placeholder removal ------------------------------------------
        section_bodies = " ".join(
            str(s.get("body") or "") for s in (manuscript.get("sections") or [])
        )
        placeholders = set(_CITE_RE.findall(section_bodies))
        # for a ready package no placeholders may remain; when the reference
        # EXPECTS a leftover placeholder (by-design failure), its presence is the
        # correct detectable outcome
        placeholder_ok = (not placeholders) if not expected_placeholder else True
        if placeholders and not expected_placeholder:
            failures_detail.append(f"LEFTOVER PLACEHOLDERS: {sorted(placeholders)}")

        # ---- anonymization ------------------------------------------------
        profile_anonymous = False
        for env in ctx.produced_artifacts:
            if env.artifact_type == "publication_profile":
                profile_anonymous = bool(envelope_payload_dict(env).get("anonymous_review"))
                break
        authors = list((manuscript.get("front_matter") or {}).get("authors") or [])
        anonymization_ok = True
        if profile_anonymous and authors:
            failures_detail.append("ANONYMIZATION: anonymous review retained author front matter")
            anonymization_ok = False

        # ---- blob persistence ---------------------------------------------
        blob_ok = True
        if not exports:
            pass  # vacuous: nothing to persist
        elif ctx.blob_store is not None:
            for e in exports:
                ref = e.get("blob_ref") or {}
                if not ref:
                    blob_ok = False
                    failures_detail.append("EXPORT WITHOUT BLOB REFERENCE")
                    continue
                try:
                    from research_harness.contracts.blob import BlobReference

                    exists = await ctx.blob_store.exists(BlobReference(**ref))
                    if not exists:
                        blob_ok = False
                        failures_detail.append(f"EXPORT BLOB MISSING: {e.get('format')}")
                except Exception:  # noqa: BLE001
                    blob_ok = False
                    failures_detail.append(f"EXPORT BLOB UNREADABLE: {e.get('format')}")
        else:
            blob_ok = False
            failures_detail.append("blob store unavailable for export persistence check")

        # ---- deterministic rerender ---------------------------------------
        export_ids = [str(e) for e in (report.get("export_ids") or [])]
        reexport_ids = [str(e) for e in (report.get("reexport_ids") or [])]
        render_ok = len(export_ids) == len(reexport_ids) and export_ids == reexport_ids
        if report and not render_ok:
            failures_detail.append("DETERMINISTIC RERENDER: re-export produced different records")

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "publication_packaging",
                "definition": definition,
            }

        n_exports = len(expected_formats) if expected_status == "ready" else 0
        metrics: dict[str, dict[str, Any]] = {
            "package_validation_accuracy": _metric(
                "package_validation_accuracy",
                1.0 if validation_ok else 0.0,
                1,
                "rate",
                "produced package status matches the reference",
            ),
            "export_success_accuracy": _metric(
                "export_success_accuracy",
                float(len(produced_formats & expected_formats))
                if expected_status == "ready"
                else 0.0,
                n_exports,
                "rate",
                "expected export formats produced",
            ),
            "bibliography_integrity": _metric(
                "bibliography_integrity",
                1.0 if bib_integrity_ok else 0.0,
                1,
                "rate",
                "bibliography deduplicated and covers every cited identity",
            ),
            "placeholder_removal_accuracy": _metric(
                "placeholder_removal_accuracy",
                1.0 if placeholder_ok else 0.0,
                1,
                "rate",
                "no [CITE:*] placeholders left in the rendered body",
            ),
            "anonymization_accuracy": _metric(
                "anonymization_accuracy",
                1.0 if anonymization_ok else 0.0,
                1,
                "rate",
                "anonymous review strips author front matter",
            ),
            "blob_persistence_accuracy": _metric(
                "blob_persistence_accuracy",
                1.0 if blob_ok else 0.0,
                1,
                "rate",
                "export content persisted to the blob store",
            ),
            "deterministic_render_accuracy": _metric(
                "deterministic_render_accuracy",
                1.0 if render_ok else 0.0,
                1,
                "rate",
                "re-exporting each format reuses the same record (stable hash)",
            ),
        }

        status = EvaluatorStatus.failed if failures_detail else EvaluatorStatus.passed

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=1.0 if not failures_detail else 0.0,
            value={
                "package_status": produced_status,
                "export_formats": sorted(produced_formats),
                "bibliography_entries": len(bib_entries),
                "metrics": metrics,
                "dimension_scores": {
                    "package_validation_accuracy": float(validation_ok),
                    "export_success_accuracy": (
                        len(produced_formats & expected_formats) / n_exports if n_exports else 1.0
                    ),
                    "bibliography_integrity": float(bib_integrity_ok),
                    "placeholder_removal_accuracy": float(placeholder_ok),
                    "anonymization_accuracy": float(anonymization_ok),
                    "blob_persistence_accuracy": float(blob_ok),
                    "deterministic_render_accuracy": float(render_ok),
                },
            },
            status=status,
            explanation="; ".join(failures_detail)
            if failures_detail
            else "all packaging checks matched",
            evidence_artifact_ids=[
                e.artifact_id
                for e in ctx.produced_artifacts
                if e.artifact_type == "submission_package"
            ],
        )


class PublicationPackagingEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.publication_packaging",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic publication-packaging evaluator (Phase 7A.1)",
            provides=["evaluator.publication_packaging"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.publication_packaging", PublicationPackagingEvaluator())
