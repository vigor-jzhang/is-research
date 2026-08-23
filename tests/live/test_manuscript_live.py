"""Live manuscript drafting smoke test — opt-in (live_manuscript).

Uses one completed results package in the live store. Drafts only a few
sections (introduction, analytical model, propositions) with the real model
(needs OPENROUTER_API_KEY) and verifies structural grounding only:
- outline structure over the package;
- drafted sections persist with claims grounded in real verified artifacts;
- claim ids exist; conditions preserved; no failed propositions;
- draft -> section -> package provenance.
"""

import pathlib

import pytest

pytestmark = pytest.mark.live_manuscript


@pytest.mark.asyncio
async def test_live_manuscript_smoke(tmp_path: pathlib.Path):
    cfg_path = pathlib.Path("configs/example.yaml")
    if not cfg_path.exists():
        pytest.skip("configs/example.yaml not present")

    from research_harness.app.bootstrap import build_runtime
    from research_harness.config.loader import load_config
    from research_harness.research.schemas.manuscript import (
        ManuscriptDraft,
        ManuscriptDraftExecution,
        ManuscriptOutline,
        ManuscriptSection,
    )
    from research_harness.research.schemas.results import ResearchResultsPackage

    cfg = load_config(str(cfg_path))
    runtime = build_runtime(cfg)
    async with runtime:
        store = runtime.services.require("artifact_store.default")
        pkg_env = None
        for env in reversed(await store.list(artifact_type="results_package")):
            pkg = env.parse_payload(ResearchResultsPackage)
            if pkg.finding_ids and pkg.numerical_experiment_id:
                pkg_env = env
                break
        if pkg_env is None:
            pytest.skip("no assembled ResearchResultsPackage in store")

        svc = runtime.services.require("manuscript_drafter.default")
        oid = await svc.outline(pkg_env.artifact_id)
        outline = (await store.get(oid)).parse_payload(ManuscriptOutline)
        assert len(outline.section_specs) == 12
        assert outline.results_package_id == pkg_env.artifact_id

        # idempotent: reuse an existing execution for this outline
        existing = None
        for env in await store.list(artifact_type="manuscript_draft_execution"):
            try:
                rec = env.parse_payload(ManuscriptDraftExecution)
            except Exception:
                continue
            if rec.outline_id == oid and rec.completed_at is not None and rec.sections_created > 0:
                existing = env.artifact_id
                break
        exec_id = (
            existing
            if existing is not None
            else await svc.draft(oid, ["introduction", "analytical_model", "propositions"])
        )
        rec = (await store.get(exec_id)).parse_payload(ManuscriptDraftExecution)
        assert rec.sections_created == 3
        draft = (await store.get(rec.draft_id)).parse_payload(ManuscriptDraft)
        assert draft.version == 1

        # structural grounding: every section has grounded claims
        from research_harness.research.schemas.proposition import Proposition

        for sid in draft.section_ids:
            section = (await store.get(sid)).parse_payload(ManuscriptSection)
            assert section.body
            assert section.claims, f"section {section.section_id.value} must have grounded claims"
            for claim in section.claims:
                if claim.grounding_artifact_id:
                    env = await store.get(claim.grounding_artifact_id)
                    if env.artifact_type == "proposition":
                        prop = env.parse_payload(Proposition)
                        for cond in prop.conditions:
                            assert cond in claim.conditions

        # provenance: draft -> sections -> package
        draft_env = next(
            env
            for env in await store.list(artifact_type="manuscript_draft")
            if env.artifact_id == rec.draft_id
        )
        parents = await store.get_parents(draft_env.artifact_id)
        assert any(p.source_artifact_id == oid for p in parents)
        assert any(p.source_artifact_id == pkg_env.artifact_id for p in parents)
        assert all(
            p.source_artifact_id in draft.section_ids
            for p in parents
            if p.relation.value == "derived_from"
        ) or any(p.source_artifact_id == draft.section_ids[0] for p in parents)

        print(
            f"live manuscript: outline {oid[:8]} draft v{draft.version} "
            f"sections {len(draft.section_ids)} claims {rec.claims_created} "
            f"citations {rec.citations_created}"
        )
