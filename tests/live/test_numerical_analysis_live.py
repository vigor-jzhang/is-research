"""Live numerical analysis smoke test — opt-in (live_numerical_analysis).

Uses one existing verified equilibrium. The numerical calculation itself is
deterministic and requires no external network. Structural validity only.
"""

import pathlib

import pytest

pytestmark = pytest.mark.live_numerical_analysis


@pytest.mark.asyncio
async def test_live_numerical_analysis_smoke(tmp_path: pathlib.Path):
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
    from research_harness.research.schemas.numerical import (
        NumericalExperiment,
        NumericalExperimentExecution,
        NumericalResult,
        ParameterSweep,
        RobustnessCheck,
        WelfareAnalysis,
    )

    cfg = load_config(str(cfg_path))
    runtime = build_runtime(cfg)
    async with runtime:
        store = runtime.services.require("artifact_store.default")

        eq_analysis_id = None
        for env in reversed(await store.list(artifact_type="equilibrium_analysis")):
            a = env.parse_payload(EquilibriumAnalysis)
            if not a.selected_candidate_id:
                continue
            for vid in a.verification_ids:
                v = (await store.get(vid)).parse_payload(EquilibriumVerification)
                if v.candidate_id == a.selected_candidate_id and v.status in (
                    VerificationStatus.verified,
                    VerificationStatus.partially_verified,
                ):
                    eq_analysis_id = env.artifact_id
                    break
            if eq_analysis_id:
                break
        if not eq_analysis_id:
            pytest.skip("no EquilibriumAnalysis with a verified candidate in store")

        svc = runtime.services.require("numerical_analysis.default")
        existing = await store.list(artifact_type="numerical_experiment_execution")
        exec_id = None
        a = (await store.get(eq_analysis_id)).parse_payload(EquilibriumAnalysis)
        for env in existing:
            rec = env.parse_payload(NumericalExperimentExecution)
            if (
                rec.equilibrium_candidate_id == a.selected_candidate_id
                and rec.completed_at is not None
            ):
                exec_id = env.artifact_id
                break
        if exec_id is None:
            exec_id = await svc.run(eq_analysis_id)
        rec = (await store.get(exec_id)).parse_payload(NumericalExperimentExecution)
        assert rec.results_created >= 1
        assert rec.engine == "sympy+python"

        exp = None
        for env in await store.list(artifact_type="numerical_experiment"):
            e = env.parse_payload(NumericalExperiment)
            if e.equilibrium_candidate_id == a.selected_candidate_id:
                exp = e
                break
        assert exp is not None

        # sweeps persisted and refer to the same candidate
        for sid in exp.sweeps:
            s = (await store.get(sid)).parse_payload(ParameterSweep)
            assert s.equilibrium_candidate_id == a.selected_candidate_id

        # baseline result with numeric outcomes (no network used)
        baseline = None
        for rid in exp.results:
            r = (await store.get(rid)).parse_payload(NumericalResult)
            if r.scenario == "baseline":
                baseline = r
                break
        assert baseline is not None
        assert baseline.outcomes  # deterministic numeric outcomes present
        assert baseline.feasible is True

        # robustness + welfare present
        assert exp.robustness
        for cid in exp.robustness:
            c = (await store.get(cid)).parse_payload(RobustnessCheck)
            assert c.outcome.value in ("supported", "violated", "not_testable")
        assert exp.welfare
        w = (await store.get(exp.welfare[0])).parse_payload(WelfareAnalysis)
        assert w.total_welfare is not None

        # provenance: experiment -> candidate
        exp_env = next(env for env in await store.list(artifact_type="numerical_experiment"))
        parents = await store.get_parents(exp_env.artifact_id)
        assert any(p.source_artifact_id == a.selected_candidate_id for p in parents)

        print(
            f"live numerical: model {rec.model_id[:8]} results {rec.results_created} "
            f"infeasible {rec.results_infeasible} robustness {rec.robustness_created} "
            f"welfare {rec.welfare_created} total_welfare {w.total_welfare}"
        )
