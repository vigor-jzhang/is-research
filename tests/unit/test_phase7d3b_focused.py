"""Phase 7D.3B focused unit tests.

Covers the provider/model preflight classification, config-driven per-task
candidate selection, remaining-task coverage, task-specific diagnostics
aggregation, the genuine proposition-verification status evaluator repair, and
the evaluator-sanity verdict contract.
"""

from __future__ import annotations

import pytest

from research_harness.contracts.common import Usage
from research_harness.contracts.model import Message, ModelResponse


class _FakeProvider:
    def __init__(self, *, mode: str = "ok", model: str = "resolved-model") -> None:
        self.mode = mode
        self.model = model
        self.calls = 0

    async def complete(self, request):  # noqa: ANN201
        self.calls += 1
        if self.mode == "timeout":
            raise TimeoutError("timed out")
        if self.mode == "rate_limit":
            raise RuntimeError("rate limit 429")
        if self.mode == "hard_error":
            raise RuntimeError("provider 500 gateway failure")
        if self.mode == "context_length":
            last = request.messages[-1].content or ""
            if len(last) > 200:
                raise RuntimeError("context_length_exceeded: too many tokens")
        content = '{"status": "ready", "ok": true}' if request.response_schema is not None else "ok"
        return ModelResponse(
            message=Message(role="assistant", content=content),
            model=self.model,
            provider="openrouter",
            usage=Usage(prompt_tokens=10, completion_tokens=5),
            latency_ms=12.0,
        )


def _service_lookup(provider) -> dict:  # noqa: ANN001
    def _lookup(name: str):
        if name == "model_provider.openrouter":
            return provider
        raise LookupError(name)

    return _lookup


class _BaseRouter:
    def resolve(self, role):  # noqa: ANN001
        return {"provider": "openrouter", "model": "default"}

    async def complete(self, role, request):  # noqa: ANN001
        raise AssertionError("base router should not be used for the candidate role")


@pytest.mark.asyncio
async def test_preflight_classifies_available():
    from research_harness.research.routing.preflight import run_candidate_preflight
    from research_harness.research.schemas.qualification import PreflightStatus
    from research_harness.research.schemas.tournament import TournamentModelConfig

    provider = _FakeProvider()
    result = await run_candidate_preflight(
        role="reasoning",
        candidate=TournamentModelConfig(
            candidate_id="fake/model", provider="openrouter", requested_model="fake/model"
        ),
        service_lookup=_service_lookup(provider),
        base_router=_BaseRouter(),
        timeout_seconds=5.0,
        retries=1,
        required_context_chars=200,
    )
    assert result.status == PreflightStatus.available
    assert result.resolved_model == "resolved-model"
    kinds = [c.kind for c in result.checks]
    assert "reachability" in kinds and "structured_output" in kinds and "context_size" in kinds
    assert all(c.passed for c in result.checks)


@pytest.mark.asyncio
async def test_preflight_classifies_capability_mismatch_on_context():
    from research_harness.research.routing.preflight import run_candidate_preflight
    from research_harness.research.schemas.qualification import PreflightStatus
    from research_harness.research.schemas.tournament import TournamentModelConfig

    provider = _FakeProvider(mode="context_length")
    result = await run_candidate_preflight(
        role="reasoning",
        candidate=TournamentModelConfig(
            candidate_id="fake/model", provider="openrouter", requested_model="fake/model"
        ),
        service_lookup=_service_lookup(provider),
        base_router=_BaseRouter(),
        timeout_seconds=5.0,
        retries=1,
        required_context_chars=200,
    )
    assert result.status == PreflightStatus.capability_mismatch
    # a capability mismatch must never be interpreted as an availability flake
    assert result.reachable is True


