"""evaluator.identity_resolution — deterministic ingestion + identity
resolution evaluator (Phase 7A.1).

Evaluates produced Phase 2B/2C artifacts (PaperRecord, ProviderRecordSnapshot,
PaperIdentity, LiteratureSearchRecord) against known-answer identity
groupings. False semantic merges (papers that should stay separate collapsed
into one identity) and false splits deterministically fail.
"""

from __future__ import annotations

from typing import Any

from research_harness.contracts.evaluator import EvaluatorContext, envelope_payload_dict
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.schemas.evaluation import (
    EvaluatorCategory,
    EvaluatorResult,
    EvaluatorStatus,
)


class IdentityResolutionEvaluator:
    evaluator_id = "evaluator.identity_resolution"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        reports = [
            e for e in ctx.produced_artifacts if e.artifact_type == "ingestion_identity_report"
        ]
        identity_envs = [e for e in ctx.produced_artifacts if e.artifact_type == "paper_identity"]
        identities = [envelope_payload_dict(e) for e in identity_envs]
        if not reports:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no ingestion_identity_report produced for the case",
            )
        report = envelope_payload_dict(max(reports, key=lambda e: e.created_at))
        paper_ids: dict[str, str] = dict(report.get("paper_ids") or {})
        reverse: dict[str, str] = {v: k for k, v in paper_ids.items()}
        superseded_run = [str(i) for i in (report.get("superseded_identity_ids") or [])]
        failed_providers = [str(p) for p in (report.get("failed_providers") or [])]

        # produced groups keyed by frozenset of case-scoped member keys
        produced_groups: list[set[str]] = []
        identity_by_members: dict[tuple[str, ...], str] = {}
        for env, ident in zip(identity_envs, identities, strict=True):
            members = [
                reverse.get(str(m), str(m)) for m in (ident.get("member_paper_artifact_ids") or [])
            ]
            members = sorted({m for m in members if m.startswith("ing-paper-")})
            if not members:
                continue
            produced_groups.append(set(members))
            identity_by_members[tuple(members)] = env.artifact_id

        # superseded produced identities (source of a supersedes edge)
        superseded_ids: set[str] = set()
        for aid, links in ctx.provenance.items():
            for link in links:
                if getattr(link, "relation", None) == "supersedes":
                    superseded_ids.add(str(getattr(link, "source_artifact_id", aid)))

        reference = ctx.case.reference or {}
        expected_groups = list(reference.get("expected_identities") or [])
        expected_superseded = [str(s) for s in (reference.get("expected_superseded") or [])]
        expected_failed = [str(s) for s in (reference.get("expected_failed_providers") or [])]

        failures_detail: list[str] = []
        canonical_ok = 0
        canonical_total = len(expected_groups)
        merged_ok = 0
        merged_total = sum(1 for g in expected_groups if len(g.get("members") or []) > 1)
        normalized_ok = 0
        normalized_total = sum(1 for g in expected_groups if g.get("normalized"))
        false_merge = 0
        false_split = 0

        # member ownership: paper -> produced group
        paper_to_group: dict[str, set[str]] = {}
        for group in produced_groups:
            for m in group:
                paper_to_group[m] = group

        for group in expected_groups:
            members = {str(m) for m in (group.get("members") or [])}
            if not members:
                continue
            # find the produced group(s) covering these papers
            covering = {frozenset(g) for g in produced_groups if members & g}
            exact = frozenset(members) in covering
            if exact:
                canonical_ok += 1
            else:
                failures_detail.append(
                    f"IDENTITY GROUP MISMATCH: expected {sorted(members)} not produced exactly"
                )
            if len(members) > 1:
                present = [m for m in members if m in paper_to_group]
                produced_covering = {frozenset(paper_to_group[m]) for m in present}
                if len(produced_covering) == 1 and frozenset(members) in produced_covering:
                    merged_ok += 1
                else:
                    false_split += 1
                    failures_detail.append(
                        f"FALSE SPLIT: expected one identity for {sorted(members)}"
                    )
                if group.get("normalized"):
                    if len(produced_covering) == 1:
                        normalized_ok += 1
                    else:
                        failures_detail.append(
                            f"DOI NORMALIZATION: variant DOIs for {sorted(members)} not merged"
                        )
            else:
                only = next(iter(members))
                owner = paper_to_group.get(only)
                if owner is not None and len(owner) > 1:
                    false_merge += 1
                    failures_detail.append(
                        f"FALSE MERGE: paper {only} merged with {sorted(owner - {only})} "
                        "(no strong identifier justifies the merge)"
                    )

        # supersession
        supersession_ok = 0
        supersession_total = len(expected_superseded)
        for key in expected_superseded:
            matched = False
            for members_key, ident_id in identity_by_members.items():
                if key in members_key and len(members_key) == 1:
                    if ident_id in superseded_ids or ident_id in superseded_run:
                        matched = True
                        break
            if matched:
                supersession_ok += 1
            else:
                failures_detail.append(
                    f"SUPERSESSION MISSING: identity containing {key} not superseded"
                )

        # partial ingestion
        partial_ok = 0
        partial_total = sum(1 for g in expected_groups for _m in (g.get("members") or []))
        if expected_failed:
            # every expected paper must still be resolved into a produced group
            partial_ok = sum(
                1
                for g in expected_groups
                for m in (g.get("members") or [])
                if str(m) in paper_to_group
            )
        else:
            partial_ok = partial_total

        def _metric(
            metric_id: str, value: float, count: int, kind: str, definition: str
        ) -> dict[str, Any]:
            return {
                "value": value,
                "count": count,
                "kind": kind,
                "dimension": "ingestion_identity",
                "definition": definition,
            }

        metrics: dict[str, dict[str, Any]] = {
            "canonical_mapping_accuracy": _metric(
                "canonical_mapping_accuracy",
                float(canonical_ok),
                canonical_total,
                "rate",
                "expected identity groups produced with exactly the expected members",
            ),
            "duplicate_collapse_accuracy": _metric(
                "duplicate_collapse_accuracy",
                float(merged_ok),
                merged_total,
                "rate",
                "expected multi-member groups collapsed into one identity",
            ),
            "false_merge_rate": _metric(
                "false_merge_rate",
                float(false_merge),
                max(false_merge, 1),
                "rate",
                "papers that should stay separate merged into one identity",
            ),
            "false_split_rate": _metric(
                "false_split_rate",
                float(false_split),
                merged_total,
                "rate",
                "papers that should be one identity split into multiple",
            ),
            "identifier_normalization_accuracy": _metric(
                "identifier_normalization_accuracy",
                float(normalized_ok),
                normalized_total,
                "rate",
                "DOI-variant records collapsed after normalization",
            ),
            "supersession_accuracy": _metric(
                "supersession_accuracy",
                float(supersession_ok),
                supersession_total,
                "rate",
                "identities superseded when a new member appears",
            ),
            "partial_ingestion_accuracy": _metric(
                "partial_ingestion_accuracy",
                float(partial_ok),
                partial_total,
                "rate",
                "papers from surviving providers ingested and resolved despite provider failure",
            ),
        }

        status = EvaluatorStatus.failed if failures_detail else EvaluatorStatus.passed

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=(
                (canonical_ok + merged_ok + supersession_ok)
                / max(canonical_total + merged_total + supersession_total, 1)
                if canonical_total
                else None
            ),
            value={
                "produced_groups": [sorted(g) for g in produced_groups],
                "superseded_identity_ids": superseded_ids,
                "failed_providers": failed_providers,
                "metrics": metrics,
                "dimension_scores": {
                    k: v
                    for k, v in {
                        "canonical_mapping_accuracy": (
                            canonical_ok / canonical_total if canonical_total else None
                        ),
                        "duplicate_collapse_accuracy": (
                            merged_ok / merged_total if merged_total else None
                        ),
                        "false_merge_rate": (
                            float(false_merge / max(false_merge, 1)) if false_merge else 0.0
                        ),
                        "false_split_rate": false_split / merged_total if merged_total else None,
                        "identifier_normalization_accuracy": (
                            normalized_ok / normalized_total if normalized_total else None
                        ),
                        "supersession_accuracy": (
                            supersession_ok / supersession_total if supersession_total else None
                        ),
                        "partial_ingestion_accuracy": (
                            partial_ok / partial_total if partial_total else None
                        ),
                    }.items()
                    if v is not None
                },
            },
            status=status,
            explanation="; ".join(failures_detail)
            if failures_detail
            else "all ingestion/identity checks matched",
            evidence_artifact_ids=[
                e.artifact_id for e in ctx.produced_artifacts if e.artifact_type == "paper_identity"
            ],
        )


class IdentityResolutionEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.identity_resolution",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic ingestion + identity-resolution evaluator (Phase 7A.1)",
            provides=["evaluator.identity_resolution"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.identity_resolution", IdentityResolutionEvaluator())
