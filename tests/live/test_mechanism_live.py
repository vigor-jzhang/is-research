"""Live mechanism-development smoke test — opt-in (live_mechanism).

Requires OPENROUTER_API_KEY + an existing GapAnalysis in the store.
Skips cleanly otherwise. Structural validation + provenance only; the run is
kept small (max 2 candidates).
"""

import os
import pathlib

import pytest

pytestmark = pytest.mark.live_mechanism


@pytest.mark.asyncio
async def test_live_mechanism_smoke(tmp_path: pathlib.Path):
    if not os.getenv("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")

    cfg_path = pathlib.Path("configs/example.yaml")
    if not cfg_path.exists():
        pytest.skip("configs/example.yaml not present")

    from research_harness.app.bootstrap import build_runtime
    from research_harness.config.loader import load_config
    from research_harness.research.schemas.gap import GapAnalysis
    from research_harness.research.schemas.mechanism import (
        GapSelection,
        MechanismAnalysisExecution,
        MechanismCandidate,
        MechanismCritique,
        SelectedMechanism,
    )

    cfg = load_config(str(cfg_path))
    runtime = build_runtime(cfg)
    async with runtime:
        store = runtime.services.require("artifact_store.default")

        # Pick the most recent GapAnalysis with gaps
        analyses = await store.list(artifact_type="gap_analysis")
        a_id = None
        for a_env in reversed(analyses):
            a = a_env.parse_payload(GapAnalysis)
            if a.gap_ids:
                a_id = a_env.artifact_id
                break
        if not a_id:
            pytest.skip("no GapAnalysis with gaps in store")

        # ---- 1. Gap selection (high autonomy; reuse existing if present) -----
        sel_svc = runtime.services.require("gap_selection.default")
        existing_sel = await store.list(artifact_type="gap_selection")
        sel_id = None
        for env in existing_sel:
            s = env.parse_payload(GapSelection)
            if s.gap_analysis_id == a_id and s.status.value == "approved":
                sel_id = env.artifact_id
                break
        if sel_id is None:
            sel_id = await sel_svc.select(a_id)
        sel = (await store.get(sel_id)).parse_payload(GapSelection)
        assert sel.selected_gap_id
        assert sel.status.value == "approved"

        # ---- 2. Mechanism generation (small: 2 candidates) -------------------
        gen_svc = runtime.services.require("mechanism_generator.default")
        gen_svc._max_candidates = 2  # type: ignore[attr-defined]  # keep it small
        existing_gen = await store.list(artifact_type="mechanism_analysis_execution")
        exec_id = None
        for env in existing_gen:
            rec = env.parse_payload(MechanismAnalysisExecution)
            if rec.gap_selection_id == sel_id and rec.candidates_created > 0:
                exec_id = env.artifact_id
                break
        if exec_id is None:
            exec_id = await gen_svc.generate(sel_id)
        rec = (await store.get(exec_id)).parse_payload(MechanismAnalysisExecution)
        assert rec.candidates_created >= 1

        candidates = [
            env.parse_payload(MechanismCandidate)
            for env in await store.list(artifact_type="mechanism_candidate")
            if env.parse_payload(MechanismCandidate).gap_selection_id == sel_id
        ]
        assert candidates
        stmt_ids = {
            env.artifact_id for env in await store.list(artifact_type="synthesis_statement")
        }
        ev_ids = {env.artifact_id for env in await store.list(artifact_type="evidence_item")}
        checked = 0
        for c in candidates:
            for sid in c.literature_support_ids:
                assert sid in stmt_ids or sid in ev_ids
                checked += 1
            for g in c.grounding:
                if g.basis.value == "literature_supported":
                    assert all(s in stmt_ids or s in ev_ids for s in g.source_ids)
            assert c.evaluation is not None
        assert checked >= 1

        # ---- 3. Critique + selection -----------------------------------------
        critic_svc = runtime.services.require("mechanism_critic.default")
        cand_id = (await store.list(artifact_type="mechanism_candidate"))[0].artifact_id
        crit_id = await critic_svc.critique(cand_id)
        crit = (await store.get(crit_id)).parse_payload(MechanismCritique)
        assert crit.verdict.value in ("keep", "revise", "reject")

        sm_id = await critic_svc.select(cand_id)
        sm = (await store.get(sm_id)).parse_payload(SelectedMechanism)
        assert sm.mechanism_candidate_id == cand_id
        assert len(sm.critique_ids) >= 1

        # ---- 4. Provenance: SelectedMechanism -> candidate -> gap ------------
        sm_parents = await store.get_parents(sm_id)
        assert any(p.source_artifact_id == cand_id for p in sm_parents)
        cand_parents = await store.get_parents(cand_id)
        gap_id = sel.selected_gap_id
        assert any(p.source_artifact_id == gap_id for p in cand_parents)

        print(
            f"live mechanism: selection {sel_id} candidates {len(candidates)} "
            f"critique {crit_id} selected {sm_id}"
        )