@pytest.mark.asyncio
async def test_preflight_never_qualifies_unavailable():
    from research_harness.research.routing.preflight import run_candidate_preflight
    from research_harness.research.schemas.qualification import PreflightStatus
    from research_harness.research.schemas.tournament import TournamentModelConfig

    provider = _FakeProvider(mode="timeout")
    result = await run_candidate_preflight(
        role="reasoning",
        candidate=TournamentModelConfig(
            candidate_id="fake/model", provider="openrouter", requested_model="fake/model"
        ),
        service_lookup=_service_lookup(provider),
        base_router=_BaseRouter(),
        timeout_seconds=1.0,
        retries=1,
        required_context_chars=200,
    )
    assert result.status == PreflightStatus.temporarily_unavailable
    assert result.resolved_model is None


@pytest.mark.asyncio
async def test_preflight_classifies_hard_provider_error():
    from research_harness.research.routing.preflight import run_candidate_preflight
    from research_harness.research.schemas.qualification import PreflightStatus
    from research_harness.research.schemas.tournament import TournamentModelConfig

    provider = _FakeProvider(mode="hard_error")
    result = await run_candidate_preflight(
        role="reasoning",
        candidate=TournamentModelConfig(
            candidate_id="fake/model", provider="openrouter", requested_model="fake/model"
        ),
        service_lookup=_service_lookup(provider),
        base_router=_BaseRouter(),
        timeout_seconds=1.0,
        retries=1,
        required_context_chars=200,
    )
    assert result.status == PreflightStatus.provider_error


def test_candidates_for_tasks_config_driven_dedup():
    from research_harness.plugins.research.evaluation_live_quality.plugin import (
        LiveQualityService,
    )

    svc = LiveQualityService(
        artifact_store=None,
        harness=None,
        role_router=None,
        service_lookup=None,
        candidates={"reasoning": ["m-a", "m-b"]},
        candidates_per_task={
            "gap_analysis": ["m-b", "m-c"],
            "mechanism_generation": ["m-b"],
        },
    )
    assert svc._candidates_for_tasks("reasoning", ["gap_analysis"]) == ["m-b", "m-c"]
    assert svc._candidates_for_tasks("reasoning", ["gap_analysis", "mechanism_generation"]) == [
        "m-b",
        "m-c",
    ]
    assert svc._candidates_for_tasks("reasoning", None) == ["m-a", "m-b"]
    assert svc._candidates_for_tasks("fast", ["screening"]) == []
    with pytest.raises(ValueError):
        svc._candidates_for_tasks("reasoning", ["not-a-task"])


def test_remaining_tasks_for_role_excludes_frozen_qualified():
    from research_harness.research.routing.tasks import remaining_tasks_for_role

    assert remaining_tasks_for_role("reasoning") == [
        "gap_analysis",
        "mechanism_generation",
        "model_specification",
        "proposition_generation",
    ]
    assert remaining_tasks_for_role("critic") == [
        "mechanism_critique",
        "model_specification_critique",
        "proposition_critique",
        "results_critique",
        "manuscript_critique",
    ]
    assert remaining_tasks_for_role("fast") == ["screening"]


def test_remaining_task_coverage_attributes_provider_unavailable():
    from research_harness.research.routing.qualification import build_remaining_task_coverage
    from research_harness.research.schemas.qualification import (
        ModelPreflight,
        PreflightStatus,
        RemainingTaskCoverage,
        TaskQualificationMatrix,
        TaskQualificationResult,
    )

    row = TaskQualificationResult(
        role="reasoning",
        task="gap_analysis",
        candidate_id="m-a",
        model={},
        benchmark_id="live-quality-reasoning-v1",
        qualified=False,
        rejection_reasons=["task deterministic_pass_rate 0.4 < 0.85"],
    )
    matrix = TaskQualificationMatrix(
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        tasks=["gap_analysis"],
        rows=[row],
        qualified_models_by_task={"gap_analysis": []},
        ranked_models_by_task={"gap_analysis": []},
        qualified_tasks_by_model={"m-a": []},
        role_qualified_models=[],
    )
    preflight = ModelPreflight(
        role="reasoning",
        candidate_id="m-b",
        provider="openrouter",
        requested_model="m-b",
        status=PreflightStatus.provider_error,
    )
    campaign = _FakeCampaign(role="reasoning", candidate_ids=["m-a", "m-b"])
    coverage = build_remaining_task_coverage([matrix], preflights=[preflight], campaigns=[campaign])
    assert isinstance(coverage, RemainingTaskCoverage)
    gap = next(r for r in coverage.rows if r.task == "gap_analysis")
    assert gap.qualified_model_count == 0
    assert gap.tested_model_count == 2
    assert gap.provider_unavailable_count == 1
    assert gap.dominant_failure_reason == "below_quality_threshold"


