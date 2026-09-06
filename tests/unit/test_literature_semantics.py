"""Regression tests for literature semantics (round 27).

Batch 7 of the §9 triage: M35, M36, M37, M39, M43.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.paper import Author, PaperRecord


def _paper(title: str, doi: str) -> PaperRecord:
    return PaperRecord(title=title, authors=[Author(name="Smith, Jane")], year=2024, doi=doi)


# ---------------------------------------------------------------------------
# M35 — overlapping identity groups must merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overlapping_groups_merge(tmp_path: pathlib.Path):
    """M35: only strict subsets were superseded, so overlaps stayed both active.

    An existing identity {A,B} and a new group {B,C} are neither equal nor
    subsets of one another. The old check superseded nothing, leaving two
    identities with B a member of both.
    """
    from research_harness.plugins.literature.identity_resolver.plugin import (
        PaperIdentityResolverService,
    )
    from research_harness.plugins.storage.artifacts_sqlite.plugin import (
        SQLiteArtifactStore,
    )
    from research_harness.research.schemas.identity import PaperIdentity

    store = SQLiteArtifactStore(path=tmp_path / "a.db")
    resolver = PaperIdentityResolverService(artifact_store=store)

    a = ArtifactEnvelope.create(payload=_paper("A", "10.1000/shared"), artifact_type="paper_record")
    b = ArtifactEnvelope.create(payload=_paper("B", "10.1000/shared"), artifact_type="paper_record")
    c = ArtifactEnvelope.create(payload=_paper("C", "10.1000/shared"), artifact_type="paper_record")
    for env in (a, b, c):
        await store.put(env)

    first = await resolver.resolve([a.artifact_id, b.artifact_id])
    assert len(first.identities_created) == 1

    # New group shares B with the existing identity but is not a superset of it.
    second = await resolver.resolve([b.artifact_id, c.artifact_id])
    assert second.identities_created, "no identity was created for the overlapping group"
    assert second.identities_superseded, "the overlapping identity was not superseded"

    created = (await store.get(second.identities_created[0])).parse_payload(PaperIdentity)
    assert sorted(created.member_paper_artifact_ids) == sorted(
        [a.artifact_id, b.artifact_id, c.artifact_id]
    ), f"expected the union of both groups, got {created.member_paper_artifact_ids}"
    await store.close()


@pytest.mark.asyncio
async def test_disjoint_groups_stay_separate(tmp_path: pathlib.Path):
    """Guard: merging overlaps must not join unrelated groups."""
    from research_harness.plugins.literature.identity_resolver.plugin import (
        PaperIdentityResolverService,
    )
    from research_harness.plugins.storage.artifacts_sqlite.plugin import (
        SQLiteArtifactStore,
    )

    store = SQLiteArtifactStore(path=tmp_path / "a.db")
    resolver = PaperIdentityResolverService(artifact_store=store)
    p1 = ArtifactEnvelope.create(payload=_paper("P", "10.1000/one"), artifact_type="paper_record")
    p2 = ArtifactEnvelope.create(payload=_paper("Q", "10.1000/two"), artifact_type="paper_record")
    await store.put(p1)
    await store.put(p2)
    result = await resolver.resolve([p1.artifact_id, p2.artifact_id])
    assert len(result.identities_created) == 2, "disjoint papers were merged"
    await store.close()


# ---------------------------------------------------------------------------
# M36 / M37 — gap analyzer statement scoping and budget
# ---------------------------------------------------------------------------


async def _mini_scenario(store, *, n_statements: int = 3):
    """Minimal corpus + synthesis with `n_statements` statements in one theme."""
    from research_harness.research.schemas.evidence import EvidenceCategory, EvidenceItem, Locator
    from research_harness.research.schemas.evidence_extraction import (
        EvidenceCorpus,
        EvidenceExtractionExecution,
    )
    from research_harness.research.schemas.research_profile import (
        PaperResearchProfile,
        ProfileClaim,
    )
    from research_harness.research.schemas.synthesis import (
        LiteratureSynthesis,
        SynthesisStatement,
        SynthesisStatementType,
        SynthesisTheme,
    )

    ev_ids = []
    for i in range(n_statements):
        ev = ArtifactEnvelope.create(
            payload=EvidenceItem(
                statement=f"Finding number {i}",
                source_artifact_id=f"doc{i}",
                category=EvidenceCategory.finding,
                locator=Locator(page=1, pages=[1]),
                extraction_method="deterministic",
            ),
            artifact_type="evidence_item",
            producer="test",
        )
        await store.put(ev)
        ev_ids.append(ev.artifact_id)

    prof = ArtifactEnvelope.create(
        payload=PaperResearchProfile(
            paper_identity_id="pi1",
            full_text_document_id="doc0",
            main_findings=[
                ProfileClaim(text="Finding number 0", evidence_item_ids=[ev_ids[0]], inference=False)
            ],
            evidence_item_ids=ev_ids,
            model_role="reasoning",
        ),
        artifact_type="paper_research_profile",
        producer="test",
    )
    await store.put(prof)

    seed = ArtifactEnvelope.create(
        payload=EvidenceExtractionExecution(full_text_corpus_id="seed"),
        artifact_type="evidence_extraction_execution",
        producer="test",
    )
    await store.put(seed)
    corpus = ArtifactEnvelope.create(
        payload=EvidenceCorpus(
            evidence_extraction_execution_id=seed.artifact_id,
            full_text_corpus_id="c1",
            paper_profile_ids=[prof.artifact_id],
            evidence_item_ids=ev_ids,
        ),
        artifact_type="evidence_corpus",
        producer="test",
    )
    await store.put(corpus)

    statements = [
        SynthesisStatement(
            statement=f"Finding number {i}",
            type=SynthesisStatementType.consensus,
            supporting_evidence_ids=[eid],
            supporting_paper_identity_ids=["pi1"],
            evidence_items_supporting=1,
        )
        for i, eid in enumerate(ev_ids)
    ]
    stmt_ids = []
    for stmt in statements:
        s_env = ArtifactEnvelope.create(
            payload=stmt, artifact_type="synthesis_statement", producer="test"
        )
        await store.put(s_env)
        stmt_ids.append(s_env.artifact_id)

    theme = ArtifactEnvelope.create(
        payload=SynthesisTheme(
            title="Findings",
            statements=statements,
            evidence_item_ids=ev_ids,
            paper_identity_ids=["pi1"],
            metadata={"statement_ids": stmt_ids},
        ),
        artifact_type="synthesis_theme",
        producer="test",
    )
    await store.put(theme)
    syn = ArtifactEnvelope.create(
        payload=LiteratureSynthesis(
            evidence_corpus_id=corpus.artifact_id,
            theme_ids=[theme.artifact_id],
            statement_ids=stmt_ids,
            counts={"themes": 1, "statements": len(stmt_ids)},
        ),
        artifact_type="literature_synthesis",
        producer="test",
    )
    await store.put(syn)
    return syn.artifact_id, corpus.artifact_id


class _Router:
    def __init__(self, payload: dict):
        self.payload = payload
        self.prompts: list[str] = []

    async def complete(self, role, request):  # noqa: ANN201
        from research_harness.contracts.model import Message, ModelResponse

        self.prompts.append(" ".join(m.content or "" for m in request.messages))
        return ModelResponse(
            message=Message(role="assistant", content=json.dumps(self.payload)),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


@pytest.mark.asyncio
async def test_unrelated_statements_are_excluded(tmp_path: pathlib.Path):
    """M36: the analyzer loaded every synthesis_statement in the store.

    A statement belonging to a different run must not enter the map that feeds
    the grounding check.
    """
    from research_harness.plugins.literature.gap_analyzer.plugin import GapAnalyzerService
    from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
    from research_harness.research.schemas.gap import GapAnalysisExecution
    from research_harness.research.schemas.synthesis import (
        SynthesisStatement,
        SynthesisStatementType,
    )

    store = SQLiteArtifactStore(path=tmp_path / "a.db")
    syn_id, corpus_id = await _mini_scenario(store, n_statements=2)

    # A statement from an unrelated run, persisted in the same store.
    foreign = ArtifactEnvelope.create(
        payload=SynthesisStatement(
            statement="An unrelated finding from another run",
            type=SynthesisStatementType.consensus,
            supporting_evidence_ids=["ev-elsewhere"],
        ),
        artifact_type="synthesis_statement",
        producer="test",
    )
    await store.put(foreign)

    router = _Router({"gaps": []})
    svc = GapAnalyzerService(model_router=router, artifact_store=store, model_role="reasoning")
    exec_id = await svc.run(syn_id, corpus_id)
    exec_record = (await store.get(exec_id)).parse_payload(GapAnalysisExecution)
    assert exec_record.statements_processed == 2, (
        f"counted {exec_record.statements_processed} statements; the unrelated one leaked in"
    )
    await store.close()


@pytest.mark.asyncio
async def test_max_statements_bounds_statements_not_themes(tmp_path: pathlib.Path):
    """M37: the budget bounded the number of themes, leaving prompts unbounded."""
    from research_harness.plugins.literature.gap_analyzer.plugin import GapAnalyzerService
    from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
    from research_harness.research.schemas.gap import GapAnalysisExecution

    store = SQLiteArtifactStore(path=tmp_path / "a.db")
    syn_id, corpus_id = await _mini_scenario(store, n_statements=3)

    router = _Router({"gaps": []})
    svc = GapAnalyzerService(
        model_router=router,
        artifact_store=store,
        model_role="reasoning",
        max_statements=1,
    )
    exec_id = await svc.run(syn_id, corpus_id)
    exec_record = (await store.get(exec_id)).parse_payload(GapAnalysisExecution)
    assert exec_record.statements_processed == 1, (
        f"max_statements=1 still processed {exec_record.statements_processed} statements"
    )
    await store.close()


# ---------------------------------------------------------------------------
# M37 — the research question must reach the model as text
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_question_text_is_resolved(tmp_path: pathlib.Path):
    """M37: the question was interpolated as a bare artifact id."""
    from research_harness.plugins.literature.gap_analyzer.plugin import GapAnalyzerService
    from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
    from research_harness.research.schemas.project import ResearchQuestion

    store = SQLiteArtifactStore(path=tmp_path / "a.db")
    q = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Does entry reduce platform prices?"),
        artifact_type="research_question",
        producer="test",
    )
    await store.put(q)
    svc = GapAnalyzerService.__new__(GapAnalyzerService)
    svc._store = store
    got = await svc._load_question_text(q.artifact_id)
    assert got == "Does entry reduce platform prices?"


@pytest.mark.asyncio
async def test_question_text_missing_is_none(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.gap_analyzer.plugin import GapAnalyzerService
    from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore

    store = SQLiteArtifactStore(path=tmp_path / "a.db")
    svc = GapAnalyzerService.__new__(GapAnalyzerService)
    svc._store = store
    assert await svc._load_question_text(None) is None
    assert await svc._load_question_text("no-such-id") is None


# ---------------------------------------------------------------------------
# M43 — never forge a reference to a snapshot that was not persisted
# ---------------------------------------------------------------------------


def test_locations_without_a_snapshot_have_no_snapshot_id():
    """M43: an unpersisted snapshot id used to be recorded anyway."""
    from research_harness.plugins.documents.locator_unpaywall.plugin import (
        UnpaywallLocatorService,
    )

    svc = UnpaywallLocatorService.__new__(UnpaywallLocatorService)
    raw = {
        "best_oa_location": {
            "url": "https://example.com/paper.pdf",
            "url_for_pdf": "https://example.com/paper.pdf",
            "host_type": "publisher",
            "version": "publishedVersion",
        },
        "oa_locations": [],
    }
    locations = svc._extract_locations_from_raw(raw, "pi1", None)
    assert locations, "no locations were extracted"
    assert all(loc.provider_snapshot_id is None for loc in locations), (
        "a location references a snapshot that does not exist"
    )


def test_locations_carry_the_snapshot_id_when_present():
    from research_harness.plugins.documents.locator_unpaywall.plugin import (
        UnpaywallLocatorService,
    )

    svc = UnpaywallLocatorService.__new__(UnpaywallLocatorService)
    raw = {
        "best_oa_location": {
            "url": "https://example.com/paper.pdf",
            "url_for_pdf": "https://example.com/paper.pdf",
            "host_type": "publisher",
            "version": "publishedVersion",
        },
        "oa_locations": [],
    }
    locations = svc._extract_locations_from_raw(raw, "pi1", "snap-1")
    assert locations and all(loc.provider_snapshot_id == "snap-1" for loc in locations)


# ---------------------------------------------------------------------------
# M39 — a store failure is not a screening outcome
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_failure_is_not_recorded_as_uncertain(tmp_path: pathlib.Path):
    """M39: `except Exception: uncertain.append(...)` faked a screening result.

    A store read failure is not the model being unable to decide, and must not
    be counted alongside genuine uncertainty.
    """
    from research_harness.plugins.literature.screening_orchestrator.plugin import (
        ScreeningOrchestratorService,
    )
    from research_harness.plugins.storage.artifacts_sqlite.plugin import (
        SQLiteArtifactStore,
    )
    from research_harness.research.schemas.execution import LiteratureSearchExecution
    from research_harness.research.schemas.screening_decision import (
        ScreeningDecision,
        ScreeningDecisionEnum,
    )
    from research_harness.research.schemas.screening_execution import (
        ScreeningExecution,
    )
    from research_harness.research.schemas.screening_protocol import (
        ProtocolStatus,
        ScreeningProtocol,
    )

    store = SQLiteArtifactStore(path=tmp_path / "a.db")

    proto = ArtifactEnvelope.create(
        payload=ScreeningProtocol(
            research_question_id="rq1",
            objective="obj",
            status=ProtocolStatus.approved,
        ),
        artifact_type="screening_protocol",
        producer="test",
    )
    await store.put(proto)
    search_exec = ArtifactEnvelope.create(
        payload=LiteratureSearchExecution(
            strategy_artifact_id="s1", paper_identity_artifact_ids=["pi1"]
        ),
        artifact_type="literature_search_execution",
        producer="test",
    )
    await store.put(search_exec)
    decision = ArtifactEnvelope.create(
        payload=ScreeningDecision(
            paper_identity_id="pi1",
            screening_view_id="v1",
            screening_protocol_id=proto.artifact_id,
            decision=ScreeningDecisionEnum.include,
            rationale_summary="r",
            confidence=0.9,
        ),
        artifact_type="screening_decision",
        producer="test",
    )
    await store.put(decision)

    svc = ScreeningOrchestratorService.__new__(ScreeningOrchestratorService)
    svc._store = store
    svc._screener = None
    svc._max_model_calls = 5
    svc._max_candidates = 100
    svc._review_uncertain = False
    svc._review_low_confidence = None
    svc._autonomy = None
    svc._events = None

    real_get = store.get

    async def _get_failing(artifact_id: str):
        # Let the protocol and search-execution loads succeed; fail the read of
        # the decision inside the reuse path.
        if artifact_id == decision.artifact_id:
            raise RuntimeError("store is down")
        return await real_get(artifact_id)

    async def _find_existing(pi_id: str, protocol_id: str) -> str | None:
        return decision.artifact_id

    svc._find_existing_decision = _find_existing  # type: ignore[method-assign]
    store.get = _get_failing  # type: ignore[method-assign]
    try:
        exec_id = await svc.screen(search_exec.artifact_id, proto.artifact_id)
    finally:
        store.get = real_get  # type: ignore[method-assign]

    execution = (await store.get(exec_id)).parse_payload(ScreeningExecution)
    assert execution.failures, "the store failure was not recorded at all"
    assert any("store is down" in str(f.get("error")) for f in execution.failures)
    assert execution.counts.get("uncertain", 0) == 0, (
        "a store failure was counted as screening uncertainty"
    )
    assert execution.counts.get("failed", 0) == 1
    await store.close()
