"""Model tournaments + role leaderboards (Phase 7B).

Runs existing frozen benchmarks against candidate models WITHOUT changing
benchmark definitions or evaluators: a `CandidateModelRouter` binds one
logical role to a candidate model and delegates every other role to the
production role router. Each benchmark run reuses the generic evaluation
harness end-to-end (workflow -> evaluators -> EvaluationReport), and the
tournament layer aggregates the persisted EvaluationRuns + per-call usage
into a deterministic role leaderboard.

The service never mutates the global user config: candidate binding exists
only inside the router instance for the duration of the tournament.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from research_harness.contracts.model import ModelRequest, ModelResponse
from research_harness.kernel.errors import ModelError, ServiceError
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.benchmarks import BUILTIN_BENCHMARKS
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import (
    Benchmark,
    EvaluationReport,
    EvaluationRun,
    EvaluatorResult,
)
from research_harness.research.schemas.tournament import (
    BenchmarkRunRef,
    ModelCallRecord,
    RoleLeaderboard,
    TournamentFailureKind,
    TournamentModelConfig,
    TournamentModelResult,
    TournamentPlan,
    TournamentRun,
)
from research_harness.research.tournament.accounting import (
    aggregate_calls,
    aggregate_run_results,
    call_cost,
)
from research_harness.research.tournament.plan import plan_hash
from research_harness.research.tournament.ranking import RANKING_RULES, build_leaderboard_entries
from research_harness.research.tournament.roles import validate_role

logger = logging.getLogger(__name__)

_PRODUCER = "evaluation.model_tournament"

_RETRYABLE_FAILURES = {
    TournamentFailureKind.timeout,
    TournamentFailureKind.provider_error,
    TournamentFailureKind.rate_limit,
}


def _classify_model_error(exc: ModelError) -> TournamentFailureKind:
    text = str(exc).lower()
    if "rate limit" in text or "429" in text:
        return TournamentFailureKind.rate_limit
    if "timeout" in text or "timed out" in text:
        return TournamentFailureKind.timeout
    if "validation" in text:
        return TournamentFailureKind.validation_failure
    return TournamentFailureKind.provider_error


class CandidateModelRouter:
    """Binds one logical role to a candidate model; other roles fall back to
    the production role router. Measures each candidate call at the model
    boundary (latency + usage + cost) and classifies failures — so provider
    failures stay distinct from research-answer (benchmark) failures."""

    def __init__(
        self,
        *,
        base_router: Any,
        role: str,
        candidate: TournamentModelConfig,
        service_lookup: Any,
        timeout_seconds: float,
        retries: int,
        benchmark_id: str = "",
    ) -> None:
        self._base = base_router
        self._role = role
        self._candidate = candidate
        self._lookup = service_lookup
        self._timeout = timeout_seconds
        self._retries = retries
        self._benchmark_id = benchmark_id
        self.records: list[ModelCallRecord] = []

    def resolve(self, role: str) -> dict[str, str]:
        if role == self._role:
            return {"provider": self._candidate.provider, "model": self._candidate.requested_model}
        return self._base.resolve(role)

    async def complete(self, role: str, request: ModelRequest) -> ModelResponse:
        if role != self._role:
            return await self._base.complete(role, request)
        return await self._complete_candidate(request)

    def _provider(self) -> Any:
        service_name = f"model_provider.{self._candidate.provider}"
        try:
            return self._lookup(service_name)
        except ServiceError as e:
            raise ModelError(
                f"no provider for candidate {self._candidate.candidate_id!r}: {e}"
            ) from e

    def _routed_request(self, request: ModelRequest) -> ModelRequest:
        meta = dict(request.metadata)
        meta["model"] = self._candidate.requested_model
        update: dict[str, Any] = {"metadata": meta}
        if self._candidate.temperature is not None:
            update["temperature"] = self._candidate.temperature
        if self._candidate.max_tokens is not None:
            update["max_tokens"] = self._candidate.max_tokens
        return request.model_copy(update=update)

    def _record(
        self,
        *,
        request: ModelRequest,
        role: str,
        status: str,
        failure: TournamentFailureKind | None,
        retries: int,
        response: ModelResponse | None = None,
        latency_ms: float | None = None,
    ) -> None:
        usage = response.usage if response is not None else None
        cost, cost_source = call_cost(usage, self._candidate.pricing)
        structured = bool(self._candidate.structured_output and request.response_schema is not None)
        resolved = response.model if response is not None else None
        self.records.append(
            ModelCallRecord(
                role=role,
                model=resolved or self._candidate.requested_model,
                requested_model=self._candidate.requested_model,
                provider=response.provider if response is not None else self._candidate.provider,
                temperature=self._candidate.temperature,
                max_tokens=self._candidate.max_tokens,
                structured=structured,
                latency_ms=latency_ms,
                prompt_tokens=usage.prompt_tokens if usage is not None else None,
                completion_tokens=usage.completion_tokens if usage is not None else None,
                total_tokens=usage.total_tokens if usage is not None else None,
                provider_cost=usage.cost if usage is not None and usage.cost is not None else None,
                calculated_cost=cost if cost_source == "pricing" else None,
                cost_source=cost_source,
                status=status,
                failure=failure,
                retries=retries,
                benchmark_id=self._benchmark_id,
            )
        )

    async def _complete_candidate(self, request: ModelRequest) -> ModelResponse:
        provider = self._provider()
        routed = self._routed_request(request)
        attempts = 0
        while True:
            attempts += 1
            start = time.monotonic()
            try:
                response = await asyncio.wait_for(provider.complete(routed), timeout=self._timeout)
                latency_ms = response.latency_ms
                if latency_ms is None:
                    latency_ms = (time.monotonic() - start) * 1000
                structured = bool(
                    self._candidate.structured_output and request.response_schema is not None
                )
                failure: TournamentFailureKind | None = None
                status = "success"
                if structured and response.message and response.message.content:
                    try:
                        json.loads(response.message.content)
                    except (ValueError, TypeError):
                        failure = TournamentFailureKind.structured_output_failure
                        status = "structured_output_failure"
                self._record(
                    request=request,
                    role=self._role,
                    status=status,
                    failure=failure,
                    retries=attempts - 1,
                    response=response,
                    latency_ms=latency_ms,
                )
                return response
            except TimeoutError:
                failure = TournamentFailureKind.timeout
            except ModelError as e:
                failure = _classify_model_error(e)
            except Exception as e:  # noqa: BLE001
                logger.warning("candidate %s raised unexpected %s", self._candidate.candidate_id, e)
                failure = TournamentFailureKind.provider_error

            if failure in _RETRYABLE_FAILURES and attempts <= self._retries:
                logger.debug(
                    "candidate %s attempt %d failed (%s); retrying",
                    self._candidate.candidate_id,
                    attempts,
                    failure.value,
                )
                continue
            self._record(
                request=request,
                role=self._role,
                status="error",
                failure=failure,
                retries=attempts - 1,
            )
            detail = failure.value if failure else "unknown"
            raise ModelError(
                f"candidate {self._candidate.candidate_id!r} failed after "
                f"{attempts} attempt(s): {detail}"
            )


class ModelTournamentService:
    def __init__(
        self,
        *,
        artifact_store: Any,
        harness: Any,
        role_router: Any,
        service_lookup: Any,
        producer: str = _PRODUCER,
    ) -> None:
        self._store = artifact_store
        self._harness = harness
        self._role_router = role_router
        self._lookup = service_lookup
        self._producer = producer

    @property
    def service_id(self) -> str:
        return "model_tournament.default"

    async def run_tournament(self, plan: TournamentPlan) -> TournamentRun:
        validate_role(plan.role)
        started = datetime.now(UTC)

        for bid in plan.benchmark_ids:
            definition = BUILTIN_BENCHMARKS.get(bid)
            if definition is not None:
                await self._harness.register_benchmark(definition)
            elif not await self._store.exists(bid):
                raise ServiceError(
                    f"tournament benchmark {bid!r} is not a builtin and not registered"
                )

        if not await self._store.exists(plan.plan_id):
            await self._store.put(
                ArtifactEnvelope.create(
                    payload=plan,
                    artifact_type="tournament_plan",
                    producer=self._producer,
                    artifact_id=plan.plan_id,
                )
            )

        phash = plan_hash(plan)
        ranking_rules = dict(plan.ranking_rules or RANKING_RULES)
        failures: list[str] = []
        model_results: list[TournamentModelResult] = []
        benchmark_versions: dict[str, int] = {}

        for candidate in plan.models:
            result, candidate_failures = await self._run_candidate(candidate, plan)
            model_results.append(result)
            failures.extend(candidate_failures)
            for ref in result.benchmark_runs:
                benchmark_versions[ref.benchmark_id] = ref.benchmark_version

        run_id = str(uuid.uuid4())
        leaderboard = self._build_leaderboard(plan, model_results, run_id)
        lb_id = leaderboard.id

        run = TournamentRun(
            id=run_id,
            plan_id=plan.plan_id,
            plan_hash=phash,
            plan_snapshot=plan.model_dump(mode="json"),
            role=plan.role,
            benchmark_ids=plan.benchmark_ids,
            benchmark_versions=benchmark_versions,
            repetitions=plan.repetitions,
            ranking_rules=ranking_rules,
            model_results=model_results,
            leaderboard_id=lb_id,
            status="completed" if not failures else "partial",
            failures=failures,
            started_at=started,
            completed_at=datetime.now(UTC),
            metadata={"plan_name": plan.name, "models": [m.requested_model for m in plan.models]},
        )
        await self._store.put(
            ArtifactEnvelope.create(
                payload=run,
                artifact_type="tournament_run",
                producer=self._producer,
                artifact_id=run.id,
            )
        )
        await self._store.put(
            ArtifactEnvelope.create(
                payload=leaderboard,
                artifact_type="role_leaderboard",
                producer=self._producer,
                artifact_id=lb_id,
            )
        )
        await self._link(run.id, plan.plan_id)
        await self._link(run.id, lb_id)
        for mr in model_results:
            for ref in mr.benchmark_runs:
                await self._link(run.id, ref.run_id)
        return run

    async def _run_candidate(
        self, candidate: TournamentModelConfig, plan: TournamentPlan
    ) -> tuple[TournamentModelResult, list[str]]:
        refs: list[BenchmarkRunRef] = []
        calls: list[ModelCallRecord] = []
        failures: list[str] = []
        # Attempts that crashed before producing a report. Counted separately
        # rather than as synthetic BenchmarkRunRefs: refs are dereferenced
        # later (advisory scoring fetches ref.run_id), so a ref with no
        # persisted run would raise.
        failed_repetitions = 0
        for bid in plan.benchmark_ids:
            evaluator_ids = await self._resolve_evaluator_ids(bid, plan)
            for rep in range(1, plan.repetitions + 1):
                router = CandidateModelRouter(
                    base_router=self._role_router,
                    role=plan.role,
                    candidate=candidate,
                    service_lookup=self._lookup,
                    timeout_seconds=plan.timeout_seconds,
                    retries=plan.retries,
                    benchmark_id=bid,
                )
                try:
                    run_id, report_id = await self._harness.run_benchmark(
                        bid,
                        evaluator_ids=evaluator_ids,
                        model_router=router,
                        model_roles={plan.role: candidate.requested_model},
                    )
                except Exception as e:  # noqa: BLE001
                    message = (
                        f"candidate {candidate.candidate_id!r} benchmark {bid} "
                        f"repetition {rep}: run failed: {e}"
                    )
                    failures.append(message)
                    logger.warning("%s", message)
                    # The attempt still happened: keep its call records so the
                    # candidate is charged for it, and count it as an attempt
                    # so a flaky model cannot be scored only on the runs that
                    # happened to succeed.
                    calls.extend(router.records)
                    failed_repetitions += 1
                    continue
                report = (await self._store.get(report_id)).parse_payload(EvaluationReport)
                refs.append(
                    BenchmarkRunRef(
                        benchmark_id=bid,
                        benchmark_version=report.benchmark_version,
                        repetition=rep,
                        run_id=run_id,
                        report_id=report_id,
                        report_status=report.status.value,
                        cases_total=report.cases_total,
                        cases_passed=report.cases_passed,
                        cases_failed=report.cases_failed,
                        cases_error=report.cases_error,
                        latency_ms=report.execution_latency_ms,
                        cost_usd=report.execution_cost_usd,
                    )
                )
                calls.extend(router.records)
                if report.status.value == "error":
                    failures.append(
                        f"candidate {candidate.candidate_id!r} benchmark {bid} "
                        f"repetition {rep}: report status error"
                    )

        call_metrics = aggregate_calls(calls)
        run_metrics = aggregate_run_results(refs, call_metrics, failed_repetitions)
        advisory = await self._advisory_score(refs, plan.advisory_evaluators)

        failure_counts = {k: int(v) for k, v in call_metrics.get("failure_counts", {}).items()}
        return (
            TournamentModelResult(
                candidate_id=candidate.candidate_id,
                config=candidate,
                resolved_model=call_metrics.get("resolved_model") or candidate.requested_model,
                role=plan.role,
                benchmark_runs=refs,
                calls=calls,
                deterministic_pass_rate=run_metrics.get("deterministic_pass_rate"),
                benchmark_pass_rate=run_metrics.get("benchmark_pass_rate"),
                case_pass_rate=run_metrics.get("case_pass_rate"),
                case_error_rate=run_metrics.get("case_error_rate"),
                repetition_failure_rate=run_metrics.get("repetition_failure_rate"),
                structured_output_success_rate=call_metrics.get("structured_output_success_rate"),
                model_error_rate=call_metrics.get("model_error_rate"),
                retry_rate=call_metrics.get("retry_rate"),
                latency_ms_mean=call_metrics.get("latency_ms_mean"),
                latency_ms_p50=call_metrics.get("latency_ms_p50"),
                latency_ms_p95=call_metrics.get("latency_ms_p95"),
                input_tokens=call_metrics.get("input_tokens"),
                output_tokens=call_metrics.get("output_tokens"),
                total_tokens=call_metrics.get("total_tokens"),
                estimated_cost=call_metrics.get("estimated_cost"),
                cost_per_successful_case=run_metrics.get("cost_per_successful_case"),
                cost_per_successful_benchmark=run_metrics.get("cost_per_successful_benchmark"),
                advisory_score=advisory,
                failure_counts=failure_counts,
            ),
            failures,
        )

    async def _resolve_evaluator_ids(
        self, benchmark_id: str, plan: TournamentPlan
    ) -> list[str] | None:
        base = list(plan.evaluator_ids or [])
        if not base:
            try:
                b_env = await self._store.get(benchmark_id)
                base = list((b_env.parse_payload(Benchmark).config or {}).get("evaluators") or [])
            except Exception:  # noqa: BLE001
                base = []
        merged = list(dict.fromkeys([*base, *plan.advisory_evaluators]))
        return merged or None

    async def _advisory_score(
        self, refs: list[BenchmarkRunRef], advisory_ids: list[str]
    ) -> float | None:
        if not advisory_ids:
            return None
        scores: list[float] = []
        for ref in refs:
            run = (await self._store.get(ref.run_id)).parse_payload(EvaluationRun)
            for rid in run.evaluator_result_ids:
                result = (await self._store.get(rid)).parse_payload(EvaluatorResult)
                if result.evaluator_id in advisory_ids and result.score is not None:
                    scores.append(float(result.score))
        if not scores:
            return None
        return sum(scores) / len(scores)

    def _build_leaderboard(
        self, plan: TournamentPlan, results: list[TournamentModelResult], run_id: str
    ) -> RoleLeaderboard:
        entries = build_leaderboard_entries(results, plan.deterministic_pass_threshold)
        return RoleLeaderboard(
            role=plan.role,
            plan_id=plan.plan_id,
            tournament_run_id=run_id,
            plan_hash=plan_hash(plan),
            ranking_rules=dict(plan.ranking_rules or RANKING_RULES),
            entries=entries,
            metadata={"repetitions": plan.repetitions},
        )

    async def get_leaderboard_for_role(self, role: str) -> RoleLeaderboard | None:
        validate_role(role)
        boards = [
            env.parse_payload(RoleLeaderboard)
            for env in await self._store.list(artifact_type="role_leaderboard")
            if env.payload.get("role") == role
        ]
        if not boards:
            return None
        boards.sort(key=lambda b: b.created_at, reverse=True)
        return boards[0]

    async def get_run(self, run_id: str) -> TournamentRun:
        return (await self._store.get(run_id)).parse_payload(TournamentRun)

    async def _link(self, source_id: str, target_id: str) -> None:
        from research_harness.research.provenance.relations import (
            ProvenanceLink,
            ProvenanceRelation,
        )

        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=source_id,
                target_artifact_id=target_id,
                producer=self._producer,
            )
        )


class ModelTournamentPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluation.model_tournament",
            version="0.1.0",
            plugin_type="research",
            description="Model tournaments + role leaderboards over the frozen benchmarks (Phase 7B)",
            provides=["model_tournament.default"],
            requires=[
                "evaluation_harness.default",
                "artifact_store.default",
                "model_router.default",
                "model_provider.openrouter",
            ],
        )

    async def setup(self, ctx: PluginContext) -> None:
        store = ctx.require("artifact_store.default")
        harness = ctx.require("evaluation_harness.default")
        role_router = ctx.require("model_router.default")

        def lookup(name: str) -> Any:
            return ctx.require(name)

        service = ModelTournamentService(
            artifact_store=store,
            harness=harness,
            role_router=role_router,
            service_lookup=lookup,
        )
        ctx.register("model_tournament.default", service)
