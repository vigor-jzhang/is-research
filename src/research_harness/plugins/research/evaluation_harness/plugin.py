"""Evaluation harness (Phase 6A/6B) — benchmark execution over production
workflows with plugin-based evaluators.

The harness delegates case execution to the workflow drivers in
`research/benchmarks/workflows.py`, which compose PRODUCTION services
(NoveltyValidationService, LiteratureSearchOrchestratorService, Phase 4C
PublicationFormatterService) with deterministic fixtures, so benchmarks run
the real code path offline. The harness never modifies production plugins; it
only drives their services and persists evaluation artifacts with provenance.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from research_harness.contracts.evaluator import Evaluator, EvaluatorContext
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.benchmarks import BenchmarkDefinition
from research_harness.research.benchmarks.workflows import (
    BenchmarkError,
    FixtureModelRouter,
    run_acquisition_workflow,
    run_citation_workflow,
    run_comparative_statics_workflow,
    run_e2e_workflow,
    run_equilibrium_workflow,
    run_evaluator_sanity_workflow,
    run_evidence_enrichment_workflow,
    run_evidence_workflow,
    run_gap_selection_workflow,
    run_gap_workflow,
    run_ingestion_identity_workflow,
    run_lq_critique_workflow,
    run_manuscript_grounding_workflow,
    run_mechanism_workflow,
    run_model_routing_workflow,
    run_model_specification_workflow,
    run_novelty_revalidation_workflow,
    run_novelty_workflow,
    run_numerical_workflow,
    run_proposition_workflow,
    run_publication_packaging_workflow,
    run_qualification_matrix_workflow,
    run_qualification_policy_workflow,
    run_results_assembly_workflow,
    run_retrieval_workflow,
    run_revalidation_workflow,
    run_routing_readiness_workflow,
    run_screening_workflow,
    run_synthesis_workflow,
    run_task_qualification_workflow,
)
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.evaluation import (
    Benchmark,
    BenchmarkCase,
    EvaluationCaseResult,
    EvaluationCaseStatus,
    EvaluationMetric,
    EvaluationMetricKind,
    EvaluationReport,
    EvaluationReportStatus,
    EvaluationRun,
    EvaluatorCategory,
    EvaluatorResult,
    EvaluatorStatus,
)

_PRODUCER = "research.evaluation_harness"


def _definition_hash(payload: Any) -> str:
    """Content hash of a benchmark/case payload ignoring registration-time
    fields (created_at), so identical definitions re-register idempotently
    while any semantic change raises a version conflict."""
    data = payload.model_dump(mode="json")
    data.pop("created_at", None)
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


class BenchmarkVersionError(BenchmarkError):
    """A registered benchmark differs from the definition being registered."""


class EvaluationHarnessService:
    def __init__(
        self,
        *,
        artifact_store: Any,
        ingestor: Any,
        identity_resolver: Any,
        evaluators: dict[str, Evaluator],
        config: dict[str, Any] | None = None,
        judge_role: str = "critic",
        cost_per_million_tokens: dict[str, float] | None = None,
        blob_store: Any | None = None,
        producer: str = _PRODUCER,
        optional_evaluators: dict[str, Any] | None = None,
    ) -> None:
        self._store = artifact_store
        self._ingestor = ingestor
        self._resolver = identity_resolver
        self._evaluators = dict(evaluators)
        for eid, evaluator in (optional_evaluators or {}).items():
            if evaluator is not None:
                self._evaluators[eid] = evaluator
        self._blob_store = blob_store
        self._config = dict(config or {})
        self._judge_role = judge_role
        self._cost_per_million = dict(cost_per_million_tokens or {"prompt": 0.0, "completion": 0.0})
        self._producer = producer

    @property
    def service_id(self) -> str:
        return "research.evaluation_harness"

    # ------------------------------------------------------------------
    # Benchmark registration (immutable, versioned)
    # ------------------------------------------------------------------

    async def register_benchmark(self, definition: BenchmarkDefinition) -> str:
        benchmark = Benchmark(
            id=definition.benchmark_id,
            version=definition.version,
            name=definition.name,
            description=definition.description,
            category=definition.category,
            config=definition.config,
            case_ids=[c.id for c in definition.cases],
        )
        env = ArtifactEnvelope.create(
            payload=benchmark,
            artifact_type="benchmark",
            producer=self._producer,
            artifact_id=definition.benchmark_id,
        )
        if await self._store.exists(definition.benchmark_id):
            existing = await self._store.get(definition.benchmark_id)
            if _definition_hash(existing.parse_payload(Benchmark)) == _definition_hash(benchmark):
                await self._ensure_cases(definition)
                return definition.benchmark_id
            raise BenchmarkVersionError(
                f"benchmark {definition.benchmark_id!r} is already registered with "
                "different content; register a new version instead of silently "
                "changing historical evaluation inputs"
            )
        await self._store.put(env)
        for case in definition.cases:
            await self._register_case(benchmark.id, case)
            await self._link(benchmark.id, case.id)
        return definition.benchmark_id

    async def _ensure_cases(self, definition: BenchmarkDefinition) -> None:
        existing = await self._store.get(definition.benchmark_id)
        benchmark = existing.parse_payload(Benchmark)
        for case in definition.cases:
            if not await self._store.exists(case.id):
                await self._register_case(benchmark.id, case)
                await self._link(benchmark.id, case.id)
                continue
            case_env = await self._store.get(case.id)
            expected = BenchmarkCase(
                id=case.id,
                benchmark_id=benchmark.id,
                version=case.version,
                name=case.name,
                description=case.description,
                input=case.input,
                reference=case.reference,
                evaluation_dimensions=case.evaluation_dimensions,
                tags=case.tags,
            )
            if _definition_hash(case_env.parse_payload(BenchmarkCase)) != _definition_hash(
                expected
            ):
                raise BenchmarkVersionError(
                    f"benchmark case {case.id!r} is already registered with different content"
                )

    async def _register_case(self, benchmark_id: str, case: Any) -> None:
        payload = BenchmarkCase(
            id=case.id,
            benchmark_id=benchmark_id,
            version=1,
            name=case.name,
            description=case.description,
            input=case.input,
            reference=case.reference,
            evaluation_dimensions=case.evaluation_dimensions,
            tags=case.tags,
        )
        await self._store.put(
            ArtifactEnvelope.create(
                payload=payload,
                artifact_type="benchmark_case",
                producer=self._producer,
                artifact_id=case.id,
            )
        )

    # ------------------------------------------------------------------
    # Benchmark execution
    # ------------------------------------------------------------------

    async def run_benchmark(
        self,
        benchmark_id: str,
        *,
        evaluator_ids: list[str] | None = None,
        model_router: Any | None = None,
        model_roles: dict[str, str] | None = None,
    ) -> tuple[str, str]:
        started = datetime.now(UTC)
        b_env = await self._store.get(benchmark_id)
        benchmark = b_env.parse_payload(Benchmark)

        run_id = str(uuid.uuid4())
        report_id = str(uuid.uuid4())

        evaluator_ids = list(
            evaluator_ids
            or benchmark.config.get("evaluators")
            or self._config.get("evaluators")
            or list(self._evaluators)
        )
        unknown = [eid for eid in evaluator_ids if eid not in self._evaluators]
        if unknown:
            raise BenchmarkError(f"unknown evaluator(s): {sorted(unknown)}")

        eval_config = {
            **self._config,
            "judge_role": self._judge_role,
        }
        run_model_roles: dict[str, str] = {"judge": self._judge_role}
        if model_roles:
            run_model_roles.update(model_roles)
        run_evaluator_versions: dict[str, str] = {}
        for eid in evaluator_ids:
            run_evaluator_versions[eid] = self._evaluators[eid].evaluator_version

        produced_ids: list[str] = []
        evaluator_result_ids: list[str] = []
        case_result_ids: list[str] = []
        case_results: list[EvaluationCaseResult] = []
        failures: list[str] = []
        case_hashes: dict[str, str] = {}
        token_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        cost_usd = 0.0

        for cid in benchmark.case_ids:
            c_env = await self._store.get(cid)
            case = c_env.parse_payload(BenchmarkCase)
            case_hashes[cid] = c_env.content_hash
            workflow_error: str | None = None
            try:
                produced, workflow_error = await self._run_case(
                    case, benchmark, model_router=model_router
                )
            except Exception as e:  # noqa: BLE001
                message = f"{cid}: case run failed: {e}"
                failures.append(message)
                produced = []
                workflow_error = message
                case_result = EvaluationCaseResult(
                    case_id=cid,
                    case_name=case.name,
                    case_version=case.version,
                    case_content_hash=c_env.content_hash,
                    status=EvaluationCaseStatus.error,
                    error=message,
                )
                cr_id = await self._persist_case_result(case_result, cid, [], [])
                case_result_ids.append(cr_id)
                case_results.append(case_result)
                continue

            produced_ids.extend(e.artifact_id for e in produced)
            results = await self._evaluate_case(
                case, c_env, produced, eval_config, benchmark, evaluator_ids, model_router
            )
            evaluator_result_ids.extend(r.id for r in results)
            for r in results:
                meta = r.model_metadata or {}
                token_usage["prompt_tokens"] += int(meta.get("prompt_tokens") or 0)
                token_usage["completion_tokens"] += int(meta.get("completion_tokens") or 0)
                cost_usd += (
                    int(meta.get("prompt_tokens") or 0) * self._cost_per_million.get("prompt", 0.0)
                    + int(meta.get("completion_tokens") or 0)
                    * self._cost_per_million.get("completion", 0.0)
                ) / 1_000_000

            case_result = self._build_case_result(case, c_env, produced, results)
            if workflow_error:
                case_result = case_result.model_copy(update={"error": workflow_error})
            cr_id = await self._persist_case_result(
                case_result,
                cid,
                [e.artifact_id for e in produced],
                [r.id for r in results],
            )
            case_result_ids.append(cr_id)
            case_results.append(case_result)

        latency_ms = max(0, int((datetime.now(UTC) - started).total_seconds() * 1000))
        report = await self._aggregate_report(
            benchmark_id=benchmark_id,
            benchmark_version=benchmark.version,
            run_id=run_id,
            report_id=report_id,
            case_results=case_results,
            evaluator_versions=run_evaluator_versions,
            model_roles=run_model_roles,
            token_usage=token_usage,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            failures=failures,
        )
        await self._store.put(
            ArtifactEnvelope.create(
                payload=report,
                artifact_type="evaluation_report",
                producer=self._producer,
                artifact_id=report_id,
            )
        )

        run = EvaluationRun(
            id=run_id,
            benchmark_id=benchmark_id,
            benchmark_version=benchmark.version,
            benchmark_content_hash=b_env.content_hash,
            case_hashes=case_hashes,
            evaluation_config=eval_config,
            evaluator_ids=evaluator_ids,
            evaluator_versions=run_evaluator_versions,
            model_roles=run_model_roles,
            produced_artifact_ids=sorted(produced_ids),
            evaluator_result_ids=evaluator_result_ids,
            case_result_ids=case_result_ids,
            report_id=report_id,
            token_usage=token_usage,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            cases_total=report.cases_total,
            cases_passed=report.cases_passed,
            cases_failed=report.cases_failed,
            cases_error=report.cases_error,
            failures=failures,
            status=report.status,
        )
        await self._store.put(
            ArtifactEnvelope.create(
                payload=run,
                artifact_type="evaluation_run",
                producer=self._producer,
                artifact_id=run_id,
            )
        )
        await self._link(benchmark_id, run_id)
        await self._link(run_id, report_id)
        for cr_id in case_result_ids:
            await self._link(run_id, cr_id)
        return run_id, report_id

    # ------------------------------------------------------------------
    # Case execution over the production workflow
    # ------------------------------------------------------------------

    async def _run_case(
        self, case: BenchmarkCase, benchmark: Benchmark, model_router: Any | None = None
    ) -> tuple[list[ArtifactEnvelope[Any]], str | None]:
        workflow = case.input.get("workflow", "novelty_validation")
        if workflow == "novelty_validation":
            produced = await run_novelty_workflow(
                artifact_store=self._store,
                ingestor=self._ingestor,
                identity_resolver=self._resolver,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "literature_retrieval":
            produced = await run_retrieval_workflow(
                artifact_store=self._store,
                ingestor=self._ingestor,
                identity_resolver=self._resolver,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "citation_correctness":
            return await run_citation_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
        if workflow == "literature_screening":
            produced = await run_screening_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "evidence_extraction":
            produced = await run_evidence_workflow(
                artifact_store=self._store,
                blob_store=self._blob_store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "gap_analysis":
            produced = await run_gap_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "mechanism_development":
            produced = await run_mechanism_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "equilibrium_derivation":
            produced = await run_equilibrium_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "numerical_analysis":
            produced = await run_numerical_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "comparative_statics":
            produced = await run_comparative_statics_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "proposition_generation":
            produced = await run_proposition_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "results_assembly":
            produced = await run_results_assembly_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "manuscript_grounding":
            produced = await run_manuscript_grounding_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "research_pipeline_e2e":
            produced = await run_e2e_workflow(
                artifact_store=self._store,
                ingestor=self._ingestor,
                identity_resolver=self._resolver,
                blob_store=self._blob_store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "evidence_enrichment":
            produced = await run_evidence_enrichment_workflow(
                artifact_store=self._store,
                ingestor=self._ingestor,
                identity_resolver=self._resolver,
                blob_store=self._blob_store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "model_routing":
            produced = await run_model_routing_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
            )
            return produced, None
        if workflow == "lq_critique":
            produced = await run_lq_critique_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "routing_readiness":
            produced = await run_routing_readiness_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
            )
            return produced, None
        if workflow == "qualification_policy":
            produced = await run_qualification_policy_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
            )
            return produced, None
        if workflow == "qualification_matrix":
            produced = await run_qualification_matrix_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
            )
            return produced, None
        if workflow == "task_qualification":
            produced = await run_task_qualification_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
            )
            return produced, None
        if workflow == "evaluator_sanity":
            produced = await run_evaluator_sanity_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
            )
            return produced, None
        if workflow == "literature_synthesis":
            produced = await run_synthesis_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "analytical_model_specification":
            produced = await run_model_specification_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "document_acquisition":
            produced = await run_acquisition_workflow(
                artifact_store=self._store,
                blob_store=self._blob_store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "incremental_revalidation":
            produced = await run_revalidation_workflow(
                artifact_store=self._store,
                blob_store=self._blob_store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "literature_ingestion_identity":
            produced = await run_ingestion_identity_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "gap_selection":
            produced = await run_gap_selection_workflow(
                artifact_store=self._store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "novelty_revalidation":
            produced = await run_novelty_revalidation_workflow(
                artifact_store=self._store,
                ingestor=self._ingestor,
                identity_resolver=self._resolver,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        if workflow == "publication_packaging":
            produced = await run_publication_packaging_workflow(
                artifact_store=self._store,
                blob_store=self._blob_store,
                case=case,
                producer=self._producer,
                model_router=model_router,
            )
            return produced, None
        raise BenchmarkError(f"unsupported benchmark workflow {workflow!r}")

    async def _evaluate_case(
        self,
        case: BenchmarkCase,
        c_env: ArtifactEnvelope[Any],
        produced: list[ArtifactEnvelope[Any]],
        eval_config: dict[str, Any],
        benchmark: Benchmark,
        evaluator_ids: list[str],
        model_router: Any | None = None,
    ) -> list[EvaluatorResult]:
        results: list[EvaluatorResult] = []
        fixture_router = FixtureModelRouter(case.input.get("llm_fixtures") or [])
        context_router = model_router or fixture_router
        provenance: dict[str, list[Any]] = {}
        for env in produced:
            try:
                provenance[env.artifact_id] = await self._store.get_parents(env.artifact_id)
            except Exception:  # noqa: BLE001
                provenance[env.artifact_id] = []
        for eid in evaluator_ids:
            evaluator = self._evaluators[eid]
            ctx = EvaluatorContext(
                case=case,
                case_envelope=c_env,
                produced_artifacts=produced,
                config={
                    **eval_config,
                    **benchmark.config,
                    **(case.input.get("evaluator_config") or {}),
                },
                model_router=context_router,
                blob_store=self._blob_store,
                provenance=provenance,
            )
            try:
                result = await evaluator.evaluate(ctx)
            except Exception as e:  # noqa: BLE001
                result = EvaluatorResult(
                    case_id=case.id,
                    evaluator_id=eid,
                    evaluator_version=evaluator.evaluator_version,
                    category=EvaluatorCategory(evaluator.category),
                    score=None,
                    value={},
                    status=EvaluatorStatus.error,
                    explanation=f"evaluator raised: {e}",
                )
            env = ArtifactEnvelope.create(
                payload=result,
                artifact_type="evaluator_result",
                producer=self._producer,
                artifact_id=result.id,
            )
            await self._store.put(env)
            await self._link(case.id, result.id)
            for ev in result.evidence_artifact_ids:
                if ev in {e.artifact_id for e in produced}:
                    await self._link(ev, result.id)
            results.append(result)
        return results

    def _build_case_result(
        self,
        case: BenchmarkCase,
        c_env: ArtifactEnvelope[Any],
        produced: list[ArtifactEnvelope[Any]],
        results: list[EvaluatorResult],
    ) -> EvaluationCaseResult:
        if any(
            r.category == EvaluatorCategory.deterministic and r.status == EvaluatorStatus.failed
            for r in results
        ):
            status = EvaluationCaseStatus.failed
        elif any(r.status == EvaluatorStatus.error for r in results):
            status = EvaluationCaseStatus.error
        else:
            status = EvaluationCaseStatus.passed

        metrics: dict[str, float] = {}
        for r in results:
            if r.category == EvaluatorCategory.deterministic:
                metrics.update(r.value.get("dimension_scores") or {})
        return EvaluationCaseResult(
            case_id=case.id,
            case_name=case.name,
            case_version=case.version,
            case_content_hash=c_env.content_hash,
            status=status,
            evaluator_result_ids=[r.id for r in results],
            produced_artifact_ids=sorted(e.artifact_id for e in produced),
            metrics=metrics,
        )

    async def _persist_case_result(
        self,
        case_result: EvaluationCaseResult,
        case_id: str,
        produced_ids: list[str],
        result_ids: list[str],
    ) -> str:
        await self._store.put(
            ArtifactEnvelope.create(
                payload=case_result,
                artifact_type="evaluation_case_result",
                producer=self._producer,
                artifact_id=case_result.id,
            )
        )
        await self._link(case_id, case_result.id)
        for rid in result_ids:
            await self._link(rid, case_result.id)
        for pid in produced_ids:
            await self._link(pid, case_result.id)
        return case_result.id

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    async def _aggregate_report(
        self,
        *,
        benchmark_id: str,
        benchmark_version: int,
        run_id: str,
        report_id: str,
        case_results: list[EvaluationCaseResult],
        evaluator_versions: dict[str, str],
        model_roles: dict[str, str],
        token_usage: dict[str, int],
        cost_usd: float,
        latency_ms: int,
        failures: list[str],
    ) -> EvaluationReport:
        total = len(case_results)
        passed = sum(1 for c in case_results if c.status == EvaluationCaseStatus.passed)
        failed = sum(1 for c in case_results if c.status == EvaluationCaseStatus.failed)
        errored = sum(1 for c in case_results if c.status == EvaluationCaseStatus.error)

        # generic aggregation of deterministic evaluator metric contributions
        accum: dict[str, dict[str, Any]] = {}
        false_clear = 0
        false_threat = 0
        evaluator_errors = 0
        for cr in case_results:
            for rid in cr.evaluator_result_ids:
                try:
                    env = await self._store.get(rid)
                except Exception:  # noqa: BLE001
                    continue
                result = env.parse_payload(EvaluatorResult)
                if result.status == EvaluatorStatus.error:
                    evaluator_errors += 1
                if result.category != EvaluatorCategory.deterministic:
                    continue
                value = result.value or {}
                false_clear += int(value.get("false_clear_count") or 0)
                false_threat += int(value.get("false_threat_count") or 0)
                for metric_id, contrib in (value.get("metrics") or {}).items():
                    acc = accum.setdefault(
                        metric_id,
                        {
                            "value": 0.0,
                            "count": 0,
                            "kind": str(contrib.get("kind") or "quantity"),
                            "dimension": str(contrib.get("dimension") or "evaluation"),
                            "definition": str(contrib.get("definition") or ""),
                        },
                    )
                    acc["value"] += float(contrib.get("value") or 0.0)
                    acc["count"] += int(contrib.get("count") or 0)

        def _rate(matches: float, denominator: int) -> float:
            return matches / denominator if denominator else 0.0

        _KINDS = {k.value: k for k in EvaluationMetricKind}
        metrics: list[EvaluationMetric] = []
        for metric_id in sorted(accum):
            acc = accum[metric_id]
            kind = _KINDS.get(acc["kind"], EvaluationMetricKind.quantity)
            value = (
                _rate(acc["value"], acc["count"])
                if kind in (EvaluationMetricKind.rate, EvaluationMetricKind.score) and acc["count"]
                else acc["value"]
            )
            metrics.append(
                EvaluationMetric(
                    metric_id=metric_id,
                    dimension=acc["dimension"],
                    kind=kind,
                    value=value,
                    count=acc["count"],
                    definition=acc["definition"],
                )
            )
        metrics.extend(
            [
                EvaluationMetric(
                    metric_id="case_pass_rate",
                    dimension="case",
                    kind=EvaluationMetricKind.rate,
                    value=passed / total if total else 0.0,
                    count=total,
                    definition="fraction of cases that passed all gating evaluators",
                ),
                EvaluationMetric(
                    metric_id="evaluator_error_count",
                    dimension="evaluator",
                    kind=EvaluationMetricKind.quantity,
                    value=float(evaluator_errors),
                    count=1,
                    definition="evaluator invocations that ended in status error",
                ),
                EvaluationMetric(
                    metric_id="execution_cost_usd",
                    dimension="cost",
                    kind=EvaluationMetricKind.cost,
                    value=cost_usd,
                    count=1,
                    definition="estimated model cost of the run (0 for offline fixtures)",
                ),
                EvaluationMetric(
                    metric_id="execution_latency_ms",
                    dimension="latency",
                    kind=EvaluationMetricKind.latency,
                    value=float(latency_ms),
                    count=1,
                    definition="wall-clock duration of the evaluation run",
                ),
            ]
        )

        if any(c.status == EvaluationCaseStatus.error for c in case_results):
            status = EvaluationReportStatus.error
        elif any(c.status == EvaluationCaseStatus.failed for c in case_results):
            status = EvaluationReportStatus.failed
        else:
            status = EvaluationReportStatus.passed

        return EvaluationReport(
            id=report_id,
            run_id=run_id,
            benchmark_id=benchmark_id,
            benchmark_version=benchmark_version,
            status=status,
            cases_total=total,
            cases_passed=passed,
            cases_failed=failed,
            cases_error=errored,
            metrics=metrics,
            case_results=case_results,
            false_positive_counts={"false_threat": false_threat},
            false_negative_counts={"false_clear": false_clear},
            execution_cost_usd=cost_usd,
            execution_latency_ms=latency_ms,
            evaluator_versions=evaluator_versions,
            model_roles=model_roles,
            metadata={"token_usage": token_usage, "failures": failures},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _link(self, source_id: str, target_id: str) -> None:
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=source_id,
                target_artifact_id=target_id,
                producer=self._producer,
            )
        )


class EvaluationHarnessPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.evaluation_harness",
            version="0.1.0",
            plugin_type="research",
            description="Evaluation harness: benchmarks, evaluators, reports (Phase 6A)",
            provides=["evaluation_harness.default"],
            requires=[
                "artifact_store.default",
                "literature_ingestor.default",
                "paper_identity_resolver.default",
                "evaluator.deterministic",
                "evaluator.retrieval",
                "evaluator.claim_grounding",
                "evaluator.citation_correctness",
                "evaluator.llm_judge",
                "evaluator.screening",
                "evaluator.evidence",
                "evaluator.gap_analysis",
                "evaluator.mechanism",
                "evaluator.equilibrium",
                "evaluator.numerical",
                "evaluator.comparative_statics",
                "evaluator.proposition",
                "evaluator.results_grounding",
                "evaluator.manuscript_grounding",
                "evaluator.pipeline_integrity",
                "evaluator.synthesis",
                "evaluator.model_specification",
                "evaluator.document_acquisition",
                "evaluator.revalidation",
                "evaluator.identity_resolution",
                "evaluator.gap_selection",
                "evaluator.novelty_revalidation",
                "evaluator.publication_packaging",
                "evaluator.evidence_enrichment",
                "evaluator.model_routing",
                "evaluator.live_quality_reasoning",
                "evaluator.live_quality_critic",
                "evaluator.live_quality_fast",
                "evaluator.routing_readiness",
                "evaluator.model_qualification",
                "evaluator.task_model_qualification",
            ],
            optional_requires=["blob_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        evaluation_cfg: dict[str, Any] = {}
        if isinstance(ctx.config.get("evaluation"), dict):
            evaluation_cfg = ctx.config["evaluation"]

        store = ctx.require("artifact_store.default")
        ingestor = ctx.require("literature_ingestor.default")
        resolver = ctx.require("paper_identity_resolver.default")

        service = EvaluationHarnessService(
            artifact_store=store,
            ingestor=ingestor,
            identity_resolver=resolver,
            evaluators={
                "evaluator.deterministic": ctx.require("evaluator.deterministic"),
                "evaluator.retrieval": ctx.require("evaluator.retrieval"),
                "evaluator.claim_grounding": ctx.require("evaluator.claim_grounding"),
                "evaluator.citation_correctness": ctx.require("evaluator.citation_correctness"),
                "evaluator.llm_judge": ctx.require("evaluator.llm_judge"),
                "evaluator.screening": ctx.require("evaluator.screening"),
                "evaluator.evidence": ctx.require("evaluator.evidence"),
                "evaluator.gap_analysis": ctx.require("evaluator.gap_analysis"),
                "evaluator.mechanism": ctx.require("evaluator.mechanism"),
                "evaluator.equilibrium": ctx.require("evaluator.equilibrium"),
                "evaluator.numerical": ctx.require("evaluator.numerical"),
                "evaluator.comparative_statics": ctx.require("evaluator.comparative_statics"),
                "evaluator.proposition": ctx.require("evaluator.proposition"),
                "evaluator.results_grounding": ctx.require("evaluator.results_grounding"),
                "evaluator.manuscript_grounding": ctx.require("evaluator.manuscript_grounding"),
                "evaluator.pipeline_integrity": ctx.require("evaluator.pipeline_integrity"),
                "evaluator.synthesis": ctx.require("evaluator.synthesis"),
                "evaluator.model_specification": ctx.require("evaluator.model_specification"),
                "evaluator.document_acquisition": ctx.require("evaluator.document_acquisition"),
                "evaluator.revalidation": ctx.require("evaluator.revalidation"),
                "evaluator.identity_resolution": ctx.require("evaluator.identity_resolution"),
                "evaluator.gap_selection": ctx.require("evaluator.gap_selection"),
                "evaluator.novelty_revalidation": ctx.require("evaluator.novelty_revalidation"),
                "evaluator.publication_packaging": ctx.require("evaluator.publication_packaging"),
                "evaluator.evidence_enrichment": ctx.require("evaluator.evidence_enrichment"),
                "evaluator.model_routing": ctx.require("evaluator.model_routing"),
                "evaluator.live_quality_reasoning": ctx.require("evaluator.live_quality_reasoning"),
                "evaluator.live_quality_critic": ctx.require("evaluator.live_quality_critic"),
                "evaluator.live_quality_fast": ctx.require("evaluator.live_quality_fast"),
                "evaluator.routing_readiness": ctx.require("evaluator.routing_readiness"),
                "evaluator.model_qualification": ctx.require("evaluator.model_qualification"),
                "evaluator.task_model_qualification": ctx.require(
                    "evaluator.task_model_qualification"
                ),
            },
            optional_evaluators={
                "evaluator.evaluator_sanity": ctx.try_get("evaluator.evaluator_sanity"),
            },
            config=evaluation_cfg,
            judge_role=str(evaluation_cfg.get("judge_role") or "critic"),
            cost_per_million_tokens=evaluation_cfg.get("cost_per_million_tokens"),
            blob_store=ctx.try_get("blob_store.default"),
        )
        ctx.register("evaluation_harness.default", service)
