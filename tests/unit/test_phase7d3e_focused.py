"""Phase 7D.3E focused unit tests.

Covers the operator-approved candidate pool config, the genuine analytical-
domain vocabulary repair (legitimate domains no longer crash the mechanism
pipeline while bogus domains stay rejected), the mechanism-critic revision
fallback robustness, provider-unavailable exclusion from qualification, and
the no-unsafe-qualification invariant (a qualified task row always satisfies
the role criteria thresholds).
"""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.common import Usage
from research_harness.contracts.model import Message, ModelResponse

APPROVED_MODEL_SLUGS = {
    "z-ai/glm-5.2:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "minimax/minimax-m3:free",
    "google/gemma-4-31b-it:free",
    "qwen/qwen3.8-flash",
    "z-ai/glm-5.3-flash",
    "google/gemini-3.7-flash",
    "deepseek/deepseek-v4-flash-0731",
    "qwen/qwen3.7-flash",
    "openai/gpt-5.6-luna",
}

_STALE_SLUGS = {
    "meta-llama/llama-3.3-70b-instruct",
    "openai/gpt-4o",
    "google/gemini-2.5-pro",
    "qwen/qwen3-32b",
    "google/gemini-2.5-flash",
    "anthropic/claude-3.7-sonnet",
    "mistralai/mistral-small-3.1-24b-instruct",
    "openai/gpt-4o-mini",
}


def _candidates_in_config(cfg: dict) -> set[str]:
    live = cfg.get("live_quality") or {}
    out: set[str] = set()
    for _role, slugs in (live.get("candidates") or {}).items():
        out.update(str(s) for s in slugs)
    for _task, slugs in (live.get("candidates_per_task") or {}).items():
        out.update(str(s) for s in slugs)
    return out


def test_approved_candidate_config_has_only_approved_models():
    """The active qualification config must contain only the operator-approved
    model pool; stale/deactivated candidates must be absent (Phase 7D.3E)."""
    import yaml

    cfg = yaml.safe_load(pathlib.Path("configs/example.yaml").read_text())
    candidates = _candidates_in_config(cfg)
    assert candidates, "expected a non-empty candidate pool"
    assert candidates <= APPROVED_MODEL_SLUGS, (
        f"unapproved slugs: {sorted(candidates - APPROVED_MODEL_SLUGS)}"
    )
    assert not candidates & _STALE_SLUGS, (
        f"stale slugs still configured: {sorted(candidates & _STALE_SLUGS)}"
    )


def test_approved_config_covers_every_uncovered_task_pool():
    """Every remaining critical task must have a per-task candidate pool (so the
    targeted sweep can run) and covered tasks keep their regression pools."""
    import yaml

    cfg = yaml.safe_load(pathlib.Path("configs/example.yaml").read_text())
    per_task = (cfg.get("live_quality") or {}).get("candidates_per_task") or {}
    for task in (
        "mechanism_generation",
        "model_specification",
        "proposition_generation",
        "results_critique",
        "manuscript_critique",
        "screening",
    ):
        assert task in per_task and per_task[task], f"missing candidate pool for {task}"
        assert {str(s) for s in per_task[task]} <= APPROVED_MODEL_SLUGS


def test_analytical_model_domain_vocabulary_accepts_legitimate_domains():
    """Genuine defect repair (Phase 7D.3E): legitimate analytical IS/economic
    domains (industrial organization, entry deterrence, ...) must validate;
    the previous under-specified eight-domain set crashed the mechanism
    pipeline on them. Bogus domains must stay rejected (validators not loosened)."""
    from research_harness.research.schemas.gap import AnalyticalModelOpportunity

    opp = AnalyticalModelOpportunity(
        suitable=True, domains=["industrial organization", "entry deterrence", "pricing"]
    )
    assert opp.domains == sorted(["industrial organization", "entry deterrence", "pricing"])
    # every previously-allowed domain still validates
    for d in ("strategic interaction", "information asymmetry", "mechanism design"):
        assert d in AnalyticalModelOpportunity(suitable=True, domains=[d]).domains
    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        AnalyticalModelOpportunity(suitable=True, domains=["quantum vibes"])


