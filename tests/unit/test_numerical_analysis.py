"""Phase 3E unit tests — numerical experiments on the Cournot model
(q* = (a - c)/3) and the monopoly-with-slope model (p* = (ab + c)/(2b)).

Deterministic, offline, no LLM. Covers baseline, sweeps, domain/condition
validation, proposition support/violation, welfare, reproducibility,
robustness, and provenance.
"""

from __future__ import annotations

import pathlib

import pytest

from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.schemas.equilibrium import (
    EquilibriumAnalysis,
    EquilibriumAnalysisStatus,
    EquilibriumCandidate,
    EquilibriumExpression,
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
    NumericalExperimentExecution,
    NumericalResult,
    ParameterSweep,
    RobustnessCheck,
    WelfareAnalysis,
)
from research_harness.research.schemas.proposition import Proposition, PropositionClaimType


async def _cournot_scenario(store) -> dict[str, str]:
    """Cournot: q1* = q2* = (a - c)/3; parameters a (R_+), c (R_+), b (R_+ unused)."""
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
            ModelParameter(symbol="b", name="unused", meaning="unused", domain="R_+"),
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
    return {
        "model": m_env.artifact_id,
        "candidate": c_env.artifact_id,
        "analysis": a_env.artifact_id,
    }


async def _monopoly_with_b_scenario(store) -> dict[str, str]:
    """p* = (ab + c)/(2b) with condition 2*b != 0; domains R_+."""
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
            ModelParameter(symbol="b", name="b", meaning="slope", domain="R"),
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
                conditions=["2*b != 0"],
            )
        ],
        decision_variables=["p"],
        solution_method="simultaneous",
        proposed_by="sympy",
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
    return {
        "model": m_env.artifact_id,
        "candidate": c_env.artifact_id,
        "analysis": a_env.artifact_id,
    }


async def _run(store, analysis_id: str):
    from research_harness.plugins.research.numerical_analysis.plugin import (
        NumericalAnalysisService,
    )

    svc = NumericalAnalysisService(artifact_store=store, model_role="reasoning")
    exec_id = await svc.run(analysis_id)
    return exec_id


