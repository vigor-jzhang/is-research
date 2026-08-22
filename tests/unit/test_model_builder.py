"""Phase 3B unit tests — model builder structural validation, critic,
immutable revision, idempotency, model-role change, provenance.

Fake models only, offline.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.evidence import EvidenceCategory, EvidenceItem, Locator
from research_harness.research.schemas.mechanism import (
    GroundingElement,
    KnowledgeBasis,
    MechanismEvaluation,
    SelectedMechanism,
)
from research_harness.research.schemas.synthesis import (
    SupportType,
    SynthesisStatement,
    SynthesisStatementType,
)


class FakeRouter:
    def __init__(self, responses: list[str] | None = None, fail: bool = False):
        self.responses = responses or []
        self.fail = fail
        self.calls = 0
        self.last_role = None

    async def complete(self, role, request):
        self.calls += 1
        self.last_role = role
        if self.fail:
            raise RuntimeError("model failure")
        idx = min(self.calls - 1, len(self.responses) - 1)
        content = self.responses[idx] if self.responses else "{}"
        return ModelResponse(
            message=Message(role="assistant", content=content),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


async def _evidence(store, statement: str) -> str:
    ev = EvidenceItem(
        statement=statement,
        source_artifact_id="doc1",
        category=EvidenceCategory.finding,
        locator=Locator(page=1, pages=[1]),
        extraction_method="model-assisted",
        confidence=0.9,
    )
    env = ArtifactEnvelope.create(payload=ev, artifact_type="evidence_item", producer="test")
    await store.put(env)
    return env.artifact_id


async def _mechanism_env(store, source_ids: list[str] | None = None) -> tuple[str, str]:
    """A SelectedMechanism with literature-supported grounding + statements.
    Returns (mechanism_id, statement_id)."""
    if not source_ids:
        source_ids = [await _evidence(store, "pricing responds to competition")]
    stmt = SynthesisStatement(
        statement="Competition raises seller entry, which changes platform pricing.",
        type=SynthesisStatementType.consensus,
        supporting_evidence_ids=list(source_ids or []),
        supporting_paper_identity_ids=["pi1"],
        papers_supporting=1,
        evidence_items_supporting=len(source_ids or []),
        support_type=SupportType.single_paper,
        confidence=0.8,
    )
    s_env = ArtifactEnvelope.create(
        payload=stmt, artifact_type="synthesis_statement", producer="test"
    )
    await store.put(s_env)
    grounding = [
        GroundingElement(
            element="platform pricing responds to competition",
            basis=KnowledgeBasis.literature_supported,
            source_ids=[s_env.artifact_id] + list(source_ids or []),
        ),
        GroundingElement(
            element="seller entry mediates the effect",
            basis=KnowledgeBasis.new_hypothesis,
            source_ids=[],
        ),
    ]
    sm = SelectedMechanism(
        gap_id="gap1",
        gap_selection_id="sel1",
        mechanism_candidate_id="cand1",
        name="Competition pricing mechanism",
        description="Platforms adjust fees as competition changes.",
        actors=["platform", "sellers"],
        strategic_interactions=["fee setting"],
        information_structure="sellers observe current fees",
        incentives=["profit maximization"],
        causal_logic="Competition increases entry; entry shifts pricing.",
        key_assumptions=["price-taking sellers"],
        expected_outcomes=["lower fees"],
        boundary_conditions=["two-sided markets"],
        grounding=grounding,
        evaluation=MechanismEvaluation(
            gap_alignment=0.9,
            theoretical_coherence=0.8,
            novelty_within_reviewed_corpus=0.7,
            analytical_tractability=0.8,
            managerial_economic_relevance=0.7,
            is_relevance=0.9,
        ),
    )
    env = ArtifactEnvelope.create(payload=sm, artifact_type="selected_mechanism", producer="test")
    await store.put(env)
    return env.artifact_id, s_env.artifact_id


def _model_spec(
    *,
    stmt_id: str = "",
    ev_id: str = "",
    duplicate: bool = False,
    undefined_payoff_sym: str | None = None,
    payoff_sym_undeclared: bool = False,
    decision_owner: str = "platform",
    unknown_owner: str | None = None,
    stage_dup: bool = False,
    stage_gap: bool = False,
    unknown_actor_stage: str | None = None,
    info_unknown_sym: str | None = None,
    info_bad_stage: int | None = None,
    unc_not_random: bool = False,
    lit_assumption_bad_source: bool = False,
    lit_assumption_no_source: bool = False,
) -> dict:
    variables = [
        {
            "symbol": "p",
            "name": "platform fee",
            "meaning": "fee platform charges sellers",
            "domain": "R_+",
            "kind": "decision_variable",
            "owner_actor_id": decision_owner,
        },
        {
            "symbol": "c",
            "name": "marginal cost",
            "meaning": "platform marginal cost",
            "domain": "R_+",
            "kind": "parameter",
        },
        {
            "symbol": "q",
            "name": "seller quantity",
            "meaning": "seller output",
            "domain": "R_+",
            "kind": "decision_variable",
            "owner_actor_id": "seller",
        },
        {
            "symbol": "theta",
            "name": "demand state",
            "meaning": "demand shock",
            "domain": "R",
            "kind": "random_variable",
        },
        {
            "symbol": "s",
            "name": "signal",
            "meaning": "seller signal of demand",
            "domain": "R",
            "kind": "observable_signal",
        },
    ]
    if duplicate:
        variables.append(
            {
                "symbol": "p",
                "name": "duplicate",
                "meaning": "dup symbol",
                "domain": "R",
                "kind": "state_variable",
            }
        )
    if unknown_owner:
        variables[0]["owner_actor_id"] = unknown_owner
    if unc_not_random:
        variables[3]["kind"] = "state_variable"

    pay_sym_used = ["p", "q", "c", "theta"] if payoff_sym_undeclared else ["p", "q", "c"]
    if undefined_payoff_sym:
        pay_sym_used = ["p", "q", "c", undefined_payoff_sym]

    timing = [
        {
            "stage_number": 0,
            "name": "parameters and types realized",
            "description": "theta realized; s drawn",
            "actor_ids": [],
        },
        {
            "stage_number": 1,
            "name": "platform sets fee",
            "description": "platform chooses p",
            "actor_ids": ["platform"],
        },
        {
            "stage_number": 2,
            "name": "seller observes signal",
            "description": "seller observes s",
            "actor_ids": ["seller"],
        },
        {
            "stage_number": 3,
            "name": "seller chooses quantity",
            "description": "seller chooses q",
            "actor_ids": ["seller"],
        },
        {
            "stage_number": 4,
            "name": "payoffs realized",
            "description": "demand and payoffs realized",
            "actor_ids": ["platform", "seller"],
        },
    ]
    if stage_dup:
        timing[1]["stage_number"] = 0
    if stage_gap:
        timing[1]["stage_number"] = 2
    if unknown_actor_stage:
        timing[1]["actor_ids"] = [unknown_actor_stage]

    info_items = [
        {
            "actor_id": "seller",
            "variable_symbols": ["s"],
            "available_at_stage": 2,
            "visibility": "private",
            "description": "seller observes demand signal",
        },
        {
            "actor_id": "platform",
            "variable_symbols": ["theta"],
            "available_at_stage": 0,
            "visibility": "public",
            "description": "platform observes demand state",
        },
    ]
    if info_unknown_sym:
        info_items[0]["variable_symbols"] = [info_unknown_sym]
    if info_bad_stage is not None:
        info_items[0]["available_at_stage"] = info_bad_stage

    assumptions = [
        {
            "statement": "seller demand is linear in the platform fee",
            "mathematical_form": {
                "expression": "a - p",
                "latex": r"a - p",
                "symbols_used": ["a", "p"],
            },
            "knowledge_basis": "modeling_assumption",
            "source_ids": [],
            "purpose": "tractability",
            "restrictiveness": "medium",
        },
        {
            "statement": "competition shifts platform pricing",
            "mathematical_form": {
                "expression": "p - c - m",
                "latex": r"p = c + m",
                "symbols_used": ["p", "c", "m"],
            },
            "knowledge_basis": "literature_supported",
            "source_ids": [stmt_id] + ([ev_id] if ev_id else []),
            "purpose": "grounding in reviewed literature",
            "restrictiveness": "low",
        },
    ]
    if lit_assumption_bad_source:
        assumptions[1]["source_ids"] = ["hallucinated-artifact-id"]
    if lit_assumption_no_source:
        assumptions[1]["source_ids"] = []

    spec = {
        "title": "Competition and platform pricing",
        "description": "A two-stage game of platform fee setting with seller entry.",
        "game_type": "dynamic private information",
        "actors": [
            {"actor_id": "platform", "name": "Platform", "role": "platform", "strategic": True},
            {"actor_id": "seller", "name": "Seller", "role": "seller", "strategic": True},
        ],
        "variables": variables,
        "parameters": [
            {"symbol": "a", "name": "demand intercept", "meaning": "market size", "domain": "R_+"},
            {"symbol": "m", "name": "markup", "meaning": "platform markup", "domain": "R_+"},
        ],
        "assumptions": assumptions,
        "timing": timing,
        "information_structure": {
            "items": info_items,
            "uncertainty": [
                {
                    "variable_symbol": "theta",
                    "distribution": "Normal(0, 1)",
                    "belief_note": "common prior",
                }
            ],
            "summary": "Demand state is public; the seller privately observes a signal.",
        },
        "payoffs": [
            {
                "actor_id": "platform",
                "objective_type": "profit",
                "expression": {
                    "expression": "p*q - c*q",
                    "latex": r"p q - c q",
                    "symbols_used": pay_sym_used,
                },
                "decision_variables": ["p"],
                "parameters": ["c"],
                "constraints": [],
            },
            {
                "actor_id": "seller",
                "objective_type": "profit",
                "expression": {
                    "expression": "q*(a - p) - q**2",
                    "latex": r"q(a - p) - q^2",
                    "symbols_used": ["q", "a", "p"],
                },
                "decision_variables": ["q"],
                "parameters": ["a"],
                "constraints": [],
            },
        ],
    }
    return spec


def _resp(spec: dict) -> str:
    return json.dumps(spec)


@pytest.mark.asyncio
async def test_valid_model_generation(tmp_path: pathlib.Path):
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ev_id = await _evidence(store, "pricing responds to competition")
    mech_id, stmt_id = await _mechanism_env(store, source_ids=[ev_id])
    router = FakeRouter([_resp(_model_spec(stmt_id=stmt_id, ev_id=ev_id))])
    svc = ModelBuilderService(model_router=router, artifact_store=store, model_role="reasoning")
    exec_id = await svc.build(mech_id)
    assert router.last_role == "reasoning"

    from research_harness.research.schemas.model import (
        FormalAnalyticalModel,
        ModelSpecificationExecution,
    )

    rec = (await store.get(exec_id)).parse_payload(ModelSpecificationExecution)
    assert rec.model_created is True
    assert rec.rejected is False

    models = [
        env.parse_payload(FormalAnalyticalModel)
        for env in await store.list(artifact_type="formal_analytical_model")
    ]
    assert len(models) == 1
    m = models[0]
    assert m.selected_mechanism_id == mech_id
    assert len(m.actors) == 2
    assert len(m.variables) == 5
    assert len(m.timing) == 5
    assert len(m.payoffs) == 2
    assert len(m.assumptions) == 2
    # symbol table complete: every expression symbol defined
    table = {v.symbol for v in m.variables} | {p.symbol for p in m.parameters}
    for p in m.payoffs:
        assert set(p.expression.symbols_used) <= table
    # decision ownership
    pvar = next(v for v in m.variables if v.symbol == "p")
    assert pvar.kind.value == "decision_variable"
    assert pvar.owner_actor_id == "platform"
    # information structure
    assert len(m.information_structure.items) == 2
    assert len(m.information_structure.uncertainty) == 1
    # assumption grounding preserved
    lit = next(a for a in m.assumptions if a.knowledge_basis.value == "literature_supported")
    assert lit.source_ids and stmt_id in lit.source_ids
    hyp = next((a for a in m.assumptions if a.knowledge_basis.value == "modeling_assumption"), None)
    assert hyp is not None


@pytest.mark.asyncio
async def test_undefined_symbol_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    mech_id, stmt_id = await _mechanism_env(store)
    spec = _model_spec(stmt_id=stmt_id)
    spec["payoffs"][0]["expression"]["symbols_used"] = ["p", "q", "c", "zeta"]
    router = FakeRouter([_resp(spec)])
    svc = ModelBuilderService(model_router=router, artifact_store=store)
    exec_id = await svc.build(mech_id)
    from research_harness.research.schemas.model import ModelSpecificationExecution

    rec = (await store.get(exec_id)).parse_payload(ModelSpecificationExecution)
    assert rec.rejected is True
    assert any("undefined symbols" in f["error"] for f in rec.failures)
    assert not (await store.list(artifact_type="formal_analytical_model"))


@pytest.mark.asyncio
async def test_expression_uses_symbol_not_listed_in_symbols_used(tmp_path: pathlib.Path):
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    mech_id, stmt_id = await _mechanism_env(store)
    spec = _model_spec(stmt_id=stmt_id)
    # expression uses theta but symbols_used omits it
    spec["payoffs"][0]["expression"]["expression"] = "p*q - c*q + theta"
    spec["payoffs"][0]["expression"]["symbols_used"] = ["p", "q", "c"]
    router = FakeRouter([_resp(spec)])
    svc = ModelBuilderService(model_router=router, artifact_store=store)
    exec_id = await svc.build(mech_id)
    from research_harness.research.schemas.model import ModelSpecificationExecution

    rec = (await store.get(exec_id)).parse_payload(ModelSpecificationExecution)
    assert rec.rejected is True
    assert any("not listed in" in f["error"] for f in rec.failures)


@pytest.mark.asyncio
async def test_duplicate_symbol_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    mech_id, stmt_id = await _mechanism_env(store)
    router = FakeRouter([_resp(_model_spec(stmt_id=stmt_id, duplicate=True))])
    svc = ModelBuilderService(model_router=router, artifact_store=store)
    exec_id = await svc.build(mech_id)
    from research_harness.research.schemas.model import ModelSpecificationExecution

    rec = (await store.get(exec_id)).parse_payload(ModelSpecificationExecution)
    assert rec.rejected is True
    assert any("duplicate symbol" in f["error"] for f in rec.failures)


@pytest.mark.asyncio
async def test_unknown_decision_owner_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    mech_id, stmt_id = await _mechanism_env(store)
    router = FakeRouter([_resp(_model_spec(stmt_id=stmt_id, unknown_owner="ghost"))])
    svc = ModelBuilderService(model_router=router, artifact_store=store)
    exec_id = await svc.build(mech_id)
    from research_harness.research.schemas.model import ModelSpecificationExecution

    rec = (await store.get(exec_id)).parse_payload(ModelSpecificationExecution)
    assert rec.rejected is True
    assert any("unknown actor" in f["error"] for f in rec.failures)


@pytest.mark.asyncio
async def test_payoff_decision_ownership_mismatch_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    mech_id, stmt_id = await _mechanism_env(store)
    spec = _model_spec(stmt_id=stmt_id)
    # seller payoff claims platform's decision variable
    spec["payoffs"][1]["decision_variables"] = ["p"]
    router = FakeRouter([_resp(spec)])
    svc = ModelBuilderService(model_router=router, artifact_store=store)
    exec_id = await svc.build(mech_id)
    from research_harness.research.schemas.model import ModelSpecificationExecution

    rec = (await store.get(exec_id)).parse_payload(ModelSpecificationExecution)
    assert rec.rejected is True
    assert any(
        "decision variables" in f["error"] and "not owned" in f["error"] for f in rec.failures
    )


@pytest.mark.asyncio
async def test_timing_inconsistencies_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderService

    for kw in ({"stage_dup": True}, {"stage_gap": True}, {"unknown_actor_stage": "ghost"}):
        store = SQLiteArtifactStore(path=tmp_path / f"art_{kw}.db")
        mech_id, stmt_id = await _mechanism_env(store)
        router = FakeRouter([_resp(_model_spec(stmt_id=stmt_id, **kw))])
        svc = ModelBuilderService(model_router=router, artifact_store=store)
        exec_id = await svc.build(mech_id)
        from research_harness.research.schemas.model import ModelSpecificationExecution

        rec = (await store.get(exec_id)).parse_payload(ModelSpecificationExecution)
        assert rec.rejected is True, kw
        assert rec.failures


@pytest.mark.asyncio
async def test_information_structure_violations_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderService

    for kw in (
        {"info_unknown_sym": "ghost"},
        {"info_bad_stage": 9},
        {"unc_not_random": True},
    ):
        store = SQLiteArtifactStore(path=tmp_path / f"art_{kw}.db")
        mech_id, stmt_id = await _mechanism_env(store)
        router = FakeRouter([_resp(_model_spec(stmt_id=stmt_id, **kw))])
        svc = ModelBuilderService(model_router=router, artifact_store=store)
        exec_id = await svc.build(mech_id)
        from research_harness.research.schemas.model import ModelSpecificationExecution

        rec = (await store.get(exec_id)).parse_payload(ModelSpecificationExecution)
        assert rec.rejected is True, kw
        assert rec.failures


@pytest.mark.asyncio
async def test_assumption_grounding_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderService

    for kw in ({"lit_assumption_bad_source": True}, {"lit_assumption_no_source": True}):
        store = SQLiteArtifactStore(path=tmp_path / f"art_{kw}.db")
        mech_id, stmt_id = await _mechanism_env(store)
        router = FakeRouter([_resp(_model_spec(stmt_id=stmt_id, **kw))])
        svc = ModelBuilderService(model_router=router, artifact_store=store)
        exec_id = await svc.build(mech_id)
        from research_harness.research.schemas.model import ModelSpecificationExecution

        rec = (await store.get(exec_id)).parse_payload(ModelSpecificationExecution)
        assert rec.rejected is True, kw
        assert rec.failures


@pytest.mark.asyncio
async def test_build_idempotent_and_role_change(tmp_path: pathlib.Path):
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    mech_id, stmt_id = await _mechanism_env(store)
    resp = _resp(_model_spec(stmt_id=stmt_id))
    router = FakeRouter([resp, resp])
    svc = ModelBuilderService(model_router=router, artifact_store=store, model_role="reasoning")
    first = await svc.build(mech_id)
    second = await svc.build(mech_id)
    assert first == second
    assert router.calls == 1
    assert len(await store.list(artifact_type="formal_analytical_model")) == 1

    svc2 = ModelBuilderService(model_router=router, artifact_store=store, model_role="long_context")
    third = await svc2.build(mech_id)
    assert third != first
    assert len(await store.list(artifact_type="formal_analytical_model")) == 2


@pytest.mark.asyncio
async def test_critique_output(tmp_path: pathlib.Path):
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderService
    from research_harness.plugins.research.model_specification_critic.plugin import (
        ModelSpecificationCriticService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    mech_id, stmt_id = await _mechanism_env(store)
    builder = ModelBuilderService(
        model_router=FakeRouter([_resp(_model_spec(stmt_id=stmt_id))]), artifact_store=store
    )
    await builder.build(mech_id)
    model_id = (await store.list(artifact_type="formal_analytical_model"))[0].artifact_id

    crit_resp = json.dumps(
        {
            "overall_assessment": "The timing is sound but the information assumption is impossible.",
            "verdict": "revise",
            "revision_recommendations": ["Make the signal public", "Drop the markup parameter"],
            "issues": [
                {
                    "category": "impossible_information",
                    "description": "Seller cannot observe theta before it is realized.",
                    "severity": "high",
                    "location": "information_structure",
                },
                {
                    "category": "redundant_assumption",
                    "description": "Markup parameter duplicates the fee.",
                    "severity": "low",
                    "location": "parameters",
                },
            ],
        }
    )
    router = FakeRouter([crit_resp])
    svc = ModelSpecificationCriticService(
        model_router=router, artifact_store=store, builder=builder, critic_role="critic"
    )
    crit_id = await svc.critique(model_id)
    assert router.last_role == "critic"
    from research_harness.research.schemas.model import ModelSpecificationCritique

    crit = (await store.get(crit_id)).parse_payload(ModelSpecificationCritique)
    assert crit.verdict == "revise"
    assert crit.issues[0].category.value == "impossible_information"
    assert crit.model_role == "critic"

    # idempotent
    assert await svc.critique(model_id) == crit_id
    assert router.calls == 1

    # critic-role change -> new critique
    svc2 = ModelSpecificationCriticService(
        model_router=router, artifact_store=store, builder=builder, critic_role="fast"
    )
    crit2 = await svc2.critique(model_id)
    assert crit2 != crit_id


@pytest.mark.asyncio
async def test_immutable_revision(tmp_path: pathlib.Path):
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderService
    from research_harness.plugins.research.model_specification_critic.plugin import (
        ModelSpecificationCriticService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    mech_id, stmt_id = await _mechanism_env(store)
    builder = ModelBuilderService(
        model_router=FakeRouter([_resp(_model_spec(stmt_id=stmt_id))]), artifact_store=store
    )
    await builder.build(mech_id)
    model_id = (await store.list(artifact_type="formal_analytical_model"))[0].artifact_id
    from research_harness.research.schemas.model import FormalAnalyticalModel

    v1 = (await store.get(model_id)).parse_payload(FormalAnalyticalModel)
    assert v1.status.value == "draft"

    crit_resp = json.dumps(
        {
            "overall_assessment": "Poor tractability of the seller payoff.",
            "verdict": "revise",
            "revision_recommendations": ["Use linear seller payoff"],
            "issues": [
                {
                    "category": "poor_tractability",
                    "description": "Quadratic term harms closed form.",
                    "severity": "medium",
                    "location": "payoffs",
                }
            ],
        }
    )
    rev_spec = _model_spec(stmt_id=stmt_id)
    rev_spec["title"] = "Competition and platform pricing (revised)"
    rev_spec["revision_notes"] = ["Made seller payoff linear per critique"]
    rev_spec["payoffs"][1]["expression"] = {
        "expression": "q*(a - p)",
        "latex": r"q(a - p)",
        "symbols_used": ["q", "a", "p"],
    }
    rev_spec["payoffs"][1]["constraints"] = []
    router = FakeRouter([crit_resp, _resp(rev_spec)])
    svc = ModelSpecificationCriticService(
        model_router=router, artifact_store=store, builder=builder, critic_role="critic"
    )
    v2_id = await svc.revise(model_id)
    assert v2_id != model_id

    from research_harness.research.schemas.model import FormalAnalyticalModel

    v2 = (await store.get(v2_id)).parse_payload(FormalAnalyticalModel)
    assert v2.status.value == "revised"
    assert "revised" in v2.title
    assert v2.revision_notes
    assert v2.selected_mechanism_id == mech_id

    # V1 untouched
    v1_now = (await store.get(model_id)).parse_payload(FormalAnalyticalModel)
    assert v1_now.title == v1.title
    assert v1_now.status.value == "draft"

    # supersedes + derived_from links
    children = await store.get_children(model_id)
    assert any(c.target_artifact_id == v2_id and c.relation.value == "supersedes" for c in children)
    assert any(
        c.target_artifact_id == v2_id and c.relation.value == "derived_from" for c in children
    )

    # idempotent revision
    assert await svc.revise(model_id) == v2_id


@pytest.mark.asyncio
async def test_revision_invalid_output_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderService
    from research_harness.plugins.research.model_specification_critic.plugin import (
        ModelSpecificationCriticService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    mech_id, stmt_id = await _mechanism_env(store)
    builder = ModelBuilderService(
        model_router=FakeRouter([_resp(_model_spec(stmt_id=stmt_id))]), artifact_store=store
    )
    await builder.build(mech_id)
    model_id = (await store.list(artifact_type="formal_analytical_model"))[0].artifact_id

    crit_resp = json.dumps(
        {
            "overall_assessment": "ok",
            "verdict": "keep",
            "issues": [],
            "revision_recommendations": [],
        }
    )
    bad_rev = _model_spec(stmt_id=stmt_id)
    bad_rev["payoffs"][0]["expression"]["symbols_used"] = ["p", "q", "c", "zeta"]
    router = FakeRouter([crit_resp, _resp(bad_rev)])
    svc = ModelSpecificationCriticService(
        model_router=router, artifact_store=store, builder=builder, critic_role="critic"
    )
    with pytest.raises(ValueError, match="structural validation"):
        await svc.revise(model_id)
    # no V2 persisted
    assert len(await store.list(artifact_type="formal_analytical_model")) == 1


@pytest.mark.asyncio
async def test_provenance_after_reopen(tmp_path: pathlib.Path):
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderService
    from research_harness.research.schemas.model import FormalAnalyticalModel

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ev_id = await _evidence(store, "pricing responds to competition")
    mech_id, stmt_id = await _mechanism_env(store, source_ids=[ev_id])
    builder = ModelBuilderService(
        model_router=FakeRouter([_resp(_model_spec(stmt_id=stmt_id, ev_id=ev_id))]),
        artifact_store=store,
    )
    await builder.build(mech_id)
    model_id = (await store.list(artifact_type="formal_analytical_model"))[0].artifact_id

    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")

    parents = await store2.get_parents(model_id)
    assert any(p.source_artifact_id == mech_id for p in parents)
    # literature-backed assumption retains individual grounding edge
    assert any(p.source_artifact_id == ev_id for p in parents)
    assert any(p.source_artifact_id == stmt_id for p in parents)
    m = (await store2.get(model_id)).parse_payload(FormalAnalyticalModel)
    lit = next(a for a in m.assumptions if a.knowledge_basis.value == "literature_supported")
    assert ev_id in lit.source_ids
    await store2.close()