class _FakeCampaign:
    def __init__(self, *, role: str, candidate_ids: list[str]) -> None:
        self.role = role
        self.candidate_ids = candidate_ids
        self.candidates = [type("C", (), {"candidate_id": c})() for c in candidate_ids]


def test_remaining_task_coverage_primary_fallback_and_dominant():
    """Coverage reports qualified primary/fallback from the ranked task matrix
    and a provider-unavailable candidate is excluded (never a qualified count)."""
    from research_harness.research.routing.qualification import build_remaining_task_coverage
    from research_harness.research.schemas.qualification import (
        ModelPreflight,
        PreflightStatus,
        TaskQualificationMatrix,
        TaskQualificationResult,
    )

    def _row(cid, task, qualified, reasons):
        return TaskQualificationResult(
            role="critic",
            task=task,
            candidate_id=cid,
            model={},
            benchmark_id="live-quality-critic-v1",
            qualified=qualified,
            rejection_reasons=reasons,
        )

    matrix = TaskQualificationMatrix(
        role="critic",
        benchmark_id="live-quality-critic-v1",
        tasks=["mechanism_critique", "results_critique"],
        rows=[
            _row("m-good", "mechanism_critique", True, []),
            _row("m-good2", "mechanism_critique", True, []),
            _row("m-bad", "mechanism_critique", False, ["task deterministic_pass_rate 0.4 < 0.85"]),
            _row("m-good", "results_critique", False, ["task deterministic_pass_rate 0.4 < 0.85"]),
        ],
        qualified_models_by_task={
            "mechanism_critique": ["m-good", "m-good2"],
            "results_critique": [],
        },
        ranked_models_by_task={"mechanism_critique": ["m-good", "m-good2"], "results_critique": []},
        qualified_tasks_by_model={
            "m-good": ["mechanism_critique"],
            "m-good2": ["mechanism_critique"],
            "m-bad": [],
        },
        role_qualified_models=["m-good", "m-good2"],
    )
    preflight = ModelPreflight(
        role="critic",
        candidate_id="m-unavail",
        provider="openrouter",
        requested_model="m-unavail",
        status=PreflightStatus.provider_error,
    )
    campaign = _FakeCampaign(
        role="critic",
        candidate_ids=["m-good", "m-good2", "m-bad", "m-unavail"],
    )
    coverage = build_remaining_task_coverage([matrix], preflights=[preflight], campaigns=[campaign])
    mech = next(r for r in coverage.rows if r.task == "mechanism_critique")
    assert mech.qualified_primary == "m-good"
    assert mech.qualified_fallback == "m-good2"
    assert mech.qualified_model_count == 2
    assert mech.tested_model_count == 4
    assert mech.provider_unavailable_count == 1
    results = next(r for r in coverage.rows if r.task == "results_critique")
    assert results.qualified_model_count == 0
    assert results.dominant_failure_reason == "below_quality_threshold"