class _RevisionFakeRouter:
    """Router that returns a fixed response for the critic revision call."""

    def __init__(self, revision_content: str) -> None:
        self._revision_content = revision_content
        self.calls = 0

    async def complete(self, role, request):  # noqa: ANN001
        self.calls += 1
        return ModelResponse(
            message=Message(role="assistant", content=self._revision_content),
            provider="openrouter",
            usage=Usage(prompt_tokens=10, completion_tokens=5),
            latency_ms=5.0,
        )


def _critic_candidate() -> tuple:
    from research_harness.research.schemas.gap import AnalyticalModelOpportunity
    from research_harness.research.schemas.mechanism import (
        KnowledgeBasis,
        MechanismCandidate,
        MechanismEvaluation,
        MechanismStatus,
    )

    candidate = MechanismCandidate(
        gap_id="gap-1",
        gap_selection_id="sel-1",
        name="Original mechanism",
        description="Original description.",
        actors=["seller"],
        strategic_interactions=["pricing"],
        information_structure="seller observes demand",
        incentives=["profit maximization"],
        causal_logic="Data enables price discrimination.",
        key_assumptions=["linear demand"],
        expected_outcomes=["surplus transfer"],
        boundary_conditions=["uncertain demand"],
        literature_support_ids=[],
        grounding=[
            {
                "element": "pricing responds to competition",
                "basis": KnowledgeBasis.research_inference,
                "source_ids": [],
            }
        ],
        analytical_model_potential=AnalyticalModelOpportunity(suitable=True, domains=["pricing"]),
        evaluation=MechanismEvaluation(
            gap_alignment=0.8,
            theoretical_coherence=0.8,
            novelty_within_reviewed_corpus=0.5,
            analytical_tractability=0.8,
            managerial_economic_relevance=0.7,
            is_relevance=0.8,
        ),
        status=MechanismStatus.candidate,
        model_role="reasoning",
    )
    return candidate


def test_mechanism_critic_revision_falls_back_on_invalid_domain():
    """Genuine robustness repair (Phase 7D.3E): a revision whose analytical
    model domains fall outside the vocabulary must NOT crash the selection;
    the service falls back to the original candidate unchanged (the documented
    revision fallback), exactly as it does for a model-call failure."""
    from research_harness.plugins.research.mechanism_critic.plugin import MechanismCriticService

    candidate = _critic_candidate()
    revision_with_bad_domain = json.dumps(
        {
            "name": "Revised mechanism",
            "description": "Revised description.",
            "actors": ["seller", "consumer"],
            "strategic_interactions": ["pricing"],
            "information_structure": "seller observes demand",
            "incentives": ["profit maximization"],
            "causal_logic": "Data enables price discrimination.",
            "key_assumptions": ["linear demand"],
            "expected_outcomes": ["surplus transfer"],
            "boundary_conditions": ["uncertain demand"],
            "grounding": [],
            "revision_notes": ["Revised per critique"],
            "analytical_model_potential": {"suitable": True, "domains": ["quantum vibes"]},
            "evaluation": {
                "gap_alignment": 0.9,
                "theoretical_coherence": 0.9,
                "novelty_within_reviewed_corpus": 0.6,
                "analytical_tractability": 0.9,
                "managerial_economic_relevance": 0.8,
                "is_relevance": 0.9,
            },
        }
    )
    svc = MechanismCriticService(
        model_router=_RevisionFakeRouter(revision_with_bad_domain),
        artifact_store=None,
        critic_role="critic",
        revision_role="reasoning",
    )
    import asyncio

    revision = asyncio.run(
        svc._revise(candidate, "cand-1", [], {"stmts": {}})  # noqa: SLF001
    )
    assert revision["name"] == "Original mechanism"
    assert revision["analytical_model_potential"] == candidate.analytical_model_potential
    assert "revision model unavailable" in revision["revision_notes"][0]


