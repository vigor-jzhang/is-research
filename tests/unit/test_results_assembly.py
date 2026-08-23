"""Phase 4A unit tests — results assembly + critique (offline, fake model).

Covers: valid assembly; failed proposition rejected; conditions preserved;
numerical/symbolic conflict surfaced by the critic; unsupported IDs rejected;
contribution linked to gap + findings; sweeping novelty claim normalized;
critic behavior; idempotency; provenance after reopen.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.equilibrium import (
    EquilibriumAnalysis,
    EquilibriumCandidate,
    EquilibriumExpression,
)
from research_harness.research.schemas.gap import GapAnalysis
from research_harness.research.schemas.mechanism import (
    SelectedMechanism,
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
from research_harness.research.schemas.numerical import (
    NumericalExperiment,
    NumericalResult,
    RobustnessCheck,
    RobustnessCheckType,
    RobustnessOutcome,
)
from research_harness.research.schemas.proposition import (
    ComparativeStatic,
    Proposition,
    PropositionClaimType,
    PropositionVerification,
    PropositionVerificationStatus,
)
from research_harness.research.schemas.results import (
    ContributionClaim,
    ResearchFinding,
    ResearchImplication,
    ResearchResultsPackage,
    ResultsCritique,
    ResultsCritiqueCategory,
)


class FakeRouter:
    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.calls = 0

    async def complete(self, role, request):
        self.calls += 1
        idx = min(self.calls - 1, len(self.responses) - 1)
        return ModelResponse(
            message=Message(role="assistant", content=json.dumps(self.responses[idx])),
            tool_calls=[],
            finish_reason="stop",
            model="fake",
        )


async def build_chain(
    store: SQLiteArtifactStore, *, violated_prop_id: str | None = None
) -> dict[str, str]:
    """Persist a minimal verified Phase 3 chain (Cournot) and return ids."""
    ids: dict[str, str] = {}

    from research_harness.research.schemas.gap import GapType, ResearchGap

    rg = ResearchGap(
        title="platform demand dynamics",
        gap_type=GapType.theoretical_gap,
        description="within the reviewed corpus, demand-driven quantity effects on platforms are unresolved",
    )
    rg_env = ArtifactEnvelope.create(
        payload=rg, artifact_type="research_gap", producer="test", artifact_id="gap1"
    )
    await store.put(rg_env)

    gap = GapAnalysis(
        literature_synthesis_id="synth1",
        evidence_corpus_id="corpus1",
        research_question_id="rq1",
        gap_ids=["gap1"],
    )
    g_env = ArtifactEnvelope.create(payload=gap, artifact_type="gap_analysis", producer="test")
    await store.put(g_env)
    ids["gap_analysis"] = g_env.artifact_id

    mech = SelectedMechanism(
        gap_id="gap1",
        gap_selection_id="sel1",
        mechanism_candidate_id="cand-mech",
        name="Cournot competition",
        description="quantity competition",
        actors=["f1", "f2"],
        strategic_interactions=["quantity"],
        causal_logic="q",
        key_assumptions=[],
        expected_outcomes=[],
        boundary_conditions=[],
        grounding=[],
    )
    m_env = ArtifactEnvelope.create(
        payload=mech, artifact_type="selected_mechanism", producer="test"
    )
    await store.put(m_env)
    ids["mechanism"] = m_env.artifact_id

    model = FormalAnalyticalModel(
        selected_mechanism_id=ids["mechanism"],
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
        ],
        assumptions=[],
        timing=[
            ModelTimingStage(
                stage_number=0, name="move", description="both", actor_ids=["f1", "f2"]
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
    mod_env = ArtifactEnvelope.create(
        payload=model, artifact_type="formal_analytical_model", producer="test"
    )
    await store.put(mod_env)
    ids["model"] = mod_env.artifact_id

    cand = EquilibriumCandidate(
        model_id=ids["model"],
        proposed_by="sympy",
        revision_round=0,
        solution_method="simultaneous",
        expressions=[
            EquilibriumExpression(
                variable="q1",
                expression=Expression(expression="a/3 - c/3", symbols_used=[]),
                conditions=[],
            ),
            EquilibriumExpression(
                variable="q2",
                expression=Expression(expression="a/3 - c/3", symbols_used=[]),
                conditions=[],
            ),
        ],
    )
    c_env = ArtifactEnvelope.create(
        payload=cand, artifact_type="equilibrium_candidate", producer="test"
    )
    await store.put(c_env)
    ids["candidate"] = c_env.artifact_id

    analysis = EquilibriumAnalysis(
        model_id=ids["model"],
        candidate_ids=[ids["candidate"]],
        selected_candidate_id=ids["candidate"],
        status="derived",
        solution_method="simultaneous",
    )
    a_env = ArtifactEnvelope.create(
        payload=analysis, artifact_type="equilibrium_analysis", producer="test"
    )
    await store.put(a_env)
    ids["equilibrium_analysis"] = a_env.artifact_id

    # one verified proposition with a condition
    prop = Proposition(
        model_id=ids["model"],
        equilibrium_candidate_id=ids["candidate"],
        comparative_statics_analysis_id="cs-a",
        statement="Increasing demand a raises each platform's equilibrium quantity.",
        claim_type=PropositionClaimType.monotonicity,
        outcome_variable="q1",
        parameter="a",
        expected_sign="positive",
        conditions=["a > 0"],
        supporting_static_ids=["static-a"],
        status="candidate",
    )
    p_env = ArtifactEnvelope.create(payload=prop, artifact_type="proposition", producer="test")
    await store.put(p_env)
    ids["prop_ok"] = p_env.artifact_id
    v = PropositionVerification(
        proposition_id=ids["prop_ok"],
        model_id=ids["model"],
        status=PropositionVerificationStatus.verified,
        checks=[],
    )
    v_env = ArtifactEnvelope.create(
        payload=v, artifact_type="proposition_verification", producer="test"
    )
    await store.put(v_env)

    # a failed proposition (never usable as support)
    prop_failed = Proposition(
        model_id=ids["model"],
        equilibrium_candidate_id=ids["candidate"],
        comparative_statics_analysis_id="cs-a",
        statement="Failed claim.",
        claim_type=PropositionClaimType.monotonicity,
        outcome_variable="q1",
        parameter="c",
        expected_sign="negative",
        conditions=[],
        supporting_static_ids=[],
        status="candidate",
    )
    pf_env = ArtifactEnvelope.create(
        payload=prop_failed, artifact_type="proposition", producer="test"
    )
    await store.put(pf_env)
    ids["prop_failed"] = pf_env.artifact_id
    vf = PropositionVerification(
        proposition_id=ids["prop_failed"],
        model_id=ids["model"],
        status=PropositionVerificationStatus.failed,
        checks=[],
    )
    vf_env = ArtifactEnvelope.create(
        payload=vf, artifact_type="proposition_verification", producer="test"
    )
    await store.put(vf_env)

    static = ComparativeStatic(
        model_id=ids["model"],
        equilibrium_candidate_id=ids["candidate"],
        outcome_variable="q1",
        parameter="a",
        derivative_expression=Expression(expression="1/3", symbols_used=[]),
        sign="positive",
        conditions=["a > 0"],
    )
    s_env = ArtifactEnvelope.create(
        payload=static, artifact_type="comparative_static", producer="test"
    )
    await store.put(s_env)
    ids["static"] = s_env.artifact_id

    result = NumericalResult(
        model_id=ids["model"],
        equilibrium_candidate_id=ids["candidate"],
        experiment_id="exp1",
        scenario="baseline",
        parameter_values={"a": 10.0, "c": 1.0},
        outcomes={"q1": 3.0, "q2": 3.0},
        feasible=True,
    )
    r_env = ArtifactEnvelope.create(
        payload=result, artifact_type="numerical_result", producer="test"
    )
    await store.put(r_env)
    ids["result"] = r_env.artifact_id

    robustness_ids: list[str] = []
    if violated_prop_id:
        rob = RobustnessCheck(
            model_id=ids["model"],
            equilibrium_candidate_id=ids["candidate"],
            experiment_id="exp1",
            proposition_id=violated_prop_id,
            check_type=RobustnessCheckType.proposition_support,
            outcome=RobustnessOutcome.violated,
            admissible_points=5,
            conclusion="sign does not hold numerically",
        )
        rob_env = ArtifactEnvelope.create(
            payload=rob, artifact_type="robustness_check", producer="test"
        )
        await store.put(rob_env)
        robustness_ids.append(rob_env.artifact_id)

    exp = NumericalExperiment(
        model_id=ids["model"],
        equilibrium_candidate_id=ids["candidate"],
        sweeps=[],
        results=[ids["result"]],
        robustness=robustness_ids,
        welfare=[],
        status="completed",
        summary="baseline only",
    )
    e_env = ArtifactEnvelope.create(
        payload=exp, artifact_type="numerical_experiment", producer="test"
    )
    await store.put(e_env)
    ids["experiment"] = e_env.artifact_id
    return ids


def assembly_response(
    ids: dict[str, str],
    *,
    drop_condition: bool = False,
    bad_id: str | None = None,
    bad_prop: str | None = None,
) -> dict:
    finding_refs = [bad_id] if bad_id else ["FINDING0"]
    return {
        "findings": [
            {
                "statement": "Larger markets raise equilibrium platform quantities.",
                "finding_type": "analytical_result",
                "supporting_proposition_ids": [bad_prop or ids["prop_ok"]],
                "supporting_comparative_static_ids": [ids["static"]],
                "supporting_numerical_result_ids": [ids["result"]],
                "conditions": [] if drop_condition else ["a > 0"],
                "confidence": "high",
                "knowledge_basis": "research_inference",
            }
        ],
        "contributions": [
            {
                "claim": "This is the first study showing that demand growth raises platform quantities.",
                "contribution_type": "theoretical",
                "finding_ids": finding_refs,
                "advances_literature": "within the reviewed corpus, the demand-driven quantity effect was unresolved",
                "novelty_claim": "We are the first to analyze this.",
            }
        ],
        "implications": [
            {
                "text": "Platform managers should scale capacity as demand grows.",
                "implication_kind": "management",
                "claim_type": "managerial_implication",
                "grounded_in_finding_ids": finding_refs,
                "note": None,
            }
        ],
        "limitations": ["single-period setting"],
    }


@pytest.mark.asyncio
async def test_valid_assembly_and_novelty_normalization(tmp_path: pathlib.Path):
    from research_harness.plugins.research.results_assembler.plugin import (
        ResultsAssemblerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    resp = assembly_response(ids)
    # patch finding id placeholder -> we don't know it yet; use positional token
    resp["contributions"][0]["finding_ids"] = ["FINDING0"]

    class OneFindingRouter:
        async def complete(self, role, request):
            return ModelResponse(
                message=Message(role="assistant", content=json.dumps(resp)),
                tool_calls=[],
                finish_reason="stop",
                model="fake",
            )

    svc = ResultsAssemblerService(model_router=OneFindingRouter(), artifact_store=store)
    exec_id = await svc.assemble(ids["experiment"])
    assert exec_id

    pkg_env = next(env for env in await store.list(artifact_type="results_package"))
    pkg = pkg_env.parse_payload(ResearchResultsPackage)
    assert pkg.gap_id == "gap1"
    assert pkg.model_id == ids["model"]
    assert pkg.equilibrium_analysis_id == ids["equilibrium_analysis"]
    assert pkg.research_question_id == "rq1"
    assert len(pkg.finding_ids) == 1
    assert len(pkg.contribution_claim_ids) == 1
    assert len(pkg.implication_ids) == 1
    assert len(pkg.limitations) == 1

    f = (await store.get(pkg.finding_ids[0])).parse_payload(ResearchFinding)
    assert f.conditions == ["a > 0"]
    assert f.supporting_proposition_ids == [ids["prop_ok"]]
    assert f.supporting_numerical_result_ids == [ids["result"]]

    c = (await store.get(pkg.contribution_claim_ids[0])).parse_payload(ContributionClaim)
    assert c.novelty_normalized is True
    assert "first study" not in c.claim.lower()
    assert "first" not in (c.novelty_claim or "").lower()
    assert c.gap_id == "gap1"
    assert c.finding_ids == pkg.finding_ids

    i = (await store.get(pkg.implication_ids[0])).parse_payload(ResearchImplication)
    assert i.implication_kind.value == "management"
    assert i.claim_type.value == "managerial_implication"


@pytest.mark.asyncio
async def test_failed_proposition_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.research.results_assembler.plugin import (
        ResultsAssemblerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    resp = assembly_response(ids)
    resp["findings"][0]["supporting_proposition_ids"] = [ids["prop_failed"]]

    svc = ResultsAssemblerService(model_router=FakeRouter([resp]), artifact_store=store)
    with pytest.raises(ValueError, match="failed proposition"):
        await svc.assemble(ids["experiment"])


@pytest.mark.asyncio
async def test_unsupported_ids_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.research.results_assembler.plugin import (
        ResultsAssemblerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    resp = assembly_response(ids)
    resp["findings"][0]["supporting_numerical_result_ids"] = ["bogus-result-id"]
    svc = ResultsAssemblerService(model_router=FakeRouter([resp]), artifact_store=store)
    with pytest.raises(ValueError, match="unsupported numerical result"):
        await svc.assemble(ids["experiment"])


@pytest.mark.asyncio
async def test_dropped_conditions_rejected(tmp_path: pathlib.Path):
    from research_harness.plugins.research.results_assembler.plugin import (
        ResultsAssemblerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    resp = assembly_response(ids, drop_condition=True)
    svc = ResultsAssemblerService(model_router=FakeRouter([resp]), artifact_store=store)
    with pytest.raises(ValueError, match="drops required conditions"):
        await svc.assemble(ids["experiment"])


@pytest.mark.asyncio
async def test_contribution_must_link_gap_and_findings(tmp_path: pathlib.Path):
    from research_harness.plugins.research.results_assembler.plugin import (
        ResultsAssemblerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    resp = assembly_response(ids)
    resp["contributions"][0]["finding_ids"] = ["missing-finding"]
    svc = ResultsAssemblerService(model_router=FakeRouter([resp]), artifact_store=store)
    with pytest.raises(ValueError, match="unknown findings"):
        await svc.assemble(ids["experiment"])


@pytest.mark.asyncio
async def test_idempotent_assembly(tmp_path: pathlib.Path):
    from research_harness.plugins.research.results_assembler.plugin import (
        ResultsAssemblerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    resp = assembly_response(ids)
    svc = ResultsAssemblerService(model_router=FakeRouter([resp]), artifact_store=store)
    exec1 = await svc.assemble(ids["experiment"])
    exec2 = await svc.assemble(ids["experiment"])
    assert exec1 == exec2
    pkgs = await store.list(artifact_type="results_package")
    assert len(pkgs) == 1


@pytest.mark.asyncio
async def test_critic_surfaces_numerical_symbolic_conflict(tmp_path: pathlib.Path):
    from research_harness.plugins.research.results_assembler.plugin import (
        ResultsAssemblerService,
    )
    from research_harness.plugins.research.results_critic.plugin import ResultsCriticService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    # register a violated robustness check on the verified proposition
    rob = RobustnessCheck(
        model_id=ids["model"],
        equilibrium_candidate_id=ids["candidate"],
        experiment_id="exp1",
        proposition_id=ids["prop_ok"],
        check_type=RobustnessCheckType.proposition_support,
        description="numerical support of the verified proposition",
        outcome=RobustnessOutcome.violated,
        admissible_points=5,
        conclusion="sign does not hold numerically",
    )
    rob_env = ArtifactEnvelope.create(
        payload=rob, artifact_type="robustness_check", producer="test"
    )
    await store.put(rob_env)
    exp = (await store.get(ids["experiment"])).parse_payload(NumericalExperiment)
    # immutable store: register a second experiment (same candidate) that carries the check
    exp2 = exp.model_copy(update={"robustness": [rob_env.artifact_id]})
    exp2_env = ArtifactEnvelope.create(
        payload=exp2,
        artifact_type="numerical_experiment",
        producer="test",
        artifact_id="exp-violated",
    )
    await store.put(exp2_env)

    resp = assembly_response(ids)
    svc = ResultsAssemblerService(model_router=FakeRouter([resp]), artifact_store=store)
    await svc.assemble("exp-violated")
    pkg_env = next(env for env in await store.list(artifact_type="results_package"))

    critic = ResultsCriticService(
        model_router=FakeRouter(
            [
                {
                    "overall_assessment": "conflict with numerics",
                    "verdict": "revise",
                    "recommendations": ["align with numerical evidence"],
                    "issues": [],
                }
            ]
        ),
        artifact_store=store,
    )
    crit_id = await critic.critique(pkg_env.artifact_id)
    c = (await store.get(crit_id)).parse_payload(ResultsCritique)
    cats = [i.category for i in c.issues]
    assert ResultsCritiqueCategory.symbolic_numerical_contradiction in cats
    assert c.verdict.value == "revise"


@pytest.mark.asyncio
async def test_critic_merges_deterministic_and_llm_issues(tmp_path: pathlib.Path):
    from research_harness.plugins.research.results_assembler.plugin import (
        ResultsAssemblerService,
    )
    from research_harness.plugins.research.results_critic.plugin import ResultsCriticService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    resp = assembly_response(ids)
    svc = ResultsAssemblerService(model_router=FakeRouter([resp]), artifact_store=store)
    await svc.assemble(ids["experiment"])
    pkg_env = next(env for env in await store.list(artifact_type="results_package"))
    pkg = pkg_env.parse_payload(ResearchResultsPackage)

    critic = ResultsCriticService(
        model_router=FakeRouter(
            [
                {
                    "overall_assessment": "mostly fine",
                    "verdict": "approve",
                    "recommendations": ["tighten wording"],
                    "issues": [
                        {
                            "category": "causal_overstatement",
                            "description": "causality not established by the model",
                            "severity": "medium",
                            "location": "contribution 1",
                        }
                    ],
                }
            ]
        ),
        artifact_store=store,
    )
    crit_id = await critic.critique(pkg_env.artifact_id)
    # deterministic weak_gap_link check: claim gap differs from package gap
    claim = (await store.get(pkg.contribution_claim_ids[0])).parse_payload(ContributionClaim)
    assert claim.gap_id == pkg.gap_id  # matching, so no deterministic issue
    c = (await store.get(crit_id)).parse_payload(ResultsCritique)
    assert any(i.category == ResultsCritiqueCategory.causal_overstatement for i in c.issues)
    assert c.overall_assessment == "mostly fine"


@pytest.mark.asyncio
async def test_critic_unsupported_novelty_after_assembly(tmp_path: pathlib.Path):
    from research_harness.plugins.research.results_critic.plugin import ResultsCriticService

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    finding = ResearchFinding(
        model_id="mod1",
        equilibrium_candidate_id="cand1",
        statement="x affects y in the model",
        finding_type="analytical_result",
        supporting_proposition_ids=[],
        supporting_comparative_static_ids=[],
        supporting_numerical_result_ids=[],
        conditions=[],
        confidence="medium",
        knowledge_basis="research_inference",
    )
    f_env = ArtifactEnvelope.create(
        payload=finding, artifact_type="research_finding", producer="test", artifact_id="f1"
    )
    await store.put(f_env)
    claim = ContributionClaim(
        gap_id="gap1",
        finding_ids=["f1"],
        claim="x affects y",
        contribution_type="theoretical",
        advances_literature="",
        novelty_claim="This is the first study of this phenomenon",
        novelty_normalized=False,
    )
    c_env = ArtifactEnvelope.create(
        payload=claim, artifact_type="contribution_claim", producer="test"
    )
    await store.put(c_env)
    pkg = ResearchResultsPackage(
        gap_id="gap1",
        selected_mechanism_id="m1",
        model_id="mod1",
        equilibrium_analysis_id="eq1",
        equilibrium_candidate_id="cand1",
        finding_ids=["f1"],
        contribution_claim_ids=[c_env.artifact_id],
        implication_ids=[],
        limitations=[],
        summary="synthetic package for critic test",
    )
    p_env = ArtifactEnvelope.create(payload=pkg, artifact_type="results_package", producer="test")
    await store.put(p_env)

    critic = ResultsCriticService(
        model_router=FakeRouter(
            [
                {
                    "overall_assessment": "novelty issue",
                    "verdict": "revise",
                    "recommendations": [],
                    "issues": [],
                }
            ]
        ),
        artifact_store=store,
    )
    crit_id = await critic.critique(p_env.artifact_id)
    c = (await store.get(crit_id)).parse_payload(ResultsCritique)
    assert any(i.category == ResultsCritiqueCategory.unsupported_novelty_claim for i in c.issues)


@pytest.mark.asyncio
async def test_provenance_after_reopen(tmp_path: pathlib.Path):
    from research_harness.plugins.research.results_assembler.plugin import (
        ResultsAssemblerService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)
    resp = assembly_response(ids)
    svc = ResultsAssemblerService(model_router=FakeRouter([resp]), artifact_store=store)
    await svc.assemble(ids["experiment"])
    pkg_env = next(env for env in await store.list(artifact_type="results_package"))
    await store.close()

    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")
    pkg = (await store2.get(pkg_env.artifact_id)).parse_payload(ResearchResultsPackage)

    # package -> contribution
    contrib_parents = await store2.get_parents(pkg.contribution_claim_ids[0])
    assert any(p.source_artifact_id == "gap1" for p in contrib_parents)
    # contribution -> finding (derived_from edge from finding to contribution)
    finding_parents = await store2.get_parents(pkg.finding_ids[0])
    assert any(p.source_artifact_id == ids["prop_ok"] for p in finding_parents)
    assert any(p.source_artifact_id == ids["result"] for p in finding_parents)
    # package derived from gap + contribution
    pkg_parents = await store2.get_parents(pkg_env.artifact_id)
    assert any(p.source_artifact_id == "gap1" for p in pkg_parents)
    assert any(p.source_artifact_id == pkg.contribution_claim_ids[0] for p in pkg_parents)
    await store2.close()
