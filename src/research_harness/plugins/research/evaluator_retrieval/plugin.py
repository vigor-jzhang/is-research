"""evaluator.retrieval — deterministic literature-retrieval evaluator
(Phase 6B).

Computes precision@k / recall@k / F1@k, MRR, duplicate_rate, and
retrieved-set quality against the case reference, over the artifacts
produced by the real `literature.search_orchestrator` pipeline.

Ranking = provider hit order: the persisted contract has no explicit rank
field, so the ranked list is the deduplicated first-hit order of
`LiteratureSearchRecord.paper_artifact_ids` (fixture providers return hits
in fixture order, which IS the intended rank order). MRR/precision@k are
only meaningful under that documented interpretation.
"""

from __future__ import annotations

import re
from typing import Any

from research_harness.contracts.evaluator import (
    EvaluatorContext,
    envelope_payload_dict,
)
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.schemas.evaluation import (
    EvaluatorCategory,
    EvaluatorResult,
    EvaluatorStatus,
)

_DEFAULT_K = [5, 10]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


class RetrievalEvaluator:
    evaluator_id = "evaluator.retrieval"
    evaluator_version = "0.1.0"
    category = EvaluatorCategory.deterministic.value

    async def evaluate(self, ctx: EvaluatorContext) -> EvaluatorResult:
        k_values = [int(k) for k in (ctx.config.get("k") or _DEFAULT_K)]
        k_values = sorted({k for k in k_values if k > 0}) or _DEFAULT_K

        executions = [
            e for e in ctx.produced_artifacts if e.artifact_type == "literature_search_execution"
        ]
        if not executions:
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                score=None,
                value={},
                status=EvaluatorStatus.failed,
                explanation="no literature_search_execution produced for the case",
            )
        exec_env = max(executions, key=lambda e: e.created_at)
        execution = envelope_payload_dict(exec_env)
        counts: dict[str, Any] = execution.get("counts") or {}

        records = {
            e.artifact_id: e
            for e in ctx.produced_artifacts
            if e.artifact_type == "literature_search_record"
        }
        identities = {
            e.artifact_id: envelope_payload_dict(e)
            for e in ctx.produced_artifacts
            if e.artifact_type == "paper_identity"
        }
        paper_to_identity: dict[str, str] = {}
        for iid, identity in identities.items():
            for pid in identity.get("member_paper_artifact_ids") or []:
                paper_to_identity.setdefault(pid, iid)

        # ranked identity list: first-hit order across search records, dedup
        ranked: list[str] = []
        seen: set[str] = set()
        for rid in execution.get("search_record_artifact_ids") or []:
            rec = records.get(rid)
            if rec is None:
                continue
            for pid in envelope_payload_dict(rec).get("paper_artifact_ids") or []:
                iid = paper_to_identity.get(pid)
                if iid is None or iid in seen:
                    continue
                seen.add(iid)
                ranked.append(iid)

        # reference keys (doi or title) -> produced identities
        key_to_identity: dict[str, str] = {}
        for env in ctx.produced_artifacts:
            if env.artifact_type != "paper_record":
                continue
            rec = envelope_payload_dict(env)
            iid = paper_to_identity.get(env.artifact_id)
            if iid is None:
                continue
            doi = (rec.get("doi") or "").strip()
            if doi:
                key_to_identity.setdefault(_norm(doi), iid)
            title = (rec.get("title") or "").strip()
            if title:
                key_to_identity.setdefault(_norm(title), iid)

        relevant_keys = list(ctx.case.reference.get("relevant") or [])
        relevant = [key_to_identity[_norm(k)] for k in relevant_keys if _norm(k) in key_to_identity]
        relevant_set = set(relevant)
        retrieved_set = set(ranked)

        raw = int(counts.get("raw_paper_records") or 0)
        unique = int(counts.get("unique_paper_identities") or 0)
        collapsed = int(counts.get("duplicate_records_collapsed") or 0)
        duplicate_rate = collapsed / raw if raw else 0.0

        missed = len(relevant_set - retrieved_set)
        irrelevant = len(retrieved_set - relevant_set)

        mrr = 0.0
        for rank, iid in enumerate(ranked, start=1):
            if iid in relevant_set:
                mrr = 1.0 / rank
                break

        k_metrics: dict[str, float] = {}
        for k in k_values:
            topk = ranked[:k]
            hits = len(set(topk) & relevant_set)
            precision = hits / k
            recall = hits / len(relevant_set) if relevant_set else 0.0
            f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
            k_metrics[f"precision@{k}"] = precision
            k_metrics[f"recall@{k}"] = recall
            k_metrics[f"f1@{k}"] = f1

        status = EvaluatorStatus.passed if missed == 0 else EvaluatorStatus.failed
        explanation_parts = [
            f"{len(relevant_set)} relevant, {len(ranked)} retrieved, {missed} missed, "
            f"{irrelevant} irrelevant, duplicate_rate {duplicate_rate:.3f}, MRR {mrr:.3f}"
        ]
        if missed:
            explanation_parts.append(f"RELEVANT MISSED: {sorted(relevant_set - retrieved_set)}")
        for k in k_values:
            explanation_parts.append(
                f"p@{k}={k_metrics[f'precision@{k}']:.3f} "
                f"r@{k}={k_metrics[f'recall@{k}']:.3f} "
                f"f1@{k}={k_metrics[f'f1@{k}']:.3f}"
            )

        metrics: dict[str, dict[str, Any]] = {}
        dimension_scores: dict[str, float] = {}
        for k in k_values:
            for name in (f"precision@{k}", f"recall@{k}", f"f1@{k}"):
                metrics[name] = {
                    "value": k_metrics[name],
                    "count": 1,
                    "kind": "rate",
                    "dimension": "retrieval",
                    "definition": (
                        f"{name}: {name.split('@')[0]} at k={k} (ranking = provider hit order)"
                    ),
                }
                dimension_scores[name] = k_metrics[name]
        metrics["mrr"] = {
            "value": mrr,
            "count": 1,
            "kind": "rate",
            "dimension": "retrieval",
            "definition": "mean reciprocal rank of the first relevant identity",
        }
        metrics["duplicate_rate"] = {
            "value": duplicate_rate,
            "count": 1,
            "kind": "rate",
            "dimension": "retrieval",
            "definition": "duplicate_records_collapsed / raw_paper_records",
        }
        metrics["relevant_papers_missed"] = {
            "value": float(missed),
            "count": 1,
            "kind": "quantity",
            "dimension": "retrieval",
            "definition": "relevant identities absent from the retrieved set",
        }
        metrics["irrelevant_papers_retrieved"] = {
            "value": float(irrelevant),
            "count": 1,
            "kind": "quantity",
            "dimension": "retrieval",
            "definition": "retrieved identities absent from the relevant set",
        }
        dimension_scores["mrr"] = mrr
        dimension_scores["duplicate_rate"] = duplicate_rate
        dimension_scores["relevant_papers_missed"] = float(missed)
        dimension_scores["irrelevant_papers_retrieved"] = float(irrelevant)

        return EvaluatorResult(
            case_id=ctx.case.id,
            evaluator_id=self.evaluator_id,
            evaluator_version=self.evaluator_version,
            category=EvaluatorCategory.deterministic,
            score=mrr,
            value={
                "ranked_identity_ids": ranked,
                "relevant_identity_ids": relevant,
                "retrieved_identity_ids": sorted(retrieved_set),
                "counts": {
                    "raw_paper_records": raw,
                    "unique_paper_identities": unique,
                    "duplicate_records_collapsed": collapsed,
                },
                "relevant_papers_missed": missed,
                "irrelevant_papers_retrieved": irrelevant,
                "mrr": mrr,
                "duplicate_rate": duplicate_rate,
                "k_metrics": k_metrics,
                "dimension_scores": dimension_scores,
                "metrics": metrics,
            },
            status=status,
            explanation="; ".join(explanation_parts),
            evidence_artifact_ids=[
                exec_env.artifact_id,
                *[
                    e.artifact_id
                    for e in ctx.produced_artifacts
                    if e.artifact_type == "literature_search_record"
                ],
                *[
                    e.artifact_id
                    for e in ctx.produced_artifacts
                    if e.artifact_type == "paper_identity"
                ],
            ],
        )


class RetrievalEvaluatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluator.retrieval",
            version="0.1.0",
            plugin_type="evaluator",
            description="Deterministic literature-retrieval evaluator (Phase 6B)",
            provides=["evaluator.retrieval"],
            requires=[],
            optional_requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("evaluator.retrieval", RetrievalEvaluator())