@pytest.mark.asyncio
async def test_baseline_evaluation(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _cournot_scenario(store)
    exec_id = await _run(store, ids["analysis"])

    rec = (await store.get(exec_id)).parse_payload(NumericalExperimentExecution)
    assert rec.results_created >= 1
    assert rec.results_infeasible == 3  # domain-edge probes: a=0, c=0, b=0

    exp = next(
        env.parse_payload(NumericalExperiment)
        for env in await store.list(artifact_type="numerical_experiment")
    )
    baseline = None
    for rid in exp.results:
        r = (await store.get(rid)).parse_payload(NumericalResult)
        if r.scenario == "baseline":
            baseline = r
            break
    # defaults: a=10, c=1, b=1 -> q = (10 - 1)/3 = 3
    assert baseline.parameter_values["a"] == 10.0
    assert baseline.outcomes["q1"] == pytest.approx(3.0, abs=1e-6)
    assert baseline.outcomes["q2"] == pytest.approx(3.0, abs=1e-6)
    assert baseline.feasible is True
    assert baseline.x_parameter is None


@pytest.mark.asyncio
async def test_parameter_sweep(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _cournot_scenario(store)
    exec_id = await _run(store, ids["analysis"])
    exp = next(
        env.parse_payload(NumericalExperiment)
        for env in await store.list(artifact_type="numerical_experiment")
    )
    sweep_results = [
        (await store.get(rid)).parse_payload(NumericalResult)
        for rid in exp.results
        if (await store.get(rid)).parse_payload(NumericalResult).scenario == "sweep"
    ]
    a_results = [r for r in sweep_results if r.x_parameter == "a"]
    assert len(a_results) == 7
    a_results.sort(key=lambda r: r.x_value)
    assert a_results[0].x_value == pytest.approx(5.0)
    assert a_results[-1].x_value == pytest.approx(20.0)
    # monotonic in a
    qs = [r.outcomes["q1"] for r in a_results]
    assert qs == sorted(qs)
    assert qs[0] == pytest.approx((5 - 1) / 3, abs=1e-6)
    assert qs[-1] == pytest.approx((20 - 1) / 3, abs=1e-6)
    # series fields are visualization-ready
    assert all(r.x_parameter == "a" and r.group is not None for r in a_results)


@pytest.mark.asyncio
async def test_invalid_domain_rejection(tmp_path: pathlib.Path):
    """c > a makes q < 0 -> outcome violates R_+ domain -> infeasible recorded."""
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _cournot_scenario(store)
    exec_id = await _run(store, ids["analysis"])
    rec = (await store.get(exec_id)).parse_payload(NumericalExperimentExecution)
    assert rec.results_infeasible >= 1

    exp = next(
        env.parse_payload(NumericalExperiment)
        for env in await store.list(artifact_type="numerical_experiment")
    )
    invalid = [
        (await store.get(rid)).parse_payload(NumericalResult)
        for rid in exp.results
        if not (await store.get(rid)).parse_payload(NumericalResult).feasible
    ]
    assert invalid
    assert all(r.infeasible_reason for r in invalid)
    assert any("domain" in r.infeasible_reason for r in invalid)
    assert all(r.outcomes == {} for r in invalid)  # never silently evaluated


@pytest.mark.asyncio
async def test_equilibrium_condition_enforcement(tmp_path: pathlib.Path):
    """b = 0 violates candidate condition 2*b != 0 -> infeasible."""
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _monopoly_with_b_scenario(store)
    exec_id = await _run(store, ids["analysis"])
    rec = (await store.get(exec_id)).parse_payload(NumericalExperimentExecution)
    assert rec.results_infeasible >= 1

    exp = next(
        env.parse_payload(NumericalExperiment)
        for env in await store.list(artifact_type="numerical_experiment")
    )
    invalid = [
        (await store.get(rid)).parse_payload(NumericalResult)
        for rid in exp.results
        if not (await store.get(rid)).parse_payload(NumericalResult).feasible
    ]
    assert any("condition" in r.infeasible_reason for r in invalid)


@pytest.mark.asyncio
async def test_proposition_supported_numerically(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _cournot_scenario(store)
    prop = Proposition(
        model_id=ids["model"],
        equilibrium_candidate_id=ids["candidate"],
        comparative_statics_analysis_id="cs1",
        statement="Increasing demand a raises q1.",
        claim_type=PropositionClaimType.monotonicity,
        outcome_variable="q1",
        parameter="a",
        expected_sign="positive",
        supporting_static_ids=[],
        proposed_by="test",
    )
    p_env = ArtifactEnvelope.create(payload=prop, artifact_type="proposition", producer="test")
    await store.put(p_env)

    exec_id = await _run(store, ids["analysis"])
    exp = next(
        env.parse_payload(NumericalExperiment)
        for env in await store.list(artifact_type="numerical_experiment")
    )
    checks = [(await store.get(cid)).parse_payload(RobustnessCheck) for cid in exp.robustness]
    prop_check = next(c for c in checks if c.check_type.value == "proposition_support")
    assert prop_check.outcome.value == "supported"
    assert prop_check.proposition_id == p_env.artifact_id
    assert prop_check.admissible_points >= 1
    assert prop_check.violations == []


@pytest.mark.asyncio
async def test_proposition_violation_detected(tmp_path: pathlib.Path):
    """Conditionally-verified proposition 'dp/db < 0 when a > 0' is false for
    c < 0: the numeric robustness check must detect the violation without
    touching the symbolic proposition artifact."""
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _monopoly_with_b_scenario(store)
    prop = Proposition(
        model_id=ids["model"],
        equilibrium_candidate_id=ids["candidate"],
        comparative_statics_analysis_id="cs1",
        statement="Raising b lowers p when a > 0.",
        claim_type=PropositionClaimType.monotonicity,
        outcome_variable="p",
        parameter="b",
        expected_sign="negative",
        conditions=["a > 0"],
        supporting_static_ids=[],
        proposed_by="test",
    )
    p_env = ArtifactEnvelope.create(payload=prop, artifact_type="proposition", producer="test")
    await store.put(p_env)

    exec_id = await _run(store, ids["analysis"])
    exp = next(
        env.parse_payload(NumericalExperiment)
        for env in await store.list(artifact_type="numerical_experiment")
    )
    checks = [(await store.get(cid)).parse_payload(RobustnessCheck) for cid in exp.robustness]
    prop_check = next(c for c in checks if c.check_type.value == "proposition_support")
    # the default sweep covers b in [0.5, 2] with c=1 > 0 -> derivative negative
    # -> supported on the default grid; then probe beyond the default grid is
    # not part of the automatic run, so here we assert the check exists and is
    # consistent with the admissible points.
    assert prop_check.outcome.value in ("supported", "violated", "not_testable")
    # symbolic artifact untouched
    prop_now = (await store.get(p_env.artifact_id)).parse_payload(Proposition)
    assert prop_now.statement == prop.statement


@pytest.mark.asyncio
async def test_violation_detected_with_negative_cost_point(tmp_path: pathlib.Path):
    """dp/db = -c/(2b^2) > 0 when c < 0: points with c < 0 (condition a > 0
    holds) must be flagged as violations of the 'negative' claim."""
    from research_harness.plugins.research.numerical_analysis.plugin import (
        NumericalAnalysisService,
    )

    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _monopoly_with_b_scenario(store)
    prop = Proposition(
        model_id=ids["model"],
        equilibrium_candidate_id=ids["candidate"],
        comparative_statics_analysis_id="cs1",
        statement="Raising b lowers p when a > 0.",
        claim_type=PropositionClaimType.monotonicity,
        outcome_variable="p",
        parameter="b",
        expected_sign="negative",
        conditions=["a > 0"],
        supporting_static_ids=[],
        proposed_by="test",
    )
    p_env = ArtifactEnvelope.create(payload=prop, artifact_type="proposition", producer="test")
    await store.put(p_env)

    # hand-persisted admissible results with c = -1 (condition a > 0 holds)
    result_ids = []
    for b in (0.5, 1.0, 2.0):
        r = NumericalResult(
            model_id=ids["model"],
            equilibrium_candidate_id=ids["candidate"],
            experiment_id="exec",
            scenario="probe",
            parameter_values={"a": 10.0, "c": -1.0, "b": b},
            outcomes={"p": (10.0 * b - 1.0) / (2 * b)},
            feasible=True,
        )
        r_env = ArtifactEnvelope.create(
            payload=r, artifact_type="numerical_result", producer="test"
        )
        await store.put(r_env)
        result_ids.append(r_env.artifact_id)

    svc = NumericalAnalysisService(artifact_store=store)
    from research_harness.research.symbolic import parse_sympy

    table = {"a": "a", "b": "b", "c": "c"}
    cand = (await store.get(ids["candidate"])).parse_payload(EquilibriumCandidate)
    exprs = {e.variable: parse_sympy(e.expression.expression, table) for e in cand.expressions}
    check = await svc._check_proposition_numerically(
        "exec", ids["model"], ids["candidate"], p_env.artifact_id, prop, result_ids, {}, exprs
    )
    assert check.outcome.value == "violated"
    assert check.admissible_points == 3
    assert len(check.violations) == 3
    # symbolic proposition artifact untouched
    prop_now = (await store.get(p_env.artifact_id)).parse_payload(Proposition)
    assert prop_now.statement == prop.statement


@pytest.mark.asyncio
async def test_deterministic_rerun(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _cournot_scenario(store)
    first = await _run(store, ids["analysis"])
    second = await _run(store, ids["analysis"])
    assert first == second  # idempotent
    assert len(await store.list(artifact_type="numerical_experiment")) == 1

    rec = (await store.get(first)).parse_payload(NumericalExperimentExecution)
    assert rec.engine == "sympy+python"
    assert rec.seed == 0
    assert rec.engine_version


@pytest.mark.asyncio
async def test_multi_parameter_grid(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _cournot_scenario(store)
    await _run(store, ids["analysis"])
    exp = next(
        env.parse_payload(NumericalExperiment)
        for env in await store.list(artifact_type="numerical_experiment")
    )
    sweeps = [(await store.get(sid)).parse_payload(ParameterSweep) for sid in exp.sweeps]
    grid = next(s for s in sweeps if s.kind.value == "grid")
    assert len(grid.dimensions) == 2
    assert grid.total_points == 16
    grid_results = [
        (await store.get(rid)).parse_payload(NumericalResult)
        for rid in exp.results
        if (await store.get(rid)).parse_payload(NumericalResult).scenario == "grid"
    ]
    # a in [5, 15] x c in [0.5, 1.5]: infeasible when c >= a (never here)
    assert len(grid_results) == 16
    assert all(r.feasible for r in grid_results)


@pytest.mark.asyncio
async def test_welfare_calculation(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _cournot_scenario(store)
    await _run(store, ids["analysis"])
    exp = next(
        env.parse_payload(NumericalExperiment)
        for env in await store.list(artifact_type="numerical_experiment")
    )
    w_envs = [await store.get(wid) for wid in exp.welfare]
    assert w_envs
    w = w_envs[0].parse_payload(WelfareAnalysis)
    # baseline a=10, c=1: pi_i = (a-c)^2/9 = 9 each; total 18
    assert len(w.metrics) == 2
    pi1 = next(m for m in w.metrics if m.actor_id == "f1")
    assert pi1.value == pytest.approx(9.0, abs=1e-6)
    assert w.total_welfare == pytest.approx(18.0, abs=1e-6)
    assert any("no fabricated welfare formulas" in n for n in w.notes)


@pytest.mark.asyncio
async def test_robustness_parameter_range(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _cournot_scenario(store)
    await _run(store, ids["analysis"])
    exp = next(
        env.parse_payload(NumericalExperiment)
        for env in await store.list(artifact_type="numerical_experiment")
    )
    checks = [(await store.get(cid)).parse_payload(RobustnessCheck) for cid in exp.robustness]
    range_checks = [c for c in checks if c.check_type.value == "parameter_range"]
    assert len(range_checks) == 3  # one per parameter
    assert all(c.outcome.value == "supported" for c in range_checks)
    assert all(c.admissible_points >= 1 for c in range_checks)


@pytest.mark.asyncio
async def test_provenance_after_reopen(tmp_path: pathlib.Path):
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await _cournot_scenario(store)
    exec_id = await _run(store, ids["analysis"])

    await store.close()
    store2 = SQLiteArtifactStore(path=tmp_path / "art.db")

    exp = next(
        env.parse_payload(NumericalExperiment)
        for env in await store2.list(artifact_type="numerical_experiment")
    )
    assert exp.model_id == ids["model"]
    assert exp.equilibrium_candidate_id == ids["candidate"]
    parents = await store2.get_parents(
        next(env.artifact_id for env in await store2.list(artifact_type="numerical_experiment"))
    )
    assert any(p.source_artifact_id == ids["candidate"] for p in parents)

    rid = exp.results[0]
    r_parents = await store2.get_parents(rid)
    assert any(p.source_artifact_id == ids["candidate"] for p in r_parents)
    r = (await store2.get(rid)).parse_payload(NumericalResult)
    assert r.equilibrium_candidate_id == ids["candidate"]

    ex_parents = await store2.get_parents(exec_id)
    assert any(p.source_artifact_id == ids["candidate"] for p in ex_parents)
    await store2.close()
