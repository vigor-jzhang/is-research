"""Regression tests for round 38 — the four partial Low findings.

L34: the config schema fabricated a ``{0.0, 0.0}`` pricing table, so every
unconfigured run reported $0.00 (and the harness's None-warning could never
fire from a config-driven run). Unset pricing is now None, and the report/run
record ``cost_configured`` so the 0.0 is interpretable; the cost metric is
reported as not measured.

L8: the generator writes ``Proposition.status = candidate`` and the
append-only store can never update it, so the one live consumer of that field
(the refutation guard in numerical_analysis) never fired. It now derives the
effective status from the newest PropositionVerification.

L29: ``effective_pricing`` fell back to a provider-pricing config section
that does not exist anywhere in the schema — dead code, removed.

L38: ``assess_candidates`` re-listed the evidence-item and full-text-document
tables once per candidate; the listings now run once per assessment and
refresh only after enrichment.
"""

from __future__ import annotations

import pathlib
from datetime import date

import pytest

from research_harness.config.schema import AppConfig
from research_harness.research.envelope import ArtifactEnvelope
from tests.unit.test_evaluation_harness import _benchmark, _harness
from tests.unit.test_novelty import (
    FakeSource,
    KeyedRouter,
    _lookup,
    _paper,
)
from tests.unit.test_scientific_core_followon import (
    CAND_ID,
    EXEC_ID,
    MODEL_ID,
    _proposition,
    _result,
    _svc,
)

# ---------------------------------------------------------------------------
# L34 — cost defaults
# ---------------------------------------------------------------------------


def test_cost_pricing_config_default_is_none() -> None:
    """The schema must not fabricate a {0.0, 0.0} pricing table."""
    assert AppConfig().evaluation.cost_per_million_tokens is None


@pytest.mark.asyncio
async def test_unconfigured_cost_is_marked_not_measured(tmp_path: pathlib.Path) -> None:
    from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
    from research_harness.research.schemas.evaluation import EvaluationReport, EvaluationRun

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = _harness(store)
    await svc.register_benchmark(_benchmark())
    run_id, report_id = await svc.run_benchmark("unit-bench")

    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    run = (await store.get(run_id)).parse_payload(EvaluationRun)
    assert report.metadata.get("cost_configured") is False
    assert run.metadata.get("cost_configured") is False
    metric = next(m for m in report.metrics if m.metric_id == "execution_cost_usd")
    assert metric.measured is False
    assert metric.count == 0
    await store.close()


@pytest.mark.asyncio
async def test_configured_cost_is_measured(tmp_path: pathlib.Path) -> None:
    from research_harness.plugins.research.evaluation_harness.plugin import (
        EvaluationHarnessService,
    )
    from research_harness.plugins.research.evaluator_deterministic.plugin import (
        DeterministicEvaluator,
    )
    from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
    from research_harness.research.schemas.evaluation import EvaluationReport

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    svc = EvaluationHarnessService(
        artifact_store=store,
        ingestor=None,
        identity_resolver=None,
        evaluators={"evaluator.deterministic": DeterministicEvaluator()},
        config={"evaluators": ["evaluator.deterministic"]},
        cost_per_million_tokens={"prompt": 1.0, "completion": 2.0},
    )
    await svc.register_benchmark(_benchmark())
    _run_id, report_id = await svc.run_benchmark("unit-bench")

    report = (await store.get(report_id)).parse_payload(EvaluationReport)
    assert report.metadata.get("cost_configured") is True
    metric = next(m for m in report.metrics if m.metric_id == "execution_cost_usd")
    assert metric.measured is True
    assert metric.count == 1
    await store.close()


