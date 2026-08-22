import pathlib

import pytest

from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.execution import LiteratureSearchExecution
from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
from research_harness.research.schemas.paper import PaperRecord
from research_harness.research.schemas.screening_execution import ScreeningExecution
from research_harness.research.schemas.screening_protocol import (
    ProtocolStatus,
    ScreeningCriterion,
    ScreeningProtocol,
)
from research_harness.research.schemas.screening_view import PaperScreeningView


class FakeViewBuilder:
    def __init__(self, store):
        self.store = store
        self.calls = []

    async def build(self, pi_id: str) -> str:
        self.calls.append(pi_id)
        # Create a deterministic view for the pi_id
        view = PaperScreeningView(
            paper_identity_id=pi_id,
            title="T",
            abstract="A",
            authors=[],
            field_sources={},
            member_paper_artifact_ids=[],
        )
        env = ArtifactEnvelope.create(
            payload=view, artifact_type="paper_screening_view", producer="fake_view"
        )
        await self.store.put(env)
        return env.artifact_id

    async def build_fails(self, pi_id: str):
        raise RuntimeError("view build failed")


class FailingViewBuilder:
    async def build(self, pi_id: str) -> str:
        raise RuntimeError("view build failed")


class FakeScreener:
    def __init__(self, store, decisions_map):
        self.store = store
        self.map = decisions_map  # pi_id -> (decision, confidence, sufficiency)
        self.calls = []
        self.counter = 0

    async def screen(self, view_id: str, protocol_id: str) -> str:
        self.calls.append((view_id, protocol_id))
        # Retrieve view to get paper_identity_id
        view_env = await self.store.get(view_id)
        view = view_env.parse_payload(PaperScreeningView)
        pi_id = view.paper_identity_id
        decision_val, confidence, suff = self.map.get(pi_id, ("include", 0.9, "sufficient"))
        from research_harness.research.schemas.screening_decision import (
            InformationSufficiency,
            ScreeningDecision,
            ScreeningDecisionEnum,
        )

        dec = ScreeningDecision(
            paper_identity_id=pi_id,
            screening_view_id=view_id,
            screening_protocol_id=protocol_id,
            decision=ScreeningDecisionEnum(decision_val),
            matched_inclusion_criteria=["I1"] if decision_val == "include" else [],
            matched_exclusion_criteria=["E1"] if decision_val == "exclude" else [],
            reason_codes=[],
            rationale_summary="fake rationale",
            confidence=confidence,
            information_sufficiency=InformationSufficiency(suff),
        )
        env = ArtifactEnvelope.create(
            payload=dec, artifact_type="screening_decision", producer="fake_screener"
        )
        await self.store.put(env)
        # Provenance
        await self.store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=pi_id,
                target_artifact_id=env.artifact_id,
                producer="fake",
            )
        )
        return env.artifact_id


class FakeAutonomy:
    def __init__(self, approved=True):
        self.approved = approved
        self.requests = []

    async def request_approval(self, req):
        self.requests.append(req)
        from research_harness.contracts.autonomy import ApprovalDecision

        return ApprovalDecision(
            request_id=req.request_id, approved=self.approved, reason="test", decided_by="fake"
        )

    async def requires_approval(self, checkpoint):
        return True


def _make_identity(store, paper_title="T"):
    paper = PaperRecord(title=paper_title)
    p_env = ArtifactEnvelope.create(payload=paper, artifact_type="paper_record", producer="test")
    return p_env


