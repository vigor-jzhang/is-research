"""Live synthesis smoke test — opt-in (live_synthesis).

Requires OPENROUTER_API_KEY and an existing EvidenceCorpus (optionally
EVIDENCE_LIVE_CORPUS_ID, else reuse the most recent one in the store).
Structural assertions only; no synthesis-quality claims.
"""

import os
import pathlib

import pytest

pytestmark = pytest.mark.live_synthesis


@pytest.mark.asyncio
async def test_live_synthesis_smoke(tmp_path: pathlib.Path):
    if not os.getenv("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")

    cfg_path = pathlib.Path("configs/example.yaml")
    if not cfg_path.exists():
        pytest.skip("configs/example.yaml not present")

    from research_harness.app.bootstrap import build_runtime
    from research_harness.config.loader import load_config
    from research_harness.research.schemas.synthesis import (
        SynthesisExecution,
    )

    cfg = load_config(str(cfg_path))
    runtime = build_runtime(cfg)
    async with runtime:
        store = runtime.services.require("artifact_store.default")

        corpus_id = os.getenv("EVIDENCE_LIVE_CORPUS_ID")
        if not corpus_id:
            corpora = await store.list(artifact_type="evidence_corpus")
            if not corpora:
                pytest.skip("no EvidenceCorpus in store")
            corpus_id = corpora[-1].artifact_id

        # Reuse existing synthesis execution with same role if present
        role = "reasoning"
        existing = await store.list(artifact_type="synthesis_execution")
        for env in existing:
            rec = env.parse_payload(SynthesisExecution)
            if rec.evidence_corpus_id == corpus_id and rec.model_role == role:
                if rec.themes_created > 0:
                    await _assert_synthesis(store)
                    pytest.skip("already synthesized; structural assertions passed")

        svc = runtime.services.require("literature_synthesizer.default")
        exec_id = await svc.run(corpus_id)
        rec = (await store.get(exec_id)).parse_payload(SynthesisExecution)
        assert rec.profiles_processed >= 1
        assert rec.batches_processed >= 1

        await _assert_synthesis(store)
        print(
            f"live synthesis: profiles {rec.profiles_processed} batches {rec.batches_processed} "
            f"themes {rec.themes_created} statements {rec.statements_created}"
        )


async def _assert_synthesis(store):
    from research_harness.research.schemas.synthesis import (
        LiteratureSynthesis,
        SynthesisStatement,
        SynthesisTheme,
    )

    syn_envs = await store.list(artifact_type="literature_synthesis")
    assert len(syn_envs) >= 1
    syn = syn_envs[-1].parse_payload(LiteratureSynthesis)
    assert syn.theme_ids
    # At least one theme
    theme_envs = await store.list(artifact_type="synthesis_theme")
    assert len(theme_envs) >= 1
    theme = theme_envs[-1].parse_payload(SynthesisTheme)
    assert theme.paper_identity_ids  # papers represented
    # Every statement references evidence ids that exist
    stmt_envs = await store.list(artifact_type="synthesis_statement")
    ev_ids = {env.artifact_id for env in await store.list(artifact_type="evidence_item")}
    checked = 0
    for env in stmt_envs:
        s = env.parse_payload(SynthesisStatement)
        if s.supporting_evidence_ids:
            assert all(eid in ev_ids for eid in s.supporting_evidence_ids)
            checked += 1
        if s.conflicting_evidence_ids:
            assert all(eid in ev_ids for eid in s.conflicting_evidence_ids)
    assert checked >= 1
    # Provenance intact
    parents = await store.get_parents(syn_envs[-1].artifact_id)
    assert any(p.relation.value == "derived_from" for p in parents)
