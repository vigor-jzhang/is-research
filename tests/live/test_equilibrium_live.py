"""Live equilibrium derivation smoke test — opt-in (live_equilibrium).

Requires OPENROUTER_API_KEY + an existing FormalAnalyticalModel in the store.
Skips cleanly otherwise. Structural derivation + symbolic verification only;
no claim of economic validity is made from this smoke test.
"""

import os
import pathlib

import pytest

pytestmark = pytest.mark.live_equilibrium


@pytest.mark.asyncio
async def test_live_equilibrium_smoke(tmp_path: pathlib.Path):
    if not os.getenv("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")

    cfg_path = pathlib.Path("configs/example.yaml")
    if not cfg_path.exists():
        pytest.skip("configs/example.yaml not present")

    from research_harness.app.bootstrap import build_runtime
    from research_harness.config.loader import load_config
    from research_harness.research.schemas.equilibrium import (
        EquilibriumAnalysis,
        EquilibriumCandidate,
        EquilibriumExecution,
        EquilibriumVerification,
        VerificationStatus,
    )
    from research_harness.research.schemas.model import FormalAnalyticalModel, SymbolKind

    cfg = load_config(str(cfg_path))
    runtime = build_runtime(cfg)
    async with runtime:
        store = runtime.services.require("artifact_store.default")

        models = await store.list(artifact_type="formal_analytical_model")
        if not models:
            pytest.skip("no FormalAnalyticalModel in store")
        # pick the most recent model that is an actual game: a decision variable
        # owned by a strategic actor and appearing in a payoff
        model_id = None
        for env in reversed(models):
            m = env.parse_payload(FormalAnalyticalModel)
            dec_vars = {v.symbol for v in m.variables if v.kind == SymbolKind.decision_variable}
            payoff_decisions = {dv for p in m.payoffs for dv in p.decision_variables}
            if dec_vars and dec_vars & payoff_decisions:
                model_id = env.artifact_id
                break
        if not model_id:
            pytest.skip(
                "no FormalAnalyticalModel with decision variables in payoffs "
                "(the live equilibrium smoke test requires a game model)"
            )

        # ---- derive (reuse existing successful run if present) ---------------
        svc = runtime.services.require("equilibrium_deriver.default")
        existing = await store.list(artifact_type="equilibrium_execution")
        exec_id = None
        for env in existing:
            rec = env.parse_payload(EquilibriumExecution)
            if rec.model_id == model_id and rec.completed_at is not None:
                if rec.status.value in ("derived", "partially_derived"):
                    exec_id = env.artifact_id
                    break
        if exec_id is None:
            exec_id = await svc.derive(model_id)
        rec = (await store.get(exec_id)).parse_payload(EquilibriumExecution)
        assert rec.model_id == model_id
        assert rec.focs_created >= 1
        assert rec.optimization_problems_created >= 1

        # ---- structural + symbolic verification only -------------------------
        analyses = await store.list(artifact_type="equilibrium_analysis")
        leaves = []
        for env in analyses:
            a = env.parse_payload(EquilibriumAnalysis)
            if a.model_id != model_id:
                continue
            children = await store.get_children(env.artifact_id)
            if not any(c.relation.value == "supersedes" for c in children):
                leaves.append(env)
        if not leaves:
            pytest.skip("no EquilibriumAnalysis for the model")
        a = max(leaves, key=lambda e: e.created_at).parse_payload(EquilibriumAnalysis)
        if not a.candidate_ids:
            pytest.skip("no equilibrium candidates produced")
        assert a.verification_ids

        cand = (await store.get(a.candidate_ids[0])).parse_payload(EquilibriumCandidate)
        assert cand.model_id == model_id
        assert cand.expressions

        v = (await store.get(a.verification_ids[0])).parse_payload(EquilibriumVerification)
        assert v.status in (
            VerificationStatus.verified,
            VerificationStatus.partially_verified,
            VerificationStatus.failed,
        )
        foc = [chk for chk in v.checks if chk.check_type.value == "foc_residual"]
        assert foc
        # residuals either all pass (verified/partial) or are honestly reported
        # (failed) — the checks must be recorded with symbolic detail
        for chk in v.checks:
            assert chk.detail
        # provenance: candidate -> model; verification -> candidate
        cand_parents = await store.get_parents(a.candidate_ids[0])
        assert any(p.source_artifact_id == model_id for p in cand_parents)
        v_parents = await store.get_parents(a.verification_ids[0])
        assert any(p.source_artifact_id == a.candidate_ids[0] for p in v_parents)

        print(
            f"live equilibrium: model {model_id[:8]} status {rec.status.value} "
            f"focs {rec.focs_created} candidates {rec.candidates_created} "
            f"verification {v.status.value}"
        )
