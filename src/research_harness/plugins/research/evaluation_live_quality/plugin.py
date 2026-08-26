"""Live-quality model validation + routing readiness (Phase 7D.0).

Runs the frozen live-quality benchmarks against a real model (via the
CandidateModelRouter over the production role router) with configurable
repetitions, aggregates the results into LiveQualityModelResult (with variance),
produces live-quality RoleLeaderboards (evidence_type=live_quality_evidence),
and computes per-role RoutingReadinessAssessment. Production routing is never
enabled automatically.
"""

from __future__ import annotations

from datetime import UTC, datetime
from statistics import fmean, pvariance
from typing import Any

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.benchmarks import BUILTIN_BENCHMARKS
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import EvaluationReport
from research_harness.research.schemas.live_quality import (
    LiveQualityModelResult,
    LiveQualityRun,
    LiveQualityTaskResult,
    QualificationCriteria,
    RoutingReadinessAssessment,
)
from research_harness.research.schemas.tournament import (
    LeaderboardEntry,
    RoleLeaderboard,
    TournamentModelConfig,
)
from research_harness.research.tournament.accounting import aggregate_calls

_PRODUCER = "evaluation.live_quality"

_PROVIDER_ERROR_KINDS = {"timeout", "provider_error", "rate_limit", "validation_failure"}


