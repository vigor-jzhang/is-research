"""Phase 3C equilibrium deriver — solvability gate, optimization problems,
FOCs, best responses, candidate equilibria, bounded revision loop.

Mathematical operations are performed with SymPy wherever possible; the LLM
may propose derivation steps or candidate expressions, but every algebraic
claim is verified symbolically by research.equilibrium_verifier. Timing is
respected: simultaneous moves and sequential backward induction are handled
explicitly. No propositions / comparative statics / numerics (Phase 3D+).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.equilibrium import (
    BestResponse,
    EquilibriumAnalysis,
    EquilibriumAnalysisStatus,
    EquilibriumCandidate,
    EquilibriumExecution,
    EquilibriumExecutionStatus,
    EquilibriumExpression,
    FirstOrderCondition,
    OptimizationProblem,
    SolutionMethod,
    VerificationStatus,
)
from research_harness.research.schemas.model import (
    Expression,
    FormalAnalyticalModel,
    SymbolKind,
)
from research_harness.research.symbolic import decision_stage_plan, parse_sympy

logger = logging.getLogger(__name__)


class _ExpressionItem(BaseModel):
    variable: str
    expression: str
    symbols_used: list[str] = Field(default_factory=list)
    latex: str | None = None
    conditions: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class _CandidateProposal(BaseModel):
    expressions: list[_ExpressionItem]

    model_config = {"extra": "forbid"}


class EquilibriumDeriverService:
    def __init__(
        self,
        model_router: Any,
        artifact_store: Any,
        verifier: Any,
        model_role: str = "reasoning",
        revision_role: str = "reasoning",
        max_revisions: int = 2,
        max_llm_calls: int = 10,
        events: Any | None = None,
    ) -> None:
        self._router = model_router
        self._store = artifact_store
        self._verifier = verifier
        self._model_role = model_role
        self._revision_role = revision_role
        self._max_revisions = max_revisions
        self._max_llm_calls = max_llm_calls
        self._events = events

    @property
    def deriver_id(self) -> str:
        return "research.equilibrium_deriver"

    async def derive(self, model_id: str) -> str:
        """Derive + verify equilibrium for a model. Returns the execution id."""
        # Idempotency: reuse a completed successful derivation for same model + role
        existing = await self._store.list(artifact_type="equilibrium_execution")
        for env in existing:
            try:
                ex = EquilibriumExecution.model_validate(env.payload)
                if (
                    ex.model_id == model_id
                    and ex.model_role == self._model_role
                    and ex.completed_at is not None
                    and ex.status
                    in (
                        EquilibriumExecutionStatus.derived,
                        EquilibriumExecutionStatus.partially_derived,
                    )
                ):
                    return env.artifact_id
            except Exception:
                continue

        m_env = await self._store.get(model_id)
        model = m_env.parse_payload(FormalAnalyticalModel)
        started = datetime.now(UTC)
        exec_record = EquilibriumExecution(
            model_id=model_id,
            status=EquilibriumExecutionStatus.solvable,
            failures=[],
            counts={"model_calls": 0, "max_revisions": self._max_revisions},
            model_role=self._model_role,
            started_at=started,
        )

        # ------------------------------------------------------------------
        # 1. Solvability gate (deterministic)
        # ------------------------------------------------------------------
        gate = self._solvability_gate(model)
        if not gate["ok"]:
            exec_record.status = EquilibriumExecutionStatus.not_solvable
            for reason in gate["reasons"]:
                exec_record.failures.append({"error": reason})
            exec_record.completed_at = datetime.now(UTC)
            exec_env = ArtifactEnvelope.create(
                payload=exec_record,
                artifact_type="equilibrium_execution",
                producer="research.equilibrium_deriver",
            )
            await self._store.put(exec_env)
            return exec_env.artifact_id

        table = {v.symbol: v for v in model.variables}
        table.update({p.symbol: p for p in model.parameters})
        payoff_syms: dict[str, Any] = {}
        for p in model.payoffs:
            payoff_syms[p.actor_id] = parse_sympy(p.expression.expression, table)

        # ------------------------------------------------------------------
        # 2. Optimization problems + FOCs + best responses (SymPy)
        # ------------------------------------------------------------------
        problems: list[str] = []
        focs: list[str] = []
        brs: list[str] = []
        foc_data: list[tuple[str, str, Any, Any]] = []  # actor, dv, foc(sympy), payoff(sympy)

        for payoff in model.payoffs:
            if not payoff.decision_variables:
                continue
            problem = OptimizationProblem(
                model_id=model_id,
                actor_id=payoff.actor_id,
                decision_variables=list(payoff.decision_variables),
                objective=payoff.expression,
                constraints=list(payoff.constraints),
                method="maximize",
                derived_by="structural",
            )
            p_env = ArtifactEnvelope.create(
                payload=problem,
                artifact_type="optimization_problem",
                producer="research.equilibrium_deriver",
            )
            await self._store.put(p_env)
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=model_id,
                    target_artifact_id=p_env.artifact_id,
                    producer="research.equilibrium_deriver",
                )
            )
            problems.append(p_env.artifact_id)

            import sympy

            pay = payoff_syms[payoff.actor_id]
            for dv in payoff.decision_variables:
                dv_sym = sympy.Symbol(dv)
                foc_sym = sympy.diff(pay, dv_sym)
                applicable = foc_sym != 0
                sols: list[Any] = []
                if applicable:
                    try:
                        sols = sympy.solve(sympy.Eq(foc_sym, 0), dv_sym)
                    except Exception:  # noqa: BLE001
                        sols = []
                foc = FirstOrderCondition(
                    model_id=model_id,
                    actor_id=payoff.actor_id,
                    decision_variable=dv,
                    payoff_expression=payoff.expression,
                    foc_expression=Expression(
                        expression=str(foc_sym),
                        symbols_used=sorted({str(s) for s in foc_sym.free_symbols}),
                    ),
                    constraints=list(payoff.constraints),
                    candidate_solutions=[
                        Expression(
                            expression=str(s),
                            symbols_used=sorted({str(sym) for sym in s.free_symbols}),
                        )
                        for s in sols
                    ],
                    applicable=applicable,
                    note=(
                        None
                        if applicable
                        else f"payoff of {payoff.actor_id} is independent of {dv}; no interior FOC"
                    ),
                )
                f_env = ArtifactEnvelope.create(
                    payload=foc,
                    artifact_type="first_order_condition",
                    producer="research.equilibrium_deriver",
                )
                await self._store.put(f_env)
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=model_id,
                        target_artifact_id=f_env.artifact_id,
                        producer="research.equilibrium_deriver",
                    )
                )
                focs.append(f_env.artifact_id)
                foc_data.append((payoff.actor_id, dv, foc_sym, pay))

                # Best response: closed form when FOC solves uniquely
                br = BestResponse(
                    model_id=model_id,
                    actor_id=payoff.actor_id,
                    decision_variable=dv,
                    implicit=True,
                    derivation="foc_solved" if sols else "foc_unsolved",
                    solution_method=(
                        SolutionMethod.sympy_solved.value
                        if sols
                        else SolutionMethod.implicit_foc.value
                    ),
                )
                if len(sols) == 1:
                    br = br.model_copy(
                        update={
                            "response_expression": Expression(
                                expression=str(sols[0]),
                                symbols_used=sorted({str(s) for s in sols[0].free_symbols}),
                            ),
                            "implicit": False,
                        }
                    )
                br_env = ArtifactEnvelope.create(
                    payload=br,
                    artifact_type="best_response",
                    producer="research.equilibrium_deriver",
                )
                await self._store.put(br_env)
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=model_id,
                        target_artifact_id=br_env.artifact_id,
                        producer="research.equilibrium_deriver",
                    )
                )
                brs.append(br_env.artifact_id)

        exec_record.optimization_problems_created = len(problems)
        exec_record.focs_created = len(focs)
        exec_record.best_responses_created = len(brs)

        # ------------------------------------------------------------------
        # 3. Solution order honoring timing
        # ------------------------------------------------------------------
        plan = self._solution_plan(model, foc_data)

        # ------------------------------------------------------------------
        # 4. Candidate equilibrium (SymPy first, LLM fallback)
        # ------------------------------------------------------------------
        candidates: list[tuple[EquilibriumCandidate, str]] = []  # (candidate, method_label)
        analysis_id = ""
        try:
            exprs, method = self._derive_candidate_sympy(model, payoff_syms, foc_data, plan)
            if exprs:
                candidates.append(
                    (
                        self._make_candidate(model_id, analysis_id, exprs, method, 0, [], "sympy"),
                        method,
                    )
                )
        except Exception as e:  # noqa: BLE001
            exec_record.failures.append({"error": f"symbolic derivation failed: {e}"})

        if not candidates:
            proposal = await self._llm_propose(model, foc_data, plan, exec_record)
            if proposal:
                candidates.append(
                    (
                        self._make_candidate(
                            model_id, analysis_id, proposal, "llm_proposed", 0, [], "llm"
                        ),
                        "llm_proposed",
                    )
                )

        # ------------------------------------------------------------------
        # 5. Verify + bounded revision loop
        # ------------------------------------------------------------------
        analysis = await self._create_analysis(
            model_id, plan, method_label=candidates[0][1] if candidates else "none"
        )
        analysis_id = analysis
        verifications: list[str] = []
        candidate_ids: list[str] = []
        best_status = VerificationStatus.pending
        revisions_used = 0

        for cand, _label in candidates:
            cand_id = await self._persist_candidate(
                cand.model_copy(update={"analysis_id": analysis_id})
            )
            candidate_ids.append(cand_id)
            v_id = await self._verifier.verify(cand_id)
            verifications.append(v_id)
            v = (await self._store.get(v_id)).parse_payload(
                __import__(
                    "research_harness.research.schemas.equilibrium",
                    fromlist=["EquilibriumVerification"],
                ).EquilibriumVerification
            )
            best_status = self._better(best_status, v.status)

            # Bounded revision loop for failed candidates
            round_no = 0
            while v.status == VerificationStatus.failed and round_no < self._max_revisions:
                round_no += 1
                revisions_used += 1
                revised = await self._llm_revise(model, cand, v, exec_record)
                if not revised:
                    break
                new_cand = self._make_candidate(
                    model_id,
                    analysis_id,
                    revised,
                    "llm_proposed",
                    round_no,
                    [f"revised after verification failure (round {round_no})"],
                    "llm",
                )
                cand_id = await self._persist_candidate(new_cand)
                candidate_ids.append(cand_id)
                v_id = await self._verifier.verify(cand_id)
                verifications.append(v_id)
                v = (await self._store.get(v_id)).parse_payload(
                    __import__(
                        "research_harness.research.schemas.equilibrium",
                        fromlist=["EquilibriumVerification"],
                    ).EquilibriumVerification
                )
                best_status = self._better(best_status, v.status)

        # ------------------------------------------------------------------
        # 6. Analysis aggregate + execution
        # ------------------------------------------------------------------
        exec_record.candidates_created = len(candidate_ids)
        exec_record.verification_status = best_status
        exec_record.revisions_used = revisions_used
        if best_status == VerificationStatus.verified:
            exec_record.status = EquilibriumExecutionStatus.derived
        elif best_status == VerificationStatus.partially_verified:
            exec_record.status = EquilibriumExecutionStatus.partially_derived
        else:
            exec_record.status = EquilibriumExecutionStatus.failed
        exec_record.completed_at = datetime.now(UTC)
        exec_env = ArtifactEnvelope.create(
            payload=exec_record,
            artifact_type="equilibrium_execution",
            producer="research.equilibrium_deriver",
        )
        await self._store.put(exec_env)

        selected = await self._select_candidate(candidate_ids, verifications)
        updated = await self._update_analysis(
            analysis_id,
            candidate_ids=candidate_ids,
            verification_ids=verifications,
            selected_candidate_id=selected,
            revision_rounds=revisions_used,
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=exec_env.artifact_id,
                target_artifact_id=updated,
                producer="research.equilibrium_deriver",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=model_id,
                target_artifact_id=exec_env.artifact_id,
                producer="research.equilibrium_deriver",
            )
        )
        return exec_env.artifact_id

    # ------------------------------------------------------------------
    # Solvability gate
    # ------------------------------------------------------------------

    def _solvability_gate(self, model: FormalAnalyticalModel) -> dict[str, Any]:
        reasons: list[str] = []
        if not model.payoffs:
            reasons.append("model has zero payoffs; no optimization problems to derive")
        strategic_actors = {a.actor_id for a in model.actors if a.strategic}
        payoff_actors = {p.actor_id for p in model.payoffs}
        dec_vars = {v.symbol for v in model.variables if v.kind == SymbolKind.decision_variable}
        owned_in_payoffs: set[str] = set()
        for p in model.payoffs:
            owned_in_payoffs.update(p.decision_variables)

        # relevant strategic actors (own decisions) must have payoffs
        for v in model.variables:
            if v.kind == SymbolKind.decision_variable and v.owner_actor_id:
                if v.owner_actor_id in strategic_actors and v.owner_actor_id not in payoff_actors:
                    reasons.append(
                        f"strategic actor {v.owner_actor_id!r} owns decision variable "
                        f"{v.symbol!r} but has no payoff function"
                    )
        # every decision variable appears in an optimization problem
        for dv in sorted(dec_vars):
            if dv not in owned_in_payoffs:
                reasons.append(
                    f"decision variable {dv!r} does not appear in any payoff/optimization problem"
                )
        # timing defines the solution order
        if not model.timing:
            reasons.append("model has no timing stages; solution order undefined")
        stage_nums = [t.stage_number for t in model.timing]
        if stage_nums != list(range(len(model.timing))):
            reasons.append(f"timing not sequential: {stage_nums}")
        decision_actors = {
            v.owner_actor_id
            for v in model.variables
            if v.kind == SymbolKind.decision_variable and v.owner_actor_id
        }
        stage_actor_ids = {aid for t in model.timing for aid in t.actor_ids}
        for actor in sorted(decision_actors):
            if actor not in stage_actor_ids:
                reasons.append(f"decision maker {actor!r} appears in no timing stage")
        # expressions must parse
        try:
            table: dict[str, Any] = {v.symbol: v for v in model.variables}
            table.update({p.symbol: p for p in model.parameters})
            for p in model.payoffs:
                parse_sympy(p.expression.expression, table)
        except Exception as e:  # noqa: BLE001
            reasons.append(f"payoff expressions not SymPy-compatible: {e}")
        return {"ok": not reasons, "reasons": reasons}

    # ------------------------------------------------------------------
    # Solution plan
    # ------------------------------------------------------------------

    def _solution_plan(
        self, model: FormalAnalyticalModel, foc_data: list[tuple[str, str, Any, Any]]
    ) -> dict[str, Any]:
        """Group decision makers by their timing stage (shared with the verifier)."""
        return decision_stage_plan(model, [(a, dv) for a, dv, _, _ in foc_data])

    # ------------------------------------------------------------------
    # Symbolic candidate derivation
    # ------------------------------------------------------------------

    def _derive_candidate_sympy(
        self,
        model: FormalAnalyticalModel,
        payoff_syms: dict[str, Any],
        foc_data: list[tuple[str, str, Any, Any]],
        plan: dict[str, Any],
    ) -> tuple[list[tuple[str, Any, list[str]]], str]:
        """Returns ([(variable, sympy_expr, conditions)], method_label).

        Raises when not derivable symbolically.
        """
        import sympy

        table: dict[str, Any] = {v.symbol: v for v in model.variables}
        table.update({p.symbol: p for p in model.parameters})
        foc_by: dict[tuple[str, str], Any] = {(a, dv): foc for a, dv, foc, _ in foc_data}

        if not plan["sequential"]:
            # simultaneous: solve the FOC system
            dvs = sorted({dv for _, dv, _, _ in foc_data})
            system = [foc_by[(a, dv)] for a, dv, _, _ in foc_data]
            sols = sympy.solve(system, dvs, dict=True)
            if not sols:
                raise ValueError("simultaneous FOC system has no closed-form solution")
            out = []
            for sol in sols:
                for dv in dvs:
                    e = sol.get(sympy.Symbol(dv))
                    if e is not None:
                        out.append((dv, sympy.simplify(e), self._conditions_of(e)))
            return out, SolutionMethod.simultaneous.value

        # sequential: backward induction by stage groups (last stage first)
        payoff_syms = dict(payoff_syms)
        subs: dict[str, Any] = {}
        recorded: dict[str, Any] = {}
        for stage in reversed(plan["stages"]):
            actors = plan["stage_groups"][stage]
            dvs = sorted({dv for a, dv, _, _ in foc_data if a in actors})
            if not dvs:
                continue
            system = [foc_by[(a, dv)] for a in actors for dv in dvs if (a, dv) in foc_by]
            if not system:
                raise ValueError(f"backward induction at stage {stage}: no FOCs")
            sols = sympy.solve(system, dvs, dict=True)
            if not sols:
                raise ValueError(
                    f"backward induction fails at stage {stage}: FOC system unsolvable"
                )
            sol = sols[0]
            for dv in dvs:
                e = sol.get(sympy.Symbol(dv))
                if e is not None:
                    recorded[dv] = e
                    subs[dv] = e
            payoff_syms = {a: p.subs(subs) for a, p in payoff_syms.items()}
            # refresh FOCs for earlier stages after substitution
            for key in list(foc_by):
                a, dv = key
                foc_by[key] = sympy.diff(payoff_syms[a], sympy.Symbol(dv))

        # resolve recorded expressions into closed forms
        out = []
        for dv, expr in recorded.items():
            e = expr
            for d2, ex2 in recorded.items():
                if d2 != dv:
                    e = e.subs(d2, ex2)
            e = sympy.simplify(e)
            out.append((dv, e, self._conditions_of(e)))
        return out, SolutionMethod.backward_induction.value

    def _conditions_of(self, expr: Any) -> list[str]:
        import sympy

        conds: list[str] = []
        _, den = sympy.fraction(sympy.together(expr))
        if den.free_symbols:
            conds.append(f"{den} != 0")
        return conds

    # ------------------------------------------------------------------
    # LLM proposal / revision (bounded)
    # ------------------------------------------------------------------

    def _build_proposal_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "expressions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "variable": {"type": "string"},
                            "expression": {"type": "string"},
                            "symbols_used": {"type": "array", "items": {"type": "string"}},
                            "latex": {"type": "string"},
                            "conditions": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["variable", "expression", "symbols_used"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["expressions"],
            "additionalProperties": False,
        }

    async def _llm_propose(
        self,
        model: FormalAnalyticalModel,
        foc_data: list[tuple[str, str, Any, Any]],
        plan: dict[str, Any],
        exec_record: EquilibriumExecution,
    ) -> list[tuple[str, str, list[str]]] | None:
        if exec_record.counts.get("model_calls", 0) >= self._max_llm_calls:
            return None
        prompt = self._build_proposal_prompt(model, foc_data, plan, revise=False)
        result = await self._llm_candidate_call(prompt)
        if result is None:
            return None
        exec_record.counts["model_calls"] = exec_record.counts.get("model_calls", 0) + 1
        return result

    async def _llm_revise(
        self,
        model: FormalAnalyticalModel,
        candidate: EquilibriumCandidate,
        verification: Any,
        exec_record: EquilibriumExecution,
    ) -> list[tuple[str, str, list[str]]] | None:
        if exec_record.counts.get("model_calls", 0) >= self._max_llm_calls:
            return None
        prompt = self._build_revision_prompt(model, candidate, verification)
        result = await self._llm_candidate_call(prompt)
        if result is None:
            return None
        exec_record.counts["model_calls"] = exec_record.counts.get("model_calls", 0) + 1
        return result

    async def _llm_candidate_call(self, prompt: str) -> list[tuple[str, str, list[str]]] | None:
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You propose candidate equilibrium expressions for a game. "
                        "Return valid JSON matching the schema. Never include chain-of-thought."
                    ),
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self._build_proposal_schema(),
            temperature=0.0,
        )
        try:
            response = await self._router.complete(self._model_role, request)
            data = json.loads(response.message.content or "")
            parsed = _CandidateProposal.model_validate(data)
        except Exception as e:  # noqa: BLE001
            logger.warning("LLM candidate proposal failed: %s", e)
            return None
        out: list[tuple[str, str, list[str]]] = []
        for item in parsed.expressions:
            out.append(
                (
                    item.variable,
                    item.expression,
                    list(item.conditions),
                )
            )
        return out

    def _make_candidate(
        self,
        model_id: str,
        analysis_id: str,
        exprs: list[tuple[str, str, list[str]]],
        method: str,
        revision_round: int,
        revision_notes: list[str],
        proposed_by: str,
    ) -> EquilibriumCandidate:
        return EquilibriumCandidate(
            model_id=model_id,
            analysis_id=analysis_id or None,
            expressions=[
                EquilibriumExpression(
                    variable=var,
                    expression=Expression(
                        expression=str(expr),
                        symbols_used=sorted(self._free_symbols(expr)),
                    ),
                    conditions=conds,
                    solution_method=method,
                )
                for var, expr, conds in exprs
            ],
            decision_variables=[var for var, _, _ in exprs],
            solution_method=method,
            proposed_by=proposed_by,
            verification_status=VerificationStatus.pending,
            revision_round=revision_round,
            revision_notes=list(revision_notes),
            metadata={"model_role": self._model_role},
        )

    def _free_symbols(self, expr: str) -> set[str]:
        try:
            import sympy

            return {str(s) for s in sympy.sympify(expr).free_symbols}
        except Exception:  # noqa: BLE001
            return set()

    async def _persist_candidate(self, candidate: EquilibriumCandidate) -> str:
        c_env = ArtifactEnvelope.create(
            payload=candidate,
            artifact_type="equilibrium_candidate",
            producer=f"research.equilibrium_deriver:{self._model_role}",
        )
        await self._store.put(c_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=candidate.model_id,
                target_artifact_id=c_env.artifact_id,
                producer="research.equilibrium_deriver",
            )
        )
        return c_env.artifact_id

    # ------------------------------------------------------------------
    # Analysis aggregate
    # ------------------------------------------------------------------

    async def _create_analysis(self, model_id: str, plan: dict[str, Any], method_label: str) -> str:
        analysis = EquilibriumAnalysis(
            model_id=model_id,
            status=EquilibriumAnalysisStatus.failed,
            solution_order=list(plan["solution_order"]),
            solution_method=method_label,
            summary="Derivation in progress",
        )
        a_env = ArtifactEnvelope.create(
            payload=analysis,
            artifact_type="equilibrium_analysis",
            producer="research.equilibrium_deriver",
        )
        await self._store.put(a_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=model_id,
                target_artifact_id=a_env.artifact_id,
                producer="research.equilibrium_deriver",
            )
        )
        return a_env.artifact_id

    async def _update_analysis(
        self,
        analysis_id: str,
        candidate_ids: list[str],
        verification_ids: list[str],
        selected_candidate_id: str | None,
        revision_rounds: int,
    ) -> str:
        from research_harness.research.schemas.equilibrium import (
            EquilibriumAnalysis as EA,
        )
        from research_harness.research.schemas.equilibrium import (
            EquilibriumAnalysisStatus,
            EquilibriumVerification,
        )

        prev = (await self._store.get(analysis_id)).parse_payload(EA)
        status = EquilibriumAnalysisStatus.failed
        if selected_candidate_id is not None:
            best = VerificationStatus.pending
            for vid in verification_ids:
                v = (await self._store.get(vid)).parse_payload(EquilibriumVerification)
                best = self._better(best, v.status)
            status = (
                EquilibriumAnalysisStatus.derived
                if best == VerificationStatus.verified
                else EquilibriumAnalysisStatus.partially_derived
            )
        updated = prev.model_copy(
            update={
                "candidate_ids": list(candidate_ids),
                "verification_ids": list(verification_ids),
                "selected_candidate_id": selected_candidate_id,
                "status": status,
                "revision_rounds": revision_rounds,
                "summary": (
                    f"Derived {len(candidate_ids)} candidate(s); "
                    f"{len(verification_ids)} verification(s); "
                    f"selected {selected_candidate_id[:8] if selected_candidate_id else 'none'}"
                ),
            }
        )
        new_env = ArtifactEnvelope.create(
            payload=updated,
            artifact_type="equilibrium_analysis",
            producer="research.equilibrium_deriver",
        )
        await self._store.put(new_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.supersedes,
                source_artifact_id=analysis_id,
                target_artifact_id=new_env.artifact_id,
                producer="research.equilibrium_deriver",
            )
        )
        return new_env.artifact_id

    async def _select_candidate(
        self, candidate_ids: list[str], verification_ids: list[str]
    ) -> str | None:
        if not candidate_ids:
            return None
        from research_harness.research.schemas.equilibrium import EquilibriumVerification

        best_id: str | None = None
        best_rank = -1
        rank = {
            VerificationStatus.verified: 3,
            VerificationStatus.partially_verified: 2,
            VerificationStatus.failed: 1,
            VerificationStatus.pending: 0,
        }
        for i, cid in enumerate(candidate_ids):
            status = VerificationStatus.pending
            if i < len(verification_ids):
                v = (await self._store.get(verification_ids[i])).parse_payload(
                    EquilibriumVerification
                )
                status = v.status
            if rank[status] > best_rank:
                best_rank = rank[status]
                best_id = cid
        return best_id

    def _better(self, a: VerificationStatus, b: VerificationStatus) -> VerificationStatus:
        rank = {
            VerificationStatus.verified: 3,
            VerificationStatus.partially_verified: 2,
            VerificationStatus.failed: 1,
            VerificationStatus.pending: 0,
        }
        return a if rank[a] >= rank[b] else b

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    def _build_proposal_prompt(
        self,
        model: FormalAnalyticalModel,
        foc_data: list[tuple[str, str, Any, Any]],
        plan: dict[str, Any],
        revise: bool,
    ) -> str:
        foc_lines = "\n".join(f"  {a}: d(payoff)/d({dv}) = {foc}" for a, dv, foc, _ in foc_data)
        pay_lines = "\n".join(
            f"  {p.actor_id}: {p.expression.expression}  (decisions {p.decision_variables})"
            for p in model.payoffs
        )
        timing_lines = "\n".join(
            f"  Stage {t.stage_number}: {t.name} — actors {t.actor_ids}" for t in model.timing
        )
        method = (
            "backward induction (solve last stage first, substitute backward)"
            if plan["sequential"]
            else "simultaneous (solve the FOC system jointly)"
        )
        order = " -> ".join(plan["solution_order"]) or "(none)"
        return f"""Propose a candidate equilibrium for the following game.

