"""Live publication smoke test — opt-in (live_publication).

Uses the existing manuscript draft in the live store. Model usage is limited
to the abstract/front matter (and cover letter); formatting, validation,
exports, and packaging are deterministic. Verifies structural grounding only.
"""

import pathlib

import pytest

pytestmark = pytest.mark.live_publication


@pytest.mark.asyncio
async def test_live_publication_smoke(tmp_path: pathlib.Path):
    cfg_path = pathlib.Path("configs/example.yaml")
    if not cfg_path.exists():
        pytest.skip("configs/example.yaml not present")

    from research_harness.app.bootstrap import build_runtime
    from research_harness.config.loader import load_config
    from research_harness.research.schemas.manuscript import ManuscriptDraft
    from research_harness.research.schemas.publication import (
        ExportRecord,
        FormattedManuscript,
        FormattedManuscriptStatus,
        SubmissionPackage,
    )

    cfg = load_config(str(cfg_path))
    runtime = build_runtime(cfg)
    async with runtime:
        store = runtime.services.require("artifact_store.default")
        draft_env = None
        for env in reversed(await store.list(artifact_type="manuscript_draft")):
            d = env.parse_payload(ManuscriptDraft)
            if d.section_ids and d.supersedes is None:
                draft_env = env
                break
        if draft_env is None:
            pytest.skip("no ManuscriptDraft in store")

        svc = runtime.services.require("publication_formatter.default")
        profile_id = await svc.create_profile(
            name="Generic IS Journal (live)",
            required_sections=[],
            abstract_max_words=200,
        )

        # format (LLM used only for front matter)
        existing = None
        for env in await store.list(artifact_type="formatted_manuscript"):
            try:
                fm = env.parse_payload(FormattedManuscript)
            except Exception:
                continue
            if fm.draft_id == draft_env.artifact_id and fm.profile_id == profile_id:
                existing = env.artifact_id
                break
        m_id = (
            existing
            if existing is not None
            else await svc.format(draft_env.artifact_id, profile_id)
        )
        fm = (await store.get(m_id)).parse_payload(FormattedManuscript)
        assert fm.sections
        assert "[CITE:" not in "\n".join(s.body for s in fm.sections)
        assert fm.front_matter.title
        from research_harness.research.schemas.manuscript import ManuscriptSection

        draft_cite_count = 0
        draft = (await store.get(draft_env.artifact_id)).parse_payload(ManuscriptDraft)
        for sid in draft.section_ids:
            s = (await store.get(sid)).parse_payload(ManuscriptSection)
            draft_cite_count += len(s.citations)
        assert len(fm.citation_map) == draft_cite_count

        # validate
        leaf, passed = await svc.validate(m_id)
        fmv = (await store.get(leaf)).parse_payload(FormattedManuscript)
        assert passed is True
        assert fmv.validation_status == FormattedManuscriptStatus.validated

        # deterministic exports via BlobStore
        for fmt in ("markdown", "latex", "docx", "pdf"):
            er_id = await svc.export(leaf, fmt)
            er = (await store.get(er_id)).parse_payload(ExportRecord)
            assert er.blob_ref["storage_key"]
            assert er.content_hash
            assert er.source_draft_id == draft_env.artifact_id

        # package
        pkg_id = await svc.package(leaf)
        pkg = (await store.get(pkg_id)).parse_payload(SubmissionPackage)
        assert pkg.status.value == "ready"
        assert len(pkg.export_records) == 4

        print(
            f"live publication: draft {draft_env.artifact_id[:8]} -> manuscript {leaf[:8]} "
            f"({len(fm.sections)} sections, {len(fm.citation_map)} citations, "
            f"{fm.total_word_count} words) -> package {pkg_id[:8]} ready"
        )