def test_proposition_verification_verified_status_is_a_pass():
    """Genuine defect repair: the production verifier emits status 'verified'
    (enum vocabulary), never the literal 'passed'. A verified verification must
    not be counted as a critical grounding failure."""

    async def _run(produced):  # noqa: ANN001
        from research_harness.contracts.evaluator import EvaluatorContext
        from research_harness.plugins.research.evaluator_live_quality_reasoning.plugin import (
            LiveQualityReasoningEvaluator,
        )
        from research_harness.research.schemas.evaluation import BenchmarkCase

        case = BenchmarkCase(
            id="lq-prop",
            benchmark_id="live-quality-reasoning-v1",
            name="proposition",
            input={"workflow": "proposition_generation", "task": "proposition_generation"},
            reference={
                "task": "proposition_generation",
                "required_concepts": ["algorithmic pricing"],
            },
            evaluation_dimensions=["proposition_generation"],
            tags=[],
        )
        ctx = EvaluatorContext(
            case=case,
            case_envelope=ArtifactEnvelope.create(payload=case, artifact_type="benchmark_case"),
            produced_artifacts=produced,
        )
        return await LiveQualityReasoningEvaluator().evaluate(ctx)

    import asyncio

    from research_harness.research.envelope import ArtifactEnvelope
    from research_harness.research.schemas.equilibrium import EquilibriumCandidate
    from research_harness.research.schemas.model import FormalAnalyticalModel
    from research_harness.research.schemas.proposition import (
        ComparativeStaticsAnalysis,
        Proposition,
        PropositionVerification,
    )

    model = FormalAnalyticalModel(
        selected_mechanism_id="lq-mech",
        title="Model",
        description="A model of surplus extraction.",
    )
    model_env = ArtifactEnvelope.create(
        payload=model,
        artifact_type="formal_analytical_model",
        artifact_id="formal-analytical-model-0",
    )
    eq = EquilibriumCandidate(model_id="formal-analytical-model-0", decision_variables=["p"])
    eq_env = ArtifactEnvelope.create(
        payload=eq, artifact_type="equilibrium_candidate", artifact_id="equilibrium-candidate-0"
    )
    cs = ComparativeStaticsAnalysis(
        model_id="formal-analytical-model-0", equilibrium_candidate_id="equilibrium-candidate-0"
    )
    cs_env = ArtifactEnvelope.create(
        payload=cs,
        artifact_type="comparative_statics_analysis",
        artifact_id="comparative-statics-0",
    )
    verification = PropositionVerification(
        proposition_id="lq-prop",
        model_id="formal-analytical-model-0",
        status="verified",
    )
    proposition = Proposition(
        model_id="formal-analytical-model-0",
        equilibrium_candidate_id="equilibrium-candidate-0",
        comparative_statics_analysis_id="comparative-statics-0",
        statement="Algorithmic pricing reduces consumer welfare.",
        claim_type="monotonicity",
        conditions=["uncertain demand"],
        expected_sign="negative",
        supporting_static_ids=[],
    )
    produced = [
        model_env,
        eq_env,
        cs_env,
        ArtifactEnvelope.create(payload=verification, artifact_type="proposition_verification"),
        ArtifactEnvelope.create(payload=proposition, artifact_type="proposition"),
    ]
    result = asyncio.run(_run(produced))
    assert "deterministic verification did not pass" not in result.explanation
    assert result.status.value == "passed"


def test_gap_diagnostics_counts_hallucinated_refs():
    from research_harness.plugins.research.evaluator_live_quality_reasoning.plugin import (
        gap_diagnostics,
    )

    gaps = [
        {
            "title": "Mechanism gap",
            "gap_type": "mechanism_gap",
            "description": "The mechanism is unclear.",
            "supporting_synthesis_statement_ids": ["produced-1", "ghost-2"],
            "supporting_evidence_ids": [],
            "contradiction_statement_ids": [],
            "relevant_paper_identity_ids": [],
            "supporting_papers": 0,
            "supporting_evidence_items": 0,
        }
    ]
    diag = gap_diagnostics(gaps, produced_ids={"produced-1"}, allowed_gap_types={"mechanism_gap"})
    assert diag["hallucinated_synthesis_evidence_refs"] == 1
    assert diag["incorrect_gap_type"] == 0
    assert diag["unsupported_gap"] == 0
    assert diag["structured_output_failure"] == 0


