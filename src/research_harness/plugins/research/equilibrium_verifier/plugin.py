"""Phase 3C equilibrium verifier — deterministic symbolic verification.

All mathematical checks are performed with SymPy: FOC residual substitution,
second-order conditions, best-response consistency, timing order, symbol
validation. No LLM involvement. Distinguishes verified / partially_verified
/ failed and never marks an LLM-proposed candidate as verified without the
symbolic checks passing.
"""

from __future__ import annotations

import logging
from typing import Any

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.equilibrium import (
    CheckType,
    EquilibriumCandidate,
    EquilibriumVerification,
    VerificationCheck,
    VerificationStatus,
)
from research_harness.research.schemas.model import (
    FormalAnalyticalModel,
    SymbolKind,
)
from research_harness.research.symbolic import (
    game_consistent_focs,
    game_consistent_payoffs,
    parse_sympy,
)

logger = logging.getLogger(__name__)


class EquilibriumVerifierService:
    def __init__(self, artifact_store: Any) -> None:
        self._store = artifact_store

    @property
    def verifier_id(self) -> str:
        return "research.equilibrium_verifier"

    async def verify(self, candidate_id: str) -> str:
        """Verify a candidate deterministically. Returns the verification id."""
        # Idempotency: reuse existing verification of this candidate
        existing = await self._store.list(artifact_type="equilibrium_verification")
        for env in existing:
            try:
                v = EquilibriumVerification.model_validate(env.payload)
                if v.candidate_id == candidate_id:
                    return env.artifact_id
            except Exception:
                continue

        c_env = await self._store.get(candidate_id)
        candidate = c_env.parse_payload(EquilibriumCandidate)
        m_env = await self._store.get(candidate.model_id)
        model = m_env.parse_payload(FormalAnalyticalModel)

        table = _symbol_table(model)
        checks, conditions, notes = self._run_checks(candidate, model, table)
        hard_failed = any(
            not c.passed
            and c.check_type
            in (
                CheckType.symbol_validation,
                CheckType.foc_residual,
                CheckType.best_response_consistency,
                CheckType.timing_order,
            )
            for c in checks
        )
        soft_unpassed = any(
            not c.passed
            and c.check_type in (CheckType.second_order_condition, CheckType.constraint_domain)
            for c in checks
        )
        if hard_failed:
            status = VerificationStatus.failed
        elif soft_unpassed:
            status = VerificationStatus.partially_verified
        else:
            status = VerificationStatus.verified

        verification = EquilibriumVerification(
            model_id=candidate.model_id,
            candidate_id=candidate_id,
            status=status,
            checks=checks,
            conditions_required=conditions,
            verification_method="symbolic",
            notes=notes,
            metadata={"revision_round": candidate.revision_round},
        )
        v_env = ArtifactEnvelope.create(
            payload=verification,
            artifact_type="equilibrium_verification",
            producer="research.equilibrium_verifier",
        )
        await self._store.put(v_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=candidate_id,
                target_artifact_id=v_env.artifact_id,
                producer="research.equilibrium_verifier",
            )
        )
        return v_env.artifact_id

    # ------------------------------------------------------------------
    # Deterministic checks
    # ------------------------------------------------------------------

    def _run_checks(
        self,
        candidate: EquilibriumCandidate,
        model: FormalAnalyticalModel,
        table: dict[str, Any],
    ) -> tuple[list[VerificationCheck], list[str], list[str]]:
        checks: list[VerificationCheck] = []
        conditions: list[str] = []
        notes: list[str] = []
        import sympy

        candidate_map: dict[str, Any] = {}
        for e in candidate.expressions:
            candidate_map[e.variable] = parse_sympy(e.expression.expression, table)
        candidate_syms = {e.variable for e in candidate.expressions}
        model_decisions = {
            v.symbol for v in model.variables if v.kind == SymbolKind.decision_variable
        }
        missing = sorted(model_decisions - candidate_syms)
        if missing:
            checks.append(
                VerificationCheck(
                    check_type=CheckType.symbol_validation,
                    passed=False,
                    detail=f"candidate lacks expressions for decision variables {missing}",
                )
            )
            return checks, conditions, notes
        extra = sorted(candidate_syms - model_decisions)
        if extra:
            checks.append(
                VerificationCheck(
                    check_type=CheckType.symbol_validation,
                    passed=False,
                    detail=f"candidate defines non-decision variables {extra}",
                )
            )
            return checks, conditions, notes

        # 1. Symbol validation: every expression symbol defined in the table
        table_syms = set(table)
        bad = []
        for e in candidate.expressions:
            free = {str(s) for s in parse_sympy(e.expression.expression, table).free_symbols}
            undefined = free - table_syms
            if undefined:
                bad.append(f"{e.variable}: {sorted(undefined)}")
        if bad:
            checks.append(
                VerificationCheck(
                    check_type=CheckType.symbol_validation,
                    passed=False,
                    detail="candidate uses undefined symbols: " + "; ".join(bad),
                )
            )
            return checks, conditions, notes
        checks.append(
            VerificationCheck(
                check_type=CheckType.symbol_validation,
                passed=True,
                detail=f"all candidate symbols defined ({len(table_syms)} symbols)",
            )
        )

        # 2. FOC residuals: substitute candidate into each actor's FOC.
        #    FOCs are game-consistent: earlier movers' FOCs are derived after
        #    substituting later movers' best responses (backward induction),
        #    matching the derivation's solution concept.
        foc_exprs: list[tuple[str, str, Any]] = game_consistent_focs(model)
        game_payoffs: dict[str, Any] = game_consistent_payoffs(model)
        foc_ok = True
        foc_evaluated = 0
        for actor, dv, foc in foc_exprs:
            foc_evaluated += 1
            residual = sympy.simplify(sympy.cancel(foc.subs(candidate_map)))  # type: ignore[arg-type]
            if residual == 0:
                checks.append(
                    VerificationCheck(
                        check_type=CheckType.foc_residual,
                        passed=True,
                        detail=f"FOC residual of {actor} w.r.t. {dv} simplifies to zero",
                        symbolic_detail=str(residual),
                    )
                )
            else:
                foc_ok = False
                checks.append(
                    VerificationCheck(
                        check_type=CheckType.foc_residual,
                        passed=False,
                        detail=f"FOC residual of {actor} w.r.t. {dv} does not vanish",
                        symbolic_detail=str(residual),
                    )
                )
        # Mandatory check inventory: an absent check must never be worth a
        # pass. ``foc_exprs`` can legitimately be empty (a payoff that declares
        # no decision variables, or a model whose declared decision variables
        # are never attached to an actor), and in that case the loop above
        # records nothing — which previously left ``hard_failed`` false and the
        # candidate ``verified`` despite no first-order condition ever being
        # evaluated. Record the gap as a failed check instead.
        decision_pairs = [
            (p.actor_id, dv) for p in model.payoffs for dv in p.decision_variables
        ]
        expected_focs = len(decision_pairs) if decision_pairs else len(model_decisions)
        if expected_focs and foc_evaluated < expected_focs:
            foc_ok = False
            checks.append(
                VerificationCheck(
                    check_type=CheckType.foc_residual,
                    passed=False,
                    detail=(
                        f"only {foc_evaluated} of {expected_focs} expected first-order "
                        f"condition(s) could be evaluated; the remainder were not "
                        f"checked, so the equilibrium is unverified"
                    ),
                )
            )

        if not foc_ok:
            return checks, conditions, notes

        # 3. Second-order conditions (interior maximization: concave payoff)
        for actor, dv, _foc in foc_exprs:
            payoff_sym = game_payoffs[actor]
            dv_sym = sympy.Symbol(dv)
            soc = sympy.simplify(
                sympy.cancel(sympy.diff(payoff_sym, dv_sym, 2).subs(candidate_map))  # type: ignore[arg-type]
            )
            if soc.free_symbols:
                conditions.append(f"{soc} < 0")
                checks.append(
                    VerificationCheck(
                        check_type=CheckType.second_order_condition,
                        passed=False,
                        detail=(
                            f"SOC of {actor} w.r.t. {dv} is symbolic "
                            f"({soc}); required condition recorded, not globally signable"
                        ),
                        symbolic_detail=str(soc),
                    )
                )
            elif sympy.ask(sympy.Q.negative(soc)) is True:
                checks.append(
                    VerificationCheck(
                        check_type=CheckType.second_order_condition,
                        passed=True,
                        detail=f"SOC of {actor} w.r.t. {dv} is {soc} < 0",
                        symbolic_detail=str(soc),
                    )
                )
            else:
                conditions.append(f"{soc} < 0")
                checks.append(
                    VerificationCheck(
                        check_type=CheckType.second_order_condition,
                        passed=False,
                        detail=(
                            f"SOC of {actor} w.r.t. {dv} is {soc}, not "
                            f"negative; local optimum unverified"
                        ),
                        symbolic_detail=str(soc),
                    )
                )

        # 4. Best-response consistency: candidate variable equals its closed-form
        #    best response with other candidates substituted, where a closed form
        #    exists (solve FOC for own variable).
        for actor, dv, foc in foc_exprs:
            dv_sym = sympy.Symbol(dv)
            try:
                br_sols = sympy.solve(foc, dv_sym)
            except Exception:  # noqa: BLE001
                continue
            if len(br_sols) != 1:
                continue
            br = br_sols[0]
            br_sub = sympy.simplify(
                sympy.cancel(br.subs({k: v for k, v in candidate_map.items() if k != dv}))
            )
            diff = sympy.simplify(sympy.cancel(candidate_map[dv] - br_sub))
            if diff == 0:
                checks.append(
                    VerificationCheck(
                        check_type=CheckType.best_response_consistency,
                        passed=True,
                        detail=f"best response of {actor} ({dv}) consistent with candidate",
                        symbolic_detail=str(diff),
                    )
                )
            else:
                checks.append(
                    VerificationCheck(
                        check_type=CheckType.best_response_consistency,
                        passed=False,
                        detail=(
                            f"best response of {actor} ({dv}) differs from candidate by {diff}"
                        ),
                        symbolic_detail=str(diff),
                    )
                )

        # 5. Timing order: for sequential solutions, stage-k expressions must
        #    depend only on variables decided at earlier stages + parameters.
        if candidate.solution_method in (
            "backward_induction",
            "sequential",
        ):
            stage_of: dict[str, int] = {}
            for t in model.timing:
                for aid in t.actor_ids:
                    for payoff in model.payoffs:
                        if payoff.actor_id == aid:
                            for dv in payoff.decision_variables:
                                stage_of[dv] = t.stage_number
            for e in candidate.expressions:
                if e.variable not in stage_of:
                    continue
                stage = stage_of[e.variable]
                free = {str(s) for s in parse_sympy(e.expression.expression, table).free_symbols}
                allowed = {s for s, st in stage_of.items() if st < stage} | (
                    table_syms - set(stage_of)
                )
                forbidden = sorted(free - allowed)
                if forbidden:
                    checks.append(
                        VerificationCheck(
                            check_type=CheckType.timing_order,
                            passed=False,
                            detail=(
                                f"sequential candidate for {e.variable} (stage {stage}) "
                                f"depends on later-stage variables {forbidden}"
                            ),
                        )
                    )
                else:
                    checks.append(
                        VerificationCheck(
                            check_type=CheckType.timing_order,
                            passed=True,
                            detail=f"candidate for {e.variable} respects timing order",
                        )
                    )

        # 6. Constraint/domain conditions: non-constant denominators != 0
        for e in candidate.expressions:
            parsed = parse_sympy(e.expression.expression, table)
            _, den = sympy.fraction(sympy.together(parsed))
            den_free = den.free_symbols - candidate_syms
            if den_free:
                cond = f"{den} != 0"
                if cond not in conditions:
                    conditions.append(cond)
                checks.append(
                    VerificationCheck(
                        check_type=CheckType.constraint_domain,
                        passed=False,
                        detail=f"candidate for {e.variable} has non-constant denominator {den}; condition recorded",
                        symbolic_detail=str(den),
                    )
                )
        return checks, conditions, notes


def _symbol_table(model: FormalAnalyticalModel) -> dict[str, Any]:
    table: dict[str, Any] = {}
    for v in model.variables:
        table[v.symbol] = v
    for p in model.parameters:
        table[p.symbol] = p
    return table


class EquilibriumVerifierPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.equilibrium_verifier",
            version="0.1.0",
            plugin_type="research",
            description="Deterministic symbolic equilibrium verification (Phase 3C)",
            provides=["equilibrium_verifier.default"],
            requires=["artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        store = ctx.require("artifact_store.default")
        self._service = EquilibriumVerifierService(artifact_store=store)
        ctx.register("equilibrium_verifier.default", self._service)
