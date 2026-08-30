"""evaluator.evidence — deterministic evidence-extraction evaluator
(Phase 6C).

Evaluates produced evidence artifacts (EvidenceItem, FullTextDocument,
EvidenceCorpus, EvidenceExtractionExecution) against semantically-specified
expected statements: category, valid pages, required/optional.

Deterministic checks:
- required-evidence recall (required statements must be extracted)
- statement grounding (an extracted statement must be contained in the
  source document's page text — unsupported evidence is measured and, when
  `expected_unsupported` is 0, fails the case)
- locator correctness (extracted pages within the expected valid pages)
- category correctness
- duplicate evidence (same normalized statement per document)
- documents with required evidence missed
"""

from __future__ import annotations

import json
import re
from typing import Any

from research_harness.contracts.evaluator import EvaluatorContext, envelope_payload_dict
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.schemas.evaluation import (
    EvaluatorCategory,
    EvaluatorResult,
    EvaluatorStatus,
)

_GROUNDING_UNVERIFIABLE = "statement grounding not verified (no blob store)"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().strip())


def _contained(haystack: str, needle: str) -> bool:
    return bool(needle) and (_norm(needle) in _norm(haystack))


class EvidenceEvaluator:
    evaluator_id = "evaluator.evidence"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        corpora = [e for e in ctx.produced_artifacts if e.artifact_type == "evidence_corpus"]
        executions = [
            e for e in ctx.produced_artifacts if e.artifact_type == "evidence_extraction_execution"
        ]
        if not corpora and not executions:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no evidence execution/corpus produced for the case",
            )
        execution = (
            envelope_payload_dict(max(executions, key=lambda e: e.created_at)) if executions else {}
        )
        chunk_failures = int(execution.get("chunks_failed") or 0)

        item_envs = [e for e in ctx.produced_artifacts if e.artifact_type == "evidence_item"]
        items: list[dict[str, Any]] = [envelope_payload_dict(e) for e in item_envs]
        doc_envs = {
            e.artifact_id: e
            for e in ctx.produced_artifacts
            if e.artifact_type == "full_text_document"
        }
        documents_without_evidence = list(
            (
                envelope_payload_dict(max(corpora, key=lambda e: e.created_at)).get(
                    "documents_without_evidence"
                )
                or []
            )
            if corpora
            else []
        )

        # ---- page text per document (via blob store) ------------------
        page_text: dict[str, str] = {}
        grounding_verified = ctx.blob_store is not None
        for doc_id, env in doc_envs.items():
            payload = envelope_payload_dict(env)
            ref = payload.get("text_blob")
            if ref is None or ctx.blob_store is None:
                continue
            try:
                data = json.loads(await ctx.blob_store.get_bytes(ref))
            except Exception:  # noqa: BLE001
                continue
            page_text[doc_id] = " ".join(
                p.get("text", "") for p in data.get("pages", []) if p.get("text")
            )

        # ---- match items to expected statements -----------------------
        reference = ctx.case.reference or {}
        expected_statements: dict[str, dict[str, Any]] = dict(
            reference.get("expected_statements") or {}
        )
        expected_unsupported = int(reference.get("expected_unsupported") or 0)
        expected_chunk_failures = int(reference.get("expected_chunk_failures") or 0)

        matched_item_ids: set[str] = set()
        required_matched = 0
        required_total = len([s for s in expected_statements.values() if s.get("required", True)])
        matched_by_statement: dict[str, dict[str, Any]] = {}
        unsupported_ids: list[str] = []
        category_mismatches: list[str] = []
        locator_mismatches: list[str] = []

        for statement, spec in expected_statements.items():
            for env, item in zip(item_envs, items, strict=True):
                if env.artifact_id in matched_item_ids:
                    continue
                if not (
                    _contained(item.get("statement") or "", statement)
                    or _contained(statement, item.get("statement") or "")
                ):
                    continue
                matched_item_ids.add(env.artifact_id)
                matched_by_statement[env.artifact_id] = item
                ok = True
                if (item.get("category") or "").lower() != spec.get("category", "").lower():
                    category_mismatches.append(
                        f"{statement[:40]}: expected category {spec.get('category')!r}, "
                        f"produced {item.get('category')!r}"
                    )
                    ok = False
                valid_pages = {int(p) for p in (spec.get("valid_pages") or [])}
                produced_pages = set(item.get("locator", {}).get("pages") or [])
                if valid_pages and not produced_pages <= valid_pages:
                    locator_mismatches.append(
                        f"{statement[:40]}: pages {sorted(produced_pages)} not within "
                        f"valid pages {sorted(valid_pages)}"
                    )
                    ok = False
                if spec.get("required", True) and ok:
                    required_matched += 1
                break

        # ---- unsupported evidence (statement not in source text) ------
        if grounding_verified:
            for env, item in zip(item_envs, items, strict=True):
                if env.artifact_id in matched_item_ids:
                    continue
                text = page_text.get(item.get("source_artifact_id") or "", "")
                if not text or not _contained(text, item.get("statement") or ""):
                    unsupported_ids.append(env.artifact_id)

        # ---- duplicate evidence per document --------------------------
        seen: dict[tuple[str, str], int] = {}
        duplicate_ids: list[str] = []
        for env, item in zip(item_envs, items, strict=True):
            key = (item.get("source_artifact_id") or "", _norm(item.get("statement") or ""))
            if key in seen:
                duplicate_ids.append(env.artifact_id)
            else:
                seen[key] = 1

        # ---- documents without required evidence ----------------------
        # attribution is semantic: each missed required statement is a
        # document-level gap
        documents_missed = required_total - required_matched

        # ---- expected documents without evidence (title- or id-keyed) --------
        expected_without = list(reference.get("expected_documents_without_evidence") or [])
        # map paper title -> doc id via produced identity chains
        identity_members: dict[str, list[str]] = {}
        for env in ctx.produced_artifacts:
            if env.artifact_type != "paper_identity":
                continue
            identity_members[env.artifact_id] = list(
                envelope_payload_dict(env).get("member_paper_artifact_ids") or []
            )
        doc_by_title: dict[str, str] = {}
        for doc_id, env in doc_envs.items():
            payload = envelope_payload_dict(env)
            identity_id = payload.get("paper_identity_id")
            for member in identity_members.get(identity_id or "", []):
                rec_env = next(
                    (
                        e
                        for e in ctx.produced_artifacts
                        if e.artifact_type == "paper_record" and e.artifact_id == member
                    ),
                    None,
                )
                if rec_env is None:
                    continue
                doc_by_title[_norm(envelope_payload_dict(rec_env).get("title") or "")] = doc_id
        expected_without_ids: set[str] = set()
        for t in expected_without:
            resolved = t if t in doc_envs else doc_by_title.get(_norm(t), t)
            expected_without_ids.add(str(resolved))
        without_mismatch: list[str] = []
        if "expected_documents_without_evidence" in reference:
            without_produced: set[str] = {str(d) for d in documents_without_evidence}
            without_mismatch = sorted(
                (expected_without_ids - without_produced)
                | (without_produced - expected_without_ids)
            )

        # ---- metrics --------------------------------------------------
        total_items = len(items)
        matched = len(matched_item_ids)
        precision = matched / total_items if total_items else 0.0
        recall = required_matched / required_total if required_total else 1.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        category_accuracy = (matched - len(category_mismatches)) / matched if matched else 1.0
        locator_accuracy = (matched - len(locator_mismatches)) / matched if matched else 1.0
        unsupported_rate = len(unsupported_ids) / total_items if total_items else 0.0
        duplicate_rate = len(duplicate_ids) / total_items if total_items else 0.0
        documents_missed = documents_missed

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "evidence",
                "definition": definition,
            }

        f1_relevant = 1 if (total_items or required_total) else 0
        metrics: dict[str, dict[str, Any]] = {
            "evidence_precision": _metric(
                "evidence_precision",
                float(matched),
                total_items,
                "rate",
                "extracted evidence matching an expected statement",
            ),
            "evidence_recall": _metric(
                "evidence_recall",
                float(required_matched),
                required_total,
                "rate",
                "required expected statements extracted",
            ),
            "evidence_f1": _metric(
                "evidence_f1",
                f1,
                f1_relevant,
                "rate",
                "harmonic mean of evidence precision and recall "
                "(mean per-case F1 over cases with items or expectations)",
            ),
            "category_accuracy": _metric(
                "category_accuracy",
                float(matched - len(category_mismatches)),
                matched,
                "rate",
                "matched evidence with the expected category",
            ),
            "locator_accuracy": _metric(
                "locator_accuracy",
                float(matched - len(locator_mismatches)),
                matched,
                "rate",
                "matched evidence with pages within the expected valid pages",
            ),
            "required_evidence_recall": _metric(
                "required_evidence_recall",
                float(required_matched),
                required_total,
                "rate",
                "required expected statements extracted",
            ),
            "unsupported_evidence_rate": _metric(
                "unsupported_evidence_rate",
                float(len(unsupported_ids)),
                total_items,
                "rate",
                "extracted statements not contained in the source page text",
            ),
            "duplicate_evidence_rate": _metric(
                "duplicate_evidence_rate",
                float(len(duplicate_ids)),
                total_items,
                "rate",
                "duplicate normalized statements per document",
            ),
            "documents_with_required_evidence_missed": _metric(
                "documents_with_required_evidence_missed",
                float(documents_missed),
                1,
                "quantity",
                "required statements that were not extracted",
            ),
            "chunk_failure_count": _metric(
                "chunk_failure_count",
                float(chunk_failures),
                1,
                "quantity",
                "chunks that failed extraction",
            ),
        }

        failures_detail: list[str] = []
        if required_matched != required_total:
            failures_detail.append(
                f"REQUIRED EVIDENCE MISSED: {required_total - required_matched}/{required_total}"
            )
        if grounding_verified and len(unsupported_ids) != expected_unsupported:
            failures_detail.append(
                f"UNSUPPORTED EVIDENCE: found {len(unsupported_ids)}, "
                f"expected {expected_unsupported}"
            )
        if chunk_failures != expected_chunk_failures:
            failures_detail.append(
                f"CHUNK FAILURES: {chunk_failures}, expected {expected_chunk_failures}"
            )
        if category_mismatches:
            failures_detail.append("CATEGORY MISMATCHES: " + "; ".join(category_mismatches))
        if locator_mismatches:
            failures_detail.append("LOCATOR MISMATCHES: " + "; ".join(locator_mismatches))
        if duplicate_ids:
            failures_detail.append(f"DUPLICATE EVIDENCE: {len(duplicate_ids)}")
        if documents_missed:
            failures_detail.append(f"DOCUMENTS WITH REQUIRED EVIDENCE MISSED: {documents_missed}")
        if without_mismatch:
            failures_detail.append(f"DOCUMENTS WITHOUT EVIDENCE MISMATCH: {without_mismatch}")
        grounding_unverifiable = not grounding_verified and bool(total_items)
        if grounding_unverifiable:
            failures_detail.append(_GROUNDING_UNVERIFIABLE)

        # A missing blob store is a deployment fact, not a benchmark failure:
        # blob_store is an *optional* harness dependency, so a legitimate
        # deployment without one must not turn every case into a failure. If it
        # is the only thing wrong, decline to judge rather than reporting a
        # failure the operator cannot act on. Every other dimension (recall,
        # locator, category, duplicates) is still evaluated either way, and a
        # skipped case is never scored as a pass.
        if failures_detail == [_GROUNDING_UNVERIFIABLE]:
            status = EvaluatorStatus.skipped
        elif failures_detail:
            status = EvaluatorStatus.failed
        else:
            status = EvaluatorStatus.passed

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=recall if required_total else None,
            value={
                "evidence_items": total_items,
                "matched_evidence": matched,
                "unsupported_evidence_ids": unsupported_ids,
                "duplicate_evidence_ids": duplicate_ids,
                "documents_with_required_missed": documents_missed,
                "documents_without_evidence": documents_without_evidence,
                "chunk_failures": chunk_failures,
                "grounding_verified": grounding_verified,
                "metrics": metrics,
                "dimension_scores": {
                    "evidence_precision": precision,
                    "evidence_recall": recall,
                    "evidence_f1": f1,
                    "category_accuracy": category_accuracy,
                    "locator_accuracy": locator_accuracy,
                    "required_evidence_recall": recall,
                    "unsupported_evidence_rate": unsupported_rate,
                    "duplicate_evidence_rate": duplicate_rate,
                    "documents_with_required_evidence_missed": float(documents_missed),
                    "chunk_failure_count": float(chunk_failures),
                },
            },
            status=status,
            explanation="; ".join(failures_detail)
            if failures_detail
            else "all evidence checks matched",
            evidence_artifact_ids=[e.artifact_id for e in item_envs],
        )


class EvidenceEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.evidence",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic evidence-extraction evaluator (Phase 6C)",
            provides=["evaluator.evidence"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.evidence", EvidenceEvaluator())
