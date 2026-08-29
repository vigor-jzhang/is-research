"""Phase 6A unit tests — evaluation harness.

Covers: benchmark loading/versioning, case hashing, evaluator registration,
deterministic evaluator execution, model-assisted evaluators with fake
models, aggregation, false-clear metric, evaluation failure isolation,
immutable historical runs, and store reopen/provenance.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from pydantic import BaseModel

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.research.evaluation_harness.plugin import (
    BenchmarkVersionError,
    EvaluationHarnessService,
)
from research_harness.plugins.research.evaluator_claim_grounding.plugin import (
    ClaimGroundingEvaluator,
)
from research_harness.plugins.research.evaluator_deterministic.plugin import (
    DeterministicEvaluator,
)
from research_harness.plugins.research.evaluator_llm_judge.plugin import LlmJudgeEvaluator
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.benchmarks import (
    BenchmarkCaseDefinition,
    BenchmarkDefinition,
)
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import (
    Benchmark,
    BenchmarkCase,
    EvaluationReport,
    EvaluationReportStatus,
    EvaluationRun,
    EvaluatorCategory,
    EvaluatorResult,
    EvaluatorStatus,
)
from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
from research_harness.research.schemas.novelty import (
    CandidateRelationship,
    EvidenceBasis,
    NoveltyCandidateAssessment,
    NoveltyClaim,
    NoveltyClaimAssessment,
    NoveltyClaimStatus,
    NoveltyClaimType,
    NoveltyCoverage,
    NoveltyReportStatus,
    NoveltyValidationReport,
)
from research_harness.research.schemas.paper import PaperRecord

CLAIM = "We are the first to show that algorithmic pricing reduces consumer welfare."


class _ReportStatusPayload(BaseModel):
    overall_status: str

    model_config = {"extra": "forbid"}


class FakeRouter:
    def __init__(self, builders: dict[str, dict]):
        self.builders = builders
        self.calls = 0

    async def complete(self, role, request):
        self.calls += 1
        text = " ".join(m.content or "" for m in request.messages)
        for marker, resp in self.builders.items():
            if marker in text:
                return ModelResponse(
                    message=Message(role="assistant", content=json.dumps(resp)),
                    tool_calls=[],
                    finish_reason="stop",
                    model="fake",
                    usage=None,
                    latency_ms=1,
                )
        raise AssertionError(f"no builder for prompt: {text[:200]}")


@pytest.fixture
async def store(tmp_path: pathlib.Path):
    s = SQLiteArtifactStore(path=tmp_path / "art.db")
    yield s
    await s.close()


def _case_def(extra_input: dict | None = None) -> BenchmarkCaseDefinition:
    return BenchmarkCaseDefinition(
        id="c1",
        name="case one",
        description="",
        input={
            "workflow": "novelty_validation",
            "submission": {"title": "T", "abstract": "A", "sections": {}},
            "fixture_sources": {},
            "llm_fixtures": [],
            **(extra_input or {}),
        },
        reference={"expected_report_status": "clear"},
        evaluation_dimensions=["report_status"],
        tags=["unit"],
    )


def _benchmark(
    version: int = 1, case: BenchmarkCaseDefinition | None = None
) -> BenchmarkDefinition:
    return BenchmarkDefinition(
        benchmark_id="unit-bench",
        version=version,
        name="Unit Benchmark",
        description="",
        category="unit",
        config={"mode": "novelty_threat", "evaluators": ["evaluator.deterministic"]},
        cases=[case or _case_def()],
    )


def _harness(store, evaluators: dict | None = None) -> EvaluationHarnessService:
    return EvaluationHarnessService(
        artifact_store=store,
        ingestor=None,
        identity_resolver=None,
        evaluators=evaluators or {"evaluator.deterministic": DeterministicEvaluator()},
        config={"evaluators": ["evaluator.deterministic"]},
    )


# ---------------------------------------------------------------------------
# Benchmark loading / versioning / case hashing
# ---------------------------------------------------------------------------


async def test_register_benchmark_idempotent(store):
    svc = _harness(store)
    bid = await svc.register_benchmark(_benchmark())
    assert bid == "unit-bench"
    assert await store.exists("unit-bench")
    assert await store.exists("c1")
    env = await store.get("unit-bench")
    assert env.parse_payload(Benchmark).case_ids == ["c1"]

    # identical re-registration is a no-op
    await svc.register_benchmark(_benchmark())
    runs = await store.list(artifact_type="benchmark")
    assert len(runs) == 1


async def test_register_benchmark_version_conflict(store):
    svc = _harness(store)
    await svc.register_benchmark(_benchmark())
    with pytest.raises(BenchmarkVersionError):
        await svc.register_benchmark(
            _benchmark(case=_case_def(extra_input={"submission": {"title": "CHANGED"}}))
        )


async def test_case_content_hash_deterministic(store):
    svc1 = _harness(store)
    await svc1.register_benchmark(_benchmark())
    c1 = await store.get("c1")
    c1_payload = c1.parse_payload(BenchmarkCase)

    # identical payload (same id + created_at) hashes identically even with a
    # different envelope artifact id
    c2 = BenchmarkCase(
        id="c1",
        benchmark_id="unit-bench",
        version=1,
        name="case one",
        description="",
        input=_case_def().input,
        reference=_case_def().reference,
        evaluation_dimensions=["report_status"],
        tags=["unit"],
        created_at=c1_payload.created_at,
    )
    c2_env = ArtifactEnvelope.create(
        payload=c2, artifact_type="benchmark_case", producer="test", artifact_id="c2"
    )
    assert c2_env.content_hash == c1.content_hash

    # a changed payload must hash differently (same id + created_at pinned)
    changed = BenchmarkCase(
        id="c1",
        benchmark_id="unit-bench",
        version=1,
        name="case one",
        description="",
        input={"workflow": "novelty_validation", "submission": {"title": "X"}},
        reference=_case_def().reference,
        evaluation_dimensions=["report_status"],
        tags=["unit"],
        created_at=c1_payload.created_at,
    )
    changed_env = ArtifactEnvelope.create(
        payload=changed, artifact_type="benchmark_case", producer="test", artifact_id="c3"
    )
    assert changed_env.content_hash != c1.content_hash


# ---------------------------------------------------------------------------
# Evaluator registration
# ---------------------------------------------------------------------------


async def test_run_benchmark_unknown_evaluator(store):
    svc = _harness(store)
    await svc.register_benchmark(_benchmark())
    with pytest.raises(Exception) as exc:
        await svc.run_benchmark("unit-bench", evaluator_ids=["evaluator.nope"])
    assert "unknown evaluator" in str(exc.value)


async def test_evaluator_services_by_id(store):
    svc = _harness(
        store,
        evaluators={
            "evaluator.deterministic": DeterministicEvaluator(),
            "evaluator.llm_judge": LlmJudgeEvaluator(model_router=FakeRouter({})),
            "evaluator.claim_grounding": ClaimGroundingEvaluator(model_router=FakeRouter({})),
        },
    )
    assert set(svc._evaluators) == {
        "evaluator.deterministic",
        "evaluator.llm_judge",
        "evaluator.claim_grounding",
    }


# ---------------------------------------------------------------------------
# Deterministic evaluator
# ---------------------------------------------------------------------------


def _produced_artifacts(
    *,
    relationship: str = "direct_prior_art",
    claim_status: str = "threatened",
    report_status: str = "blocked",
    paper_title: str = "The Prior Art Paper",
) -> list[ArtifactEnvelope]:
    record = ArtifactEnvelope.create(
        payload=PaperRecord(title=paper_title, year=2020, doi="10.1/pa"),
        artifact_type="paper_record",
        producer="test",
    )
    identity = ArtifactEnvelope.create(
        payload=PaperIdentity(
            member_paper_artifact_ids=[record.artifact_id],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.manual,
            resolution_evidence=[],
        ),
        artifact_type="paper_identity",
        producer="test",
    )
    claim = ArtifactEnvelope.create(
        payload=NoveltyClaim(
            manuscript_id="m",
            section_id="introduction",
            claim_text=CLAIM,
            claim_type=NoveltyClaimType.absolute_priority,
            risk="critical",
            importance="major",
            extraction_method="deterministic",
            source_quote=CLAIM,
        ),
        artifact_type="novelty_claim",
        producer="test",
    )
    assessment = ArtifactEnvelope.create(
        payload=NoveltyCandidateAssessment(
            claim_id=claim.artifact_id,
            candidate_set_id="cs",
            paper_identity_id=identity.artifact_id,
            relationship=CandidateRelationship(relationship),
            evidence_basis=EvidenceBasis.abstract,
            evidence_artifact_ids=[record.artifact_id],
        ),
        artifact_type="novelty_candidate_assessment",
        producer="test",
    )
    claim_assessment = ArtifactEnvelope.create(
        payload=NoveltyClaimAssessment(
            claim_id=claim.artifact_id,
            manuscript_id="m",
            search_plan_id="p",
            search_execution_id="e",
            candidate_set_id="cs",
            candidate_assessment_ids=[assessment.artifact_id],
            status=NoveltyClaimStatus(claim_status),
            coverage=NoveltyCoverage(coverage_sufficient=True, candidate_count=1),
        ),
        artifact_type="novelty_claim_assessment",
        producer="test",
    )
    report = ArtifactEnvelope.create(
        payload=NoveltyValidationReport(
            submission_package_id="pkg",
            manuscript_id="m",
            draft_id="d",
            manuscript_content_hash="h",
            as_of_date="2026-08-01",
            claim_ids=[claim.artifact_id],
            claim_assessment_ids=[claim_assessment.artifact_id],
            candidate_assessment_ids=[assessment.artifact_id],
            overall_status=NoveltyReportStatus(report_status),
        ),
        artifact_type="novelty_validation_report",
        producer="test",
    )
    return [record, identity, claim, assessment, claim_assessment, report]


def _case(reference: dict) -> BenchmarkCase:
    return BenchmarkCase(
        id="c1",
        benchmark_id="b",
        version=1,
        name="case",
        input={},
        reference=reference,
        evaluation_dimensions=["candidate_relationship", "claim_status", "report_status"],
        tags=[],
    )


async def test_deterministic_evaluator_passes_matching_case():
    evaluator = DeterministicEvaluator()
    case = _case(
        {
            "prior_art": [{"title": "The Prior Art Paper", "relationship": "direct_prior_art"}],
            "expected_claim_statuses": {CLAIM: "threatened"},
            "expected_report_status": "blocked",
        }
    )
    ctx = EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=_produced_artifacts(),
        config={"mode": "novelty_threat"},
    )
    result = await evaluator.evaluate(ctx)
    assert result.status == EvaluatorStatus.passed
    assert result.score == 1.0
    assert result.value["relationship_matches"] == 1
    assert result.value["claim_status_matches"] == 1
    assert result.value["report_status_match"] == 1
    assert result.value["false_clear_count"] == 0
    assert result.value["dimension_scores"] == {
        "candidate_relationship": 1.0,
        "claim_status": 1.0,
        "report_status": 1.0,
        "false_clear": 1.0,
    }


async def test_false_clear_is_measured():
    """Expected threatened, produced clear => false-clear detected and gating."""
    evaluator = DeterministicEvaluator()
    case = _case(
        {
            "prior_art": [{"title": "The Prior Art Paper", "relationship": "direct_prior_art"}],
            "expected_claim_statuses": {CLAIM: "threatened"},
            "expected_report_status": "blocked",
        }
    )
    ctx = EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=_produced_artifacts(
            relationship="distinct",
            claim_status="not_threatened_within_search_scope",
            report_status="clear",
        ),
        config={"mode": "novelty_threat"},
    )
    result = await evaluator.evaluate(ctx)
    assert result.status == EvaluatorStatus.failed
    assert result.value["false_clear_count"] == 1
    assert result.value["expected_at_risk"] == 1
    assert result.value["claim_status_matches"] == 0
    assert result.value["report_status_match"] == 0
    assert "FALSE CLEAR" in result.explanation
    assert result.value["dimension_scores"]["false_clear"] == 0.0


async def test_deterministic_evaluator_reference_equality():
    evaluator = DeterministicEvaluator()
    case = _case({})
    env = ArtifactEnvelope.create(
        payload=_ReportStatusPayload(overall_status="clear"),
        artifact_type="novelty_validation_report",
        producer="test",
    )
    ctx = EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=[env],
        config={
            "mode": "reference_equality",
            "artifact_type": "novelty_validation_report",
            "field": "overall_status",
            "expected": "clear",
        },
    )
    result = await evaluator.evaluate(ctx)
    assert result.status == EvaluatorStatus.passed
    assert result.score == 1.0


async def test_deterministic_evaluator_missing_report():
    evaluator = DeterministicEvaluator()
    case = _case({"expected_report_status": "clear"})
    ctx = EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=[],
        config={"mode": "novelty_threat"},
    )
    result = await evaluator.evaluate(ctx)
    assert result.status == EvaluatorStatus.failed
    assert result.score is None


# ---------------------------------------------------------------------------
# Model-assisted evaluators with fake models
# ---------------------------------------------------------------------------


async def test_llm_judge_with_fake_model():
    router = FakeRouter({"judge": {"score": 0.8, "status": "pass", "explanation": "solid"}})
    evaluator = LlmJudgeEvaluator(model_router=router, role="critic")
    case = _case({"expected_report_status": "clear"})
    env = ArtifactEnvelope.create(
        payload=_ReportStatusPayload(overall_status="clear"),
        artifact_type="novelty_validation_report",
        producer="test",
    )
    ctx = EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=[env],
        config={
            "judge_role": "critic",
            "llm_judge": {"system": "judge", "prompt_template": "judge it"},
        },
    )
    result = await evaluator.evaluate(ctx)
    assert result.status == EvaluatorStatus.passed
    assert result.score == 0.8
    assert result.category == EvaluatorCategory.model_assisted
    assert result.model_metadata["role"] == "critic"
    assert router.calls == 1


async def test_claim_grounding_with_fake_model():
    router = FakeRouter(
        {"grounded in the cited evidence": {"verdict": "grounded", "explanation": "ok"}}
    )
    evaluator = ClaimGroundingEvaluator(model_router=router, role="critic")
    produced = _produced_artifacts()
    claim = next(e for e in produced if e.artifact_type == "novelty_claim")
    case = _case({})
    ctx = EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=produced,
        config={"judge_role": "critic"},
    )
    result = await evaluator.evaluate(ctx)
    assert result.status == EvaluatorStatus.passed
    assert result.score == 1.0
    assert result.value == {
        "claims": 1,
        "grounded": 1,
        "partially_grounded": 0,
        "ungrounded": 0,
        "verdicts": {claim.artifact_id: "grounded"},
    }


async def test_claim_grounding_deterministic_unverified_without_model():
    router = FakeRouter({})
    evaluator = ClaimGroundingEvaluator(model_router=router, role="critic")
    claim = ArtifactEnvelope.create(
        payload=NoveltyClaim(
            manuscript_id="m",
            section_id="introduction",
            claim_text=CLAIM,
            claim_type=NoveltyClaimType.absolute_priority,
            risk="critical",
            importance="major",
            extraction_method="deterministic",
            source_quote=CLAIM,
        ),
        artifact_type="novelty_claim",
        producer="test",
    )
    case = _case({})
    ctx = EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=[claim],
        config={"judge_role": "critic"},
    )
    result = await evaluator.evaluate(ctx)
    assert result.status == EvaluatorStatus.failed
    assert result.score == 0.0
    assert result.value["ungrounded"] == 1
    assert router.calls == 0


# ---------------------------------------------------------------------------
# Aggregation / failure isolation / immutability / provenance
# ---------------------------------------------------------------------------


async def _seed_run_artifacts(store) -> tuple[str, str]:
    svc = _harness(store)
    await svc.register_benchmark(_benchmark())
    # execute the benchmark against fixtures: minimal case with empty
    # submission still produces a report (unverified path)
    run_id, report_id = await svc.run_benchmark("unit-bench")
    return run_id, report_id


async def test_run_aggregates_report_and_immutable_reput(store):
    svc = _harness(store)
    await svc.register_benchmark(_benchmark())
    run_id, report_id = await svc.run_benchmark("unit-bench")
    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert run.report_id == report_id
    assert report.run_id == run_id
    assert report.cases_total == 1
    assert report.benchmark_id == "unit-bench"
    metrics = {m.metric_id: m for m in report.metrics}
    assert "case_pass_rate" in metrics
    assert "report_status_accuracy" in metrics
    assert metrics["report_status_accuracy"].value in (0.0, 1.0)
    # immutability: re-putting the same artifact id is rejected
    env = await store.get(run_id)
    with pytest.raises(Exception):
        await store.put(env)
    # case result provenance is recorded
    parents = await store.get_parents(run_id)
    assert any(p.source_artifact_id == "unit-bench" for p in parents)


async def test_evaluator_failure_isolation(store):
    class BoomEvaluator:
        evaluator_id = "evaluator.boom"
        evaluator_version = "0.1.0"
        category = "model_assisted"

        async def evaluate(self, ctx):
            raise RuntimeError("boom")

    svc = _harness(
        store,
        evaluators={
            "evaluator.deterministic": DeterministicEvaluator(),
            "evaluator.boom": BoomEvaluator(),
        },
    )
    await svc.register_benchmark(
        BenchmarkDefinition(
            benchmark_id="unit-bench",
            version=1,
            name="Unit Benchmark",
            description="",
            category="unit",
            config={
                "mode": "novelty_threat",
                "evaluators": ["evaluator.deterministic", "evaluator.boom"],
            },
            cases=[_case_def()],
        )
    )
    run_id, _ = await svc.run_benchmark("unit-bench")
    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    # one evaluator errored; case status is error (isolated), run completed
    assert run.cases_total == 1
    results = [
        (await store.get(rid)).parse_payload(EvaluatorResult) for rid in run.evaluator_result_ids
    ]
    boom = [r for r in results if r.evaluator_id == "evaluator.boom"]
    assert boom and boom[0].status == EvaluatorStatus.error
    assert "boom" in boom[0].explanation


async def test_store_reopen_provenance(store, tmp_path: pathlib.Path):
    svc = _harness(store)
    await svc.register_benchmark(_benchmark())
    run_id, report_id = await svc.run_benchmark("unit-bench")
    await store.close()

    reopened = SQLiteArtifactStore(path=tmp_path / "art.db")
    env = await reopened.get(report_id)
    report = env.parse_payload(EvaluationReport)
    assert report.id == report_id
    run_env = await reopened.get(run_id)
    run = run_env.parse_payload(EvaluationRun)
    # chain: report -> run -> benchmark -> case (cases are downstream of the
    # benchmark)
    report_parents = await reopened.get_parents(report_id)
    assert {p.source_artifact_id for p in report_parents} == {run_id}
    run_parents = await reopened.get_parents(run_id)
    assert {p.source_artifact_id for p in run_parents} == {"unit-bench"}
    benchmark_children = await reopened.get_children("unit-bench")
    assert {c.target_artifact_id for c in benchmark_children} == {"c1", run_id}
    # run -> case results (plus the report)
    children = await reopened.get_children(run_id)
    child_ids = {c.target_artifact_id for c in children}
    assert set(run.case_result_ids) <= child_ids
    assert report_id in child_ids
    # evaluator results downstream of produced artifacts
    for cr_id in run.case_result_ids:
        parents = await reopened.get_parents(cr_id)
        assert any(p.source_artifact_id == run_id for p in parents)
    await reopened.close()


async def test_run_reproducibility_metadata(store):
    svc = _harness(store)
    await svc.register_benchmark(_benchmark())
    run_id, _ = await svc.run_benchmark("unit-bench")
    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    assert run.benchmark_content_hash
    assert run.case_hashes["c1"]
    assert run.evaluator_versions["evaluator.deterministic"]
    assert run.evaluation_config["judge_role"] == "critic"
    assert run.cost_usd >= 0.0
    assert run.latency_ms >= 0


async def test_skipped_evaluator_is_not_scored_as_passed(store):
    """An evaluator that declines to judge must not produce a passing case.

    ``EvaluatorStatus.skipped`` had no branch in the status ladder, so it fell
    through to ``passed``. The only producer emits it when nothing was
    produced to evaluate, i.e. a production failure was reported as a pass.
    """

    class SkippedEvaluator:
        evaluator_id = "evaluator.skipped"
        evaluator_version = "0.1.0"
        category = "deterministic"

        async def evaluate(self, ctx):
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                evaluator_version=self.evaluator_version,
                category=EvaluatorCategory.deterministic,
                status=EvaluatorStatus.skipped,
                value={"metrics": {}, "dimension_scores": {}},
                explanation="nothing produced to evaluate",
            )

    svc = _harness(
        store,
        evaluators={
            "evaluator.deterministic": DeterministicEvaluator(),
            "evaluator.skipped": SkippedEvaluator(),
        },
    )
    await svc.register_benchmark(
        BenchmarkDefinition(
            benchmark_id="unit-bench",
            version=1,
            name="Unit Benchmark",
            description="",
            category="unit",
            config={"mode": "novelty_threat", "evaluators": ["evaluator.skipped"]},
            cases=[_case_def()],
        )
    )
    run_id, report_id = await svc.run_benchmark("unit-bench")

    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    assert run.cases_total == 1
    assert run.cases_passed == 0
    assert run.cases_skipped == 1

    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert report.cases_passed == 0
    assert report.cases_skipped == 1
    assert report.status == EvaluationReportStatus.failed

    # The accounting invariant must still hold.
    assert (
        report.cases_passed
        + report.cases_failed
        + report.cases_error
        + report.cases_skipped
        == report.cases_total
    )


async def test_failure_takes_precedence_over_skipped(store):
    """A genuine deterministic failure must still outrank a skip."""

    class SkippedEvaluator:
        evaluator_id = "evaluator.skipped"
        evaluator_version = "0.1.0"
        category = "deterministic"

        async def evaluate(self, ctx):
            return EvaluatorResult(
                case_id=ctx.case.id,
                evaluator_id=self.evaluator_id,
                category=EvaluatorCategory.deterministic,
                status=EvaluatorStatus.skipped,
                value={},
                explanation="nothing produced to evaluate",
            )

    svc = _harness(
        store,
        evaluators={
            "evaluator.deterministic": DeterministicEvaluator(),
            "evaluator.skipped": SkippedEvaluator(),
        },
    )
    await svc.register_benchmark(
        BenchmarkDefinition(
            benchmark_id="unit-bench",
            version=1,
            name="Unit Benchmark",
            description="",
            category="unit",
            config={
                "mode": "novelty_threat",
                "evaluators": ["evaluator.deterministic", "evaluator.skipped"],
            },
            cases=[_case_def()],
        )
    )
    _run_id, report_id = await svc.run_benchmark("unit-bench")
    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    # Whatever the deterministic verdict, a skip must never become a pass.
    assert report.cases_passed == 0
    assert (
        report.cases_passed
        + report.cases_failed
        + report.cases_error
        + report.cases_skipped
        == report.cases_total
    )
