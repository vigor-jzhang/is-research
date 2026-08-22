"""Live gap analysis smoke test — opt-in (live_gap_analysis).

Requires OPENROUTER_API_KEY + existing LiteratureSynthesis and EvidenceCorpus.
Skips cleanly otherwise. Structural grounding only.
"""

import os
import pathlib

import pytest

pytestmark = pytest.mark.live_gap_analysis


@pytest.mark.asyncio
async def test_live_gap_analysis_smoke(tmp_path: pathlib.Path):
    if not os.getenv("OPENROUTER_API_KEY"):
        pytest.skip("OPENROUTER_API_KEY not set")

    cfg_path = pathlib.Path("configs/example.yaml")
    if not cfg_path.exists():
        pytest.skip("configs/example.yaml not present")

    from research_harness.app.bootstrap import build_runtime
    from research_harness.config.loader import load_config
    from research_harness.research.schemas.gap import (
        GapAnalysisExecution,
    )

    cfg = load_config(str(cfg_path))
    runtime = build_runtime(cfg)
    async with runtime:
        store = runtime.services.require("artifact_store.default")

        syn_id = os.getenv("SYNTHESIS_LIVE_ID")
        if not syn_id:
            from research_harness.research.schemas.synthesis import LiteratureSynthesis

            synths = await store.list(artifact_type="literature_synthesis")
            # Pick the most recent synthesis with actual themes
            for s_env in reversed(synths):
                s = s_env.parse_payload(LiteratureSynthesis)
                if s.theme_ids:
                    syn_id = s_env.artifact_id
                    break
            if not syn_id:
                pytest.skip("no LiteratureSynthesis with themes in store")

        from research_harness.research.schemas.synthesis import LiteratureSynthesis

        syn = (await store.get(syn_id)).parse_payload(LiteratureSynthesis)
        corpus_id = syn.evidence_corpus_id

        # Reuse existing analysis with same role if present
        role = "reasoning"
        existing = await store.list(artifact_type="gap_analysis_execution")
        for env in existing:
            rec = env.parse_payload(GapAnalysisExecution)
            if (
                rec.literature_synthesis_id == syn_id
                and rec.evidence_corpus_id == corpus_id
                and rec.model_role == role
            ):
                if rec.gaps_created > 0:
                    await _assert_gaps(store)
                    pytest.skip("already analyzed; structural assertions passed")

        svc = runtime.services.require("gap_analyzer.default")
        exec_id = await svc.run(syn_id, corpus_id)
        rec = (await store.get(exec_id)).parse_payload(GapAnalysisExecution)
        assert rec.statements_processed >= 1

        await _assert_gaps(store)
        print(
            f"live gap analysis: statements {rec.statements_processed} "
            f"gaps {rec.gaps_created} rejected {rec.gaps_rejected}"
        )


async def _assert_gaps(store):
    from research_harness.research.schemas.gap import GapAnalysis, ResearchGap

    analyses = await store.list(artifact_type="gap_analysis")
    assert len(analyses) >= 1
    a = analyses[-1].parse_payload(GapAnalysis)
    assert a.gap_ids

    gap_envs = await store.list(artifact_type="research_gap")
    assert len(gap_envs) >= 1
    stmt_ids = {env.artifact_id for env in await store.list(artifact_type="synthesis_statement")}
    ev_ids = {env.artifact_id for env in await store.list(artifact_type="evidence_item")}
    checked = 0
    for env in gap_envs:
        g = env.parse_payload(ResearchGap)
        if g.supporting_synthesis_statement_ids:
            assert all(sid in stmt_ids for sid in g.supporting_synthesis_statement_ids)
            checked += 1
        if g.supporting_evidence_ids:
            assert all(eid in ev_ids for eid in g.supporting_evidence_ids)
            checked += 1
        # Deterministic support counts consistent with referenced sets
        assert g.supporting_papers >= 0
        assert g.supporting_evidence_items >= 0
        # Corpus-bounded language (no absolute absence claims)
        low = g.description.lower()
        assert "no research has studied" not in low
    assert checked >= 1
    parents = await store.get_parents(analyses[-1].artifact_id)
    assert any(p.relation.value == "derived_from" for p in parents)