Timing:
{timing_lines}

Solution order (honoring timing): {order}
Derivation method: {method}

Payoffs:
{pay_lines}

First-order conditions (already computed symbolically):
{foc_lines}

Propose expressions for EVERY decision variable of the model: {", ".join(sorted({dv for _, dv, _, _ in foc_data}))}

Rules:
- Only use symbols declared in the model; list them in symbols_used.
- Expressions must be SymPy-compatible arithmetic (no if/else, no conditionals).
- Record any parameter restrictions / interiority / positivity / denominator != 0
  conditions explicitly in conditions.
- Return valid JSON only, no chain-of-thought.
"""

    def _build_revision_prompt(
        self,
        model: FormalAnalyticalModel,
        candidate: EquilibriumCandidate,
        verification: Any,
    ) -> str:
        cand_lines = "\n".join(
            f"  {e.variable} = {e.expression.expression}  (conditions: {e.conditions or '-'})"
            for e in candidate.expressions
        )
        fail_lines = "\n".join(
            f"  [{c.check_type.value}] passed={c.passed}: {c.detail}"
            + (f"  symbolic: {c.symbolic_detail}" if c.symbolic_detail else "")
            for c in verification.checks
            if not c.passed
        )
        return f"""The following equilibrium candidate FAILED symbolic verification.

