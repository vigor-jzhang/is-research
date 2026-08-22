import json
import pathlib

import pytest

from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.identity import (
    PaperIdentity,
    ResolutionMethod,
)
from research_harness.research.schemas.screening_protocol import (
    ProtocolStatus,
    ScreeningCriterion,
    ScreeningProtocol,
)
from research_harness.research.schemas.screening_view import PaperScreeningView


class FakeRouter:
    def __init__(self, content: str, should_fail=False):
        self.content = content
        self.should_fail = should_fail
        self.last_role = None
        self.last_request = None
        self.calls = 0

    async def complete(self, role, request):
        self.last_role = role
        self.last_request = request
        self.calls += 1
        if self.should_fail:
            raise RuntimeError("model failure")
        return ModelResponse(
            message=Message(role="assistant", content=self.content),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


def _protocol(store, approved=True):
    proto = ScreeningProtocol(
        research_question_id="rq1",
        objective="Test objective",
        inclusion_criteria=[
            ScreeningCriterion(
                criterion_id="I1", kind="inclusion", description="Include if about pricing"
            )
        ],
        exclusion_criteria=[
            ScreeningCriterion(
                criterion_id="E1", kind="exclusion", description="Exclude if non-scholarly"
            )
        ],
        status=ProtocolStatus.approved if approved else ProtocolStatus.draft,
    )
    env = ArtifactEnvelope.create(
        payload=proto, artifact_type="screening_protocol", producer="test"
    )
    return env


def _view(store, paper_identity_id, title="Title", abstract="Abstract about pricing", year=2020):
    view = PaperScreeningView(
        paper_identity_id=paper_identity_id,
        title=title,
        abstract=abstract,
        authors=["A One"],
        year=year,
        venue="J",
        field_sources={},
        member_paper_artifact_ids=[],
    )
    env = ArtifactEnvelope.create(
        payload=view, artifact_type="paper_screening_view", producer="test"
    )
    return env


@pytest.mark.asyncio
async def test_screener_happy_include(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.title_abstract_screener.plugin import (
        TitleAbstractScreenerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    # Setup identity -> view -> protocol
    pi = PaperIdentity(
        member_paper_artifact_ids=["p1"],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    pi_env = ArtifactEnvelope.create(payload=pi, artifact_type="paper_identity", producer="test")
    await store.put(pi_env)
    proto_env = _protocol(store, approved=True)
    await store.put(proto_env)
    view_env = _view(
        store,
        pi_env.artifact_id,
        title="Pricing in platforms",
        abstract="Study on algorithmic pricing platforms",
    )
    await store.put(view_env)

    decision_json = json.dumps(
        {
            "decision": "include",
            "matched_inclusion_criteria": ["I1"],
            "matched_exclusion_criteria": [],
            "reason_codes": ["R1"],
            "rationale_summary": "Matches I1 and no exclusion, sufficient info.",
            "confidence": 0.9,
            "information_sufficiency": "sufficient",
        }
    )
    router = FakeRouter(content=decision_json)
    svc = TitleAbstractScreenerService(model_router=router, artifact_store=store, model_role="fast")
    dec_id = await svc.screen(view_env.artifact_id, proto_env.artifact_id)
    assert dec_id
    from research_harness.research.schemas.screening_decision import ScreeningDecision

    dec = (await store.get(dec_id)).parse_payload(ScreeningDecision)
    assert dec.decision.value == "include"
    assert dec.matched_inclusion_criteria == ["I1"]
    assert dec.confidence == 0.9
    assert router.last_role == "fast"
    # Provenance
    parents = await store.get_parents(dec_id)
    assert any(p.source_artifact_id == pi_env.artifact_id for p in parents)
    assert any(p.source_artifact_id == view_env.artifact_id for p in parents)
    assert any(p.source_artifact_id == proto_env.artifact_id for p in parents)
    await store.close()


@pytest.mark.asyncio
async def test_screener_rejects_draft_protocol(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.title_abstract_screener.plugin import (
        TitleAbstractScreenerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    pi = PaperIdentity(
        member_paper_artifact_ids=["p1"],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    pi_env = ArtifactEnvelope.create(payload=pi, artifact_type="paper_identity", producer="test")
    await store.put(pi_env)
    proto_env = _protocol(store, approved=False)
    await store.put(proto_env)
    view_env = _view(store, pi_env.artifact_id)
    await store.put(view_env)
    router = FakeRouter(
        content=json.dumps(
            {
                "decision": "include",
                "matched_inclusion_criteria": ["I1"],
                "matched_exclusion_criteria": [],
                "reason_codes": [],
                "rationale_summary": "r",
                "confidence": 0.9,
                "information_sufficiency": "sufficient",
            }
        )
    )
    svc = TitleAbstractScreenerService(model_router=router, artifact_store=store)
    with pytest.raises(ValueError, match="must be 'approved'"):
        await svc.screen(view_env.artifact_id, proto_env.artifact_id)
    await store.close()


@pytest.mark.asyncio
async def test_screener_hallucinated_criterion_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.title_abstract_screener.plugin import (
        TitleAbstractScreenerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    pi = PaperIdentity(
        member_paper_artifact_ids=["p1"],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    pi_env = ArtifactEnvelope.create(payload=pi, artifact_type="paper_identity", producer="test")
    await store.put(pi_env)
    proto_env = _protocol(store, approved=True)
    await store.put(proto_env)
    view_env = _view(store, pi_env.artifact_id)
    await store.put(view_env)
    bad_json = json.dumps(
        {
            "decision": "include",
            "matched_inclusion_criteria": ["I999"],
            "matched_exclusion_criteria": [],
            "reason_codes": [],
            "rationale_summary": "r",
            "confidence": 0.9,
            "information_sufficiency": "sufficient",
        }
    )
    router = FakeRouter(content=bad_json)
    svc = TitleAbstractScreenerService(model_router=router, artifact_store=store)
    with pytest.raises(ValueError, match="hallucinated"):
        await svc.screen(view_env.artifact_id, proto_env.artifact_id)
    await store.close()


@pytest.mark.asyncio
async def test_screener_invalid_json_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.title_abstract_screener.plugin import (
        TitleAbstractScreenerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    pi = PaperIdentity(
        member_paper_artifact_ids=["p1"],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    pi_env = ArtifactEnvelope.create(payload=pi, artifact_type="paper_identity", producer="test")
    await store.put(pi_env)
    proto_env = _protocol(store)
    await store.put(proto_env)
    view_env = _view(store, pi_env.artifact_id)
    await store.put(view_env)
    router = FakeRouter(content="not json")
    svc = TitleAbstractScreenerService(model_router=router, artifact_store=store)
    with pytest.raises(ValueError, match="invalid JSON"):
        await svc.screen(view_env.artifact_id, proto_env.artifact_id)
    await store.close()


@pytest.mark.asyncio
async def test_screener_missing_abstract_uses_uncertain_insufficient(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.title_abstract_screener.plugin import (
        TitleAbstractScreenerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    pi = PaperIdentity(
        member_paper_artifact_ids=["p1"],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    pi_env = ArtifactEnvelope.create(payload=pi, artifact_type="paper_identity", producer="test")
    await store.put(pi_env)
    proto_env = _protocol(store)
    await store.put(proto_env)
    view_env = _view(store, pi_env.artifact_id, title="Ambiguous Title", abstract=None)
    await store.put(view_env)
    # Model correctly returns uncertain + insufficient per spec (missing abstract must not auto-exclude)
    decision_json = json.dumps(
        {
            "decision": "uncertain",
            "matched_inclusion_criteria": [],
            "matched_exclusion_criteria": [],
            "reason_codes": [],
            "rationale_summary": "No abstract, insufficient to determine.",
            "confidence": 0.5,
            "information_sufficiency": "insufficient",
        }
    )
    router = FakeRouter(content=decision_json)
    svc = TitleAbstractScreenerService(model_router=router, artifact_store=store)
    dec_id = await svc.screen(view_env.artifact_id, proto_env.artifact_id)
    from research_harness.research.schemas.screening_decision import ScreeningDecision

    dec = (await store.get(dec_id)).parse_payload(ScreeningDecision)
    assert dec.decision.value == "uncertain"
    assert dec.information_sufficiency.value == "insufficient"
    await store.close()


@pytest.mark.asyncio
async def test_screener_exclusion_matched_but_decision_exclude_semantics(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.title_abstract_screener.plugin import (
        TitleAbstractScreenerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    pi = PaperIdentity(
        member_paper_artifact_ids=["p1"],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    pi_env = ArtifactEnvelope.create(payload=pi, artifact_type="paper_identity", producer="test")
    await store.put(pi_env)
    proto_env = _protocol(store)
    await store.put(proto_env)
    view_env = _view(store, pi_env.artifact_id, abstract="Non-scholarly blog post")
    await store.put(view_env)
    # Model matches exclusion
    decision_json = json.dumps(
        {
            "decision": "exclude",
            "matched_inclusion_criteria": [],
            "matched_exclusion_criteria": ["E1"],
            "reason_codes": [],
            "rationale_summary": "Matches exclusion E1",
            "confidence": 0.95,
            "information_sufficiency": "sufficient",
        }
    )
    router = FakeRouter(content=decision_json)
    svc = TitleAbstractScreenerService(model_router=router, artifact_store=store)
    dec_id = await svc.screen(view_env.artifact_id, proto_env.artifact_id)
    from research_harness.research.schemas.screening_decision import ScreeningDecision

    dec = (await store.get(dec_id)).parse_payload(ScreeningDecision)
    assert dec.decision.value == "exclude"
    assert dec.matched_exclusion_criteria == ["E1"]
    await store.close()


@pytest.mark.asyncio
async def test_screener_idempotency_same_view_protocol(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.title_abstract_screener.plugin import (
        TitleAbstractScreenerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    pi = PaperIdentity(
        member_paper_artifact_ids=["p1"],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    pi_env = ArtifactEnvelope.create(payload=pi, artifact_type="paper_identity", producer="test")
    await store.put(pi_env)
    proto_env = _protocol(store)
    await store.put(proto_env)
    view_env = _view(store, pi_env.artifact_id)
    await store.put(view_env)
    decision_json = json.dumps(
        {
            "decision": "include",
            "matched_inclusion_criteria": ["I1"],
            "matched_exclusion_criteria": [],
            "reason_codes": [],
            "rationale_summary": "r",
            "confidence": 0.9,
            "information_sufficiency": "sufficient",
        }
    )
    router = FakeRouter(content=decision_json)
    svc = TitleAbstractScreenerService(model_router=router, artifact_store=store)
    id1 = await svc.screen(view_env.artifact_id, proto_env.artifact_id)
    id2 = await svc.screen(view_env.artifact_id, proto_env.artifact_id)
    assert id1 == id2
    assert router.calls == 1  # second call reused without model call
    decisions = await store.list(artifact_type="screening_decision")
    assert len(decisions) == 1
    await store.close()


@pytest.mark.asyncio
async def test_screener_model_failure_wrapped(tmp_path: pathlib.Path):
    from research_harness.plugins.literature.title_abstract_screener.plugin import (
        TitleAbstractScreenerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    pi = PaperIdentity(
        member_paper_artifact_ids=["p1"],
        canonical_identifiers=[],
        resolution_method=ResolutionMethod.exact_identifier,
        resolution_evidence=[],
    )
    pi_env = ArtifactEnvelope.create(payload=pi, artifact_type="paper_identity", producer="test")
    await store.put(pi_env)
    proto_env = _protocol(store)
    await store.put(proto_env)
    view_env = _view(store, pi_env.artifact_id)
    await store.put(view_env)
    router = FakeRouter(content="", should_fail=True)
    svc = TitleAbstractScreenerService(model_router=router, artifact_store=store)
    with pytest.raises(RuntimeError, match="model call failed"):
        await svc.screen(view_env.artifact_id, proto_env.artifact_id)
    await store.close()
