import json
import pathlib

import pytest

from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.execution import LiteratureSearchExecution
from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
from research_harness.research.schemas.paper import PaperRecord
from research_harness.research.schemas.project import ResearchQuestion
from research_harness.research.schemas.screening_protocol import ProtocolStatus


class FakeModelRouter:
    def __init__(self):
        self.protocol_proposal = {
            "objective": "Screen papers for algorithmic pricing review",
            "inclusion_criteria": [
                {
                    "criterion_id": "I1",
                    "description": "Study on algorithmic pricing",
                    "rationale": "core topic",
                    "required": True,
                },
                {
                    "criterion_id": "I2",
                    "description": "Empirical or modeling study",
                    "rationale": "",
                    "required": True,
                },
            ],
            "exclusion_criteria": [
                {
                    "criterion_id": "E1",
                    "description": "Non-scholarly blog post",
                    "rationale": "",
                    "required": True,
                },
            ],
            "decision_rules": "Include if I1+I2 and no E1; exclude if E1; uncertain if insufficient.",
        }
        self.calls = []

    async def complete(self, role, request):
        self.calls.append((role, request))
        # Detect protocol vs screener by response_schema properties
        schema = request.response_schema or {}
        props = schema.get("properties", {})
        if "inclusion_criteria" in props:
            # protocol builder
            assert role == "reasoning"
            return ModelResponse(
                message=Message(role="assistant", content=json.dumps(self.protocol_proposal)),
                tool_calls=[],
                finish_reason="stop",
                model="fake",
            )
        else:
            # screener - parse prompt to decide based on deterministic view Title
            content = ""
            for m in request.messages:
                content += m.content + "\n"
            # Extract Title line to avoid matching Inclusion Criteria header
            title_line = ""
            for line in content.splitlines():
                if line.strip().startswith("Title:"):
                    title_line = line.split("Title:", 1)[1].strip().upper()
                    break
            if "INCLUDE" in title_line:
                decision = "include"
                incl = ["I1", "I2"]
                excl = []
                suff = "sufficient"
                conf = 0.9
            elif "EXCLUDE" in title_line or "BLOG" in title_line:
                decision = "exclude"
                incl = []
                excl = ["E1"]
                suff = "sufficient"
                conf = 0.95
            else:
                decision = "uncertain"
                incl = []
                excl = []
                suff = "insufficient"
                conf = 0.5
            payload = {
                "decision": decision,
                "matched_inclusion_criteria": incl,
                "matched_exclusion_criteria": excl,
                "reason_codes": ["R1"],
                "rationale_summary": f"Fake decision {decision}",
                "confidence": conf,
                "information_sufficiency": suff,
            }
            assert role == "fast"
            return ModelResponse(
                message=Message(role="assistant", content=json.dumps(payload)),
                tool_calls=[],
                finish_reason="stop",
                model="fake",
            )

    def resolve(self, role):
        return {"provider": "fake", "model": "fake"}


class FakeAutonomy:
    async def request_approval(self, req):
        from research_harness.contracts.autonomy import ApprovalDecision

        return ApprovalDecision(
            request_id=req.request_id, approved=True, reason="auto", decided_by="fake"
        )

    async def requires_approval(self, checkpoint):
        return False