Model: {model.title}
Candidate:
{cand_lines}

Failed checks:
{fail_lines}

Propose a corrected equilibrium. Correct the algebraic expressions so that
substituting them into the first-order conditions yields residual 0.
Keep the same variable set. Record conditions explicitly. Expressions must be
SymPy-compatible. Return valid JSON only, no chain-of-thought.
"""


class EquilibriumDeriverPlugin(Plugin):
    def __init__(self, model_role: str | None = None) -> None:
        self._model_role_override = model_role
        self._service: EquilibriumDeriverService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.equilibrium_deriver",
            version="0.1.0",
            plugin_type="research",
            description="Equilibrium derivation + symbolic verification (Phase 3C)",
            provides=["equilibrium_deriver.default"],
            requires=[
                "model_router.default",
                "artifact_store.default",
                "equilibrium_verifier.default",
            ],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        research_cfg: dict[str, Any] = {}
        if "research" in cfg and isinstance(cfg["research"], dict):
            research_cfg = (
                cfg["research"].get("equilibrium", {})
                if isinstance(cfg["research"].get("equilibrium"), dict)
                else {}
            )
        model_role = (
            self._model_role_override
            or research_cfg.get("deriver_role")
            or research_cfg.get("model_role")
            or "reasoning"
        )
        revision_role = research_cfg.get("revision_role") or "reasoning"
        max_revisions = int(research_cfg.get("max_revisions", 2))
        max_llm_calls = int(research_cfg.get("max_llm_calls", 10))

        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        verifier = ctx.require("equilibrium_verifier.default")
        self._service = EquilibriumDeriverService(
            model_router=router,
            artifact_store=store,
            verifier=verifier,
            model_role=str(model_role),
            revision_role=str(revision_role),
            max_revisions=max_revisions,
            max_llm_calls=max_llm_calls,
            events=ctx.events,
        )
        ctx.register("equilibrium_deriver.default", self._service)
