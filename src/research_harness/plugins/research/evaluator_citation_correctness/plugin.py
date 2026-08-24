"""evaluator.citation_correctness — deterministic citation evaluator
(Phase 6A/6B).

Modes:
- placeholder_check (default): every `[CITE:key]` marker in a produced
  formatted manuscript resolves in its bibliography; skips when no
  citation-bearing manuscript is produced.
- manuscript_citation: full Phase 6B benchmark mode over the real Phase 4C
  formatter output — resolution accuracy, bibliography coverage and
  deduplication, citation-map accuracy, leftover placeholders, unsupported
  entries, invented bibliographic fields, inline rendering, and anonymous
  review. Any invented bibliographic field is a deterministic failure.
"""

from __future__ import annotations

import re
from typing import Any

from research_harness.contracts.evaluator import (
    EvaluatorContext,
    EvaluatorError,
    envelope_payload_dict,
)
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.schemas.evaluation import (
    EvaluatorCategory,
    EvaluatorResult,
    EvaluatorStatus,
)
from research_harness.research.schemas.publication import FormattedManuscript

_CITE_MARKER = re.compile(r"\[CITE:([A-Za-z0-9_.:-]+)\]")


class CitationCorrectnessEvaluator:
    evaluator_id = "evaluator.citation_correctness"
    evaluator_version = "0.2.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        mode = ctx.config.get("citation_mode", "placeholder_check")
        if mode == "manuscript_citation":
            return self._manuscript_citation(ctx)
        if mode == "placeholder_check":
            return self._placeholder_check(ctx)
        raise EvaluatorError(f"unknown citation_mode {mode!r}")

    # ------------------------------------------------------------------
    # placeholder_check mode (Phase 6A)
    # ------------------------------------------------------------------

    def _placeholder_check(self, ctx: EvaluatorContext) -> EvaluatorResult:
        manuscript_envs = [
            e for e in ctx.produced_artifacts if e.artifact_type == "formatted_manuscript"
        ]
        if not manuscript_envs:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.skipped,
                explanation="no formatted manuscript produced; nothing to check",
            )
        env = max(manuscript_envs, key=lambda e: e.created_at)
        manuscript = env.parse_payload(FormattedManuscript)

        entry_keys: set[str] = set()
        if manuscript.bibliography is not None:
            for entry in manuscript.bibliography.entries:
                entry_keys.update(entry.citation_ids)

        bodies = [manuscript.front_matter.title, manuscript.front_matter.abstract]
        bodies.extend(s.body for s in manuscript.sections)

        found: list[str] = []
        for body in bodies:
            found.extend(_CITE_MARKER.findall(body))
        found = list(dict.fromkeys(found))
        unresolved = [k for k in found if k not in entry_keys]

        resolved = len([k for k in found if k not in unresolved])
        total = len(found)
        status = (
            EvaluatorStatus.passed
            if total and not unresolved
            else (EvaluatorStatus.failed if unresolved else EvaluatorStatus.passed)
        )
        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=(resolved / total) if total else None,
            value={
                "citations_found": total,
                "citations_resolved": resolved,
                "citations_unresolved": len(unresolved),
                "unresolved_keys": unresolved,
                "bibliography_entries": len(entry_keys),
            },
            status=status,
            explanation=(
                f"{resolved}/{total} citation markers resolved"
                if total
                else "no citation markers found"
            ),
            evidence_artifact_ids=[env.artifact_id],
        )

    # ------------------------------------------------------------------
    # manuscript_citation mode (Phase 6B benchmark)
    # ------------------------------------------------------------------

    def _manuscript_citation(self, ctx: EvaluatorContext) -> EvaluatorResult:
        reference = ctx.case.reference or {}
        manuscript_envs = [
            e for e in ctx.produced_artifacts if e.artifact_type == "formatted_manuscript"
        ]

        if not manuscript_envs:
            if reference.get("expected_formatter_failure"):
                return EvaluatorResult(
                    case_id=ctx.case.id,
                    evaluator_id=self.evaluator_id,
                    evaluator_version=self.evaluator_version,
                    category=EvaluatorCategory.deterministic,
                    score=None,
                    value={
                        "formatter_failed": True,
                        "metrics": {
                            "formatter_failure_ok": {
                                "value": 1.0,
                                "count": 1,
                                "kind": "rate",
                                "dimension": "citation",
                                "definition": (
                                    "formatter refused to format (e.g. missing "
                                    "PaperIdentity) instead of fabricating citations"
                                ),
                            }
                        },
                        "dimension_scores": {"formatter_failure": 1.0},
                    },
                    status=EvaluatorStatus.passed,
                    explanation=(
                        "formatter refused to format (e.g. missing PaperIdentity); "
                        "no fabricated citations"
                    ),
                )
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={"formatter_failed": True},
                status=EvaluatorStatus.failed,
                explanation="no formatted manuscript produced for the case",
            )

        ms_env = max(manuscript_envs, key=lambda e: e.created_at)
        manuscript = ms_env.parse_payload(FormattedManuscript)
        bibliography = manuscript.bibliography
        entries = list(bibliography.entries) if bibliography is not None else []

        bodies = [manuscript.front_matter.title, manuscript.front_matter.abstract]
        bodies.extend(s.body for s in manuscript.sections)

        expected_map = reference.get("expected_identity_map") or {}
        expected_grouping = reference.get("expected_bibliography_citation_ids") or {}
        expected_inline = reference.get("expected_inline") or {}
        expected_leftover = list(reference.get("expected_leftover_placeholders") or [])

        # 1. leftover placeholders: any [CITE:*] still present after rendering
        leftovers = sorted({m for body in bodies for m in _CITE_MARKER.findall(body)})
        extra_leftovers = sorted(set(leftovers) - set(expected_leftover))

        # 2. citation-map accuracy (citation id -> paper identity)
        map_matches = sum(
            1 for cid, iid in expected_map.items() if manuscript.citation_map.get(cid) == iid
        )
        map_total = len(expected_map)

        # 3. resolution: expected citations must have a bibliography entry
        entry_citation_ids = {cid for e in entries for cid in e.citation_ids}
        unresolved = [cid for cid in expected_map if cid not in entry_citation_ids]
        resolved = sum(1 for cid in expected_map if cid in entry_citation_ids)
        citation_total = len(expected_map)

        # 4. bibliography deduplication (one entry per identity, grouped ids)
        entries_by_identity = {e.paper_identity_id: e for e in entries}
        grouping = {iid: sorted(e.citation_ids) for iid, e in entries_by_identity.items()}
        grouping_matches = sum(
            1 for iid, ids in expected_grouping.items() if grouping.get(iid) == sorted(ids)
        )
        grouping_total = len(expected_grouping)

        # 5. coverage: every entry must correspond to a citation of the manuscript
        unsupported = [
            e.paper_identity_id
            for e in entries
            if not (set(e.citation_ids) & set(manuscript.citation_map))
        ]
        supported = len(entries) - len(unsupported)
        entries_total = len(entries)

        # 6. invented bibliographic fields vs source PaperRecords
        invented: list[str] = []
        records = {
            e.artifact_id: envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "paper_record"
        }
        identities = {
            e.artifact_id: envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "paper_identity"
        }
        for entry in entries:
            identity = identities.get(entry.paper_identity_id)
            if identity is None:
                continue
            member_ids = list(identity.get("member_paper_artifact_ids") or [])
            record = next((records[pid] for pid in member_ids if pid in records), None)
            if record is None:
                continue
            if (record.get("title") or "") != (entry.title or ""):
                invented.append(f"{entry.paper_identity_id}:title")
            if (record.get("year") or None) != (entry.year or None):
                invented.append(f"{entry.paper_identity_id}:year")
            if (record.get("venue") or None) != (entry.venue or None):
                invented.append(f"{entry.paper_identity_id}:venue")
            if (record.get("doi") or None) != (entry.doi or None):
                invented.append(f"{entry.paper_identity_id}:doi")
            record_names = [
                a.get("name") if isinstance(a, dict) else str(a)
                for a in (record.get("authors") or [])
            ]
            if set(record_names) != set(entry.authors or []):
                invented.append(f"{entry.paper_identity_id}:authors")

        # 7. inline rendering spot-checks
        inline_matches = sum(
            1 for expected in expected_inline.values() if any(expected in body for body in bodies)
        )
        inline_total = len(expected_inline)

        # 8. anonymous review
        anonymous_ok = None
        if reference.get("expected_anonymous") is not None:
            anonymous_ok = (
                1.0
                if (
                    manuscript.anonymous_review
                    and manuscript.front_matter.authors == []
                    and manuscript.front_matter.affiliations == []
                )
                else 0.0
            )

        failures: list[str] = []
        if extra_leftovers:
            failures.append(f"leftover placeholders: {extra_leftovers}")
        if unresolved:
            failures.append(f"unresolved citations: {unresolved}")
        if map_matches != map_total:
            failures.append(f"citation_map mismatches: {map_matches}/{map_total}")
        if grouping_matches != grouping_total:
            failures.append(f"bibliography dedup mismatches: {grouping_matches}/{grouping_total}")
        if unsupported:
            failures.append(f"unsupported bibliography entries: {unsupported}")
        if invented:
            failures.append(f"INVENTED BIBLIOGRAPHIC FIELDS: {invented}")
        if inline_matches != inline_total:
            failures.append(f"inline rendering mismatches: {inline_matches}/{inline_total}")
        if anonymous_ok == 0.0:
            failures.append("anonymous review expected but front matter reveals authors")
        status = EvaluatorStatus.failed if failures else EvaluatorStatus.passed

        metrics: dict[str, dict[str, Any]] = {}
        if citation_total:
            metrics["citation_resolution_accuracy"] = {
                "value": float(resolved),
                "count": citation_total,
                "kind": "rate",
                "dimension": "citation",
                "definition": (
                    "expected citations that received a bibliography entry, pooled over cases"
                ),
            }
            metrics["unresolved_citation_count"] = {
                "value": float(len(unresolved)),
                "count": 1,
                "kind": "quantity",
                "dimension": "citation",
                "definition": "expected citations missing from the bibliography",
            }
            metrics["citation_map_accuracy"] = {
                "value": float(map_matches),
                "count": map_total,
                "kind": "rate",
                "dimension": "citation",
                "definition": ("citation_map entries that match the expected identity mapping"),
            }
        if entries_total:
            metrics["bibliography_coverage"] = {
                "value": float(supported),
                "count": entries_total,
                "kind": "rate",
                "dimension": "citation",
                "definition": ("bibliography entries with a supporting citation in the text"),
            }
            metrics["unsupported_bibliography_entry_count"] = {
                "value": float(len(unsupported)),
                "count": 1,
                "kind": "quantity",
                "dimension": "citation",
                "definition": "bibliography entries without a supporting citation",
            }
        if grouping_total:
            metrics["bibliography_deduplication_accuracy"] = {
                "value": float(grouping_matches),
                "count": grouping_total,
                "kind": "rate",
                "dimension": "citation",
                "definition": ("expected citation-id groups per paper identity, pooled over cases"),
            }
        metrics["leftover_placeholder_count"] = {
            "value": float(len(leftovers)),
            "count": 1,
            "kind": "quantity",
            "dimension": "citation",
            "definition": "[CITE:*] placeholders left unreplaced in rendered text",
        }
        metrics["invented_bibliographic_field_count"] = {
            "value": float(len(invented)),
            "count": 1,
            "kind": "quantity",
            "dimension": "citation",
            "definition": "bibliography fields not present in the source PaperRecord",
        }
        if inline_total:
            metrics["inline_citation_accuracy"] = {
                "value": float(inline_matches),
                "count": inline_total,
                "kind": "rate",
                "dimension": "citation",
                "definition": "expected inline renderings found in the text",
            }
        if anonymous_ok is not None:
            metrics["anonymous_review_ok"] = {
                "value": anonymous_ok,
                "count": 1,
                "kind": "rate",
                "dimension": "citation",
                "definition": "anonymous profile hides author front matter",
            }

        dimension_scores = {
            "citation_resolution": resolved / citation_total if citation_total else None,
            "citation_map": map_matches / map_total if map_total else None,
            "bibliography_dedup": (grouping_matches / grouping_total if grouping_total else None),
            "bibliography_coverage": supported / entries_total if entries_total else None,
            "leftover": 0.0 if leftovers else 1.0,
            "invented": 0.0 if invented else 1.0,
        }
        dimension_scores = {k: v for k, v in dimension_scores.items() if v is not None}

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=(resolved / citation_total) if citation_total else None,
            value={
                "citation_map": manuscript.citation_map,
                "leftover_placeholders": leftovers,
                "unresolved_citations": unresolved,
                "bibliography_entries": len(entries),
                "entries_by_identity": grouping,
                "unsupported_entries": unsupported,
                "invented_fields": invented,
                "inline_matches": inline_matches,
                "inline_total": inline_total,
                "anonymous_ok": anonymous_ok,
                "formatter_failed": False,
                "metrics": metrics,
                "dimension_scores": dimension_scores,
            },
            status=status,
            explanation="; ".join(failures) if failures else "all citation checks matched",
            evidence_artifact_ids=[ms_env.artifact_id],
        )


class CitationCorrectnessEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.citation_correctness",
            version="0.2.0",
            plugin_type="evaluator",
            description="Deterministic citation-resolution evaluator (Phase 6A/6B)",
            provides=["evaluator.citation_correctness"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.citation_correctness", CitationCorrectnessEvaluator())