@pytest.mark.asyncio
async def test_phase2d_screening_end_to_end_offline(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    router = FakeModelRouter()
    autonomy = FakeAutonomy()

    # 1. Create ResearchQuestion
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="How does algorithmic pricing affect competition?"),
        artifact_type="research_question",
    )
    await store.put(rq)

    # 2. Build ScreeningProtocol via service with fake model
    from research_harness.plugins.literature.screening_protocol_builder.plugin import (
        ScreeningProtocolBuilderService,
    )

    proto_svc = ScreeningProtocolBuilderService(
        model_router=router, artifact_store=store, autonomy_policy=autonomy, model_role="reasoning"
    )
    protocol_id = await proto_svc.build(rq.artifact_id)
    from research_harness.research.schemas.screening_protocol import ScreeningProtocol

    proto = (await store.get(protocol_id)).parse_payload(ScreeningProtocol)
    assert proto.status == ProtocolStatus.approved
    assert len(proto.inclusion_criteria) == 2
    assert len(proto.exclusion_criteria) == 1

    # 3. Create 3 PaperRecords -> identities (one missing abstract -> uncertain)
    p1 = PaperRecord(
        title="INCLUDE pricing study on platforms",
        abstract="Study on algorithmic pricing with empirical model",
        year=2021,
        venue="J1",
        doi="10.123/a",
    )
    p2 = PaperRecord(
        title="EXCLUDE blog post on pricing",
        abstract="Blog post non-scholarly content",
        year=2021,
        venue="Blog",
        doi="10.123/b",
    )
    p3 = PaperRecord(
        title="Ambiguous title", abstract=None, year=2021, doi="10.123/c"
    )  # missing abstract -> uncertain

    p1_env = ArtifactEnvelope.create(payload=p1, artifact_type="paper_record", producer="test")
    p2_env = ArtifactEnvelope.create(payload=p2, artifact_type="paper_record", producer="test")
    p3_env = ArtifactEnvelope.create(payload=p3, artifact_type="paper_record", producer="test")
    await store.put(p1_env)
    await store.put(p2_env)
    await store.put(p3_env)

    # Create identities (singleton each)
    def _ident(pid):
        return PaperIdentity(
            member_paper_artifact_ids=[pid],
            canonical_identifiers=[],
            resolution_method=ResolutionMethod.exact_identifier,
            resolution_evidence=[],
        )

    id1_env = ArtifactEnvelope.create(
        payload=_ident(p1_env.artifact_id), artifact_type="paper_identity", producer="test"
    )
    id2_env = ArtifactEnvelope.create(
        payload=_ident(p2_env.artifact_id), artifact_type="paper_identity", producer="test"
    )
    id3_env = ArtifactEnvelope.create(
        payload=_ident(p3_env.artifact_id), artifact_type="paper_identity", producer="test"
    )
    await store.put(id1_env)
    await store.put(id2_env)
    await store.put(id3_env)

    # Create a LiteratureSearchExecution that aggregates these 3 identities
    exec_rec = LiteratureSearchExecution(
        strategy_artifact_id="s",
        query_artifact_ids=[],
        search_record_artifact_ids=[],
        paper_artifact_ids=[p1_env.artifact_id, p2_env.artifact_id, p3_env.artifact_id],
        paper_identity_artifact_ids=[id1_env.artifact_id, id2_env.artifact_id, id3_env.artifact_id],
        counts={"raw_paper_records": 3},
        provider_failures=[],
    )
    exec_env = ArtifactEnvelope.create(
        payload=exec_rec, artifact_type="literature_search_execution", producer="test"
    )
    await store.put(exec_env)

    # 4. Run screening orchestrator with real view builder + screener + orchestrator
    from research_harness.plugins.literature.screening_orchestrator.plugin import (
        ScreeningOrchestratorService,
    )
    from research_harness.plugins.literature.screening_view_builder.plugin import (
        ScreeningViewBuilderService,
    )
    from research_harness.plugins.literature.title_abstract_screener.plugin import (
        TitleAbstractScreenerService,
    )

    view_builder = ScreeningViewBuilderService(artifact_store=store)
    screener = TitleAbstractScreenerService(
        model_router=router, artifact_store=store, model_role="fast"
    )
    orchestrator = ScreeningOrchestratorService(
        artifact_store=store,
        view_builder=view_builder,
        screener=screener,
        autonomy_policy=autonomy,
        max_candidates=500,
        max_model_calls=500,
        review_uncertain=True,
        review_low_confidence_below=0.65,
    )

    screening_exec_id = await orchestrator.screen(exec_env.artifact_id, protocol_id)

    # 5. Verify ScreeningExecution counts
    from research_harness.research.schemas.screening_execution import (
        ScreenedLiteratureSet,
        ScreeningExecution,
    )

    screening_exec = (await store.get(screening_exec_id)).parse_payload(ScreeningExecution)
    assert screening_exec.counts["total_candidates"] == 3
    assert screening_exec.counts["included"] == 1
    assert screening_exec.counts["excluded"] == 1
    assert screening_exec.counts["uncertain"] == 1
    assert screening_exec.counts["failed"] == 0
    assert len(screening_exec.decision_artifact_ids) == 3
    # Check review created for uncertain (id3)
    assert len(screening_exec.review_artifact_ids) == 1

    # 6. Verify ScreenedLiteratureSet partition and that missing abstract correctly marked uncertain not excluded
    sets = await store.list(artifact_type="screened_literature_set")
    assert len(sets) == 1
    s = sets[0].parse_payload(ScreenedLiteratureSet)
    assert id1_env.artifact_id in s.included_identity_ids
    assert id2_env.artifact_id in s.excluded_identity_ids
    assert id3_env.artifact_id in s.uncertain_identity_ids
    assert s.screening_execution_id == screening_exec_id

    # 7. Verify provenance
    # ScreeningExecution derived_from protocol and search execution
    parents = await store.get_parents(screening_exec_id)
    assert any(p.source_artifact_id == protocol_id for p in parents)
    assert any(p.source_artifact_id == exec_env.artifact_id for p in parents)
    # Screened set derived_from execution
    set_parents = await store.get_parents(sets[0].artifact_id)
    assert any(p.source_artifact_id == screening_exec_id for p in set_parents)

    # 8. Verify PaperScreeningView deterministic and conflict handling (p3 has missing_abstract)
    views = await store.list(artifact_type="paper_screening_view")
    assert len(views) == 3
    # Find view for id3
    from research_harness.research.schemas.screening_view import PaperScreeningView

    for v_env in views:
        v = v_env.parse_payload(PaperScreeningView)
        if v.paper_identity_id == id3_env.artifact_id:
            assert v.abstract is None
            assert v.metadata.get("missing_abstract") is True

    # 9. Idempotency: second run reuses decisions (no new model calls for same inputs if we track, but our FakeRouter will be called only for new? Actually orchestrator should reuse)
    before_calls = len(router.calls)
    # Need to count only fast calls for screening
    fast_before = len([c for c in router.calls if c[0] == "fast"])
    screening_exec_id2 = await orchestrator.screen(exec_env.artifact_id, protocol_id)
    fast_after = len([c for c in router.calls if c[0] == "fast"])
    # Second run should reuse all 3 decisions -> no new fast calls
    assert fast_after == fast_before
    # Verify reused count
    screening_exec2 = (await store.get(screening_exec_id2)).parse_payload(ScreeningExecution)
    assert screening_exec2.counts["reused"] == 3

    # 10. Superseded filtering: create a superseded identity and ensure orchestrator skips it
    p4 = PaperRecord(title="Superseded paper", abstract="old", year=2020)
    p4_env = ArtifactEnvelope.create(payload=p4, artifact_type="paper_record", producer="test")
    await store.put(p4_env)
    old_id = PaperIdentity(
        member_paper_artifact_ids=[p4_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    old_env = ArtifactEnvelope.create(
        payload=old_id, artifact_type="paper_identity", producer="test"
    )
    await store.put(old_env)
    new_id = PaperIdentity(
        member_paper_artifact_ids=[p4_env.artifact_id],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    new_env = ArtifactEnvelope.create(
        payload=new_id, artifact_type="paper_identity", producer="test"
    )
    await store.put(new_env)
    await store.add_provenance(
        ProvenanceLink(
            relation=ProvenanceRelation.supersedes,
            source_artifact_id=old_env.artifact_id,
            target_artifact_id=new_env.artifact_id,
            producer="test",
        )
    )
    # New execution that includes superseded old id
    exec2 = LiteratureSearchExecution(
        strategy_artifact_id="s",
        query_artifact_ids=[],
        search_record_artifact_ids=[],
        paper_artifact_ids=[],
        paper_identity_artifact_ids=[old_env.artifact_id, id1_env.artifact_id],
        counts={},
        provider_failures=[],
    )
    exec2_env = ArtifactEnvelope.create(
        payload=exec2, artifact_type="literature_search_execution", producer="test"
    )
    await store.put(exec2_env)
    out_id = await orchestrator.screen(exec2_env.artifact_id, protocol_id)
    out = (await store.get(out_id)).parse_payload(ScreeningExecution)
    assert (
        old_env.artifact_id not in out.candidate_identity_ids or out.counts["total_candidates"] == 1
    )
    assert out.counts["total_candidates"] == 1  # only id1 is current

    # 11. DB reopen persistence
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")
    assert await store2.exists(protocol_id)
    assert await store2.exists(screening_exec_id)
    for did in screening_exec.decision_artifact_ids:
        assert await store2.exists(did)
    await store2.close()
