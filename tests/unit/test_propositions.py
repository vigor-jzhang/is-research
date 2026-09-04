"""Phase 3D unit tests — comparative statics, proposition verification,
critique, interpretation. Deterministic closed-form examples. Fake models
only, offline.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.equilibrium import (
    EquilibriumAnalysis,
    EquilibriumAnalysisStatus,
    EquilibriumCandidate,
    EquilibriumExpression,
    VerificationStatus,
)
from research_harness.research.schemas.model import (
    Expression,
    FormalAnalyticalModel,
    InformationStructure,
    ModelActor,
    ModelParameter,
    ModelTimingStage,
    ModelVariable,
    PayoffFunction,
    SymbolKind,
)
from research_harness.research.schemas.proposition import (
    ComparativeStatic,
    ComparativeStaticsAnalysis,
    ComparativeStaticsExecution,
    EconomicInterpretation,
    Proposition,
    PropositionCritique,
    PropositionVerification,
    PropositionVerificationStatus,
    StaticSign,
)


class FakeRouter:
    def __init__(self, responses: list[str] | None = None):
        self.responses = responses or []
        self.calls = 0
        self.last_role = None

    async def complete(self, role, request):
        self.calls += 1
        self.last_role = role
        idx = min(self.calls - 1, len(self.responses) - 1)
        content = self.responses[idx] if self.responses else "{}"
        return ModelResponse(
            message=Message(role="assistant", content=content),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


async def _cournot_scenario(store, with_b: bool = False) -> dict[str, str]:
    """Verified Cournot equilibrium q* = (a-c)/3 on two outcomes (or monopoly)."""
    model = FormalAnalyticalModel(
        selected_mechanism_id="mech1",
        title="Cournot platforms",
        description="simultaneous quantities",
        actors=[
            ModelActor(actor_id="f1", name="Firm 1", strategic=True),
            ModelActor(actor_id="f2", name="Firm 2", strategic=True),
        ],
        variables=[
            ModelVariable(
                symbol="q1",
                name="q1",
                meaning="q1",
                domain="R_+",
                kind=SymbolKind.decision_variable,
                owner_actor_id="f1",
            ),
            ModelVariable(
                symbol="q2",
                name="q2",
                meaning="q2",
                domain="R_+",
                kind=SymbolKind.decision_variable,
                owner_actor_id="f2",
            ),
        ],
        parameters=[
            ModelParameter(symbol="a", name="demand", meaning="demand", domain="R_+"),
            ModelParameter(symbol="c", name="cost", meaning="cost", domain="R_+"),
            ModelParameter(symbol="b", name="unused", meaning="unused parameter", domain="R_+"),
        ],
        assumptions=[],
        timing=[
            ModelTimingStage(
                stage_number=0, name="move", description="both choose", actor_ids=["f1", "f2"]
            )
        ],
        information_structure=InformationStructure(),
        payoffs=[
            PayoffFunction(
                actor_id="f1",
                objective_type="profit",
                expression=Expression(expression="q1*(a - q1 - q2) - c*q1", symbols_used=[]),
                decision_variables=["q1"],
                parameters=["a", "c"],
            ),
            PayoffFunction(
                actor_id="f2",
                objective_type="profit",
                expression=Expression(expression="q2*(a - q1 - q2) - c*q2", symbols_used=[]),
                decision_variables=["q2"],
                parameters=["a", "c"],
            ),
        ],
    )
    m_env = ArtifactEnvelope.create(
        payload=model, artifact_type="formal_analytical_model", producer="test"
    )
    await store.put(m_env)

    cand = EquilibriumCandidate(
        model_id=m_env.artifact_id,
        expressions=[
            EquilibriumExpression(
                variable="q1",
                expression=Expression(expression="(a - c)/3", symbols_used=["a", "c"]),
            ),
            EquilibriumExpression(
                variable="q2",
                expression=Expression(expression="(a - c)/3", symbols_used=["a", "c"]),
            ),
        ],
        decision_variables=["q1", "q2"],
        solution_method="simultaneous",
        proposed_by="sympy",
        verification_status=VerificationStatus.verified,
    )
    c_env = ArtifactEnvelope.create(
        payload=cand, artifact_type="equilibrium_candidate", producer="test"
    )
    await store.put(c_env)
    await store.add_provenance(
        ProvenanceLink(
            relation=ProvenanceRelation.derived_from,
            source_artifact_id=m_env.artifact_id,
            target_artifact_id=c_env.artifact_id,
            producer="test",
        )
    )

    analysis = EquilibriumAnalysis(
        model_id=m_env.artifact_id,
        candidate_ids=[c_env.artifact_id],
        selected_candidate_id=c_env.artifact_id,
        status=EquilibriumAnalysisStatus.derived,
    )
    a_env = ArtifactEnvelope.create(
        payload=analysis, artifact_type="equilibrium_analysis", producer="test"
    )
    await store.put(a_env)
    return {
        "model": m_env.artifact_id,
        "candidate": c_env.artifact_id,
        "analysis": a_env.artifact_id,
    }


async def _run_statics(store, analysis_id: str) -> tuple[str, str]:
    from research_harness.plugins.research.comparative_statics.plugin import (
        ComparativeStaticsService,
    )

    svc = ComparativeStaticsService(artifact_store=store)
    exec_id = await svc.run(analysis_id)
    cs_id = await svc.resolve_analysis(exec_id)
    return exec_id, cs_id


async def _static_of(store, cs_id, outcome, param) -> ComparativeStatic:
    a = (await store.get(cs_id)).parse_payload(ComparativeStaticsAnalysis)
    for sid in a.static_ids:
        s = (await store.get(sid)).parse_payload(ComparativeStatic)
        if s.outcome_variable == outcome and s.parameter == param:
            return s
    raise AssertionError(f"no static for d{outcome}/d{param}")


def _prop_resp(*props: dict) -> str:
    return json.dumps({"propositions": list(props)})


def _monotonicity(
    statement: str, outcome: str, param: str, sign: str, static_id: str, conditions=None
) -> dict:
    return {
        "statement": statement,
        "claim_type": "monotonicity",
        "outcome_variable": outcome,
        "parameter": param,
        "expected_sign": sign,
        "conditions": conditions or [],
        "supporting_static_ids": [static_id],
    }


# ---------------------------------------------------------------------------
# Comparative statics (deterministic)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_positive_negative_zero_statics(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _cournot_scenario(store)
    exec_id, cs_id = await _run_statics(store, ids["analysis"])

    rec = (await store.get(exec_id)).parse_payload(ComparativeStaticsExecution)
    assert rec.statics_created == 6  # 2 outcomes x 3 parameters

    dq_a = await _static_of(store, cs_id, "q1", "a")
    assert dq_a.sign == StaticSign.positive
    assert dq_a.derivative_expression.expression == "1/3"
    dq_c = await _static_of(store, cs_id, "q1", "c")
    assert dq_c.sign == StaticSign.negative
    assert dq_c.derivative_expression.expression == "-1/3"
    dq_b = await _static_of(store, cs_id, "q1", "b")
    assert dq_b.sign == StaticSign.zero

    # provenance: static derived_from candidate
    static_env = (await store.list(artifact_type="comparative_static"))[0]
    parents = await store.get_parents(static_env.artifact_id)
    assert any(p.source_artifact_id == ids["candidate"] for p in parents)


@pytest.mark.asyncio
async def test_ambiguous_sign_with_conditions(tmp_path: pathlib.Path):
    """p* = (ab + c)/(2b): dp/db = -c/(2b^2), negative once a, b, c are in R_+.

    Before H8 the declared domains were never turned into SymPy assumptions, so
    this sign could not be proven and came back `ambiguous` with conditions.
    """
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    model = FormalAnalyticalModel(
        selected_mechanism_id="mech1",
        title="Monopoly with slope",
        description="monopoly",
        actors=[ModelActor(actor_id="platform", name="P", strategic=True)],
        variables=[
            ModelVariable(
                symbol="p",
                name="p",
                meaning="p",
                domain="R_+",
                kind=SymbolKind.decision_variable,
                owner_actor_id="platform",
            )
        ],
        parameters=[
            ModelParameter(symbol="a", name="a", meaning="demand", domain="R_+"),
            ModelParameter(symbol="c", name="c", meaning="cost", domain="R_+"),
            ModelParameter(symbol="b", name="b", meaning="slope", domain="R_+"),
        ],
        assumptions=[],
        timing=[
            ModelTimingStage(
                stage_number=0, name="move", description="choose p", actor_ids=["platform"]
            )
        ],
        information_structure=InformationStructure(),
        payoffs=[
            PayoffFunction(
                actor_id="platform",
                objective_type="profit",
                expression=Expression(expression="b*p*(a - p) - c*(a - p)", symbols_used=[]),
                decision_variables=["p"],
                parameters=["a", "c", "b"],
            )
        ],
    )
    m_env = ArtifactEnvelope.create(
        payload=model, artifact_type="formal_analytical_model", producer="test"
    )
    await store.put(m_env)
    cand = EquilibriumCandidate(
        model_id=m_env.artifact_id,
        expressions=[
            EquilibriumExpression(
                variable="p",
                expression=Expression(expression="(a*b + c)/(2*b)", symbols_used=["a", "b", "c"]),
            )
        ],
        decision_variables=["p"],
        solution_method="simultaneous",
        proposed_by="sympy",
        verification_status=VerificationStatus.verified,
    )
    c_env = ArtifactEnvelope.create(
        payload=cand, artifact_type="equilibrium_candidate", producer="test"
    )
    await store.put(c_env)
    a_env = ArtifactEnvelope.create(
        payload=EquilibriumAnalysis(
            model_id=m_env.artifact_id,
            candidate_ids=[c_env.artifact_id],
            selected_candidate_id=c_env.artifact_id,
            status=EquilibriumAnalysisStatus.derived,
        ),
        artifact_type="equilibrium_analysis",
        producer="test",
    )
    await store.put(a_env)

    exec_id, cs_id = await _run_statics(store, a_env.artifact_id)
    dp_b = await _static_of(store, cs_id, "p", "b")
    assert dp_b.sign == StaticSign.negative
    # Provable from the declared positive domains, so no conditions recorded.
    assert not dp_b.conditions

    # idempotent rerun
    exec2, cs2 = await _run_statics(store, a_env.artifact_id)
    assert exec2 == exec_id
    assert cs2 == cs_id


# ---------------------------------------------------------------------------
# Proposition verification (deterministic)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_proposition_verified_positive(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _cournot_scenario(store)
    _, cs_id = await _run_statics(store, ids["analysis"])
    static = await _static_of(store, cs_id, "q1", "a")

    from research_harness.plugins.research.proposition_verifier.plugin import (
        PropositionVerifierService,
    )

    prop = Proposition(
        model_id=ids["model"],
        equilibrium_candidate_id=ids["candidate"],
        comparative_statics_analysis_id=cs_id,
        statement="Increasing demand a raises platform 1's equilibrium quantity.",
        claim_type="monotonicity",
        outcome_variable="q1",
        parameter="a",
        expected_sign="positive",
        supporting_static_ids=[await _static_id(store, static)],
        proposed_by="llm",
    )
    prop_id = await _persist_prop(store, prop)

    verifier = PropositionVerifierService(artifact_store=store)
    v_id = await verifier.verify(prop_id)
    v = (await store.get(v_id)).parse_payload(PropositionVerification)
    assert v.status == PropositionVerificationStatus.verified
    assert all(chk.passed for chk in v.checks)


async def _static_id(store, static: ComparativeStatic) -> str:
    for env in await store.list(artifact_type="comparative_static"):
        s = env.parse_payload(ComparativeStatic)
        if (
            s.outcome_variable == static.outcome_variable
            and s.parameter == static.parameter
            and s.derivative_expression.expression == static.derivative_expression.expression
        ):
            return env.artifact_id
    raise AssertionError("static not found")


async def _persist_prop(store, prop: Proposition) -> str:
    env = ArtifactEnvelope.create(payload=prop, artifact_type="proposition", producer="test")
    await store.put(env)
    return env.artifact_id


@pytest.mark.asyncio
async def test_proposition_incorrect_sign_rejected(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _cournot_scenario(store)
    _, cs_id = await _run_statics(store, ids["analysis"])
    static = await _static_of(store, cs_id, "q1", "a")  # positive
    prop = Proposition(
        model_id=ids["model"],
        equilibrium_candidate_id=ids["candidate"],
        comparative_statics_analysis_id=cs_id,
        statement="Demand a lowers platform 1's quantity.",
        claim_type="monotonicity",
        outcome_variable="q1",
        parameter="a",
        expected_sign="negative",
        supporting_static_ids=[await _static_id(store, static)],
        proposed_by="llm",
    )
    prop_id = await _persist_prop(store, prop)
    from research_harness.plugins.research.proposition_verifier.plugin import (
        PropositionVerifierService,
    )

    v = (
        await store.get(await PropositionVerifierService(artifact_store=store).verify(prop_id))
    ).parse_payload(PropositionVerification)
    assert v.status == PropositionVerificationStatus.failed
    dsign = [c for c in v.checks if c.check_type.value == "derivative_sign"]
    assert dsign and not dsign[0].passed


@pytest.mark.asyncio
async def test_proposition_missing_condition_rejected(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    # ambiguous static: dp/db = -c/(2b^2)
    model = FormalAnalyticalModel(
        selected_mechanism_id="mech1",
        title="M",
        description="m",
        actors=[ModelActor(actor_id="p", name="P", strategic=True)],
        variables=[
            ModelVariable(
                symbol="p",
                name="p",
                meaning="p",
                domain="R_+",
                kind=SymbolKind.decision_variable,
                owner_actor_id="p",
            )
        ],
        parameters=[
            ModelParameter(symbol="a", name="a", meaning="a", domain="R_+"),
            ModelParameter(symbol="c", name="c", meaning="c", domain="R"),
            ModelParameter(symbol="b", name="b", meaning="b", domain="R_+"),
        ],
        assumptions=[],
        timing=[ModelTimingStage(stage_number=0, name="move", description="m", actor_ids=["p"])],
        information_structure=InformationStructure(),
        payoffs=[
            PayoffFunction(
                actor_id="p",
                objective_type="profit",
                expression=Expression(expression="b*p*(a - p) - c*(a - p)", symbols_used=[]),
                decision_variables=["p"],
                parameters=["a", "c", "b"],
            )
        ],
    )
    m_env = ArtifactEnvelope.create(
        payload=model, artifact_type="formal_analytical_model", producer="test"
    )
    await store.put(m_env)
    cand = EquilibriumCandidate(
        model_id=m_env.artifact_id,
        expressions=[
            EquilibriumExpression(
                variable="p",
                expression=Expression(expression="(a*b + c)/(2*b)", symbols_used=["a", "b", "c"]),
            )
        ],
        decision_variables=["p"],
        solution_method="simultaneous",
        proposed_by="sympy",
        verification_status=VerificationStatus.verified,
    )
    c_env = ArtifactEnvelope.create(
        payload=cand, artifact_type="equilibrium_candidate", producer="test"
    )
    await store.put(c_env)
    a_env = ArtifactEnvelope.create(
        payload=EquilibriumAnalysis(
            model_id=m_env.artifact_id,
            candidate_ids=[c_env.artifact_id],
            selected_candidate_id=c_env.artifact_id,
            status=EquilibriumAnalysisStatus.derived,
        ),
        artifact_type="equilibrium_analysis",
        producer="test",
    )
    await store.put(a_env)
    _, cs_id = await _run_statics(store, a_env.artifact_id)
    static = await _static_of(store, cs_id, "p", "b")
    assert static.sign == StaticSign.ambiguous

    from research_harness.plugins.research.proposition_verifier.plugin import (
        PropositionVerifierService,
    )

    # no conditions declared -> failed
    prop = Proposition(
        model_id=m_env.artifact_id,
        equilibrium_candidate_id=c_env.artifact_id,
        comparative_statics_analysis_id=cs_id,
        statement="b lowers p.",
        claim_type="monotonicity",
        outcome_variable="p",
        parameter="b",
        expected_sign="negative",
        supporting_static_ids=[await _static_id(store, static)],
        proposed_by="llm",
    )
    prop_id = await _persist_prop(store, prop)
    v = (
        await store.get(await PropositionVerifierService(artifact_store=store).verify(prop_id))
    ).parse_payload(PropositionVerification)
    assert v.status == PropositionVerificationStatus.failed
    assert any(c.check_type.value == "condition_requirement" and not c.passed for c in v.checks)

    # with explicit conditions -> conditionally verified
    prop2 = prop.model_copy(
        update={
            "statement": "b lowers p when c > 0.",
            "conditions": ["c > 0"],
        }
    )
    prop2_id = await _persist_prop(store, prop2)
    v2 = (
        await store.get(await PropositionVerifierService(artifact_store=store).verify(prop2_id))
    ).parse_payload(PropositionVerification)
    assert v2.status == PropositionVerificationStatus.conditionally_verified
    assert v2.conditions_required == ["c > 0"]


@pytest.mark.asyncio
async def test_proposition_equality_claim(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _cournot_scenario(store)
    _, cs_id = await _run_statics(store, ids["analysis"])
    from research_harness.plugins.research.proposition_verifier.plugin import (
        PropositionVerifierService,
    )

    prop = Proposition(
        model_id=ids["model"],
        equilibrium_candidate_id=ids["candidate"],
        comparative_statics_analysis_id=cs_id,
        statement="Symmetric equilibrium: both platforms choose the same quantity.",
        claim_type="equality",
        mathematical_form=Expression(expression="q1 = q2", symbols_used=["q1", "q2"]),
        supporting_static_ids=[],
        proposed_by="llm",
    )
    prop_id = await _persist_prop(store, prop)
    v = (
        await store.get(await PropositionVerifierService(artifact_store=store).verify(prop_id))
    ).parse_payload(PropositionVerification)
    assert v.status == PropositionVerificationStatus.verified
    eq = [c for c in v.checks if c.check_type.value == "algebraic_relation"]
    assert eq and eq[0].passed


@pytest.mark.asyncio
async def test_proposition_equilibrium_consistency(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _cournot_scenario(store)
    _, cs_id = await _run_statics(store, ids["analysis"])
    from research_harness.plugins.research.proposition_verifier.plugin import (
        PropositionVerifierService,
    )

    prop = Proposition(
        model_id=ids["model"],
        equilibrium_candidate_id=ids["candidate"],
        comparative_statics_analysis_id=cs_id,
        statement="demand raises q1.",
        claim_type="monotonicity",
        outcome_variable="q1",
        parameter="a",
        expected_sign="positive",
        supporting_static_ids=["hallucinated-static-id"],
        proposed_by="llm",
    )
    prop_id = await _persist_prop(store, prop)
    v = (
        await store.get(await PropositionVerifierService(artifact_store=store).verify(prop_id))
    ).parse_payload(PropositionVerification)
    assert v.status == PropositionVerificationStatus.failed
    cons = [c for c in v.checks if c.check_type.value == "equilibrium_consistency"]
    assert cons and not cons[0].passed


# ---------------------------------------------------------------------------
# Generation + critique + interpretation
# ---------------------------------------------------------------------------


def _critique_resp() -> str:
    return json.dumps(
        {
            "overall_assessment": "Sound but slightly overclaims; conditions must stay explicit.",
            "verdict": "revise",
            "recommendations": ["keep the conditions explicit"],
            "issues": [
                {
                    "category": "overclaiming",
                    "description": "The statement drops the required conditions.",
                    "severity": "high",
                    "location": "statement",
                },
                {
                    "category": "weak_is_relevance",
                    "description": "IS relevance is thin.",
                    "severity": "low",
                    "location": "statement",
                },
            ],
        }
    )


def _interpretation_resp() -> str:
    return json.dumps(
        {
            "mathematical_result": "dq1/da = 1/3 > 0 at the verified equilibrium.",
            "economic_interpretation": "Larger markets raise each platform's equilibrium output.",
            "managerial_implication": "Platforms should scale up in growing markets.",
            "is_theoretical_implication": "Market size is a key driver of platform scale.",
            "consistency_note": "Restated within the verified sign; no additional claims.",
        }
    )


@pytest.mark.asyncio
async def test_generate_verify_critique_interpret(tmp_path: pathlib.Path):
    from research_harness.plugins.research.proposition_critic.plugin import (
        PropositionCriticService,
    )
    from research_harness.plugins.research.proposition_generator.plugin import (
        PropositionGeneratorService,
    )
    from research_harness.plugins.research.proposition_verifier.plugin import (
        PropositionVerifierService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _cournot_scenario(store)
    _, cs_id = await _run_statics(store, ids["analysis"])
    static = await _static_of(store, cs_id, "q1", "a")
    sid = await _static_id(store, static)

    gen_resp = _prop_resp(
        _monotonicity(
            "Increasing demand a raises platform 1's quantity.", "q1", "a", "positive", sid
        )
    )
    router = FakeRouter([gen_resp, _critique_resp(), _interpretation_resp()])
    verifier = PropositionVerifierService(artifact_store=store)
    critic = PropositionCriticService(model_router=router, artifact_store=store)
    gen = PropositionGeneratorService(
        model_router=router, artifact_store=store, verifier=verifier, critic=critic
    )
    prop_ids = await gen.generate(cs_id)
    assert len(prop_ids) == 1
    assert router.calls == 3  # generate + critique + interpretation

    prop = (await store.get(prop_ids[0])).parse_payload(Proposition)
    assert prop.claim_type.value == "monotonicity"

    v = (await store.list(artifact_type="proposition_verification"))[0].parse_payload(
        PropositionVerification
    )
    assert v.status == PropositionVerificationStatus.verified

    crit = (await store.list(artifact_type="proposition_critique"))[0].parse_payload(
        PropositionCritique
    )
    assert crit.verdict.value == "revise"
    assert any(i.category.value == "overclaiming" for i in crit.issues)
    assert crit.model_role == "critic"

    interp = (await store.list(artifact_type="economic_interpretation"))[0].parse_payload(
        EconomicInterpretation
    )
    assert interp.proposition_id == prop_ids[0]
    assert interp.economic_interpretation
    assert interp.managerial_implication
    assert interp.is_theoretical_implication
    assert interp.model_role == "reasoning"

    # idempotent rerun: same propositions, no extra LLM calls
    again = await gen.generate(cs_id)
    assert again == prop_ids
    assert router.calls == 3


@pytest.mark.asyncio
async def test_interpretation_grounding_provenance(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _cournot_scenario(store)
    _, cs_id = await _run_statics(store, ids["analysis"])
    static = await _static_of(store, cs_id, "q1", "a")
    sid = await _static_id(store, static)
    router = FakeRouter(
        [
            _prop_resp(_monotonicity("Increasing demand a raises q1.", "q1", "a", "positive", sid)),
            _critique_resp(),
            _interpretation_resp(),
        ]
    )
    from research_harness.plugins.research.proposition_critic.plugin import (
        PropositionCriticService,
    )
    from research_harness.plugins.research.proposition_generator.plugin import (
        PropositionGeneratorService,
    )
    from research_harness.plugins.research.proposition_verifier.plugin import (
        PropositionVerifierService,
    )

    verifier = PropositionVerifierService(artifact_store=store)
    critic = PropositionCriticService(model_router=router, artifact_store=store)
    gen = PropositionGeneratorService(
        model_router=router, artifact_store=store, verifier=verifier, critic=critic
    )
    prop_ids = await gen.generate(cs_id)
    prop_id = prop_ids[0]

    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")

    interp_envs = await store2.list(artifact_type="economic_interpretation")
    assert len(interp_envs) == 1
    i = interp_envs[0].parse_payload(EconomicInterpretation)
    assert i.proposition_id == prop_id
    parents = await store2.get_parents(interp_envs[0].artifact_id)
    assert any(p.source_artifact_id == prop_id for p in parents)

    prop_parents = await store2.get_parents(prop_id)
    assert any(p.source_artifact_id == cs_id for p in prop_parents)
    assert any(p.source_artifact_id == sid for p in prop_parents)

    # chain: interpretation -> proposition -> static -> candidate -> model
    s_parents = await store2.get_parents(sid)
    assert any(p.source_artifact_id == ids["candidate"] for p in s_parents)
    c_parents = await store2.get_parents(ids["candidate"])
    assert any(p.source_artifact_id == ids["model"] for p in c_parents)
    await store2.close()


@pytest.mark.asyncio
async def test_role_change_creates_new_propositions(tmp_path: pathlib.Path):
    from research_harness.plugins.research.proposition_critic.plugin import (
        PropositionCriticService,
    )
    from research_harness.plugins.research.proposition_generator.plugin import (
        PropositionGeneratorService,
    )
    from research_harness.plugins.research.proposition_verifier.plugin import (
        PropositionVerifierService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _cournot_scenario(store)
    _, cs_id = await _run_statics(store, ids["analysis"])
    static = await _static_of(store, cs_id, "q1", "a")
    sid = await _static_id(store, static)

    router = FakeRouter(
        [
            _prop_resp(_monotonicity("a raises q1.", "q1", "a", "positive", sid)),
            _critique_resp(),
            _interpretation_resp(),
            _prop_resp(_monotonicity("a raises q1.", "q1", "a", "positive", sid)),
            _critique_resp(),
            _interpretation_resp(),
        ]
    )
    verifier = PropositionVerifierService(artifact_store=store)
    critic = PropositionCriticService(model_router=router, artifact_store=store)
    gen = PropositionGeneratorService(
        model_router=router,
        artifact_store=store,
        verifier=verifier,
        critic=critic,
        generator_role="reasoning",
    )
    first = await gen.generate(cs_id)
    gen2 = PropositionGeneratorService(
        model_router=router,
        artifact_store=store,
        verifier=verifier,
        critic=critic,
        generator_role="long_context",
    )
    second = await gen2.generate(cs_id)
    assert second != first
    props = [
        env.parse_payload(Proposition) for env in await store.list(artifact_type="proposition")
    ]
    roles = {p.model_role for p in props}
    assert roles == {"reasoning", "long_context"}