def test_model_specification_diagnostics_undefined_symbol():
    from research_harness.plugins.research.evaluator_live_quality_reasoning.plugin import (
        model_specification_diagnostics,
    )

    model = {
        "actors": [{"actor_id": "seller", "name": "Seller", "strategic": True}],
        "variables": [
            {
                "symbol": "p",
                "name": "price",
                "meaning": "price",
                "domain": "R_+",
                "kind": "decision_variable",
                "owner_actor_id": "seller",
            }
        ],
        "parameters": [],
        "timing": [
            {
                "stage_number": 0,
                "name": "pricing",
                "description": "pricing",
                "actor_ids": ["seller"],
            }
        ],
        "payoffs": [
            {
                "actor_id": "seller",
                "expression": {"expression": "p * q", "symbols_used": ["p", "q"]},
            }
        ],
    }
    diag = model_specification_diagnostics([model], produced_ids=set())
    assert diag["undefined_symbols"] == 1
    assert diag["malformed_mathematical_expression"] == 0


def test_fast_diagnostics_false_inclusion():
    async def _run(produced):  # noqa: ANN001
        from research_harness.contracts.evaluator import EvaluatorContext
        from research_harness.plugins.research.evaluator_live_quality_fast.plugin import (
            LiveQualityFastEvaluator,
        )
        from research_harness.research.schemas.evaluation import BenchmarkCase

        case = BenchmarkCase(
            id="lq-fast",
            benchmark_id="live-quality-fast-v1",
            name="screening",
            input={"workflow": "literature_screening", "task": "screening"},
            reference={
                "task": "screening",
                "expected_decisions": {"Paper A": "exclude"},
                "required_decision_accuracy": 0.8,
            },
            evaluation_dimensions=["screening"],
            tags=[],
        )
        ctx = EvaluatorContext(
            case=case,
            case_envelope=ArtifactEnvelope.create(payload=case, artifact_type="benchmark_case"),
            produced_artifacts=produced,
        )
        return await LiveQualityFastEvaluator().evaluate(ctx)

    import asyncio

    from research_harness.research.envelope import ArtifactEnvelope
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.paper import PaperRecord
    from research_harness.research.schemas.screening_decision import ScreeningDecision

    record = PaperRecord(title="Paper A")
    identity = PaperIdentity(
        member_paper_artifact_ids=[record.id if hasattr(record, "id") else "paper-record-0"],
        resolution_method=ResolutionMethod.exact_identifier,
    )
    decision = ScreeningDecision(
        paper_identity_id="paper-identity-0",
        screening_view_id="v",
        screening_protocol_id="p",
        decision="include",
        rationale_summary="x",
        confidence=0.9,
    )
    record_env = ArtifactEnvelope.create(
        payload=record, artifact_type="paper_record", artifact_id="paper-record-0"
    )
    identity_env = ArtifactEnvelope.create(
        payload=identity,
        artifact_type="paper_identity",
        artifact_id="paper-identity-0",
    )
    decision_env = ArtifactEnvelope.create(
        payload=decision,
        artifact_type="screening_decision",
        artifact_id="screening-decision-0",
    )
    result = asyncio.run(_run([record_env, identity_env, decision_env]))
    diag = (result.value or {}).get("task_diagnostics") or {}
    assert diag["false_inclusion"] == 1
    assert result.status.value == "failed"


def test_aggregate_task_performance_merges_task_diagnostics():
    from research_harness.research.routing.qualification import aggregate_task_performance
    from research_harness.research.schemas.live_quality import (
        LiveQualityModelResult,
        LiveQualityTaskPerformance,
    )

    result = LiveQualityModelResult(
        candidate_id="m-a",
        model={},
        role="reasoning",
        benchmark_id="live-quality-reasoning-v1",
        task_performance=[
            LiveQualityTaskPerformance(
                task_id="lq-gap-analysis",
                task_diagnostics={"hallucinated_synthesis_evidence_refs": 2, "unsupported_gap": 1},
            ),
            LiveQualityTaskPerformance(
                task_id="lq-gap-analysis",
                task_diagnostics={"unsupported_gap": 1, "incorrect_gap_type": 1},
            ),
        ],
    )
    tp = aggregate_task_performance(result, "gap_analysis")
    assert tp.task_diagnostics == {
        "hallucinated_synthesis_evidence_refs": 2,
        "unsupported_gap": 2,
        "incorrect_gap_type": 1,
    }
