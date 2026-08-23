"""Live results assembly smoke test — opt-in (live_results_assembly).

Uses one completed Phase 3 chain (a NumericalExperiment in the live store).
Assembles a ResearchResultsPackage with the real model (needs
OPENROUTER_API_KEY) and verifies structural grounding only:
- all referenced ids exist and are verified (propositions), results exist;
- conditions preserved;
- contribution links to the gap + findings;
- provenance package -> contribution -> finding -> proposition/result.
"""

import pathlib

import pytest

pytestmark = pytest.mark.live_results_assembly


@pytest.mark.asyncio
async def test_live_results_assembly_smoke(tmp_path: pathlib.Path):
    cfg_path = pathlib.Path("configs/example.yaml")
    if not cfg_path.exists():
        pytest.skip("configs/example.yaml not present")

    from research_harness.app.bootstrap import build_runtime
    from research_harness.config.loader import load_config
    from research_harness.research.envelope import ArtifactEnvelope
    from research_harness.research.schemas.numerical import NumericalExperiment
    from research_harness.research.schemas.results import (
        ContributionClaim,
        ResearchFinding,
        ResearchResultsPackage,
        ResultsAssemblyExecution,
    )

    cfg = load_config(str(cfg_path))
    runtime = build_runtime(cfg)
    async with runtime:
        store = runtime.services.require("artifact_store.default")

        exp_env = None
        for env in reversed(await store.list(artifact_type="numerical_experiment")):
            exp = env.parse_payload(NumericalExperiment)
            if exp.status == "completed" and exp.results:
                exp_env = env
                break
        if exp_env is None:
            pytest.skip("no completed NumericalExperiment with results in store")
        exp = exp_env.parse_payload(NumericalExperiment)

        from research_harness.research.schemas.model import FormalAnalyticalModel

        model = (await store.get(exp.model_id)).parse_payload(FormalAnalyticalModel)
        if not await store.exists(model.selected_mechanism_id):
            # The operator-seeded Cournot game (Phase 3C) references mechanism id
            # 'operator' without artifacts; seed the operator placeholders so the
            # 4A chain is complete (same operator-seeding pattern as Phase 3C).
            from research_harness.research.schemas.gap import GapAnalysis, GapType, ResearchGap
            from research_harness.research.schemas.mechanism import SelectedMechanism

            gap_id = "operator-gap"
            rg = ResearchGap(
                title="operator-seeded platform game",
                gap_type=GapType.theoretical_gap,
                description="operator-seeded placeholder gap for the seeded Cournot platform game",
            )
            await store.put(
                ArtifactEnvelope.create(
                    payload=rg,
                    artifact_type="research_gap",
                    producer="operator",
                    artifact_id=gap_id,
                )
            )
            ga = GapAnalysis(
                literature_synthesis_id="operator",
                evidence_corpus_id="operator",
                research_question_id="operator-rq",
                gap_ids=[gap_id],
            )
            await store.put(
                ArtifactEnvelope.create(
                    payload=ga, artifact_type="gap_analysis", producer="operator"
                )
            )
            mech = SelectedMechanism(
                gap_id=gap_id,
                gap_selection_id="operator",
                mechanism_candidate_id="operator",
                name="operator-seeded Cournot competition",
                description="operator-seeded quantity competition game",
                actors=["f1", "f2"],
                strategic_interactions=["quantity"],
                causal_logic="q",
                key_assumptions=[],
                expected_outcomes=[],
                boundary_conditions=[],
                grounding=[],
            )
            await store.put(
                ArtifactEnvelope.create(
                    payload=mech,
                    artifact_type="selected_mechanism",
                    producer="operator",
                    artifact_id=model.selected_mechanism_id,
                )
            )

        svc = runtime.services.require("results_assembler.default")
        # idempotent: reuse existing assembly for this experiment
        existing = None
        for env in await store.list(artifact_type="results_assembly_execution"):
            try:
                rec = env.parse_payload(ResultsAssemblyExecution)
            except Exception:
                continue
            if rec.numerical_experiment_id == exp_env.artifact_id and rec.completed_at is not None:
                existing = env.artifact_id
                break
        exec_id = existing if existing is not None else await svc.assemble(exp_env.artifact_id)
        rec = (await store.get(exec_id)).parse_payload(ResultsAssemblyExecution)
        assert rec.findings_created >= 1
        assert rec.contributions_created >= 1

        pkg_env = None
        for env in await store.list(artifact_type="results_package"):
            pkg = env.parse_payload(ResearchResultsPackage)
            if pkg.numerical_experiment_id == exp_env.artifact_id:
                pkg_env = env
                break
        assert pkg_env is not None
        pkg = pkg_env.parse_payload(ResearchResultsPackage)

        # structural grounding: finding -> verified proposition + real results
        result_ids = set(exp.results)
        for fid in pkg.finding_ids:
            f = (await store.get(fid)).parse_payload(ResearchFinding)
            for rid in f.supporting_numerical_result_ids:
                assert rid in result_ids
            # conditions preserved vs referenced propositions
            from research_harness.research.schemas.proposition import Proposition

            for pid in f.supporting_proposition_ids:
                prop = (await store.get(pid)).parse_payload(Proposition)
                for cond in prop.conditions:
                    assert cond in f.conditions

        # contribution links the gap + findings
        for cid in pkg.contribution_claim_ids:
            c = (await store.get(cid)).parse_payload(ContributionClaim)
            assert c.gap_id == pkg.gap_id
            assert set(c.finding_ids) <= set(pkg.finding_ids)
            assert c.novelty_normalized is False  # post-normalization persistence

        # provenance: package -> contribution -> finding -> candidate
        pkg_parents = await store.get_parents(pkg_env.artifact_id)
        assert any(
            pp.source_artifact_id == cid for cid in pkg.contribution_claim_ids for pp in pkg_parents
        )
        contrib_parents = await store.get_parents(pkg.contribution_claim_ids[0])
        assert any(p.source_artifact_id == pkg.finding_ids[0] for p in contrib_parents)
        finding_parents = await store.get_parents(pkg.finding_ids[0])
        assert any(p.source_artifact_id == pkg.equilibrium_candidate_id for p in finding_parents)

        print(
            f"live assembly: package {pkg_env.artifact_id[:8]} findings {len(pkg.finding_ids)} "
            f"contributions {len(pkg.contribution_claim_ids)} implications {len(pkg.implication_ids)} "
            f"limitations {len(pkg.limitations)}"
        )
