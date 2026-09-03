"""Phase 7C unit tests — evidence-enrichment evaluator."""

from __future__ import annotations

from research_harness.contracts.evaluator import EvaluatorContext
from research_harness.plugins.research.evaluator_evidence_enrichment.plugin import (
    EvidenceEnrichmentEvaluator,
)
from research_harness.research.benchmarks.workflows import (
    EnrichmentRunRecord,
    EvidenceEnrichmentReport,
)
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evaluation import BenchmarkCase, EvaluatorStatus
from research_harness.research.schemas.novelty import (
    EnrichmentAttemptStatus,
    EnrichmentOutcome,
    EvidenceBasis,
    EvidenceEnrichmentAttempt,
    EvidenceEnrichmentExecution,
    EvidenceEnrichmentPlan,
)


def _plan(
    pid: str = "plan-1", identity: str = "identity-1", claim: str = "claim-1"
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=EvidenceEnrichmentPlan(
            candidate_assessment_id=None,
            paper_identity_id=identity,
            claim_id=claim,
            requested_evidence_types=["abstract"],
            acquisition_strategies=["provider_get_abstract"],
            reason="title_only is insufficient",
            provider_service_policy={"abstract_providers": ["semantic_scholar"]},
        ),
        artifact_type="evidence_enrichment_plan",
        producer="test",
        artifact_id=pid,
    )


def _attempt(
    aid: str = "attempt-1",
    status: EnrichmentAttemptStatus = EnrichmentAttemptStatus.success,
    retrieved: list[str] | None = None,
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=EvidenceEnrichmentAttempt(
            plan_id="plan-1",
            strategy="provider_get_abstract",
            provider="semantic_scholar",
            status=status,
            retrieved_artifact_ids=list(retrieved or []),
        ),
        artifact_type="evidence_enrichment_attempt",
        producer="test",
        artifact_id=aid,
    )


def _item(iid: str = "item-1") -> ArtifactEnvelope:
    from research_harness.research.schemas.evidence import EvidenceCategory, EvidenceItem

    return ArtifactEnvelope.create(
        payload=EvidenceItem(
            statement="abstract text",
            source_artifact_id="paper-acquired",
            category=EvidenceCategory.result,
            extraction_method="provider_import",
            metadata={"novelty_enrichment": True},
        ),
        artifact_type="evidence_item",
        producer="test",
        artifact_id=iid,
    )


def _identity(identity: str = "identity-1") -> ArtifactEnvelope:
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod

    return ArtifactEnvelope.create(
        payload=PaperIdentity(
            member_paper_artifact_ids=["paper-sparse"],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.manual,
            resolution_evidence=[],
        ),
        artifact_type="paper_identity",
        producer="test",
        artifact_id=identity,
    )


def _execution(
    eid: str = "exec-1",
    plan_id: str = "plan-1",
    attempt_ids: list[str] | None = None,
    result_ids: list[str] | None = None,
    before: EvidenceBasis = EvidenceBasis.title_only,
    after: EvidenceBasis = EvidenceBasis.abstract,
    outcome: EnrichmentOutcome = EnrichmentOutcome.enriched,
) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=EvidenceEnrichmentExecution(
            plan_id=plan_id,
            attempt_ids=list(attempt_ids or ["attempt-1"]),
            resulting_evidence_ids=list(result_ids or ["item-1"]),
            before_evidence_basis=before,
            after_evidence_basis=after,
            outcome=outcome,
        ),
        artifact_type="evidence_enrichment_execution",
        producer="test",
        artifact_id=eid,
    )


def _report(runs: list[dict]) -> ArtifactEnvelope:
    return ArtifactEnvelope.create(
        payload=EvidenceEnrichmentReport(
            benchmark_case_id="c1",
            runs=[
                EnrichmentRunRecord(
                    label=str(run.get("label") or "run"),
                    report_id=str(run.get("report_id") or "rep"),
                    enrichment_execution_ids=list(run.get("execution_ids") or []),
                    preacquisition_execution_ids=list(run.get("pre_ids") or []),
                    candidate_bases=dict(run.get("candidate_bases") or {}),
                )
                for run in runs
            ],
        ),
        artifact_type="evidence_enrichment_report",
        producer="test",
    )


def _case(reference: dict) -> BenchmarkCase:
    return BenchmarkCase(
        id="c1",
        benchmark_id="b",
        version=1,
        name="case",
        input={},
        reference=reference,
        evaluation_dimensions=["evidence_enrichment"],
        tags=[],
    )


def _ctx(case: BenchmarkCase, produced: list, provenance: dict | None = None) -> EvaluatorContext:
    return EvaluatorContext(
        case=case,
        case_envelope=ArtifactEnvelope.create(
            payload=case, artifact_type="benchmark_case", producer="test"
        ),
        produced_artifacts=produced,
        config={},
        provenance=provenance or {},
    )


async def test_no_report_fails():
    result = await EvidenceEnrichmentEvaluator().evaluate(_ctx(_case({}), []))
    assert result.status == EvaluatorStatus.failed


async def test_enriched_grounded_passes():
    ref = {
        "expected_run_count": 1,
        "expected_outcomes": ["enriched"],
        "expected_before_basis": ["title_only"],
        "expected_after_basis": ["abstract"],
        "expected_attempt_statuses": ["success"],
        "expected_grounded": True,
    }
    produced = [
        _report([{"label": "baseline", "execution_ids": ["exec-1"]}]),
        _plan(),
        _attempt(retrieved=["item-1"]),
        _item(),
        _identity(),
        _execution(),
    ]
    result = await EvidenceEnrichmentEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["dimension_scores"]["enrichment_grounding_accuracy"] == 1.0


