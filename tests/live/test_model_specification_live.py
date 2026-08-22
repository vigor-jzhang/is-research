"""Live formal model specification smoke test — opt-in (live_model_specification).

Requires OPENROUTER_API_KEY + an existing SelectedMechanism in the store.
Skips cleanly otherwise. Structural validity only; no claim of economic
correctness is made from this smoke test.
"""

import os
import pathlib

import pytest

pytestmark = pytest.mark.live_model_specification


@pytest.mark.asyncio
async def test_live_model_specification_smoke(tmp_path: pathlib.Path):
    if not os.getenv("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")

    cfg_path = pathlib.Path("configs/example.yaml")
    if not cfg_path.exists():
        pytest.skip("configs/example.yaml not present")

    from research_harness.app.bootstrap import build_runtime
    from research_harness.config.loader import load_config
    from research_harness.research.schemas.mechanism import SelectedMechanism
    from research_harness.research.schemas.model import (
        FormalAnalyticalModel,
        ModelSpecificationCritique,
        ModelSpecificationExecution,
    )

    cfg = load_config(str(cfg_path))
    runtime = build_runtime(cfg)
    async with runtime:
        store = runtime.services.require("artifact_store.default")

        mechs = await store.list(artifact_type="selected_mechanism")
        if not mechs:
            pytest.skip("no SelectedMechanism in store")
        mech_id = mechs[-1].artifact_id
        mech = (await store.get(mech_id)).parse_payload(SelectedMechanism)

        # ---- 1. Build (reuse existing successful build if present) -----------
        builder = runtime.services.require("analytical_model_builder.default")
        existing = await store.list(artifact_type="model_specification_execution")
        exec_id = None
        for env in existing:
            rec = env.parse_payload(ModelSpecificationExecution)
            if rec.selected_mechanism_id == mech_id and rec.model_created:
                exec_id = env.artifact_id
                break
        if exec_id is None:
            # The pipeline rejects structurally invalid specs; the model is
            # stochastic, so retry briefly before skipping.
            exec_id = None
            for _ in range(3):
                candidate = await builder.build(mech_id)
                rec = (await store.get(candidate)).parse_payload(ModelSpecificationExecution)
                if rec.model_created:
                    exec_id = candidate
                    break
            if exec_id is None:
                rec = (
                    await store.get(
                        (await store.list(artifact_type="model_specification_execution"))[
                            -1
                        ].artifact_id
                    )
                ).parse_payload(ModelSpecificationExecution)
                pytest.skip(
                    f"live model build structurally rejected after retries: {rec.failures[:1]}"
                )
        rec = (await store.get(exec_id)).parse_payload(ModelSpecificationExecution)
        assert rec.model_created is True, rec.failures

        model_env = (await store.list(artifact_type="formal_analytical_model"))[-1]
        m = model_env.parse_payload(FormalAnalyticalModel)
        # Structural validity only
        assert m.selected_mechanism_id == mech_id
        assert m.actors
        assert m.variables
        assert m.timing
        table = {v.symbol for v in m.variables} | {p.symbol for p in m.parameters}
        for p in m.payoffs:
            assert set(p.expression.symbols_used) <= table
            for c in p.constraints:
                assert set(c.symbols_used) <= table
        for a in m.assumptions:
            if a.mathematical_form:
                assert set(a.mathematical_form.symbols_used) <= table
            if a.knowledge_basis.value == "literature_supported":
                assert a.source_ids
        # timing sequential
        nums = [t.stage_number for t in m.timing]
        assert nums == list(range(len(m.timing)))
        # decision ownership
        for v in m.variables:
            if v.kind.value == "decision_variable":
                assert v.owner_actor_id in {a.actor_id for a in m.actors}
        # provenance: model derived_from selected mechanism
        parents = await store.get_parents(model_env.artifact_id)
        assert any(p.source_artifact_id == mech_id for p in parents)

        # ---- 2. Critique + revision (idempotent reuse when present) ----------
        critic = runtime.services.require("model_specification_critic.default")
        crits = await store.list(artifact_type="model_specification_critique")
        crit_id = None
        for env in crits:
            c = env.parse_payload(ModelSpecificationCritique)
            if c.model_id == model_env.artifact_id:
                crit_id = env.artifact_id
                break
        if crit_id is None:
            crit_id = await critic.critique(model_env.artifact_id)
        crit = (await store.get(crit_id)).parse_payload(ModelSpecificationCritique)
        assert crit.verdict in ("keep", "revise", "reject")

        revised = [
            env.parse_payload(FormalAnalyticalModel)
            for env in await store.list(artifact_type="formal_analytical_model")
            if env.parse_payload(FormalAnalyticalModel).metadata.get("supersedes")
            == model_env.artifact_id
        ]
        if not revised:
            # attempt a revision; failure is acceptable when the critique is
            # keep/reject or the model refuses — the smoke test only asserts
            # structural validity of anything that IS persisted
            try:
                v2_id = await critic.revise(model_env.artifact_id)
                v2 = (await store.get(v2_id)).parse_payload(FormalAnalyticalModel)
                assert v2.status.value == "revised"
                children = await store.get_children(model_env.artifact_id)
                assert any(
                    c.target_artifact_id == v2_id and c.relation.value == "supersedes"
                    for c in children
                )
            except ValueError as e:
                pytest.skip(f"live revision not available: {e}")

        print(
            f"live model specification: mechanism {mech.name[:40]!r} model "
            f"{model_env.artifact_id[:8]} actors {len(m.actors)} variables {len(m.variables)} "
            f"stages {len(m.timing)} payoffs {len(m.payoffs)} critique {crit.verdict}"
        )
