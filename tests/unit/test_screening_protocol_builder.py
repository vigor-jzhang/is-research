import json
import pathlib

import pytest

from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.project import ResearchQuestion
from research_harness.research.schemas.screening_protocol import ProtocolStatus


class FakeRouter:
    def __init__(self, content: str, should_fail=False):
        self.content = content
        self.should_fail = should_fail
        self.last_role = None
        self.last_request = None

    async def complete(self, role, request):
        self.last_role = role
        self.last_request = request
        if self.should_fail:
            raise RuntimeError("model failure")
        return ModelResponse(
            message=Message(role="assistant", content=self.content),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


class FakeAutonomyApprove:
    async def request_approval(self, req):
        from research_harness.contracts.autonomy import ApprovalDecision

        return ApprovalDecision(
            request_id=req.request_id, approved=True, reason="auto-approved", decided_by="test"
        )

    async def requires_approval(self, checkpoint):
        return False


class FakeAutonomyReject:
    async def request_approval(self, req):
        from research_harness.contracts.autonomy import ApprovalDecision

        return ApprovalDecision(
            request_id=req.request_id, approved=False, reason="needs revision", decided_by="test"
        )

    async def requires_approval(self, checkpoint):
        return True


def _proposal(inclusion=1, exclusion=1):
    return {
        "objective": "Screen papers on algorithmic pricing",
        "inclusion_criteria": [
            {
                "criterion_id": f"I{i + 1}",
                "description": f"Inclusion {i + 1}",
                "rationale": "r",
                "required": True,
            }
            for i in range(inclusion)
        ],
        "exclusion_criteria": [
            {
                "criterion_id": f"E{i + 1}",
                "description": f"Exclusion {i + 1}",
                "rationale": "r",
                "required": True,
            }
            for i in range(exclusion)
        ],
        "decision_rules": "Include if all required inclusion and no exclusion.",
    }


@pytest.mark.asyncio
async def test_protocol_builder_approved_on_high_autonomy(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_protocol_builder.plugin import (
        ScreeningProtocolBuilderService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q: algorithmic pricing?"),
        artifact_type="research_question",
    )
    await store.put(rq)
    router = FakeRouter(content=json.dumps(_proposal()))
    svc = ScreeningProtocolBuilderService(
        model_router=router,
        artifact_store=store,
        autonomy_policy=FakeAutonomyApprove(),
        model_role="reasoning",
    )
    proto_id = await svc.build(rq.artifact_id)
    from research_harness.research.schemas.screening_protocol import ScreeningProtocol

    proto = (await store.get(proto_id)).parse_payload(ScreeningProtocol)
    assert proto.status == ProtocolStatus.approved
    assert proto.objective == "Screen papers on algorithmic pricing"
    assert len(proto.inclusion_criteria) == 1
    # draft should still exist and be superseded
    children = await store.get_children(proto_id)
    # Actually approved supersedes draft, so draft is parent
    # Find draft by listing all protocols
    all_protos = await store.list(artifact_type="screening_protocol")
    assert len(all_protos) == 2  # draft + approved
    # Check that approved has provenance supersedes draft
    # approved's parents should include supersedes link? Actually add_provenance source=draft target=approved relation=supersedes
    # So get_parents of approved should contain the supersedes link
    parents = await store.get_parents(proto_id)
    assert any(p.relation.value == "supersedes" for p in parents)
    await store.close()


@pytest.mark.asyncio
async def test_protocol_builder_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_protocol_builder.plugin import (
        ScreeningProtocolBuilderService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)
    router = FakeRouter(content=json.dumps(_proposal()))
    svc = ScreeningProtocolBuilderService(
        model_router=router, artifact_store=store, autonomy_policy=FakeAutonomyReject()
    )
    with pytest.raises(ValueError, match="rejected"):
        await svc.build(rq.artifact_id)
    # Should have created draft and rejected
    all_protos = await store.list(artifact_type="screening_protocol")
    assert len(all_protos) == 2
    from research_harness.research.schemas.screening_protocol import ScreeningProtocol

    statuses = {
        (await store.get(e.artifact_id)).parse_payload(ScreeningProtocol).status for e in all_protos
    }
    assert ProtocolStatus.draft in statuses
    assert ProtocolStatus.rejected in statuses
    await store.close()


@pytest.mark.asyncio
async def test_protocol_builder_duplicate_ids_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_protocol_builder.plugin import (
        ScreeningProtocolBuilderService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)
    dup = _proposal()
    dup["inclusion_criteria"][0]["criterion_id"] = "I1"
    dup["exclusion_criteria"][0]["criterion_id"] = "I1"  # duplicate across lists
    router = FakeRouter(content=json.dumps(dup))
    svc = ScreeningProtocolBuilderService(
        model_router=router, artifact_store=store, autonomy_policy=FakeAutonomyApprove()
    )
    with pytest.raises(ValueError, match="duplicate"):
        await svc.build(rq.artifact_id)
    await store.close()


@pytest.mark.asyncio
async def test_protocol_builder_too_many_criteria(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_protocol_builder.plugin import (
        ScreeningProtocolBuilderService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)
    many = _proposal(inclusion=13, exclusion=0)
    # Need to bypass model limit but service should still validate via max_inclusion=12
    router = FakeRouter(content=json.dumps(many))
    svc = ScreeningProtocolBuilderService(
        model_router=router,
        artifact_store=store,
        autonomy_policy=FakeAutonomyApprove(),
        max_inclusion=12,
    )
    with pytest.raises(ValueError, match="too many"):
        await svc.build(rq.artifact_id)
    await store.close()


@pytest.mark.asyncio
async def test_protocol_builder_empty_inclusion_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_protocol_builder.plugin import (
        ScreeningProtocolBuilderService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)
    empty = _proposal(inclusion=0)
    empty["inclusion_criteria"] = []
    router = FakeRouter(content=json.dumps(empty))
    svc = ScreeningProtocolBuilderService(
        model_router=router, artifact_store=store, autonomy_policy=FakeAutonomyApprove()
    )
    with pytest.raises(ValueError, match="at least one inclusion"):
        await svc.build(rq.artifact_id)
    await store.close()


@pytest.mark.asyncio
async def test_protocol_builder_malformed_json(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_protocol_builder.plugin import (
        ScreeningProtocolBuilderService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)
    router = FakeRouter(content="not json")
    svc = ScreeningProtocolBuilderService(
        model_router=router, artifact_store=store, autonomy_policy=FakeAutonomyApprove()
    )
    with pytest.raises(ValueError, match="invalid JSON"):
        await svc.build(rq.artifact_id)
    await store.close()


@pytest.mark.asyncio
async def test_protocol_builder_model_failure(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_protocol_builder.plugin import (
        ScreeningProtocolBuilderService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)
    router = FakeRouter(content="", should_fail=True)
    svc = ScreeningProtocolBuilderService(
        model_router=router, artifact_store=store, autonomy_policy=FakeAutonomyApprove()
    )
    with pytest.raises(RuntimeError, match="model call failed"):
        await svc.build(rq.artifact_id)
    await store.close()


@pytest.mark.asyncio
async def test_protocol_builder_validates_required_fields(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.screening_protocol_builder.plugin import (
        ScreeningProtocolBuilderService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    rq = ArtifactEnvelope.create(
        payload=ResearchQuestion(question="Q"), artifact_type="research_question"
    )
    await store.put(rq)
    bad = _proposal()
    bad["inclusion_criteria"][0]["description"] = "   "
    router = FakeRouter(content=json.dumps(bad))
    svc = ScreeningProtocolBuilderService(
        model_router=router, artifact_store=store, autonomy_policy=FakeAutonomyApprove()
    )
    with pytest.raises(ValueError):
        await svc.build(rq.artifact_id)
    await store.close()