def test_mechanism_critic_revision_applies_valid_domain():
    """A revision with a now-legitimate domain (industrial organization) applies
    instead of falling back — the vocabulary repair unblocks valid revisions."""
    from research_harness.plugins.research.mechanism_critic.plugin import MechanismCriticService

    candidate = _critic_candidate()
    revision_ok = json.dumps(
        {
            "name": "Revised mechanism",
            "description": "Revised description.",
            "actors": ["seller", "consumer"],
            "strategic_interactions": ["pricing"],
            "information_structure": "seller observes demand",
            "incentives": ["profit maximization"],
            "causal_logic": "Data enables price discrimination.",
            "key_assumptions": ["linear demand"],
            "expected_outcomes": ["surplus transfer"],
            "boundary_conditions": ["uncertain demand"],
            "grounding": [],
            "revision_notes": ["Revised per critique"],
            "analytical_model_potential": {
                "suitable": True,
                "domains": ["industrial organization", "entry deterrence"],
            },
            "evaluation": {
                "gap_alignment": 0.9,
                "theoretical_coherence": 0.9,
                "novelty_within_reviewed_corpus": 0.6,
                "analytical_tractability": 0.9,
                "managerial_economic_relevance": 0.8,
                "is_relevance": 0.9,
            },
        }
    )
    svc = MechanismCriticService(
        model_router=_RevisionFakeRouter(revision_ok),
        artifact_store=None,
        critic_role="critic",
        revision_role="reasoning",
    )
    import asyncio

    revision = asyncio.run(
        svc._revise(candidate, "cand-1", [], {"stmts": {}})  # noqa: SLF001
    )
    assert revision["name"] == "Revised mechanism"
    assert revision["analytical_model_potential"].domains == [
        "entry deterrence",
        "industrial organization",
    ]


def test_remaining_coverage_excludes_provider_unavailable_from_qualified():
    """Provider-unavailable models are counted separately (never as qualified)
    and a task with only provider-unavailable candidates reports that as the
    dominant reason, keeping provider failure distinct from model quality."""
    from research_harness.research.routing.qualification import build_remaining_task_coverage
    from research_harness.research.schemas.qualification import (
        ModelPreflight,
        PreflightStatus,
        RemainingTaskCoverage,
        TaskQualificationMatrix,
        TaskQualificationResult,
    )

    row = TaskQualificationResult(
        role="fast",
        task="screening",
        candidate_id="m-a",
        model={},
        benchmark_id="live-quality-fast-v1",
        qualified=False,
        rejection_reasons=["task deterministic_pass_rate 0.4 < 0.9"],
    )
    matrix = TaskQualificationMatrix(
        role="fast",
        benchmark_id="live-quality-fast-v1",
        tasks=["screening"],
        rows=[row],
        qualified_models_by_task={"screening": []},
        ranked_models_by_task={"screening": []},
        qualified_tasks_by_model={"m-a": []},
        role_qualified_models=[],
    )
    preflight_a = ModelPreflight(
        role="fast",
        candidate_id="m-b",
        provider="openrouter",
        requested_model="m-b",
        status=PreflightStatus.provider_error,
    )
    preflight_b = ModelPreflight(
        role="fast",
        candidate_id="m-c",
        provider="openrouter",
        requested_model="m-c",
        status=PreflightStatus.temporarily_unavailable,
    )

    class _Campaign:
        role = "fast"
        candidates = [
            type("C", (), {"candidate_id": "m-a"})(),
            type("C", (), {"candidate_id": "m-b"})(),
            type("C", (), {"candidate_id": "m-c"})(),
        ]

    coverage = build_remaining_task_coverage(
        [matrix], preflights=[preflight_a, preflight_b], campaigns=[_Campaign()]
    )
    assert isinstance(coverage, RemainingTaskCoverage)
    scr = next(r for r in coverage.rows if r.task == "screening")
    assert scr.qualified_model_count == 0
    assert scr.tested_model_count == 3
    assert scr.provider_unavailable_count == 2
    # m-a exercised below threshold, so quality is the dominant reason; the
    # provider-unavailable models are counted separately and never qualified.
    assert scr.dominant_failure_reason == "below_quality_threshold"


