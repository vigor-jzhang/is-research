"""Live propositions smoke test — opt-in (live_propositions).

Requires OPENROUTER_API_KEY + an EquilibriumAnalysis with a verified candidate
in the store. Skips cleanly otherwise. Structural validity and symbolic
grounding only; no claim of economic validity is made.
"""

import os
import pathlib

import pytest

pytestmark = pytest.mark.live_propositions


@pytest.mark.asyncio
async def test_live_propositions_smoke(tmp_path: pathlib.Path):
    if not os.getenv("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")

    cfg_path = pathlib.Path("configs/example.yaml")
    if not cfg_path.exists():
        pytest.skip("configs/example.yaml not present")

    from research_harness.app.bootstrap import build_runtime
    from research_harness.config.loader import load_config
    from research_harness.research.schemas.equilibrium import (
        EquilibriumAnalysis,
        EquilibriumVerification,
        VerificationStatus,
    )
    from research_harness.research.schemas.proposition import (
        ComparativeStaticsAnalysis,
        ComparativeStaticsExecution,
        EconomicInterpretation,
        Proposition,
        PropositionCritique,
        PropositionVerification,
        PropositionVerificationStatus,
    )

    cfg = load_config(str(cfg_path))
    runtime = build_runtime(cfg)
    async with runtime:
        store = runtime.services.require("artifact_store.default")

        # pick an EquilibriumAnalysis whose selected candidate is verified
        eq_analysis_id = None
        for env in reversed(await store.list(artifact_type="equilibrium_analysis")):
            a = env.parse_payload(EquilibriumAnalysis)
            if not a.selected_candidate_id:
                continue
            verified = False
            for vid in a.verification_ids:
                v = (await store.get(vid)).parse_payload(EquilibriumVerification)
                if v.candidate_id == a.selected_candidate_id and v.status in (
                    VerificationStatus.verified,
                    VerificationStatus.partially_verified,
                ):
                    verified = True
                    break
            if verified:
                eq_analysis_id = env.artifact_id
                break
        if not eq_analysis_id:
            pytest.skip("no EquilibriumAnalysis with a verified candidate in store")

        # ---- 1. Comparative statics (deterministic) --------------------------
        cs_svc = runtime.services.require("comparative_statics.default")
        existing = await store.list(artifact_type="comparative_statics_execution")
        cs_exec = None
        for env in existing:
            rec = env.parse_payload(ComparativeStaticsExecution)
            if (
                rec.equilibrium_candidate_id
                == (await store.get(eq_analysis_id))
                .parse_payload(EquilibriumAnalysis)
                .selected_candidate_id
            ):
                cs_exec = env.artifact_id
                break
        if cs_exec is None:
            cs_exec = await cs_svc.run(eq_analysis_id)
        rec = (await store.get(cs_exec)).parse_payload(ComparativeStaticsExecution)
        assert rec.statics_created >= 1
        cs_id = await cs_svc.resolve_analysis(cs_exec)
        cs = (await store.get(cs_id)).parse_payload(ComparativeStaticsAnalysis)
        assert cs.static_ids

        # ---- 2. Propositions (generate -> verify -> critique -> interpret) ----
        gen_svc = runtime.services.require("proposition_generator.default")
        existing_props = [
            env.parse_payload(Proposition) for env in await store.list(artifact_type="proposition")
        ]
        if any(p.comparative_statics_analysis_id == cs_id for p in existing_props):
            prop_ids = [
                env.artifact_id
                for env in await store.list(artifact_type="proposition")
                if env.parse_payload(Proposition).comparative_statics_analysis_id == cs_id
            ]
        else:
            prop_ids = await gen_svc.generate(cs_id)
        assert prop_ids

        verifications = [
            env.parse_payload(PropositionVerification)
            for env in await store.list(artifact_type="proposition_verification")
            if env.parse_payload(PropositionVerification).proposition_id in prop_ids
        ]
        assert verifications
        # every proposition has a verification; none may be marked verified
        # without the symbolic checks (statuses are symbolic outcomes)
        for v in verifications:
            assert v.status in (
                PropositionVerificationStatus.verified,
                PropositionVerificationStatus.conditionally_verified,
                PropositionVerificationStatus.failed,
            )
            assert v.checks
            # monotonicity claims must ground in a supporting static
            for p_env in await store.list(artifact_type="proposition"):
                p = p_env.parse_payload(Proposition)
                if p.claim_type.value == "monotonicity":
                    assert p.supporting_static_ids

        critiques = [
            env.parse_payload(PropositionCritique)
            for env in await store.list(artifact_type="proposition_critique")
            if env.parse_payload(PropositionCritique).proposition_id in prop_ids
        ]
        assert critiques  # critiqued when not failed

        interps = [
            env.parse_payload(EconomicInterpretation)
            for env in await store.list(artifact_type="economic_interpretation")
            if env.parse_payload(EconomicInterpretation).proposition_id in prop_ids
        ]
        assert interps

        # provenance: interpretation -> proposition -> static -> candidate
        for env in await store.list(artifact_type="economic_interpretation"):
            i = env.parse_payload(EconomicInterpretation)
            if i.proposition_id not in prop_ids:
                continue
            parents = await store.get_parents(env.artifact_id)
            assert any(p.source_artifact_id == i.proposition_id for p in parents)
        for pid in prop_ids:
            p = (await store.get(pid)).parse_payload(Proposition)
            assert p.equilibrium_candidate_id == cs.equilibrium_candidate_id

        print(
            f"live propositions: statics {rec.statics_created} propositions {len(prop_ids)} "
            f"verifications {len(verifications)} critiques {len(critiques)} interpretations {len(interps)}"
        )