class LiveQualityService:
    def __init__(
        self,
        *,
        artifact_store: Any,
        harness: Any,
        role_router: Any,
        service_lookup: Any,
        current_roles: dict[str, dict[str, str]] | None = None,
        producer: str = _PRODUCER,
    ) -> None:
        self._store = artifact_store
        self._harness = harness
        self._role_router = role_router
        self._lookup = service_lookup
        self._current_roles = dict(current_roles or {})
        self._producer = producer

    @property
    def service_id(self) -> str:
        return "live_quality.default"

    async def run_live_quality(
        self,
        role: str,
        benchmark_id: str,
        model_config: TournamentModelConfig,
        *,
        repetitions: int = 3,
        timeout_seconds: float = 120.0,
        retries: int = 2,
        criteria: QualificationCriteria | None = None,
    ) -> LiveQualityRun:
        from research_harness.research.routing.roles import validate_role

        validate_role(role)
        definition = BUILTIN_BENCHMARKS.get(benchmark_id)
        if definition is None:
            raise ValueError(f"unknown live-quality benchmark {benchmark_id!r}")
        await self._harness.register_benchmark(definition)

        started = datetime.now(UTC)
        task_results: list[LiveQualityTaskResult] = []
        calls: list[Any] = []
        critical_grounding = 0
        for rep in range(1, repetitions + 1):
            router = await self._make_router(
                role, benchmark_id, model_config, timeout_seconds, retries
            )
            try:
                run_id, report_id = await self._harness.run_benchmark(
                    benchmark_id,
                    model_router=router,
                    model_roles={role: model_config.requested_model},
                )
            except Exception:  # noqa: BLE001
                task_results.append(
                    LiveQualityTaskResult(
                        repetition=rep,
                        run_id="",
                        report_id="",
                        report_status="error",
                        cases_total=0,
                        cases_passed=0,
                        cases_failed=0,
                        cases_error=0,
                        task_pass_rate=0.0,
                        task_completed=False,
                        failure_count=1,
                    )
                )
                continue
            report = (await self._store.get(report_id)).parse_payload(EvaluationReport)
            critical_grounding += self._report_metric(report, "critical_grounding_failures")
            task_results.append(
                LiveQualityTaskResult(
                    repetition=rep,
                    run_id=run_id,
                    report_id=report_id,
                    report_status=report.status.value,
                    cases_total=report.cases_total,
                    cases_passed=report.cases_passed,
                    cases_failed=report.cases_failed,
                    cases_error=report.cases_error,
                    task_pass_rate=(
                        report.cases_passed / report.cases_total if report.cases_total else 0.0
                    ),
                    task_completed=report.cases_error == 0,
                    critical_grounding_failures=self._report_metric(
                        report, "critical_grounding_failures"
                    ),
                    latency_ms=report.execution_latency_ms,
                    failure_count=len(run_failures(report)),
                )
            )
            calls.extend(router.records)

        call_metrics = aggregate_calls(calls)
        pass_rates = [t.task_pass_rate for t in task_results]
        det_rates = [
            (t.cases_passed / (t.cases_passed + t.cases_failed))
            if (t.cases_passed + t.cases_failed)
            else 0.0
            for t in task_results
        ]
        provider_errors = sum(
            1
            for c in calls
            if c.status == "error"
            and (c.failure or "")
            and c.failure.value in _PROVIDER_ERROR_KINDS
        )
        structured_failures = sum(1 for c in calls if c.status == "structured_output_failure")

        result = LiveQualityModelResult(
            candidate_id=model_config.candidate_id,
            model=model_config.model_dump(mode="json"),
            resolved_model=call_metrics.get("resolved_model") or model_config.requested_model,
            role=role,
            benchmark_id=benchmark_id,
            benchmark_version=definition.version,
            repetitions=len(task_results),
            task_results=task_results,
            deterministic_pass_rate_mean=fmean(det_rates) if det_rates else None,
            deterministic_pass_rate_worst=min(det_rates) if det_rates else None,
            deterministic_pass_rate_variance=(pvariance(det_rates) if len(det_rates) > 1 else 0.0),
            case_pass_rate_mean=fmean(pass_rates) if pass_rates else None,
            structured_output_success_rate=call_metrics.get("structured_output_success_rate"),
            structured_output_failure_frequency=(
                structured_failures / len(calls) if calls else None
            ),
            provider_error_frequency=(provider_errors / len(calls)) if calls else None,
            model_error_rate=call_metrics.get("model_error_rate"),
            critical_grounding_failures=critical_grounding,
            latency_ms_p50_mean=call_metrics.get("latency_ms_p50"),
            total_tokens=call_metrics.get("total_tokens"),
            estimated_cost=call_metrics.get("estimated_cost"),
            failure_counts={k: int(v) for k, v in call_metrics.get("failure_counts", {}).items()},
        )

        from research_harness.research.routing.readiness import qualify_model

        criteria = criteria or criteria_for_role(role)
        qualified, reasons = qualify_model(result, criteria)
        result.qualification = qualified
        result.qualification_reasons = reasons

        leaderboard_id = await self._persist_leaderboard(result, criteria)

        run = LiveQualityRun(
            role=role,
            benchmark_id=benchmark_id,
            benchmark_version=definition.version,
            model=result.model,
            repetitions=result.repetitions,
            result=result,
            leaderboard_id=leaderboard_id,
            started_at=started,
            completed_at=datetime.now(UTC),
            metadata={"criteria": criteria.model_dump(mode="json")},
        )
        await self._store.put(
            ArtifactEnvelope.create(
                payload=run,
                artifact_type="live_quality_run",
                producer=self._producer,
                artifact_id=run.id,
            )
        )
        return run

    async def _make_router(
        self,
        role: str,
        benchmark_id: str,
        model_config: TournamentModelConfig,
        timeout: float,
        retries: int,
    ) -> Any:
        from research_harness.plugins.research.evaluation_model_tournament.plugin import (
            CandidateModelRouter,
        )

        return CandidateModelRouter(
            base_router=self._role_router,
            role=role,
            candidate=model_config,
            service_lookup=self._lookup,
            timeout_seconds=timeout,
            retries=retries,
            benchmark_id=benchmark_id,
        )

    async def _persist_leaderboard(
        self, result: LiveQualityModelResult, criteria: QualificationCriteria
    ) -> str:
        entry = LeaderboardEntry(
            candidate_id=result.candidate_id,
            model=result.model,
            resolved_model=result.resolved_model,
            rank=1,
            eligibility="eligible" if result.qualification else "not_eligible",
            eligibility_reason="; ".join(result.qualification_reasons)
            if not result.qualification
            else "live-quality qualified",
            deterministic_pass_rate=result.deterministic_pass_rate_mean,
            benchmark_pass_rate=result.case_pass_rate_mean,
            case_pass_rate=result.case_pass_rate_mean,
            structured_output_success_rate=result.structured_output_success_rate,
            model_error_rate=result.provider_error_frequency,
            latency_ms_p50=result.latency_ms_p50_mean,
            estimated_cost=result.estimated_cost,
            caveats=["live-quality evidence"] if not result.qualification else [],
        )
        board = RoleLeaderboard(
            role=result.role,
            plan_id=f"live-quality:{result.benchmark_id}",
            tournament_run_id="",
            plan_hash="live-quality",
            entries=[entry],
            metadata={"repetitions": result.repetitions, "benchmark_id": result.benchmark_id},
            evidence_type="live_quality_evidence",
        )
        await self._store.put(
            ArtifactEnvelope.create(
                payload=board,
                artifact_type="role_leaderboard",
                producer=self._producer,
                artifact_id=board.id,
            )
        )
        return board.id

    async def assess_readiness(
        self, role: str, criteria: QualificationCriteria | None = None
    ) -> RoutingReadinessAssessment:
        from research_harness.research.routing.roles import validate_role

        validate_role(role)
        from research_harness.research.routing.readiness import (
            assess_role_readiness,
            criteria_for_role,
            summary_for,
        )

        runs = [
            env.parse_payload(LiveQualityRun)
            for env in await self._store.list(artifact_type="live_quality_run")
            if env.payload.get("role") == role
        ]
        latest: dict[str, LiveQualityModelResult] = {}
        for run in runs:
            key = (
                run.result.resolved_model
                or run.result.model.get("requested_model")
                or run.result.candidate_id
            )
            existing = latest.get(key)
            if existing is None or run.result.evidence_timestamp > existing.evidence_timestamp:
                latest[key] = run.result

        criteria = criteria or criteria_for_role(role)
        configured = (self._current_roles.get(role) or {}).get("model")
        verdict = assess_role_readiness(
            latest,
            criteria,
            configured_model=configured,
            require_fallback=True,
        )

        assessment = RoutingReadinessAssessment(
            role=role,
            criteria=criteria,
            qualified=bool(verdict["qualified"]),
            reasons=list(verdict["reasons"]),
            qualified_models=list(verdict["qualified_models"]),
            fallback_qualified=bool(verdict["fallback_qualified"]),
            fallback_model=verdict["fallback_model"],
            configured_model=configured,
            evidence={cid: summary_for(r) for cid, r in latest.items()},
            unsafe_production_qualification=False,
        )
        await self._store.put(
            ArtifactEnvelope.create(
                payload=assessment,
                artifact_type="routing_readiness_assessment",
                producer=self._producer,
                artifact_id=assessment.id,
            )
        )
        return assessment

    async def get_run(self, run_id: str) -> LiveQualityRun:
        return (await self._store.get(run_id)).parse_payload(LiveQualityRun)

    def _report_metric(self, report: EvaluationReport, metric_id: str) -> int:
        for m in report.metrics:
            if m.metric_id == metric_id:
                return int(m.value)
        return 0