def _qualifying_row(*, role: str, task: str, candidate_id: str, det: float, provider: float = 0.0):
    from research_harness.research.schemas.qualification import TaskQualificationResult

    return TaskQualificationResult(
        role=role,
        task=task,
        candidate_id=candidate_id,
        model={},
        benchmark_id=f"live-quality-{role}-v1",
        repetitions=3,
        deterministic_pass_rate_mean=det,
        deterministic_pass_rate_worst=det,
        deterministic_pass_rate_variance=0.0,
        structured_output_success_rate=0.95,
        provider_error_frequency=provider,
        critical_grounding_failures=0,
        qualified=False,
        rejection_reasons=[],
    )


def test_qualified_rows_always_meet_criteria_thresholds():
    """No-unsafe-qualification invariant: a row marked qualified_for_task must
    satisfy every role-criteria threshold (det, structured-output, provider,
    grounding). A qualified row with det below the threshold is impossible."""
    from research_harness.research.routing.qualification import qualify_task
    from research_harness.research.routing.readiness import criteria_for_role
    from research_harness.research.schemas.live_quality import (
        LiveQualityModelResult,
        LiveQualityTaskPerformance,
    )

    for role, threshold in (("reasoning", 0.85), ("critic", 0.85), ("fast", 0.9)):
        criteria = criteria_for_role(role)
        for det in (0.0, 0.5, 0.84, 0.86, 0.9, 1.0):
            result = LiveQualityModelResult(
                candidate_id="m",
                model={},
                role=role,
                benchmark_id=f"live-quality-{role}-v1",
                task_performance=[
                    LiveQualityTaskPerformance(
                        task_id="lq-task",
                        pass_rates=[det],
                        structured_output_success_rate=0.95,
                        provider_error_frequency=0.0,
                        critical_grounding_failures=0,
                    )
                ],
            )
            qualified, reasons = qualify_task(result, "task", criteria)
            if qualified:
                assert det >= threshold, (role, det)
                assert not reasons


def test_task_matrix_primary_fallback_only_from_qualified():
    """Primary/fallback selection comes only from qualified models, ranked by
    per-task deterministic rate; an unqualified model is never primary."""
    from research_harness.research.routing.qualification import build_task_matrix
    from research_harness.research.schemas.live_quality import (
        LiveQualityModelResult,
        LiveQualityTaskPerformance,
    )

    def _result(candidate_id: str, det: float) -> LiveQualityModelResult:
        return LiveQualityModelResult(
            candidate_id=candidate_id,
            model={},
            role="critic",
            benchmark_id="live-quality-critic-v1",
            task_performance=[
                LiveQualityTaskPerformance(
                    task_id="lq-mechanism-critique",
                    repetitions=3,
                    pass_rates=[det],
                    structured_output_success_rate=0.95,
                    provider_error_frequency=0.0,
                    critical_grounding_failures=0,
                )
            ],
        )

    live = {
        "m-good": _result("m-good", 1.0),
        "m-second": _result("m-second", 0.9),
        "m-bad": _result("m-bad", 0.4),
    }
    matrix, rows = build_task_matrix(
        live, role="critic", benchmark_id="live-quality-critic-v1", repetitions=3
    )
    assert matrix.qualified_models_by_task["mechanism_critique"] == ["m-good", "m-second"]
    assert matrix.ranked_models_by_task["mechanism_critique"] == ["m-good", "m-second"]
    assert "m-bad" not in matrix.ranked_models_by_task["mechanism_critique"]