async def test_unsupported_rejection_no_invention_passes():
    ref = {
        "expected_run_count": 1,
        "expected_outcomes": ["failed"],
        "expected_before_basis": ["title_only"],
        "expected_after_basis": ["title_only"],
        "expected_attempt_statuses": ["not_found"],
        "expected_no_invented_evidence": True,
        "expected_grounded": True,
    }
    produced = [
        _report([{"label": "baseline", "execution_ids": ["exec-1"]}]),
        _plan(),
        _attempt(status=EnrichmentAttemptStatus.not_found, retrieved=[]),
        _identity(),
        _execution(
            attempt_ids=["attempt-1"],
            result_ids=[],
            before=EvidenceBasis.title_only,
            after=EvidenceBasis.title_only,
            outcome=EnrichmentOutcome.failed,
        ),
    ]
    result = await EvidenceEnrichmentEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.passed
    assert result.value["dimension_scores"]["unsupported_rejection_accuracy"] == 1.0


async def test_wrong_outcome_fails():
    ref = {
        "expected_run_count": 1,
        "expected_outcomes": ["failed"],
    }
    produced = [
        _report([{"label": "baseline", "execution_ids": ["exec-1"]}]),
        _execution(outcome=EnrichmentOutcome.enriched),
    ]
    result = await EvidenceEnrichmentEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.failed


async def test_stale_reuse_across_runs_fails():
    ref = {
        "expected_run_count": 2,
        "expected_outcomes": ["enriched", "failed"],
        "expected_executions_differ": True,
    }
    produced = [
        _report(
            [
                {"label": "baseline", "execution_ids": ["exec-1"]},
                {"label": "changed", "execution_ids": ["exec-1"]},
            ]
        ),
    ]
    result = await EvidenceEnrichmentEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.failed
    assert result.value["dimension_scores"]["stale_reuse_rate"] > 0.0


async def test_enriched_execution_without_basis_upgrade_fails():
    ref = {
        "expected_run_count": 1,
        "expected_outcomes": ["enriched"],
        "expected_grounded": True,
    }
    produced = [
        _report([{"label": "baseline", "execution_ids": ["exec-1"]}]),
        _execution(before=EvidenceBasis.title_only, after=EvidenceBasis.title_only),
    ]
    result = await EvidenceEnrichmentEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.failed


async def test_source_preserved_when_expected():
    ref = {"expected_run_count": 1, "expected_source_preserved": True}
    produced = [
        _report([{"label": "baseline", "execution_ids": ["exec-1"]}]),
        _execution(),
    ]
    from research_harness.research.schemas.paper import PaperRecord

    produced.append(
        ArtifactEnvelope.create(
            payload=PaperRecord(title="sparse"), artifact_type="paper_record", producer="test"
        )
    )
    result = await EvidenceEnrichmentEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.status == EvaluatorStatus.passed


# --- regression: stale_reuse_rate and provenance_version_accuracy ---------
#
# ``stale_reuse_rate`` used ``max(stale, 1)`` as its denominator, so it was
# always exactly 0.0 or 1.0 -- a boolean dressed as a rate, useless for
# trending. ``provenance_version_accuracy`` was a copy of
# grounding_hits/grounding_total, so the declared check ("execution -> plan ->
# identity links hold") was never actually performed.


async def test_stale_reuse_rate_is_a_proportion_not_a_flag():
    """2 of 3 enrichments in the second run are stale -> 2/3, not 1.0."""
    ref = {
        "expected_run_count": 2,
        "expected_outcomes": ["enriched", "enriched", "enriched"],
    }
    produced = [
        _report(
            [
                {"label": "baseline", "execution_ids": ["e1", "e2", "e3"]},
                {"label": "changed", "execution_ids": ["e1", "e2", "e4"]},
            ]
        ),
    ]
    result = await EvidenceEnrichmentEvaluator().evaluate(_ctx(_case(ref), produced))

    rate = result.value["dimension_scores"]["stale_reuse_rate"]
    # e1 and e2 are reused from the baseline; e4 is new -> 2 of 3 eligible.
    assert rate == 2 / 3, f"stale_reuse_rate was {rate}, expected 2/3"


async def test_stale_reuse_rate_is_zero_when_nothing_is_reused():
    ref = {
        "expected_run_count": 2,
        "expected_outcomes": ["enriched", "enriched"],
    }
    produced = [
        _report(
            [
                {"label": "baseline", "execution_ids": ["e1"]},
                {"label": "changed", "execution_ids": ["e2"]},
            ]
        ),
    ]
    result = await EvidenceEnrichmentEvaluator().evaluate(_ctx(_case(ref), produced))
    assert result.value["dimension_scores"]["stale_reuse_rate"] == 0.0


async def test_provenance_metric_is_not_a_copy_of_grounding():
    """provenance_version_accuracy must measure provenance, not grounding."""
    ref = {
        "expected_run_count": 1,
        "expected_outcomes": ["enriched"],
        "expected_grounded": True,
    }
    produced = [
        _report([{"label": "baseline", "execution_ids": ["e1"]}]),
        _execution(eid="e1", outcome=EnrichmentOutcome.enriched, plan_id="plan-1"),
        _plan(pid="plan-1"),
    ]
    # No provenance supplied, so the provenance check reports "not measured"
    # instead of silently reusing the grounding figure.
    result = await EvidenceEnrichmentEvaluator().evaluate(_ctx(_case(ref), produced))

    metrics = result.value["metrics"]
    grounding = metrics["enrichment_grounding_accuracy"]
    provenance = metrics["provenance_version_accuracy"]

    # Grounding was measured here; provenance had no links to measure.
    assert grounding["count"] > 0
    assert provenance["count"] == 0, "provenance claimed a measurement without any links"
    assert provenance["value"] != grounding["value"] or (
        provenance["count"] != grounding["count"]
    )
