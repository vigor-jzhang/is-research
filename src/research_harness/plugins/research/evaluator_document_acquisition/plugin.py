"""evaluator.document_acquisition — deterministic acquisition/extraction
evaluator (Phase 7A).

Evaluates produced Phase 2E artifacts (DocumentAcquisition, FullTextDocument,
DocumentAcquisitionExecution, FullTextCorpus) against known-answer references
keyed by paper index (case-scoped `paper-{i}` ids):

- acquisition success: downloaded/imported vs not_available / invalid_content /
  too_large / access_restricted / failed classification
- text-extraction success: downloaded papers that reached `extracted` text
- failure classification: per-paper produced status matches the reference
- fallback usage: the successful acquisition came from a non-first location
- duplicate-blob reuse: same location+bytes reuse the same acquisition id
- corpus availability: FullTextCorpus available / unavailable / restricted
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

_STATUS_PRECEDENCE = {
    "downloaded": 5,
    "imported": 5,
    "access_restricted": 4,
    "invalid_content": 3,
    "too_large": 2,
    "not_available": 1,
    "failed": 0,
}


def _effective_status(statuses: list[str]) -> str:
    if not statuses:
        return "failed"
    return max(statuses, key=lambda s: _STATUS_PRECEDENCE.get(s, 0))


class DocumentAcquisitionEvaluator:
    evaluator_id = "evaluator.document_acquisition"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        identities = sorted(
            (e for e in ctx.produced_artifacts if e.artifact_type == "paper_identity"),
            key=lambda e: e.created_at,
        )
        acquisitions = [
            envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "document_acquisition"
        ]
        if not identities and not acquisitions:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no acquisition artifacts produced for the case",
            )
        corpora = [
            envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "full_text_corpus"
        ]

        # case-scoped reference keys `paper-{i}` -> produced identity by order
        identity_by_index: dict[int, str] = {i: e.artifact_id for i, e in enumerate(identities)}

        def _paper_key(key: str) -> int:
            m = re.search(r"paper-(\d+)", str(key))
            return int(m.group(1)) if m else -1

        reference = ctx.case.reference or {}
        expected_statuses = dict(reference.get("expected_statuses") or {})
        expected_text_status = dict(reference.get("expected_text_status") or {})
        expected_available = list(reference.get("expected_corpus_available") or [])
        expected_unavailable = list(reference.get("expected_corpus_unavailable") or [])
        expected_restricted = list(reference.get("expected_corpus_restricted") or [])
        expected_failed = list(reference.get("expected_corpus_failed") or [])
        expected_fallback = bool(reference.get("expected_fallback_used") or False)
        expected_duplicate = bool(reference.get("expected_duplicate_reuse") or False)

        acq_by_identity: dict[str, list[dict[str, Any]]] = {}
        acq_by_id: dict[str, dict[str, Any]] = {}
        for env in ctx.produced_artifacts:
            if env.artifact_type != "document_acquisition":
                continue
            payload = envelope_payload_dict(env)
            acq_by_id[env.artifact_id] = payload
            acq_by_identity.setdefault(str(payload.get("paper_identity_id") or ""), []).append(
                payload
            )
        loc_by_identity: dict[str, list[str]] = {}
        for env in ctx.produced_artifacts:
            if env.artifact_type != "document_location":
                continue
            payload = envelope_payload_dict(env)
            loc_by_identity.setdefault(str(payload.get("paper_identity_id") or ""), []).append(
                env.artifact_id
            )
        doc_by_acq: dict[str, dict[str, Any]] = {}
        doc_by_identity: dict[str, str] = {}
        for env in ctx.produced_artifacts:
            if env.artifact_type != "full_text_document":
                continue
            payload = envelope_payload_dict(env)
            doc_by_acq[str(payload.get("document_acquisition_id") or "")] = payload
            doc_by_identity.setdefault(str(payload.get("paper_identity_id") or ""), env.artifact_id)

        failures_detail: list[str] = []
        status_ok = 0
        status_total = len(expected_statuses)
        text_ok = 0
        text_total = 0
        success_ok = 0
        success_total = len(expected_statuses)
        fallback_ok = 0
        duplicate_ok = 0
        corpus_ok = 0
        corpus_total = 0

        for key, expected_status in expected_statuses.items():
            idx = _paper_key(key)
            identity_id = identity_by_index.get(idx)
            if identity_id is None:
                failures_detail.append(f"no produced identity for {key!r}")
                continue
            acqs = acq_by_identity.get(identity_id, [])
            produced_status = _effective_status([str(a.get("status") or "") for a in acqs])
            if produced_status != expected_status:
                failures_detail.append(
                    f"ACQUISITION STATUS: {key} expected {expected_status!r}, "
                    f"produced {produced_status!r} ({[str(a.get('status')) for a in acqs]})"
                )
            else:
                status_ok += 1
            if produced_status in ("downloaded", "imported"):
                success_ok += 1

            # text status for the successful acquisition
            if key in expected_text_status:
                text_total += 1
                successful = next(
                    (a for a in acqs if str(a.get("status") or "") in ("downloaded", "imported")),
                    None,
                )
                successful_id = next((aid for aid, a in acq_by_id.items() if a is successful), None)
                produced_text = (
                    doc_by_acq.get(str(successful_id), {}).get("text_status")
                    if successful_id
                    else None
                )
                if produced_text == expected_text_status[key]:
                    text_ok += 1
                else:
                    failures_detail.append(
                        f"TEXT STATUS: {key} expected {expected_text_status[key]!r}, "
                        f"produced {produced_text!r}"
                    )

            # fallback usage: successful acquisition used a non-first location
            if key in {k for k in expected_statuses if expected_fallback and key == k} and (
                produced_status in ("downloaded", "imported")
            ):
                locations = loc_by_identity.get(identity_id, [])
                successful = next(
                    (a for a in acqs if str(a.get("status") or "") in ("downloaded", "imported")),
                    None,
                )
                if locations and successful:
                    if str(successful.get("document_location_id") or "") != locations[0]:
                        fallback_ok += 1
                    else:
                        failures_detail.append(
                            f"FALLBACK: {key} succeeded on the first location only"
                        )
                else:
                    fallback_ok += 1

        # duplicate-blob reuse: fetching the same location+bytes reuses the
        # same acquisition id (no duplicate blob artifact)
        if expected_duplicate:
            downloaded = [
                a for a in acquisitions if str(a.get("status") or "") in ("downloaded", "imported")
            ]
            seen: dict[tuple[str, str], list[str]] = {}
            for a in downloaded:
                key = (
                    str(a.get("document_location_id") or ""),
                    str(a.get("sha256") or ""),
                )
                seen.setdefault(key, []).append(str(a.get("id") or ""))
            dup_groups = [v for v in seen.values() if len(v) > 1]
            if dup_groups:
                failures_detail.append(
                    f"DUPLICATE BLOB: same location+sha acquired {len(dup_groups)} times"
                )
            else:
                duplicate_ok = 1

        # corpus availability
        if corpora:
            corpus = max(corpora, key=lambda c: c.get("created_at") or 0)
            produced_available = {str(d) for d in (corpus.get("available_document_ids") or [])}
            produced_unavailable = {str(d) for d in (corpus.get("unavailable_identity_ids") or [])}
            produced_restricted = {str(d) for d in (corpus.get("restricted_identity_ids") or [])}
            produced_failed = {str(d) for d in (corpus.get("failed_identity_ids") or [])}

            # available_document_ids are FullTextDocument ids; unavailable/
            # restricted are PaperIdentity ids
            def _resolve_identity(key: str) -> str:
                return identity_by_index.get(_paper_key(key), str(key))

            exp_available = {
                doc_by_identity.get(_resolve_identity(k), _resolve_identity(k))
                for k in expected_available
            }
            exp_unavailable = {_resolve_identity(k) for k in expected_unavailable}
            exp_restricted = {_resolve_identity(k) for k in expected_restricted}
            exp_failed = {_resolve_identity(k) for k in expected_failed}
            corpus_total = (
                len(exp_available) + len(exp_unavailable) + len(exp_restricted) + len(exp_failed)
            )
            mismatches = (
                (exp_available - produced_available)
                | (produced_available - exp_available)
                | (exp_unavailable - produced_unavailable)
                | (produced_unavailable - exp_unavailable)
                | (exp_restricted - produced_restricted)
                | (produced_restricted - exp_restricted)
                | (exp_failed - produced_failed)
                | (produced_failed - exp_failed)
            )
            if mismatches:
                failures_detail.append(f"CORPUS AVAILABILITY MISMATCH: {sorted(mismatches)[:5]}")
            else:
                corpus_ok = corpus_total
        elif (
            expected_available
            or expected_unavailable
            or expected_restricted
            or expected_failed
        ):
            # No FullTextCorpus was produced at all, so every corpus
            # expectation went unchecked. Skipping silently would let a run
            # whose corpus assembly failed score as though the corpus had been
            # verified (corpus_ok/corpus_total both stay 0).
            failures_detail.append(
                "CORPUS MISSING: the case declares corpus classifications but no "
                "full_text_corpus artifact was produced"
            )

        # ---- metrics -------------------------------------------------------

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "document_acquisition",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "acquisition_success_rate": _metric(
                "acquisition_success_rate",
                float(success_ok),
                success_total,
                "rate",
                "papers acquired as downloaded/imported",
            ),
            "extraction_success_rate": _metric(
                "extraction_success_rate",
                float(text_ok),
                text_total,
                "rate",
                "downloaded papers whose text reached the expected status",
            ),
            "failure_classification_accuracy": _metric(
                "failure_classification_accuracy",
                float(status_ok),
                status_total,
                "rate",
                "per-paper acquisition status matches the reference",
            ),
            "fallback_usage_accuracy": _metric(
                "fallback_usage_accuracy",
                float(fallback_ok),
                1 if expected_fallback else 0,
                "rate",
                "successful acquisition used a fallback location",
            ),
            "duplicate_blob_reuse_accuracy": _metric(
                "duplicate_blob_reuse_accuracy",
                float(duplicate_ok),
                1 if expected_duplicate else 0,
                "rate",
                "same location+bytes reuse one acquisition (no duplicate blob)",
            ),
            "corpus_availability_accuracy": _metric(
                "corpus_availability_accuracy",
                float(corpus_ok),
                corpus_total,
                "rate",
                "FullTextCorpus available/unavailable/restricted matches the reference",
            ),
        }

        status = EvaluatorStatus.failed if failures_detail else EvaluatorStatus.passed

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=(
                status_ok / status_total if status_total else (1.0 if not failures_detail else None)
            ),
            value={
                "per_paper_status": {
                    key: _effective_status(
                        [
                            str(a.get("status") or "")
                            for a in acq_by_identity.get(
                                identity_by_index.get(_paper_key(key), ""), []
                            )
                        ]
                    )
                    for key in expected_statuses
                },
                "metrics": metrics,
                "dimension_scores": {
                    k: v
                    for k, v in {
                        "acquisition_success_rate": (
                            success_ok / success_total if success_total else None
                        ),
                        "extraction_success_rate": (text_ok / text_total if text_total else None),
                        "failure_classification_accuracy": (
                            status_ok / status_total if status_total else None
                        ),
                        "fallback_usage_accuracy": fallback_ok if expected_fallback else None,
                        "duplicate_blob_reuse_accuracy": (
                            duplicate_ok if expected_duplicate else None
                        ),
                        "corpus_availability_accuracy": (
                            corpus_ok / corpus_total if corpus_total else None
                        ),
                    }.items()
                    if v is not None
                },
            },
            status=status,
            explanation="; ".join(failures_detail)
            if failures_detail
            else "all acquisition checks matched",
            evidence_artifact_ids=[
                e.artifact_id
                for e in ctx.produced_artifacts
                if e.artifact_type == "document_acquisition"
            ],
        )


class DocumentAcquisitionEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.document_acquisition",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic document-acquisition evaluator (Phase 7A)",
            provides=["evaluator.document_acquisition"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.document_acquisition", DocumentAcquisitionEvaluator())
