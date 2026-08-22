"""Phase 3B offline integration — full chain with fake models, no network:

SelectedMechanism
  -> model specification (structured, validated)
  -> independent critique
  -> revised FormalAnalyticalModel V2 (supersedes V1)
Provenance verified after SQLite reopen.
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
    def __init__(self, by_role: dict[str, list[str]]):
        self.by_role = {k: list(v) for k, v in by_role.items()}
        self.calls: list[str] = []

    async def complete(self, role, request):
        self.calls.append(role)
        pool = self.by_role.get(role) or [""]
        idx = min(self.calls.count(role) - 1, len(pool) - 1)
        return ModelResponse(
            message=Message(role="assistant", content=pool[idx]),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


def _model_spec(revised: bool = False) -> dict:
    spec = {
        "title": "Competition and platform pricing" + (" (revised)" if revised else ""),
        "description": "A dynamic game of platform fee setting with private seller signals.",
        "game_type": "dynamic private information",
        "actors": [
            {"actor_id": "platform", "name": "Platform", "role": "platform", "strategic": True},
            {"actor_id": "seller", "name": "Seller", "role": "seller", "strategic": True},
        ],
        "variables": [
            {
                "symbol": "p",
                "name": "platform fee",
                "meaning": "fee the platform charges sellers",
                "domain": "R_+",
                "kind": "decision_variable",
                "owner_actor_id": "platform",
            },
            {
                "symbol": "q",
                "name": "seller quantity",
                "meaning": "quantity the seller brings to market",
                "domain": "R_+",
                "kind": "decision_variable",
                "owner_actor_id": "seller",
            },
            {
                "symbol": "c",
                "name": "marginal cost",
                "meaning": "platform marginal cost",
                "domain": "R_+",
                "kind": "parameter",
            },
            {
                "symbol": "theta",
                "name": "demand state",
                "meaning": "exogenous demand shock",
                "domain": "R",
                "kind": "random_variable",
            },
            {
                "symbol": "s",
                "name": "seller signal",
                "meaning": "private signal of the demand state",
                "domain": "R",
                "kind": "observable_signal",
            },
        ],
        "parameters": [
            {"symbol": "a", "name": "demand intercept", "meaning": "market size", "domain": "R_+"},
            {"symbol": "m", "name": "markup", "meaning": "platform markup", "domain": "R_+"},
        ],
        "assumptions": [
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
                    "latex": r"p - c - m",
                    "symbols_used": ["p", "c", "m"],
                },
                "knowledge_basis": "literature_supported",
                "source_ids": ["__STMT__", "__EV__"],
                "purpose": "grounding in the reviewed literature",
                "restrictiveness": "low",
            },
        ],
        "timing": [
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
        ],
        "information_structure": {
            "items": [
                {
                    "actor_id": "seller",
                    "variable_symbols": ["s"],
                    "available_at_stage": 2,
                    "visibility": "private",
                    "description": "seller privately observes the signal",
                },
                {
                    "actor_id": "platform",
                    "variable_symbols": ["theta"],
                    "available_at_stage": 0,
                    "visibility": "public",
                    "description": "platform observes the demand state",
                },
            ],
            "uncertainty": [
                {
                    "variable_symbol": "theta",
                    "distribution": "Normal(0, 1)",
                    "belief_note": "common prior",
                }
            ],
            "summary": "demand state public; seller privately informed via signal",
        },
        "payoffs": [
            {
                "actor_id": "platform",
                "objective_type": "profit",
                "expression": {
                    "expression": "p*q - c*q",
                    "latex": r"p q - c q",
                    "symbols_used": ["p", "q", "c"],
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
    if revised:
        spec["revision_notes"] = ["seller payoff made linear; signal kept private per critique"]
        spec["payoffs"][1]["expression"] = {
            "expression": "q*(a - p)",
            "latex": r"q(a - p)",
            "symbols_used": ["q", "a", "p"],
        }
    return spec


@pytest.mark.asyncio
async def test_phase3b_full_chain(tmp_path: pathlib.Path):
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderService
    from research_harness.plugins.research.model_specification_critic.plugin import (
        ModelSpecificationCriticService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")

    # ---- Phase 3A inputs: evidence -> statement -> SelectedMechanism ---------
    ev = EvidenceItem(
        statement="pricing responds to competition",
        source_artifact_id="doc1",
        category=EvidenceCategory.finding,
        locator=Locator(page=3, pages=[3]),
        extraction_method="model-assisted",
        confidence=0.9,
    )
    ev_env = ArtifactEnvelope.create(payload=ev, artifact_type="evidence_item", producer="test")
    await store.put(ev_env)

    stmt = SynthesisStatement(
        statement="Competition raises seller entry, which changes platform pricing.",
        type=SynthesisStatementType.consensus,
        supporting_evidence_ids=[ev_env.artifact_id],
        supporting_paper_identity_ids=["pi1"],
        papers_supporting=1,
        evidence_items_supporting=1,
        support_type=SupportType.single_paper,
        confidence=0.8,
    )
    st_env = ArtifactEnvelope.create(
        payload=stmt, artifact_type="synthesis_statement", producer="test"
    )
    await store.put(st_env)

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
        grounding=[
            GroundingElement(
                element="platform pricing responds to competition",
                basis=KnowledgeBasis.literature_supported,
                source_ids=[st_env.artifact_id, ev_env.artifact_id],
            ),
            GroundingElement(
                element="seller entry mediates the effect",
                basis=KnowledgeBasis.new_hypothesis,
                source_ids=[],
            ),
        ],
        evaluation=MechanismEvaluation(
            gap_alignment=0.9,
            theoretical_coherence=0.8,
            novelty_within_reviewed_corpus=0.7,
            analytical_tractability=0.8,
            managerial_economic_relevance=0.7,
            is_relevance=0.9,
        ),
    )
    sm_env = ArtifactEnvelope.create(
        payload=sm, artifact_type="selected_mechanism", producer="test"
    )
    await store.put(sm_env)
    mech_id = sm_env.artifact_id

    # ---- 1. Model build ------------------------------------------------------
    v1_spec = _model_spec()
    v1_spec["assumptions"][1]["source_ids"] = [st_env.artifact_id, ev_env.artifact_id]
    v1_resp = json.dumps(v1_spec)

    builder = ModelBuilderService(
        model_router=FakeRouter({"reasoning": [v1_resp]}), artifact_store=store
    )
    exec_id = await builder.build(mech_id)
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
    v1 = models[0]
    assert v1.title == "Competition and platform pricing"
    assert v1.status.value == "draft"
    assert len(v1.actors) == 2
    assert len(v1.variables) == 5
    assert len(v1.timing) == 5
    assert len(v1.payoffs) == 2
    assert v1.information_structure.items[0].actor_id == "seller"
    assert v1.information_structure.items[0].visibility.value == "private"
    lit = next(a for a in v1.assumptions if a.knowledge_basis.value == "literature_supported")
    assert ev_env.artifact_id in lit.source_ids

    # ---- 2. Critique ----------------------------------------------------------
    crit_resp = json.dumps(
        {
            "overall_assessment": "Tractable but the seller payoff and timing warrant a revision.",
            "verdict": "revise",
            "revision_recommendations": ["Simplify seller payoff to a linear form"],
            "issues": [
                {
                    "category": "poor_tractability",
                    "description": "Quadratic seller payoff harms closed-form solutions.",
                    "severity": "medium",
                    "location": "payoffs",
                },
                {
                    "category": "mechanism_model_mismatch",
                    "description": "Timing omits an explicit entry stage from the mechanism.",
                    "severity": "medium",
                    "location": "timing",
                },
            ],
        }
    )
    v2_spec = _model_spec(revised=True)
    v2_spec["assumptions"][1]["source_ids"] = [st_env.artifact_id, ev_env.artifact_id]
    v2_resp = json.dumps(v2_spec)

    critic = ModelSpecificationCriticService(
        model_router=FakeRouter({"critic": [crit_resp], "reasoning": [v2_resp]}),
        artifact_store=store,
        builder=builder,
    )
    crit_id = await critic.critique(
        (await store.list(artifact_type="formal_analytical_model"))[0].artifact_id
    )
    from research_harness.research.schemas.model import ModelSpecificationCritique

    crit = (await store.get(crit_id)).parse_payload(ModelSpecificationCritique)
    assert crit.verdict == "revise"
    assert crit.issues[0].category.value == "poor_tractability"

    # ---- 3. Revision (V1 -> V2) ----------------------------------------------
    model_id = (await store.list(artifact_type="formal_analytical_model"))[0].artifact_id
    v2_id = await critic.revise(model_id)
    assert v2_id != model_id

    v2 = (await store.get(v2_id)).parse_payload(FormalAnalyticalModel)
    assert v2.status.value == "revised"
    assert v2.title == "Competition and platform pricing (revised)"
    assert v2.revision_notes
    assert "q*(a - p)" in v2.payoffs[1].expression.expression
    assert v2.selected_mechanism_id == mech_id

    # V1 never mutated
    v1_now = (await store.get(model_id)).parse_payload(FormalAnalyticalModel)
    assert v1_now.status.value == "draft"
    assert "q**2" in v1_now.payoffs[1].expression.expression

    # ---- 4. Provenance after reopen -------------------------------------------
    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")

    v2_parents = await store2.get_parents(v2_id)
    assert any(p.source_artifact_id == model_id for p in v2_parents)
    assert any(p.source_artifact_id == crit_id for p in v2_parents)
    children = await store2.get_children(model_id)
    assert any(c.target_artifact_id == v2_id and c.relation.value == "supersedes" for c in children)

    v1_parents = await store2.get_parents(model_id)
    assert any(p.source_artifact_id == mech_id for p in v1_parents)
    # literature-backed assumption grounding edges reach Phase 2 evidence
    assert any(p.source_artifact_id == ev_env.artifact_id for p in v1_parents)
    assert any(p.source_artifact_id == st_env.artifact_id for p in v1_parents)

    # full chain: model -> selected mechanism -> ... -> evidence
    v2m = (await store2.get(v2_id)).parse_payload(FormalAnalyticalModel)
    assert v2m.selected_mechanism_id == mech_id
    mech = (await store2.get(mech_id)).parse_payload(SelectedMechanism)
    assert any(g.source_ids and ev_env.artifact_id in g.source_ids for g in mech.grounding)
    await store2.close()