def run_failures(report: EvaluationReport) -> list[str]:
    return list((report.metadata or {}).get("failures") or [])


def criteria_for_role(role: str) -> QualificationCriteria:
    from research_harness.research.routing.readiness import criteria_for_role as _c

    return _c(role)


class LiveQualityPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="evaluation.live_quality",
            version="0.1.0",
            plugin_type="research",
            description=(
                "Live-quality model validation + routing readiness (Phase 7D.0); "
                "production routing is never enabled automatically"
            ),
            provides=["live_quality.default"],
            requires=[
                "evaluation_harness.default",
                "artifact_store.default",
                "model_router.default",
                "model_provider.openrouter",
            ],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        models_cfg: dict[str, Any] = {}
        if "models" in cfg:
            models_cfg = cfg["models"]
        elif "roles" in cfg:
            models_cfg = {"roles": cfg["roles"]}
        current_roles: dict[str, dict[str, str]] = {}
        for role, rcfg in (models_cfg.get("roles") or {}).items():
            if isinstance(rcfg, dict) and rcfg.get("model"):
                current_roles[str(role)] = {
                    "provider": str(rcfg.get("provider") or "openrouter"),
                    "model": str(rcfg["model"]),
                }

        def _lookup(name: str) -> Any:
            return ctx.require(name)

        service = LiveQualityService(
            artifact_store=ctx.require("artifact_store.default"),
            harness=ctx.require("evaluation_harness.default"),
            role_router=ctx.require("model_router.default"),
            service_lookup=_lookup,
            current_roles=current_roles,
        )
        ctx.register("live_quality.default", service)