# ---------------------------------------------------------------------------
# L8 — derived proposition status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refutation_via_verification_blocks_numerical_support(
    tmp_path: pathlib.Path,
) -> None:
    """The stored status stays `candidate` (the store is append-only); the
    refutation guard must consult the newest verification instead."""
    import sympy

    from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
    from research_harness.research.schemas.numerical import RobustnessCheck
    from research_harness.research.schemas.proposition import (
        PropositionStatus,
        PropositionVerification,
        PropositionVerificationStatus,
    )

    store = SQLiteArtifactStore(path=tmp_path / "a.db")
    prop_id = await _proposition(
        store, status=PropositionStatus.candidate, expected_sign="positive"
    )
    verification = PropositionVerification(
        proposition_id=prop_id,
        model_id=MODEL_ID,
        status=PropositionVerificationStatus.failed,
        checks=[],
    )
    await store.put(
        ArtifactEnvelope.create(
            payload=verification,
            artifact_type="proposition_verification",
            producer="test",
        )
    )

    check_ids = await _svc(store)._robustness(
        EXEC_ID,
        MODEL_ID,
        CAND_ID,
        [_result(a=2.0, c=1.0)],
        {},
        {"profit": sympy.Symbol("a")},
    )
    checks = [(await store.get(cid)).parse_payload(RobustnessCheck) for cid in check_ids]
    prop_checks = [c for c in checks if c.proposition_id]
    assert prop_checks, "no proposition check was produced"
    assert prop_checks[0].outcome.value == "not_testable"
    assert "failed" in prop_checks[0].conclusion
    await store.close()


# ---------------------------------------------------------------------------
# L29 — dead pricing resolver removed
# ---------------------------------------------------------------------------


def test_effective_pricing_dead_resolver_removed() -> None:
    from research_harness.research.tournament import accounting

    assert not hasattr(accounting, "effective_pricing")


# ---------------------------------------------------------------------------
# L38 — evidence listings hoisted out of the candidate loop
# ---------------------------------------------------------------------------


class _ListCountingStore:
    """Delegates everything; counts `list()` calls per artifact type."""

    def __init__(self, inner):
        self._inner = inner
        self.list_calls: dict[str, int] = {}

    def __getattr__(self, name):  # noqa: ANN204
        return getattr(self._inner, name)

    async def list(self, *args, **kwargs):
        key = kwargs.get("artifact_type") or (args[0] if args else "?")
        self.list_calls[key] = self.list_calls.get(key, 0) + 1
        return await self._inner.list(*args, **kwargs)


@pytest.mark.asyncio
async def test_assessment_lists_evidence_tables_once(tmp_path: pathlib.Path) -> None:
    from research_harness.plugins.literature.identity_resolver.plugin import (
        PaperIdentityResolverService,
    )
    from research_harness.plugins.literature.ingestion.plugin import LiteratureIngestor
    from research_harness.plugins.research.novelty_validator.plugin import (
        NoveltyValidationService,
    )
    from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
    from research_harness.research.schemas.novelty import (
        ClaimRiskLevel,
        NoveltyCandidateSet,
        NoveltyClaim,
        NoveltyClaimType,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    counting = _ListCountingStore(store)
    sources = {"semantic_scholar": FakeSource("semantic_scholar", {})}
    svc = NoveltyValidationService(
        model_router=KeyedRouter({}, default=None),
        artifact_store=counting,
        ingestor=LiteratureIngestor(artifact_store=counting),
        identity_resolver=PaperIdentityResolverService(artifact_store=counting),
        service_lookup=_lookup(sources),
        max_llm_calls=100,
        providers=["semantic_scholar"],
    )
    # keep the count deterministic: no enrichment refreshes
    svc._enrichment_enabled = False

    sources["semantic_scholar"].catchall = [
        _paper("Alpha Work", 2019, "10.1/a", "Abstract about alpha mechanisms."),
        _paper("Beta Work", 2020, "10.1/b", "Abstract about beta mechanisms."),
        _paper("Gamma Work", 2021, "10.1/c", "Abstract about gamma mechanisms."),
    ]

    claim = NoveltyClaim(
        manuscript_id="m1",
        section_id="introduction",
        claim_text="We provide fresh evidence on platform competition.",
        claim_type=NoveltyClaimType.absolute_priority,
        risk=ClaimRiskLevel.medium,
        extraction_method="deterministic",
        source_quote="fresh evidence",
    )
    claim_id = await svc._put(claim, "novelty_claim")
    plan_id = await svc.plan_searches(claim_id, as_of=date(2026, 8, 23), offline=True)
    exec_id = await svc.execute_searches(plan_id)
    cset_id = await svc.build_candidate_set(claim_id, plan_id, exec_id)
    cset = (await store.get(cset_id)).parse_payload(NoveltyCandidateSet)
    assert len(cset.candidates) == 3, "fixture must produce three candidates"

    await svc.assess_candidates(claim_id, cset_id, offline=True)

    # pre-fix these were one call per candidate (3 each)
    assert counting.list_calls.get("evidence_item", 0) == 1
    assert counting.list_calls.get("full_text_document", 0) == 1
    await store.close()
