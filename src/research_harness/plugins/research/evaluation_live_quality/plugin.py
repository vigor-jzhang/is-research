"""Live-quality model validation + routing readiness (Phase 7D.0).

Runs the frozen live-quality benchmarks against a real model (via the
CandidateModelRouter over the production role router) with configurable
repetitions, aggregates the results into LiveQualityModelResult (with variance),
produces live-quality RoleLeaderboards (evidence_type=live_quality_evidence),
and computes per-role RoutingReadinessAssessment. Production routing is never
enabled automatically.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from statistics import fmean, pvariance
from typing import Any

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.benchmarks import BUILTIN_BENCHMARKS
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import (
    Benchmark,
    BenchmarkCase,
    EvaluationReport,
    EvaluatorResult,
    EvaluatorStatus,
)
from research_harness.research.schemas.live_quality import (
    FailureAttributionKind,
    LiveQualityModelResult,
    LiveQualityRun,
    LiveQualityTaskPerformance,
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

_BENCHMARK_BY_ROLE = {
    "fast": "live-quality-fast-v1",
    "reasoning": "live-quality-reasoning-v1",
    "critic": "live-quality-critic-v1",
}


class LiveQualityService:
    def __init__(
        self,
        *,
        artifact_store: Any,
        harness: Any,
        role_router: Any,
        service_lookup: Any,
        current_roles: dict[str, dict[str, str]] | None = None,
        candidates: dict[str, list[str]] | None = None,
        candidates_per_task: dict[str, list[str]] | None = None,
        repetitions: int = 3,
        preflight: dict[str, Any] | None = None,
        producer: str = _PRODUCER,
    ) -> None:
        self._store = artifact_store
        self._harness = harness
        self._role_router = role_router
        self._lookup = service_lookup
        self._current_roles = dict(current_roles or {})
        self._candidates = dict(candidates or {})
        self._candidates_per_task = dict(candidates_per_task or {})
        self._repetitions = repetitions
        self._preflight_cfg = dict(preflight or {})
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
        case_rates: dict[str, list[float]] = {}
        case_names: dict[str, str] = {}
        case_grounding: dict[str, int] = {}
        case_failures: dict[str, list[str]] = {}
        case_structured: dict[str, list[float]] = {}
        case_evidence: dict[str, list[dict[str, Any]]] = {}
        case_evidence_source: dict[str, str] = {}
        case_diagnostics: dict[str, dict[str, int]] = {}
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
            await self._collect_case_stats(
                report,
                case_rates,
                case_names,
                case_grounding,
                case_failures,
                case_structured,
                case_evidence,
                case_evidence_source,
                case_diagnostics,
            )
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

        from research_harness.research.routing.qualification import (
            stability_status,
        )
        from research_harness.research.routing.readiness import qualify_model

        criteria = criteria or criteria_for_role(role)
        self._apply_attribution(result, case_failures, benchmark_id)
        result.task_performance = self._build_task_performance(
            case_rates,
            case_names,
            case_grounding,
            case_failures,
            case_structured,
            case_evidence,
            case_evidence_source,
            case_diagnostics,
        )
        result.stability = stability_status(result, criteria)
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

    async def run_qualification_campaign(
        self,
        role: str,
        *,
        candidates: list[str] | None = None,
        repetitions: int | None = None,
        benchmark_id: str | None = None,
        tasks: list[str] | None = None,
    ) -> Any:
        """Run a live-model qualification campaign for a role: each candidate ->
        live-quality benchmark (>=3 reps) -> LiveQualityModelResult -> verdict.

        Uses config-driven candidates (no slugs hard-coded in service logic).
        Phase 7D.3B: `tasks` selects config-driven per-task candidate pools
        (candidates_per_task with role fallback, deduplicated) so remaining
        tasks are targeted without re-running qualified evidence/synthesis
        unnecessarily. Production routing stays disabled."""
        from research_harness.research.routing.qualification import (
            build_role_summary,
            candidate_result,
        )
        from research_harness.research.routing.readiness import criteria_for_role
        from research_harness.research.routing.roles import validate_role
        from research_harness.research.schemas.qualification import QualificationCampaign

        validate_role(role)
        if candidates is None:
            candidates = self._candidates_for_tasks(role, tasks)
        else:
            candidates = list(candidates)
        if not candidates:
            raise ValueError(f"no qualification candidates configured for role {role!r}")
        repetitions = repetitions or self._repetitions
        benchmark_id = benchmark_id or _BENCHMARK_BY_ROLE[role]
        criteria = criteria_for_role(role)

        candidate_results = []
        run_ids: list[str] = []
        leaderboard_ids: list[str] = []
        for slug in candidates:
            model_config = TournamentModelConfig(
                candidate_id=slug, provider="openrouter", requested_model=slug
            )
            run = await self.run_live_quality(
                role, benchmark_id, model_config, repetitions=repetitions
            )
            cr = candidate_result(
                run.result,
                criteria,
                live_quality_run_id=run.id,
                leaderboard_id=run.leaderboard_id,
            )
            candidate_results.append(cr)
            run_ids.append(run.id)
            if run.leaderboard_id:
                leaderboard_ids.append(run.leaderboard_id)

        summary = build_role_summary(
            candidate_results, criteria, benchmark_id=benchmark_id, repetitions=repetitions
        )
        campaign = QualificationCampaign(
            role=role,
            benchmark_id=benchmark_id,
            repetitions=repetitions,
            candidates=candidate_results,
            summary=summary,
            live_quality_run_ids=run_ids,
            leaderboard_ids=leaderboard_ids,
            criteria=criteria,
            metadata={"tasks": list(tasks or [])},
        )
        await self._store.put(
            ArtifactEnvelope.create(
                payload=campaign,
                artifact_type="qualification_campaign",
                producer=self._producer,
                artifact_id=campaign.id,
            )
        )
        return campaign

    async def get_campaign(self, campaign_id: str) -> Any:
        from research_harness.research.schemas.qualification import QualificationCampaign

        return (await self._store.get(campaign_id)).parse_payload(QualificationCampaign)

    async def list_campaigns(self, role: str | None = None) -> list[Any]:
        from research_harness.research.schemas.qualification import QualificationCampaign

        campaigns = [
            env.parse_payload(QualificationCampaign)
            for env in await self._store.list(artifact_type="qualification_campaign")
            if role is None or env.payload.get("role") == role
        ]
        campaigns.sort(key=lambda c: c.completed_at, reverse=True)
        return campaigns

    async def build_qualification_matrix(self, role: str | None = None) -> list[Any]:
        """Build (and persist) the production-qualification matrix (Phase 7D.2)
        from the latest campaign per role. Becomes the activation input for
        Phase 7D controlled routing."""
        from research_harness.research.routing.qualification import build_qualification_matrix
        from research_harness.research.routing.readiness import criteria_for_role
        from research_harness.research.schemas.qualification import (
            ProductionQualificationMatrix,
        )

        campaigns = await self.list_campaigns()
        latest_by_role: dict[str, Any] = {}
        for c in campaigns:
            if role is not None and c.role != role:
                continue
            if c.role not in latest_by_role or c.completed_at > latest_by_role[c.role].completed_at:
                latest_by_role[c.role] = c

        matrices: list[ProductionQualificationMatrix] = []
        for r, campaign in sorted(latest_by_role.items()):
            criteria = criteria_for_role(r)
            from research_harness.research.routing.qualification import candidate_result

            latest = await self._latest_live_results_for_role(r)
            candidate_results = [
                candidate_result(lr, criteria, live_quality_run_id=None) for lr in latest.values()
            ]
            if not candidate_results:
                continue
            # eligibility (same rule as build_role_summary): qualified + not unstable
            qualified = [c for c in candidate_results if c.qualified]
            ranked = sorted(
                qualified,
                key=lambda c: (
                    -(c.deterministic_pass_rate_mean or -1.0),
                    c.estimated_cost if c.estimated_cost is not None else float("inf"),
                    c.candidate_id,
                ),
            )
            primary = ranked[0].candidate_id if ranked else None
            for c in candidate_results:
                stable_ok = (c.stability or "unstable") != "unstable"
                c.primary_eligible = bool(c.qualified and stable_ok)
                c.fallback_eligible = bool(c.qualified and stable_ok and c.candidate_id != primary)
            matrix = build_qualification_matrix(
                candidate_results,
                role=r,
                benchmark_id=campaign.benchmark_id,
                repetitions=campaign.repetitions,
                criteria=criteria,
            )
            await self._store.put(
                ArtifactEnvelope.create(
                    payload=matrix,
                    artifact_type="production_qualification_matrix",
                    producer=self._producer,
                    artifact_id=matrix.id,
                )
            )
            matrices.append(matrix)
        return matrices

    async def get_matrix(self, matrix_id: str) -> Any:
        from research_harness.research.schemas.qualification import (
            ProductionQualificationMatrix,
        )

        return (await self._store.get(matrix_id)).parse_payload(ProductionQualificationMatrix)

    async def _live_results_for_campaign(self, campaign: Any) -> dict[str, Any]:
        """Reconstruct {candidate_id: LiveQualityModelResult} from a campaign's
        live-quality runs (full task performance incl. Phase 7D.3 fields)."""
        from research_harness.research.schemas.live_quality import LiveQualityRun

        live_results: dict[str, Any] = {}
        for run_id in campaign.live_quality_run_ids:
            try:
                run = (await self._store.get(run_id)).parse_payload(LiveQualityRun)
            except Exception:  # noqa: BLE001
                continue
            live_results[run.result.candidate_id] = run.result
        return live_results

    async def _latest_live_results_for_role(self, role: str) -> dict[str, Any]:
        """Latest LiveQualityModelResult per candidate across all the role's
        campaigns (Phase 7D.3D). Lets per-candidate campaigns be run
        incrementally (each persists on completion) while the task matrix
        aggregates the most recent evidence per candidate."""
        from research_harness.research.schemas.live_quality import LiveQualityRun

        campaigns = await self.list_campaigns(role=role)
        latest: dict[str, Any] = {}
        for campaign in campaigns:
            for run_id in campaign.live_quality_run_ids:
                try:
                    run = (await self._store.get(run_id)).parse_payload(LiveQualityRun)
                except Exception:  # noqa: BLE001
                    continue
                cid = run.result.candidate_id
                if (
                    cid not in latest
                    or run.result.evidence_timestamp > latest[cid].evidence_timestamp
                ):
                    latest[cid] = run.result
        return latest

    async def task_qualification(
        self,
        role: str,
        task: str | None = None,
    ) -> list[Any]:
        """Build (and persist) the role's TaskQualificationMatrix from the latest
        live-quality run per candidate across all of the role's campaigns
        (Phase 7D.3/7D.3D). Task-level qualification uses the same thresholds;
        it never implies role qualification. Optionally filter to one task."""
        from research_harness.research.routing.qualification import build_task_matrix
        from research_harness.research.routing.readiness import criteria_for_role
        from research_harness.research.routing.roles import validate_role
        from research_harness.research.routing.tasks import tasks_for_role

        validate_role(role)
        campaigns = await self.list_campaigns(role=role)
        if not campaigns:
            raise ValueError(
                f"no qualification campaign for role {role!r}; run `routing qualify` first"
            )
        live_results = await self._latest_live_results_for_role(role)
        if not live_results:
            raise ValueError(f"no live-quality runs available for role {role!r} campaign")
        criteria = criteria_for_role(role)
        matrix, _rows = build_task_matrix(
            live_results,
            role=role,
            benchmark_id=campaigns[0].benchmark_id,
            repetitions=campaigns[0].repetitions,
            criteria=criteria,
        )
        await self._store.put(
            ArtifactEnvelope.create(
                payload=matrix,
                artifact_type="task_qualification_matrix",
                producer=self._producer,
                artifact_id=matrix.id,
            )
        )
        if task is not None:
            tasks_for_role(role)  # validate role's task set
            if task not in tasks_for_role(role):
                raise ValueError(
                    f"unknown task {task!r} for role {role!r}; expected {tasks_for_role(role)}"
                )
            return [r for r in matrix.rows if r.task == task]
        return matrix.rows

    async def get_task_matrix(self, matrix_id: str) -> Any:
        from research_harness.research.schemas.qualification import TaskQualificationMatrix

        return (await self._store.get(matrix_id)).parse_payload(TaskQualificationMatrix)

    async def capability_profile(self, model_id: str) -> list[Any]:
        """Build ModelCapabilityProfile(s) for a model across roles with
        campaigns (Phase 7D.3)."""
        from research_harness.research.routing.qualification import (
            build_model_capability_profiles,
            build_task_matrix,
        )
        from research_harness.research.schemas.qualification import ModelCapabilityProfile

        profiles: list[ModelCapabilityProfile] = []
        for role in ("fast", "reasoning", "critic"):
            campaigns = await self.list_campaigns(role=role)
            if not campaigns:
                continue
            campaign = campaigns[0]
            live_results = await self._live_results_for_campaign(campaign)
            if model_id not in live_results:
                continue
            matrix, _rows = build_task_matrix(
                live_results,
                role=role,
                benchmark_id=campaign.benchmark_id,
                repetitions=campaign.repetitions,
            )
            stability_by_model = {c.candidate_id: c.stability for c in campaign.candidates}
            latency_by_model = {c.candidate_id: c.latency_ms_p50 for c in campaign.candidates}
            cost_by_model = {c.candidate_id: c.estimated_cost for c in campaign.candidates}
            tokens_by_model = {c.candidate_id: c.total_tokens for c in campaign.candidates}
            for profile in build_model_capability_profiles(
                matrix,
                stability_by_model=stability_by_model,
                latency_by_model=latency_by_model,
                cost_by_model=cost_by_model,
                tokens_by_model=tokens_by_model,
            ):
                if profile.model == model_id:
                    profiles.append(profile)
        return profiles

    def _report_metric(self, report: EvaluationReport, metric_id: str) -> int:
        for m in report.metrics:
            if m.metric_id == metric_id:
                return int(m.value)
        return 0

    # ------------------------------------------------------------------
    # Phase 7D.3B: provider/model preflight + remaining-task coverage
    # ------------------------------------------------------------------

    async def preflight(self, role: str | None = None, model: str | None = None) -> list[Any]:
        """Run the lightweight provider/model capability preflight (Phase 7D.3B).

        Probes each candidate (config-driven; no slugs in service logic) for
        reachability, structured JSON output, required context size, and the
        timeout/retry path. Classifies: available | temporarily_unavailable |
        capability_mismatch | provider_error. A provider-unavailable model is
        never interpreted as academically incapable and is never qualified."""
        from research_harness.research.routing.preflight import (
            preflight_required_context_chars,
            run_candidate_preflight,
        )
        from research_harness.research.schemas.qualification import ModelPreflight
        from research_harness.research.schemas.tournament import TournamentModelConfig

        roles = [role] if role else ("fast", "reasoning", "critic")
        preflights: list[ModelPreflight] = []
        for r in roles:
            slugs = [model] if model else self._preflight_candidates_for_role(r)
            if not slugs:
                continue
            benchmark_id = _BENCHMARK_BY_ROLE[r]
            cases = await self._benchmark_case_inputs(benchmark_id)
            required_context = await preflight_required_context_chars(role=r, benchmark_cases=cases)
            for slug in slugs:
                candidate = TournamentModelConfig(
                    candidate_id=slug, provider="openrouter", requested_model=slug
                )
                preflight = await run_candidate_preflight(
                    role=r,
                    candidate=candidate,
                    service_lookup=self._lookup,
                    base_router=self._role_router,
                    timeout_seconds=float(self._preflight_cfg.get("timeout_seconds", 120.0)),
                    retries=int(self._preflight_cfg.get("retries", 2)),
                    required_context_chars=required_context,
                    probe_max_tokens=int(self._preflight_cfg.get("probe_max_tokens", 200)),
                )
                await self._store.put(
                    ArtifactEnvelope.create(
                        payload=preflight,
                        artifact_type="model_preflight",
                        producer=self._producer,
                        artifact_id=preflight.id,
                    )
                )
                preflights.append(preflight)
        return preflights

    async def _benchmark_case_inputs(self, benchmark_id: str) -> list[dict[str, Any]]:
        """Load a role's live-quality benchmark case inputs for context sizing.

        Prefers the registered benchmark; falls back to the builtin definition
        so preflight works before the benchmark has been registered on a store."""
        from research_harness.research.benchmarks import BUILTIN_BENCHMARKS

        definition = BUILTIN_BENCHMARKS.get(benchmark_id)
        cases: list[dict[str, Any]] = []
        try:
            benchmark_env = await self._store.get(benchmark_id)
        except Exception:  # noqa: BLE001
            benchmark_env = None
        if benchmark_env is not None:
            benchmark = benchmark_env.parse_payload(Benchmark)
            for case_id in benchmark.case_ids:
                try:
                    env = await self._store.get(case_id)
                except Exception:  # noqa: BLE001
                    continue
                case = env.parse_payload(BenchmarkCase)
                cases.append(case.model_dump(mode="json"))
        elif definition is not None:
            cases = [asdict(c) for c in definition.cases]
        return cases

    def _preflight_candidates_for_role(self, role: str) -> list[str]:
        """Union of the role-level candidates and every per-task pool for the
        role (Phase 7D.3C), so stronger per-task candidates are preflighted too.
        Config-driven; no slugs in service logic."""
        from research_harness.research.routing.tasks import tasks_for_role

        seen: dict[str, None] = {}
        for slug in self._candidates.get(role, []):
            seen.setdefault(slug, None)
        for task in tasks_for_role(role):
            for slug in self._candidates_per_task.get(task, []):
                seen.setdefault(slug, None)
        return list(seen)

    def _candidates_for_tasks(self, role: str, tasks: list[str] | None) -> list[str]:
        """Config-driven candidate selection for the requested tasks.

        Prefers `live_quality.candidates_per_task[task]` and falls back to the
        role candidate list; candidates are deduplicated so each model is
        tested once. No model slugs in service logic."""
        from research_harness.research.routing.tasks import tasks_for_role

        if not tasks:
            return list(self._candidates.get(role, []))
        requested = {str(t) for t in tasks}
        unknown = [t for t in requested if t not in set(tasks_for_role(role))]
        if unknown:
            raise ValueError(f"unknown tasks for role {role!r}: {sorted(unknown)}")
        slugs: list[str] = []
        for task in tasks_for_role(role):
            if task not in requested:
                continue
            slugs.extend(self._candidates_per_task.get(task) or self._candidates.get(role, []))
        seen: set[str] = set()
        ordered: list[str] = []
        for slug in slugs:
            if slug not in seen:
                seen.add(slug)
                ordered.append(slug)
        return ordered

    async def remaining_task_coverage(self) -> Any:
        """Build (and persist) the RemainingTaskCoverage (Phase 7D.3B)."""
        from research_harness.research.routing.qualification import (
            build_remaining_task_coverage,
        )
        from research_harness.research.schemas.qualification import (
            ModelPreflight,
            QualificationCampaign,
            TaskQualificationMatrix,
        )

        campaigns = [
            env.parse_payload(QualificationCampaign)
            for env in await self._store.list(artifact_type="qualification_campaign")
        ]
        matrices: list[Any] = []
        latest: dict[str, Any] = {}
        for env in await self._store.list(artifact_type="task_qualification_matrix"):
            matrix = env.parse_payload(TaskQualificationMatrix)
            if matrix.role not in latest or matrix.created_at > latest[matrix.role].created_at:
                latest[matrix.role] = matrix
        matrices = list(latest.values())
        preflights = [
            env.parse_payload(ModelPreflight)
            for env in await self._store.list(artifact_type="model_preflight")
        ]
        coverage = build_remaining_task_coverage(
            matrices, preflights=preflights, campaigns=campaigns
        )
        await self._store.put(
            ArtifactEnvelope.create(
                payload=coverage,
                artifact_type="remaining_task_coverage",
                producer=self._producer,
                artifact_id=coverage.id,
            )
        )
        return coverage

    # ------------------------------------------------------------------
    # Phase 7D.2: per-task performance + failure attribution
    # ------------------------------------------------------------------

    async def _collect_case_stats(
        self,
        report: EvaluationReport,
        case_rates: dict[str, list[float]],
        case_names: dict[str, str],
        case_grounding: dict[str, int],
        case_failures: dict[str, list[str]],
        case_structured: dict[str, list[float]],
        case_evidence: dict[str, list[dict[str, Any]]],
        case_evidence_source: dict[str, str],
        case_diagnostics: dict[str, dict[str, int]],
    ) -> None:
        """Accumulate per-case pass rates, names, grounding counts, structured-
        output success, failure texts, evidence artifacts, and task-specific
        diagnostics (Phase 7D.3B) across repetitions from the stored report."""
        for cr in report.case_results:
            case_rates.setdefault(cr.case_id, []).append(
                1.0 if cr.status.value == "passed" else 0.0
            )
            case_names.setdefault(cr.case_id, cr.case_name)
            if cr.status.value != "passed":
                texts = await self._case_failure_texts(cr.evaluator_result_ids)
                if cr.error:
                    texts.append(str(cr.error))
                case_failures.setdefault(cr.case_id, []).extend(texts)
                case_grounding[cr.case_id] = case_grounding.get(cr.case_id, 0) + (
                    await self._evaluator_grounding(cr.evaluator_result_ids)
                )
            structured = (cr.metrics or {}).get("structured_output_success")
            if structured is not None:
                case_structured.setdefault(cr.case_id, []).append(float(structured))
            diag = await self._evaluator_diagnostics(cr.evaluator_result_ids)
            if diag:
                merged = case_diagnostics.setdefault(cr.case_id, {})
                for key, value in diag.items():
                    merged[key] = merged.get(key, 0) + int(value)
            if cr.case_id == "lq-evidence-extraction":
                evidence, source = await self._evidence_diagnostics_inputs(cr.produced_artifact_ids)
                if evidence:
                    case_evidence.setdefault(cr.case_id, []).extend(evidence)
                if source:
                    case_evidence_source[cr.case_id] = source

    async def _evaluator_diagnostics(self, evaluator_result_ids: list[str]) -> dict[str, int]:
        """Collect Phase 7D.3B task-specific diagnostics from evaluator values."""
        merged: dict[str, int] = {}
        for rid in evaluator_result_ids:
            try:
                env = await self._store.get(rid)
                result = env.parse_payload(EvaluatorResult)
            except Exception:  # noqa: BLE001
                continue
            for key, value in ((result.value or {}).get("task_diagnostics") or {}).items():
                merged[str(key)] = merged.get(str(key), 0) + int(value)
        return merged

    async def _evidence_diagnostics_inputs(
        self, produced_artifact_ids: list[str]
    ) -> tuple[list[dict[str, Any]], str]:
        evidence: list[dict[str, Any]] = []
        source_text = ""
        for aid in produced_artifact_ids:
            try:
                env = await self._store.get(aid)
            except Exception:  # noqa: BLE001
                continue
            payload = env.payload
            if env.artifact_type == "evidence_item":
                evidence.append(dict(payload))
            elif env.artifact_type in ("document", "paper_record"):
                text_parts: list[str] = []
                for page in payload.get("pages") or []:
                    text_parts.append(str(page.get("text") or ""))
                source_text += " ".join(text_parts)
        return evidence, source_text

    async def _case_failure_texts(self, evaluator_result_ids: list[str]) -> list[str]:
        texts: list[str] = []
        for rid in evaluator_result_ids:
            try:
                env = await self._store.get(rid)
                result = env.parse_payload(EvaluatorResult)
            except Exception:  # noqa: BLE001
                continue
            if result.status != EvaluatorStatus.passed and result.explanation:
                texts.append(result.explanation)
        return texts

    async def _evaluator_grounding(self, evaluator_result_ids: list[str]) -> int:
        total = 0
        for rid in evaluator_result_ids:
            try:
                env = await self._store.get(rid)
                result = env.parse_payload(EvaluatorResult)
            except Exception:  # noqa: BLE001
                continue
            total += int((result.value or {}).get("critical_grounding_failures") or 0)
        return total

    def _apply_attribution(
        self, result: LiveQualityModelResult, case_failures: dict[str, list[str]], benchmark_id: str
    ) -> None:
        """Attribute case failures into genuine vs excluded (confirmed
        benchmark/evaluator defect) buckets, then merge call-level failures."""
        from research_harness.research.benchmarks.calibration import confirmed_defect_map
        from research_harness.research.routing.qualification import attribute_failures

        defect_cases = {
            cid for (bid, cid), _kind in confirmed_defect_map().items() if bid == benchmark_id
        }
        attribution: dict[str, int] = {}
        excluded: dict[str, int] = {}
        for case_id, texts in case_failures.items():
            a, e = attribute_failures(texts, defect_case_ids=defect_cases, case_id=case_id)
            for kind, count in a.items():
                attribution[kind] = attribution.get(kind, 0) + count
            for kind, count in e.items():
                excluded[kind] = excluded.get(kind, 0) + count
        # call-level failures (structured output + provider errors)
        call_failure_to_kind = {
            "structured_output_failure": FailureAttributionKind.structured_output_failure.value,
            "timeout": FailureAttributionKind.timeout.value,
            "rate_limit": FailureAttributionKind.rate_limit.value,
            "provider_error": FailureAttributionKind.provider_error.value,
            "validation_failure": FailureAttributionKind.provider_error.value,
        }
        for kind, count in (result.failure_counts or {}).items():
            mapped = call_failure_to_kind.get(str(kind))
            if mapped:
                attribution[mapped] = attribution.get(mapped, 0) + int(count)
        result.failure_attribution = attribution
        result.excluded_failure_attribution = excluded

    def _build_task_performance(
        self,
        case_rates: dict[str, list[float]],
        case_names: dict[str, str],
        case_grounding: dict[str, int],
        case_failures: dict[str, list[str]],
        case_structured: dict[str, list[float]] | None = None,
        case_evidence: dict[str, list[dict[str, Any]]] | None = None,
        case_evidence_source: dict[str, str] | None = None,
        case_diagnostics: dict[str, dict[str, int]] | None = None,
    ) -> list[LiveQualityTaskPerformance]:
        from research_harness.research.routing.qualification import (
            attribute_failure_text,
            evidence_extraction_diagnostics,
        )

        case_structured = case_structured or {}
        case_evidence = case_evidence or {}
        case_evidence_source = case_evidence_source or {}
        case_diagnostics = case_diagnostics or {}
        performance: list[LiveQualityTaskPerformance] = []
        for case_id in sorted(case_rates):
            rates = case_rates[case_id]
            attribution: dict[str, int] = {}
            for text in case_failures.get(case_id, []):
                kind = attribute_failure_text(text).value
                attribution[kind] = attribution.get(kind, 0) + 1
            provider_errors = sum(
                attribution.get(kind, 0)
                for kind in (
                    FailureAttributionKind.provider_error.value,
                    FailureAttributionKind.timeout.value,
                    FailureAttributionKind.rate_limit.value,
                )
            )
            structured_rates = case_structured.get(case_id, [])
            performance.append(
                LiveQualityTaskPerformance(
                    task_id=case_id,
                    task_name=case_names.get(case_id, ""),
                    repetitions=len(rates),
                    pass_rate_mean=fmean(rates) if rates else None,
                    pass_rate_worst=min(rates) if rates else None,
                    pass_rate_variance=(pvariance(rates) if len(rates) > 1 else 0.0),
                    pass_rates=list(rates),
                    structured_output_success_rate=(
                        fmean(structured_rates) if structured_rates else None
                    ),
                    provider_error_frequency=(provider_errors / len(rates) if rates else None),
                    critical_grounding_failures=case_grounding.get(case_id, 0),
                    failure_attribution=attribution,
                    evidence_diagnostics=evidence_extraction_diagnostics(
                        case_failures.get(case_id, []),
                        produced_evidence=case_evidence.get(case_id),
                        source_text=case_evidence_source.get(case_id, ""),
                    ),
                    task_diagnostics=dict(case_diagnostics.get(case_id, {})),
                )
            )
        return performance


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

        live_cfg = cfg.get("live_quality") or {}
        candidates = {
            str(role): [str(m) for m in models]
            for role, models in (live_cfg.get("candidates") or {}).items()
        }
        candidates_per_task = {
            str(task): [str(m) for m in models]
            for task, models in (live_cfg.get("candidates_per_task") or {}).items()
        }
        repetitions = int(live_cfg.get("repetitions") or 3)
        preflight = dict(live_cfg.get("preflight") or {})

        service = LiveQualityService(
            artifact_store=ctx.require("artifact_store.default"),
            harness=ctx.require("evaluation_harness.default"),
            role_router=ctx.require("model_router.default"),
            service_lookup=_lookup,
            current_roles=current_roles,
            candidates=candidates,
            candidates_per_task=candidates_per_task,
            repetitions=repetitions,
            preflight=preflight,
        )
        ctx.register("live_quality.default", service)