@pytest.mark.asyncio
async def test_orchestrator_filters_superseded(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_orchestrator.plugin import (
        ScreeningOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    # Create two identities, one will be superseded
    p1 = PaperRecord(title="P1")
    p1_env = ArtifactEnvelope.create(payload=p1, artifact_type="paper_record", producer="test")
    await store.put(p1_env)
    p2 = PaperRecord(title="P2")
    p2_env = ArtifactEnvelope.create(payload=p2, artifact_type="paper_record", producer="test")
    await store.put(p2_env)
    id1 = PaperIdentity(
        member_paper_artifact_ids=[p1_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    id1_env = ArtifactEnvelope.create(payload=id1, artifact_type="paper_identity", producer="test")
    await store.put(id1_env)
    id2 = PaperIdentity(
        member_paper_artifact_ids=[p2_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    id2_env = ArtifactEnvelope.create(payload=id2, artifact_type="paper_identity", producer="test")
    await store.put(id2_env)
    # Supersede id1
    id1_new = PaperIdentity(
        member_paper_artifact_ids=[p1_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
        metadata={"sup": True},
    )
    id1_new_env = ArtifactEnvelope.create(
        payload=id1_new, artifact_type="paper_identity", producer="test"
    )
    await store.put(id1_new_env)
    await store.add_provenance(
        ProvenanceLink(
            relation=ProvenanceRelation.supersedes,
            source_artifact_id=id1_env.artifact_id,
            target_artifact_id=id1_new_env.artifact_id,
            producer="test",
        )
    )

    proto = ScreeningProtocol(
        research_question_id="rq",
        objective="obj",
        inclusion_criteria=[
            ScreeningCriterion(criterion_id="I1", kind="inclusion", description="d")
        ],
        status=ProtocolStatus.approved,
    )
    proto_env = ArtifactEnvelope.create(
        payload=proto, artifact_type="screening_protocol", producer="test"
    )
    await store.put(proto_env)
    exec_rec = LiteratureSearchExecution(
        strategy_artifact_id="s",
        query_artifact_ids=[],
        search_record_artifact_ids=[],
        paper_artifact_ids=[],
        paper_identity_artifact_ids=[id1_env.artifact_id, id2_env.artifact_id],
        counts={},
        provider_failures=[],
    )
    exec_env = ArtifactEnvelope.create(
        payload=exec_rec, artifact_type="literature_search_execution", producer="test"
    )
    await store.put(exec_env)

    view_builder = FakeViewBuilder(store)
    screener = FakeScreener(store, {})
    autonomy = FakeAutonomy()
    svc = ScreeningOrchestratorService(
        artifact_store=store,
        view_builder=view_builder,
        screener=screener,
        autonomy_policy=autonomy,
        max_candidates=500,
        max_model_calls=500,
    )
    out_id = await svc.screen(exec_env.artifact_id, proto_env.artifact_id)
    out = (await store.get(out_id)).parse_payload(ScreeningExecution)
    # Old superseded id1 should be filtered, only id2 processed
    assert out.counts["total_candidates"] == 1
    assert out.candidate_identity_ids == [id2_env.artifact_id]
    assert id1_env.artifact_id not in out.candidate_identity_ids
    # Check that view builder not called for superseded old id if filtering works
    # Check that view builder not called for superseded old id if filtering works
    # In our setup candidate_ids = [old_id1, id2], old_id1 is superseded, so calls should be 1 (only id2) if correctly filtered
    # But note new identity id1_new is not in candidate list, so total_candidates should be 1? Actually total_candidates is len(current_candidates) after filtering
    assert out.counts["total_candidates"] == 1
    assert len(view_builder.calls) == 1
    assert view_builder.calls[0] == id2_env.artifact_id
    await store.close()


@pytest.mark.asyncio
async def test_orchestrator_budgets_max_candidates(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_orchestrator.plugin import (
        ScreeningOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    # Create 5 identities
    ids = []
    for i in range(5):
        p = PaperRecord(title=f"P{i}")
        p_env = ArtifactEnvelope.create(payload=p, artifact_type="paper_record", producer="test")
        await store.put(p_env)
        pi = PaperIdentity(
            member_paper_artifact_ids=[p_env.artifact_id],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.exact_identifier,
            resolution_evidence=[],
        )
        pi_env = ArtifactEnvelope.create(
            payload=pi, artifact_type="paper_identity", producer="test"
        )
        await store.put(pi_env)
        ids.append(pi_env.artifact_id)
    proto = ScreeningProtocol(
        research_question_id="rq",
        objective="obj",
        inclusion_criteria=[
            ScreeningCriterion(criterion_id="I1", kind="inclusion", description="d")
        ],
        status=ProtocolStatus.approved,
    )
    proto_env = ArtifactEnvelope.create(
        payload=proto, artifact_type="screening_protocol", producer="test"
    )
    await store.put(proto_env)
    exec_rec = LiteratureSearchExecution(
        strategy_artifact_id="s",
        query_artifact_ids=[],
        search_record_artifact_ids=[],
        paper_artifact_ids=[],
        paper_identity_artifact_ids=ids,
        counts={},
        provider_failures=[],
    )
    exec_env = ArtifactEnvelope.create(
        payload=exec_rec, artifact_type="literature_search_execution", producer="test"
    )
    await store.put(exec_env)
    view_builder = FakeViewBuilder(store)
    screener = FakeScreener(store, {})
    svc = ScreeningOrchestratorService(
        artifact_store=store,
        view_builder=view_builder,
        screener=screener,
        max_candidates=2,
        max_model_calls=500,
    )
    out_id = await svc.screen(exec_env.artifact_id, proto_env.artifact_id)
    out = (await store.get(out_id)).parse_payload(ScreeningExecution)
    assert out.counts["total_candidates"] == 5
    assert len(out.decision_artifact_ids) == 2
    assert out.budget_stop_reason == "max_candidates 2 reached"
    await store.close()


@pytest.mark.asyncio
async def test_orchestrator_budgets_max_model_calls(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_orchestrator.plugin import (
        ScreeningOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = []
    for i in range(4):
        p = PaperRecord(title=f"P{i}")
        p_env = ArtifactEnvelope.create(payload=p, artifact_type="paper_record", producer="test")
        await store.put(p_env)
        pi = PaperIdentity(
            member_paper_artifact_ids=[p_env.artifact_id],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.exact_identifier,
            resolution_evidence=[],
        )
        pi_env = ArtifactEnvelope.create(
            payload=pi, artifact_type="paper_identity", producer="test"
        )
        await store.put(pi_env)
        ids.append(pi_env.artifact_id)
    proto = ScreeningProtocol(
        research_question_id="rq",
        objective="obj",
        inclusion_criteria=[
            ScreeningCriterion(criterion_id="I1", kind="inclusion", description="d")
        ],
        status=ProtocolStatus.approved,
    )
    proto_env = ArtifactEnvelope.create(
        payload=proto, artifact_type="screening_protocol", producer="test"
    )
    await store.put(proto_env)
    exec_rec = LiteratureSearchExecution(
        strategy_artifact_id="s",
        query_artifact_ids=[],
        search_record_artifact_ids=[],
        paper_artifact_ids=[],
        paper_identity_artifact_ids=ids,
        counts={},
        provider_failures=[],
    )
    exec_env = ArtifactEnvelope.create(
        payload=exec_rec, artifact_type="literature_search_execution", producer="test"
    )
    await store.put(exec_env)
    view_builder = FakeViewBuilder(store)
    screener = FakeScreener(store, {})
    svc = ScreeningOrchestratorService(
        artifact_store=store,
        view_builder=view_builder,
        screener=screener,
        max_candidates=500,
        max_model_calls=1,
    )
    out_id = await svc.screen(exec_env.artifact_id, proto_env.artifact_id)
    out = (await store.get(out_id)).parse_payload(ScreeningExecution)
    assert len(out.decision_artifact_ids) == 1
    assert out.budget_stop_reason == "max_model_calls 1 reached"
    await store.close()


@pytest.mark.asyncio
async def test_orchestrator_idempotency_reuses_decisions(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_orchestrator.plugin import (
        ScreeningOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    p = PaperRecord(title="P")
    p_env = ArtifactEnvelope.create(payload=p, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    pi = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    pi_env = ArtifactEnvelope.create(payload=pi, artifact_type="paper_identity", producer="test")
    await store.put(pi_env)
    proto = ScreeningProtocol(
        research_question_id="rq",
        objective="obj",
        inclusion_criteria=[
            ScreeningCriterion(criterion_id="I1", kind="inclusion", description="d")
        ],
        status=ProtocolStatus.approved,
    )
    proto_env = ArtifactEnvelope.create(
        payload=proto, artifact_type="screening_protocol", producer="test"
    )
    await store.put(proto_env)
    exec_rec = LiteratureSearchExecution(
        strategy_artifact_id="s",
        query_artifact_ids=[],
        search_record_artifact_ids=[],
        paper_artifact_ids=[],
        paper_identity_artifact_ids=[pi_env.artifact_id],
        counts={},
        provider_failures=[],
    )
    exec_env = ArtifactEnvelope.create(
        payload=exec_rec, artifact_type="literature_search_execution", producer="test"
    )
    await store.put(exec_env)
    view_builder = FakeViewBuilder(store)
    screener = FakeScreener(store, {})
    svc = ScreeningOrchestratorService(
        artifact_store=store,
        view_builder=view_builder,
        screener=screener,
        max_candidates=500,
        max_model_calls=500,
    )
    out1 = await svc.screen(exec_env.artifact_id, proto_env.artifact_id)
    # Second run should reuse
    svc2 = ScreeningOrchestratorService(
        artifact_store=store,
        view_builder=FakeViewBuilder(store),
        screener=FakeScreener(store, {}),
        max_candidates=500,
        max_model_calls=500,
    )
    out2 = await svc2.screen(exec_env.artifact_id, proto_env.artifact_id)
    out1_rec = (await store.get(out1)).parse_payload(ScreeningExecution)
    out2_rec = (await store.get(out2)).parse_payload(ScreeningExecution)
    assert out1_rec.decision_artifact_ids[0] == out2_rec.decision_artifact_ids[0]
    assert out2_rec.counts["reused"] == 1
    assert len(out2_rec.screening_view_ids) == 0  # reused, no new view built
    await store.close()


@pytest.mark.asyncio
async def test_orchestrator_review_for_uncertain(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_orchestrator.plugin import (
        ScreeningOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    p = PaperRecord(title="P")
    p_env = ArtifactEnvelope.create(payload=p, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    pi = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    pi_env = ArtifactEnvelope.create(payload=pi, artifact_type="paper_identity", producer="test")
    await store.put(pi_env)
    proto = ScreeningProtocol(
        research_question_id="rq",
        objective="obj",
        inclusion_criteria=[
            ScreeningCriterion(criterion_id="I1", kind="inclusion", description="d")
        ],
        status=ProtocolStatus.approved,
    )
    proto_env = ArtifactEnvelope.create(
        payload=proto, artifact_type="screening_protocol", producer="test"
    )
    await store.put(proto_env)
    exec_rec = LiteratureSearchExecution(
        strategy_artifact_id="s",
        query_artifact_ids=[],
        search_record_artifact_ids=[],
        paper_artifact_ids=[],
        paper_identity_artifact_ids=[pi_env.artifact_id],
        counts={},
        provider_failures=[],
    )
    exec_env = ArtifactEnvelope.create(
        payload=exec_rec, artifact_type="literature_search_execution", producer="test"
    )
    await store.put(exec_env)
    view_builder = FakeViewBuilder(store)
    screener = FakeScreener(store, {pi_env.artifact_id: ("uncertain", 0.5, "insufficient")})
    autonomy = FakeAutonomy()
    svc = ScreeningOrchestratorService(
        artifact_store=store,
        view_builder=view_builder,
        screener=screener,
        autonomy_policy=autonomy,
        review_uncertain=True,
        review_low_confidence_below=0.65,
    )
    out_id = await svc.screen(exec_env.artifact_id, proto_env.artifact_id)
    out = (await store.get(out_id)).parse_payload(ScreeningExecution)
    assert len(out.review_artifact_ids) == 1
    # Also check that screened set has uncertain
    sets = await store.list(artifact_type="screened_literature_set")
    assert len(sets) == 1
    from research_harness.research.schemas.screening_execution import ScreenedLiteratureSet

    s = sets[0].parse_payload(ScreenedLiteratureSet)
    assert pi_env.artifact_id in s.uncertain_identity_ids
    assert len(autonomy.requests) == 1
    await store.close()


@pytest.mark.asyncio
async def test_orchestrator_low_confidence_review(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_orchestrator.plugin import (
        ScreeningOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    p = PaperRecord(title="P")
    p_env = ArtifactEnvelope.create(payload=p, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    pi = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    pi_env = ArtifactEnvelope.create(payload=pi, artifact_type="paper_identity", producer="test")
    await store.put(pi_env)
    proto = ScreeningProtocol(
        research_question_id="rq",
        objective="obj",
        inclusion_criteria=[
            ScreeningCriterion(criterion_id="I1", kind="inclusion", description="d")
        ],
        status=ProtocolStatus.approved,
    )
    proto_env = ArtifactEnvelope.create(
        payload=proto, artifact_type="screening_protocol", producer="test"
    )
    await store.put(proto_env)
    exec_rec = LiteratureSearchExecution(
        strategy_artifact_id="s",
        query_artifact_ids=[],
        search_record_artifact_ids=[],
        paper_artifact_ids=[],
        paper_identity_artifact_ids=[pi_env.artifact_id],
        counts={},
        provider_failures=[],
    )
    exec_env = ArtifactEnvelope.create(
        payload=exec_rec, artifact_type="literature_search_execution", producer="test"
    )
    await store.put(exec_env)
    view_builder = FakeViewBuilder(store)
    screener = FakeScreener(
        store, {pi_env.artifact_id: ("include", 0.5, "sufficient")}
    )  # low confidence 0.5 < 0.65
    autonomy = FakeAutonomy()
    svc = ScreeningOrchestratorService(
        artifact_store=store,
        view_builder=view_builder,
        screener=screener,
        autonomy_policy=autonomy,
        review_low_confidence_below=0.65,
    )
    out_id = await svc.screen(exec_env.artifact_id, proto_env.artifact_id)
    out = (await store.get(out_id)).parse_payload(ScreeningExecution)
    assert len(out.review_artifact_ids) == 1
    await store.close()


@pytest.mark.asyncio
async def test_orchestrator_failed_view_recorded(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_orchestrator.plugin import (
        ScreeningOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    p = PaperRecord(title="P")
    p_env = ArtifactEnvelope.create(payload=p, artifact_type="paper_record", producer="test")
    await store.put(p_env)
    pi = PaperIdentity(
        member_paper_artifact_ids=[p_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    pi_env = ArtifactEnvelope.create(payload=pi, artifact_type="paper_identity", producer="test")
    await store.put(pi_env)
    proto = ScreeningProtocol(
        research_question_id="rq",
        objective="obj",
        inclusion_criteria=[
            ScreeningCriterion(criterion_id="I1", kind="inclusion", description="d")
        ],
        status=ProtocolStatus.approved,
    )
    proto_env = ArtifactEnvelope.create(
        payload=proto, artifact_type="screening_protocol", producer="test"
    )
    await store.put(proto_env)
    exec_rec = LiteratureSearchExecution(
        strategy_artifact_id="s",
        query_artifact_ids=[],
        search_record_artifact_ids=[],
        paper_artifact_ids=[],
        paper_identity_artifact_ids=[pi_env.artifact_id],
        counts={},
        provider_failures=[],
    )
    exec_env = ArtifactEnvelope.create(
        payload=exec_rec, artifact_type="literature_search_execution", producer="test"
    )
    await store.put(exec_env)
    view_builder = FailingViewBuilder()
    screener = FakeScreener(store, {})
    svc = ScreeningOrchestratorService(
        artifact_store=store, view_builder=view_builder, screener=screener
    )
    out_id = await svc.screen(exec_env.artifact_id, proto_env.artifact_id)
    out = (await store.get(out_id)).parse_payload(ScreeningExecution)
    assert out.counts["failed"] == 1
    assert len(out.failures) == 1
    assert out.failures[0]["stage"] == "view"
    assert len(out.decision_artifact_ids) == 0
    await store.close()


@pytest.mark.asyncio
async def test_orchestrator_creates_screened_set_partition(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_orchestrator.plugin import (
        ScreeningOrchestratorService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = []
    decisions = {}
    for i, dec in enumerate(["include", "exclude", "uncertain"]):
        p = PaperRecord(title=f"P{i}")
        p_env = ArtifactEnvelope.create(payload=p, artifact_type="paper_record", producer="test")
        await store.put(p_env)
        pi = PaperIdentity(
            member_paper_artifact_ids=[p_env.artifact_id],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.exact_identifier,
            resolution_evidence=[],
        )
        pi_env = ArtifactEnvelope.create(
            payload=pi, artifact_type="paper_identity", producer="test"
        )
        await store.put(pi_env)
        ids.append(pi_env.artifact_id)
        decisions[pi_env.artifact_id] = (
            dec,
            0.9 if dec != "uncertain" else 0.5,
            "sufficient" if dec != "uncertain" else "insufficient",
        )
    proto = ScreeningProtocol(
        research_question_id="rq",
        objective="obj",
        inclusion_criteria=[
            ScreeningCriterion(criterion_id="I1", kind="inclusion", description="d")
        ],
        exclusion_criteria=[
            ScreeningCriterion(criterion_id="E1", kind="exclusion", description="e")
        ],
        status=ProtocolStatus.approved,
    )
    proto_env = ArtifactEnvelope.create(
        payload=proto, artifact_type="screening_protocol", producer="test"
    )
    await store.put(proto_env)
    exec_rec = LiteratureSearchExecution(
        strategy_artifact_id="s",
        query_artifact_ids=[],
        search_record_artifact_ids=[],
        paper_artifact_ids=[],
        paper_identity_artifact_ids=ids,
        counts={},
        provider_failures=[],
    )
    exec_env = ArtifactEnvelope.create(
        payload=exec_rec, artifact_type="literature_search_execution", producer="test"
    )
    await store.put(exec_env)
    view_builder = FakeViewBuilder(store)
    screener = FakeScreener(store, decisions)
    svc = ScreeningOrchestratorService(
        artifact_store=store,
        view_builder=view_builder,
        screener=screener,
        review_uncertain=False,
        review_low_confidence_below=None,
    )
    out_id = await svc.screen(exec_env.artifact_id, proto_env.artifact_id)
    out = (await store.get(out_id)).parse_payload(ScreeningExecution)
    assert out.counts["included"] == 1
    assert out.counts["excluded"] == 1
    assert out.counts["uncertain"] == 1
    sets = await store.list(artifact_type="screened_literature_set")
    from research_harness.research.schemas.screening_execution import ScreenedLiteratureSet

    s = sets[0].parse_payload(ScreenedLiteratureSet)
    assert len(s.included_identity_ids) == 1
    assert len(s.excluded_identity_ids) == 1
    assert len(s.uncertain_identity_ids) == 1
    await store.close()
