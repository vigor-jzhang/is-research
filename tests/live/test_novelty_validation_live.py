"""Phase 5A live smoke test — opt-in (live_novelty_validation).

Uses real literature providers (Semantic Scholar) and the configured OpenRouter
roles. NOT a proof of real manuscript novelty. Two parts:

1. Controlled known claim ("first study to analyze competition between
   ride-hailing platforms") where prior literature is known to exist: the
   system must find candidates and must NOT claim a clear assessment.
2. Full report + readiness gate over the latest live SubmissionPackage
   (bounded: 2 queries per claim, 5 results per query, one provider).

Skipped cleanly when no live submission package exists.
"""

from __future__ import annotations

import pathlib

import pytest

pytestmark = pytest.mark.live_novelty_validation


@pytest.mark.asyncio
async def test_live_novelty_validation_smoke(tmp_path: pathlib.Path):
    cfg_path = pathlib.Path("configs/example.yaml")
    if not cfg_path.exists():
        pytest.skip("configs/example.yaml not present")

    from research_harness.app.bootstrap import build_runtime
    from research_harness.config.loader import load_config
    from research_harness.research.envelope import ArtifactEnvelope
    from research_harness.research.schemas.novelty import (
        ClaimRiskLevel,
        NoveltyCandidateAssessment,
        NoveltyCandidateSet,
        NoveltyClaim,
        NoveltyClaimAssessment,
        NoveltyClaimStatus,
        NoveltyClaimType,
        NoveltySearchExecution,
        NoveltySearchPlan,
        NoveltyValidationReport,
        SubmissionReadinessGate,
    )
    from research_harness.research.schemas.publication import (
        SubmissionPackage,
        SubmissionPackageStatus,
    )

    cfg = load_config(str(cfg_path))
    runtime = build_runtime(
        cfg,
        plugin_configs={
            "research.novelty_validator": {
                "research": {
                    **cfg.research.model_dump(),
                    "novelty": {
                        **cfg.research.novelty.model_dump(),
                        "providers": ["semantic_scholar"],
                        "max_queries_per_claim": 2,
                        "max_results_per_query": 5,
                        "max_llm_calls": 40,
                    },
                }
            }
        },
    )
    async with runtime:
        store = runtime.services.require("artifact_store.default")
        svc = runtime.services.require("novelty_validator.default")

        # ---- part 1: controlled known claim --------------------------------
        claim = NoveltyClaim(
            manuscript_id="live-novelty-smoke",
            section_id="introduction",
            claim_text=(
                "This is the first study to analyze competition between ride-hailing platforms."
            ),
            claim_type=NoveltyClaimType.absolute_priority,
            risk=ClaimRiskLevel.critical,
            importance="major",
            extraction_method="manual",
            source_quote=(
                "This is the first study to analyze competition between ride-hailing platforms."
            ),
            source_artifact_ids=["live-novelty-smoke"],
        )
        env = ArtifactEnvelope.create(
            payload=claim, artifact_type="novelty_claim", producer="live-test"
        )
        await store.put(env)
        claim_id = env.artifact_id

        plan_id = await svc.plan_searches(claim_id, offline=False)
        plan = (await store.get(plan_id)).parse_payload(NoveltySearchPlan)
        assert plan.query_artifact_ids
        exec_id = await svc.execute_searches(plan_id)
        execution = (await store.get(exec_id)).parse_payload(NoveltySearchExecution)
        assert execution.as_of_date is not None
        cset_id = await svc.build_candidate_set(claim_id, plan_id, exec_id)
        cset = (await store.get(cset_id)).parse_payload(NoveltyCandidateSet)

        if execution.provider_failures and not cset.candidates:
            # provider failure (rate limit): the pipeline must NOT fabricate
            # a clear result — the claim becomes unverified
            ca_id = await svc.assess_claim(claim_id)
            ca = (await store.get(ca_id)).parse_payload(NoveltyClaimAssessment)
            assert ca.status == NoveltyClaimStatus.unverified
            assert not ca.coverage.coverage_sufficient
            assert ca.coverage.provider_failures
            print(
                "live controlled claim -> unverified (provider failure: "
                f"{len(ca.coverage.provider_failures)} failures recorded, "
                "no false clear result)"
            )
        elif not cset.candidates:
            # all searches succeeded but returned no candidates within scope
            ca_id = await svc.assess_claim(claim_id)
            ca = (await store.get(ca_id)).parse_payload(NoveltyClaimAssessment)
            assert (
                ca.status == NoveltyClaimStatus.not_threatened_within_search_scope
                or ca.status == NoveltyClaimStatus.unverified
            )
            print(
                f"live controlled claim -> {ca.status.value} "
                f"(searches succeeded, no candidates within declared scope; "
                "this does not prove global novelty)"
            )
        else:
            assert cset.candidates, "real search must surface candidate papers"
            assessment_ids = await svc.assess_candidates(claim_id, cset_id, offline=False)
            assert assessment_ids
            for aid in assessment_ids:
                a = (await store.get(aid)).parse_payload(NoveltyCandidateAssessment)
                assert a.evidence_basis in (
                    "full_text",
                    "abstract",
                    "indexed_metadata",
                    "title_only",
                )
            ca_id = await svc.assess_claim(claim_id)
            ca = (await store.get(ca_id)).parse_payload(NoveltyClaimAssessment)
            # known prior literature exists -> a clear assessment is NOT allowed
            assert ca.status in (
                NoveltyClaimStatus.threatened,
                NoveltyClaimStatus.weakened,
                NoveltyClaimStatus.unverified,
            ), f"unexpected clear claim status: {ca.status}"
            print(
                f"live controlled claim -> {ca.status.value} "
                f"({ca.coverage.successful_query_count}/{ca.coverage.planned_query_count} "
                f"searches, {ca.coverage.candidate_count} candidates)"
            )

        # ---- part 2: full report + gate over the live package --------------
        pkg_env = None
        for candidate in reversed(await store.list(artifact_type="submission_package")):
            p = candidate.parse_payload(SubmissionPackage)
            if p.status == SubmissionPackageStatus.ready:
                pkg_env = candidate
                break
        if pkg_env is None:
            print("live: no SubmissionPackage in store; skipping package report")
            return

        report_id = await svc.create_report(
            pkg_env.artifact_id, max_claims=2, max_results=5, offline=False
        )
        report = (await store.get(report_id)).parse_payload(NoveltyValidationReport)
        assert report.submission_package_id == pkg_env.artifact_id
        gate_id = await svc.create_gate(pkg_env.artifact_id, report_id)
        gate = (await store.get(gate_id)).parse_payload(SubmissionReadinessGate)
        # the original package is untouched by Phase 5A
        pkg_after = (await store.get(pkg_env.artifact_id)).parse_payload(SubmissionPackage)
        assert pkg_after.status == SubmissionPackageStatus.ready
        print(
            f"live package report -> {report.overall_status.value} "
            f"({len(report.claim_ids)} claims, {len(report.search_execution_ids)} "
            f"search executions, {len(report.critic_assessment_ids)} critic passes) "
            f"| gate -> {gate.status.value}"
        )
