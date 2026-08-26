"""Phase 7B offline integration — model tournaments + role leaderboards.

Runs the REAL tournament service over the frozen benchmarks with a scripted
fake provider (no network). The fake provider serves the benchmarks' own
fixture responses so deterministic evaluators see correct scripted output,
while per-model behavior (correctness, latency, usage, cost, failures,
structured-output failures, retries) is injected independently for each
candidate. Every scenario stays fully offline.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import pytest

from research_harness.app.bootstrap import build_runtime
from research_harness.config.loader import load_config_from_dict
from research_harness.contracts.common import Usage
from research_harness.contracts.model import Message, ModelRequest, ModelResponse
from research_harness.kernel.errors import ModelError
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.benchmarks import BUILTIN_BENCHMARKS
from research_harness.research.schemas.tournament import (
    RoleLeaderboard,
    TournamentPlan,
    TournamentRun,
)

ALL_EVALUATORS = [
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
]


class ModelBehavior:
    def __init__(
        self,
        correctness: float = 1.0,
        latency_ms: float = 40.0,
        latency_jitter: float = 12.0,
        prompt_tokens: int = 120,
        completion_tokens: int = 80,
        cost: float | None = None,
        fail_first_n: int = 0,
        rate_limit_first_n: int = 0,
        invalid_json_rate: float = 0.0,
    ) -> None:
        self.correctness = correctness
        self.latency_ms = latency_ms
        self.latency_jitter = latency_jitter
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cost = cost
        self.fail_first_n = fail_first_n
        self.rate_limit_first_n = rate_limit_first_n
        self.invalid_json_rate = invalid_json_rate


class FakeProviderPlugin(Plugin):
    """Fake model_provider.openrouter serving benchmark fixture responses with
    per-model behavior. Fully offline and deterministic."""

    def __init__(self, fixtures: list[dict], behaviors: dict[str, ModelBehavior]) -> None:
        self._fixtures = list(fixtures)
        self._behaviors = behaviors
        self.calls: list[ModelRequest] = []
        self.call_count_by_model: dict[str, int] = {}

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="model.openrouter",
            version="0.1.0",
            plugin_type="model",
            description="fake provider for offline tournament tests",
            provides=["model_provider.openrouter"],
            requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("model_provider.openrouter", self._provider())

    def _provider(self) -> object:
        class _Provider:
            capabilities = None

            async def complete(self_, request: ModelRequest) -> ModelResponse:
                return self._complete(request)

            async def close(self_):
                pass

        return _Provider()

    def _behavior(self, model: str) -> ModelBehavior:
        return self._behaviors.get(model, ModelBehavior())

    def _h(self, seed: str) -> int:
        return int.from_bytes(hashlib.sha256(seed.encode()).digest()[:4], "big")

    def _complete(self, request: ModelRequest) -> ModelResponse:
        model = str(request.metadata.get("model") or "unknown")
        self.calls.append(request)
        self.call_count_by_model[model] = self.call_count_by_model.get(model, 0) + 1
        count = self.call_count_by_model[model]
        beh = self._behavior(model)
        text = " ".join(m.content or "" for m in request.messages)

        if beh.fail_first_n and count <= beh.fail_first_n:
            raise ModelError(f"OpenRouter request timed out after 30s (call {count})")
        if beh.rate_limit_first_n and count <= beh.rate_limit_first_n:
            raise ModelError("OpenRouter rate limited (429)")

        fixture = next((f for f in self._fixtures if f.get("match") and f["match"] in text), None)
        response: object = (fixture or {}).get("response", {})
        h = self._h(f"{model}|{text}")
        if beh.invalid_json_rate > 0 and (h % 100) / 100.0 < beh.invalid_json_rate:
            content = "not valid json at all"
        elif (h % 100) / 100.0 >= beh.correctness:
            content = "{}"
        else:
            content = json.dumps(response)

        latency = beh.latency_ms + (h % (int(beh.latency_jitter) + 1))
        usage = Usage(
            prompt_tokens=beh.prompt_tokens,
            completion_tokens=beh.completion_tokens,
            total_tokens=beh.prompt_tokens + beh.completion_tokens,
            cost=beh.cost,
        )
        return ModelResponse(
            message=Message(role="assistant", content=content),
            tool_calls=[],
            finish_reason="stop",
            model=model,
            provider="openrouter",
            usage=usage,
            latency_ms=float(latency),
        )


def _fixtures_for(benchmark_ids: list[str]) -> list[dict]:
    fixtures: list[dict] = []
    for bid in benchmark_ids:
        for case in BUILTIN_BENCHMARKS[bid].cases:
            fixtures.extend(case.input.get("llm_fixtures") or [])
    return fixtures


def _build_runtime(
    tmp_path: pathlib.Path, behaviors: dict[str, ModelBehavior], fixtures: list[dict]
):
    cfg = load_config_from_dict(
        {
            "runtime": {"autonomy": "high"},
            "plugins": [
                "storage.artifacts_sqlite",
                "storage.blobs_filesystem",
                "literature.ingestion",
                "literature.identity_resolver",
                "routing.role_router",
                "research.evaluation_harness",
                "evaluation.model_tournament",
                *ALL_EVALUATORS,
            ],
            "artifacts": {"store": "sqlite", "path": str(tmp_path / "artifacts.db")},
            "documents": {"blob_root": str(tmp_path / "blobs")},
            "models": {
                "roles": {
                    "fast": {"provider": "openrouter", "model": "fallback-fast"},
                    "reasoning": {"provider": "openrouter", "model": "fallback-reasoning"},
                    "critic": {"provider": "openrouter", "model": "fallback-critic"},
                    "long_context": {"provider": "openrouter", "model": "fallback-long"},
                }
            },
        }
    )
    provider = FakeProviderPlugin(fixtures=fixtures, behaviors=behaviors)
    return build_runtime(cfg, extra_plugins=[provider]), provider


def _plan(
    *,
    role: str,
    benchmarks: list[str],
    models: list[dict],
    repetitions: int = 1,
    retries: int = 2,
    threshold: float = 0.85,
    pricing: bool = False,
) -> TournamentPlan:
    return TournamentPlan.model_validate(
        {
            "name": "offline test",
            "role": role,
            "benchmark_ids": benchmarks,
            "repetitions": repetitions,
            "retries": retries,
            "deterministic_pass_threshold": threshold,
            "models": models,
        }
    )


async def _run_tournament(runtime, plan: TournamentPlan) -> TournamentRun:
    svc = runtime.services.require("model_tournament.default")
    return await svc.run_tournament(plan)


SCREENING = ["literature-screening-v1"]


@pytest.mark.asyncio
async def test_two_models_different_correctness_ranked_correctly(tmp_path):
    behaviors = {
        "good-model": ModelBehavior(correctness=1.0, cost=0.001),
        "bad-model": ModelBehavior(correctness=0.5, cost=0.0001),
    }
    runtime, _provider = _build_runtime(tmp_path, behaviors, _fixtures_for(SCREENING))
    async with runtime:
        plan = _plan(
            role="fast",
            benchmarks=SCREENING,
            models=[
                {"candidate_id": "good", "requested_model": "good-model"},
                {"candidate_id": "bad", "requested_model": "bad-model"},
            ],
        )
        run = await _run_tournament(runtime, plan)
        assert run.status == "completed"
        by_id = {mr.candidate_id: mr for mr in run.model_results}
        good = by_id["good"]
        bad = by_id["bad"]
        assert good.deterministic_pass_rate is not None
        assert bad.deterministic_pass_rate is not None
        assert good.deterministic_pass_rate > bad.deterministic_pass_rate
        assert good.case_pass_rate > bad.case_pass_rate

        board = (
            await runtime.services.require("artifact_store.default").get(run.leaderboard_id)
        ).parse_payload(RoleLeaderboard)
        ranks = {e.candidate_id: e.rank for e in board.entries}
        assert ranks["good"] == 1
        assert ranks["bad"] == 2
        # cheaper-but-failing must NOT outrank the correct model
        assert board.entries[0].candidate_id == "good"
        assert board.entries[0].eligibility == "eligible"


@pytest.mark.asyncio
async def test_correct_but_slower_outranks_cheap_failing(tmp_path):
    behaviors = {
        "slow-correct": ModelBehavior(correctness=1.0, latency_ms=400.0, cost=0.002),
        "fast-cheap-failing": ModelBehavior(correctness=0.3, latency_ms=20.0, cost=0.00001),
    }
    runtime, _ = _build_runtime(tmp_path, behaviors, _fixtures_for(SCREENING))
    async with runtime:
        plan = _plan(
            role="fast",
            benchmarks=SCREENING,
            models=[
                {"candidate_id": "slow", "requested_model": "slow-correct"},
                {"candidate_id": "cheapfail", "requested_model": "fast-cheap-failing"},
            ],
        )
        run = await _run_tournament(runtime, plan)
        board = (
            await runtime.services.require("artifact_store.default").get(run.leaderboard_id)
        ).parse_payload(RoleLeaderboard)
        assert board.entries[0].candidate_id == "slow"
        assert board.entries[0].latency_ms_p50 is not None
        assert board.entries[1].latency_ms_p50 is not None
        assert board.entries[0].latency_ms_p50 > board.entries[1].latency_ms_p50
        # latency never outranks correctness
        assert board.entries[0].deterministic_pass_rate > board.entries[1].deterministic_pass_rate


@pytest.mark.asyncio
async def test_retries_and_latency_aggregation(tmp_path):
    behaviors = {
        "retry-model": ModelBehavior(correctness=1.0, fail_first_n=2, latency_ms=50.0),
    }
    runtime, provider = _build_runtime(tmp_path, behaviors, _fixtures_for(SCREENING))
    async with runtime:
        plan = _plan(
            role="fast",
            benchmarks=SCREENING,
            models=[{"candidate_id": "retry", "requested_model": "retry-model"}],
            retries=2,
        )
        run = await _run_tournament(runtime, plan)
        mr = run.model_results[0]
        # first two attempts per call raised; router retried them
        assert mr.retry_rate is not None and mr.retry_rate > 0
        assert mr.model_error_rate is not None and mr.model_error_rate == 0.0  # all recovered
        assert mr.latency_ms_mean is not None and mr.latency_ms_mean > 0
        assert mr.latency_ms_p50 is not None and mr.latency_ms_p95 is not None
        assert mr.latency_ms_p95 >= mr.latency_ms_p50
        assert mr.structured_output_success_rate == 1.0


@pytest.mark.asyncio
async def test_token_and_cost_aggregation_and_missing_cost(tmp_path):
    behaviors = {
        "priced-model": ModelBehavior(
            correctness=1.0, prompt_tokens=200, completion_tokens=100, cost=0.005
        ),
        "unpriced-model": ModelBehavior(
            correctness=1.0, prompt_tokens=200, completion_tokens=100, cost=None
        ),
    }
    runtime, _ = _build_runtime(tmp_path, behaviors, _fixtures_for(SCREENING))
    async with runtime:
        plan = _plan(
            role="fast",
            benchmarks=SCREENING,
            models=[
                {"candidate_id": "priced", "requested_model": "priced-model"},
                {"candidate_id": "unpriced", "requested_model": "unpriced-model"},
            ],
        )
        run = await _run_tournament(runtime, plan)
        by_id = {mr.candidate_id: mr for mr in run.model_results}
        priced = by_id["priced"]
        unpriced = by_id["unpriced"]
        assert priced.input_tokens and priced.input_tokens > 0
        assert priced.output_tokens and priced.output_tokens > 0
        assert priced.total_tokens == priced.input_tokens + priced.output_tokens
        assert priced.estimated_cost is not None and priced.estimated_cost > 0
        assert priced.cost_per_successful_case is not None
        # unpriced: cost stays unknown (never invented)
        assert unpriced.estimated_cost is None
        assert unpriced.cost_per_successful_case is None
        board = (
            await runtime.services.require("artifact_store.default").get(run.leaderboard_id)
        ).parse_payload(RoleLeaderboard)
        unpriced_entry = next(e for e in board.entries if e.candidate_id == "unpriced")
        assert any("cost unknown" in c for c in unpriced_entry.caveats)


@pytest.mark.asyncio
async def test_pricing_from_plan_when_provider_cost_missing(tmp_path):
    behaviors = {
        "plan-priced": ModelBehavior(
            correctness=1.0, prompt_tokens=1000, completion_tokens=500, cost=None
        ),
    }
    runtime, _ = _build_runtime(tmp_path, behaviors, _fixtures_for(SCREENING))
    async with runtime:
        plan = _plan(
            role="fast",
            benchmarks=SCREENING,
            models=[
                {
                    "candidate_id": "pp",
                    "requested_model": "plan-priced",
                    "pricing": {
                        "source": "test-catalog",
                        "version": "1",
                        "input_per_million": 1.0,
                        "output_per_million": 2.0,
                    },
                }
            ],
        )
        run = await _run_tournament(runtime, plan)
        mr = run.model_results[0]
        prompt = sum(int(c.prompt_tokens or 0) for c in mr.calls)
        completion = sum(int(c.completion_tokens or 0) for c in mr.calls)
        expected = (prompt * 1.0 + completion * 2.0) / 1_000_000
        assert mr.estimated_cost is not None and abs(mr.estimated_cost - expected) < 1e-9
        assert mr.calls[0].cost_source == "pricing"


@pytest.mark.asyncio
async def test_structured_output_failures_measured(tmp_path):
    behaviors = {
        "sloppy-model": ModelBehavior(correctness=1.0, invalid_json_rate=0.4),
    }
    runtime, _ = _build_runtime(tmp_path, behaviors, _fixtures_for(SCREENING))
    async with runtime:
        plan = _plan(
            role="fast",
            benchmarks=SCREENING,
            models=[{"candidate_id": "sloppy", "requested_model": "sloppy-model"}],
        )
        run = await _run_tournament(runtime, plan)
        mr = run.model_results[0]
        assert mr.structured_output_success_rate is not None
        assert 0.0 < mr.structured_output_success_rate < 1.0
        assert mr.failure_counts.get("structured_output_failure", 0) > 0


@pytest.mark.asyncio
async def test_repeated_runs_preserved_and_reproducible(tmp_path):
    behaviors = {"stable-model": ModelBehavior(correctness=1.0)}
    runtime, _ = _build_runtime(tmp_path, behaviors, _fixtures_for(SCREENING))
    async with runtime:
        plan = _plan(
            role="fast",
            benchmarks=SCREENING,
            models=[{"candidate_id": "stable", "requested_model": "stable-model"}],
            repetitions=2,
        )
        run = await _run_tournament(runtime, plan)
        mr = run.model_results[0]
        # two EvaluationRuns per benchmark preserved, never overwritten
        assert len(mr.benchmark_runs) == 2
        assert len({ref.run_id for ref in mr.benchmark_runs}) == 2
        assert {ref.repetition for ref in mr.benchmark_runs} == {1, 2}
        assert mr.deterministic_pass_rate == 1.0
        # deterministic rerun reproducibility: identical plan + behavior => same outcome
        plan2 = _plan(
            role="fast",
            benchmarks=SCREENING,
            models=[{"candidate_id": "stable", "requested_model": "stable-model"}],
            repetitions=2,
        )
        run2 = await _run_tournament(runtime, plan2)
        assert (
            run2.model_results[0].deterministic_pass_rate
            == run.model_results[0].deterministic_pass_rate
        )
        assert run2.id != run.id  # a later tournament is a new artifact


@pytest.mark.asyncio
async def test_role_separation_fast_vs_reasoning(tmp_path):
    behaviors = {
        "cand": ModelBehavior(correctness=1.0),
        "fallback-fast": ModelBehavior(correctness=1.0),
        "fallback-reasoning": ModelBehavior(correctness=1.0),
        "fallback-critic": ModelBehavior(correctness=1.0),
        "fallback-long": ModelBehavior(correctness=1.0),
    }
    runtime, _ = _build_runtime(tmp_path, behaviors, _fixtures_for(SCREENING))
    async with runtime:
        plan = _plan(
            role="fast",
            benchmarks=SCREENING,
            models=[{"candidate_id": "cand", "requested_model": "cand"}],
        )
        run = await _run_tournament(runtime, plan)
        mr = run.model_results[0]
        # only the bound role's calls are measured for the candidate
        assert all(c.role == "fast" for c in mr.calls)
        assert len(mr.calls) > 0


@pytest.mark.asyncio
async def test_reasoning_role_novelty_threat(tmp_path):
    behaviors = {
        "reasoning-cand": ModelBehavior(correctness=1.0),
        "fallback-critic": ModelBehavior(correctness=1.0),
        "fallback-reasoning": ModelBehavior(correctness=1.0),
        "fallback-fast": ModelBehavior(correctness=1.0),
        "fallback-long": ModelBehavior(correctness=1.0),
    }
    runtime, _ = _build_runtime(tmp_path, behaviors, _fixtures_for(["novelty-threat-v1"]))
    async with runtime:
        plan = _plan(
            role="reasoning",
            benchmarks=["novelty-threat-v1"],
            models=[{"candidate_id": "rc", "requested_model": "reasoning-cand"}],
        )
        run = await _run_tournament(runtime, plan)
        mr = run.model_results[0]
        assert all(c.role == "reasoning" for c in mr.calls)
        assert len(mr.calls) > 0
        # the benchmark itself is fully offline and deterministic
        assert mr.deterministic_pass_rate is not None


@pytest.mark.asyncio
async def test_provenance_after_reopen(tmp_path):
    behaviors = {"prov-model": ModelBehavior(correctness=1.0)}
    runtime, _ = _build_runtime(tmp_path, behaviors, _fixtures_for(SCREENING))
    run_id = None
    plan_id = None
    async with runtime:
        plan = _plan(
            role="fast",
            benchmarks=SCREENING,
            models=[{"candidate_id": "prov", "requested_model": "prov-model"}],
        )
        run = await _run_tournament(runtime, plan)
        run_id = run.id
        plan_id = run.plan_id
    db_path = tmp_path / "artifacts.db"
    assert db_path.exists()

    # reopen a fresh runtime on the same store
    cfg = load_config_from_dict(
        {
            "plugins": [
                "storage.artifacts_sqlite",
                "storage.blobs_filesystem",
                "literature.ingestion",
                "literature.identity_resolver",
                "routing.role_router",
                "research.evaluation_harness",
                "evaluation.model_tournament",
                *ALL_EVALUATORS,
            ],
            "artifacts": {"store": "sqlite", "path": str(db_path)},
            "documents": {"blob_root": str(tmp_path / "blobs")},
            "models": {
                "roles": {
                    "fast": {"provider": "openrouter", "model": "fallback-fast"},
                    "reasoning": {"provider": "openrouter", "model": "fallback-reasoning"},
                    "critic": {"provider": "openrouter", "model": "fallback-critic"},
                    "long_context": {"provider": "openrouter", "model": "fallback-long"},
                }
            },
        }
    )
    provider2 = FakeProviderPlugin(
        fixtures=_fixtures_for(SCREENING), behaviors={"prov-model": ModelBehavior(correctness=1.0)}
    )
    runtime2 = build_runtime(cfg, extra_plugins=[provider2])
    async with runtime2:
        store = runtime2.services.require("artifact_store.default")
        env = await store.get(run_id)
        assert env is not None
        run = env.parse_payload(TournamentRun)
        assert run.plan_id == plan_id
        assert run.plan_hash
        assert len(run.model_results) == 1
        # provenance chain: tournament_run -> plan / leaderboard / evaluation runs
        children = await store.get_children(run_id)
        ids = {c.target_artifact_id for c in children}
        assert run.plan_id in ids
        assert run.leaderboard_id in ids
        assert {r.run_id for r in run.model_results[0].benchmark_runs}.issubset(ids)
