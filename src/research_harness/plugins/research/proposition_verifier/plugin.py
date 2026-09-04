"""Phase 3D proposition verifier — deterministic symbolic verification.

Checks derivative sign against the verified comparative static, algebraic
equality claims, required parameter conditions, and consistency with the
supporting equilibrium/statics. No LLM involvement: an LLM-generated
proposition is never marked verified without these symbolic checks passing.
"""

from __future__ import annotations

import logging
from typing import Any

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.proposition import (
    ComparativeStatic,
    Proposition,
    PropositionCheck,
    PropositionCheckType,
    PropositionClaimType,
    PropositionVerification,
    PropositionVerificationStatus,
    StaticSign,
)
from research_harness.research.symbolic import parse_sympy, safe_sympify

logger = logging.getLogger(__name__)


class PropositionVerifierService:
    def __init__(self, artifact_store: Any) -> None:
        self._store = artifact_store

    @property
    def service_id(self) -> str:
        return "research.proposition_verifier"

    async def verify(self, proposition_id: str) -> str:
        """Verify a proposition deterministically. Returns the verification id."""
        # Idempotency: reuse existing verification of this proposition
        existing = await self._store.list(artifact_type="proposition_verification")
        for env in existing:
            try:
                v = PropositionVerification.model_validate(env.payload)
                if v.proposition_id == proposition_id:
                    return env.artifact_id
            except Exception:
                continue

        p_env = await self._store.get(proposition_id)
        prop = p_env.parse_payload(Proposition)
        candidate_map: dict[str, Any] = {}
        candidate_error: str | None = None
        try:
            if not prop.equilibrium_candidate_id:
                raise ValueError("proposition references no equilibrium candidate")
            cand_env = await self._store.get(prop.equilibrium_candidate_id)
            cand = cand_env.parse_payload(
                __import__(
                    "research_harness.research.schemas.equilibrium",
                    fromlist=["EquilibriumCandidate"],
                ).EquilibriumCandidate
            )
            for e in cand.expressions:
                candidate_map[e.variable] = safe_sympify(
                    e.expression.expression, auto_symbols=True
                )
        except Exception as e:  # noqa: BLE001
            # H6: this used to leave candidate_map = {} and verify anyway. With
            # no substitutions, an equality claim like q1 = q2 is checked as a
            # *symbolic* identity -- if it simplifies to 0 the proposition is
            # reported as verified, on the strength of a candidate we could not
            # even load. Record the failure instead of verifying vacuously.
            candidate_error = (
                f"equilibrium candidate {prop.equilibrium_candidate_id!r} could not be "
                f"loaded: {e}"
            )
            logger.error("proposition %s: %s", proposition_id, candidate_error)

        if candidate_error is not None:
            return await self._store_failed_verification(
                proposition_id=proposition_id,
                model_id=prop.model_id,
                reason=candidate_error,
            )
        checks, conditions, conditional = await self._run_checks(prop, candidate_map)

        hard_failed = any(
            not c.passed
            and c.check_type
            in (
                PropositionCheckType.equilibrium_consistency,
                PropositionCheckType.derivative_sign,
                PropositionCheckType.algebraic_relation,
                PropositionCheckType.symbol_validation,
                PropositionCheckType.condition_requirement,
            )
            for c in checks
        )
        if hard_failed:
            status = PropositionVerificationStatus.failed
        elif conditional:
            status = PropositionVerificationStatus.conditionally_verified
        else:
            status = PropositionVerificationStatus.verified

        verification = PropositionVerification(
            proposition_id=proposition_id,
            model_id=prop.model_id,
            status=status,
            checks=checks,
            conditions_required=conditions,
            verification_method="symbolic",
        )
        v_env = ArtifactEnvelope.create(
            payload=verification,
            artifact_type="proposition_verification",
            producer="research.proposition_verifier",
        )
        await self._store.put(v_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=proposition_id,
                target_artifact_id=v_env.artifact_id,
                producer="research.proposition_verifier",
            )
        )
        return v_env.artifact_id

    # ------------------------------------------------------------------
    # Deterministic checks
    # ------------------------------------------------------------------

    async def _store_failed_verification(
        self, *, proposition_id: str, model_id: str, reason: str
    ) -> str:
        """Persist a `failed` verification that explains why it could not run.

        Used when the proposition cannot be checked at all (H6). Contained
        rather than raised so one unloadable candidate does not abort the whole
        generation batch, but it is never reported as verified.
        """
        check = PropositionCheck(
            check_type=PropositionCheckType.equilibrium_consistency,
            passed=False,
            detail=reason,
        )
        verification = PropositionVerification(
            proposition_id=proposition_id,
            model_id=model_id,
            status=PropositionVerificationStatus.failed,
            checks=[check],
            conditions_required=[],
            verification_method="symbolic",
            metadata={"not_verifiable_reason": reason},
        )
        v_env = ArtifactEnvelope.create(
            payload=verification,
            artifact_type="proposition_verification",
            producer="research.proposition_verifier",
        )
        await self._store.put(v_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=proposition_id,
                target_artifact_id=v_env.artifact_id,
                producer="research.proposition_verifier",
            )
        )
        return v_env.artifact_id

    def _sign_implied_by_conditions(
        self,
        derivative_expression: str,
        conditions: list[str],
        table: dict[str, Any],
        expected: str,
    ) -> tuple[bool | None, str]:
        """Ask whether the declared conditions actually imply the claimed sign.

        H7: an `ambiguous` static plus any non-empty condition string used to be
        recorded as a passing check, with a detail line asserting the sign was
        "consistent with the declared conditions" -- an assertion nothing
        verified. This asks SymPy under those assumptions.

        Returns (True | False | None, explanation). None means "could not be
        determined", which is a legitimate outcome for SymPy's assumption
        system and must not be treated as success.
        """
        import sympy

        try:
            expr = parse_sympy(derivative_expression, table)
        except Exception as e:  # noqa: BLE001
            return None, f"derivative not parseable: {e}"

        assumptions = []
        for cond in conditions:
            try:
                # Conditions are relations ("a > 0", "2*b != 0"), which
                # `parse_sympy` rejects by design -- comparisons are only
                # permitted via safe_sympify's allow_comparison. Without this
                # the assumption set is always empty and the query below can
                # never contradict anything, silently restoring the original
                # H7 behaviour.
                rel = safe_sympify(cond, table, allow_comparison=True)
            except Exception as e:  # noqa: BLE001
                return None, f"condition {cond!r} not parseable: {e}"
            if not isinstance(rel, sympy.Rel):
                return None, f"condition {cond!r} is not a relation"
            assumptions.append(rel)

        if not assumptions:
            return None, "no usable conditions"

        query = {
            "positive": sympy.Q.positive(expr),
            "negative": sympy.Q.negative(expr),
            "zero": sympy.Q.zero(expr),
        }.get(expected)
        if query is None:
            return None, f"unsupported expected sign {expected!r}"

        try:
            with sympy.assuming(*assumptions):
                result = sympy.ask(query)
        except Exception as e:  # noqa: BLE001
            return None, f"symbolic query failed: {e}"
        if result is None:
            return None, "sign could not be determined under the declared conditions"
        return bool(result), (
            f"declared conditions {'imply' if result else 'contradict'} "
            f"d(outcome)/d(parameter) is {expected}"
        )

    async def _run_checks(
        self, prop: Proposition, candidate_map: dict[str, Any] | None = None
    ) -> tuple[list[PropositionCheck], list[str], bool]:
        checks: list[PropositionCheck] = []
        conditions: list[str] = []
        import sympy

        # 1. Equilibrium consistency: supporting statics exist, reference the
        #    same candidate and (for monotonicity) the same outcome+parameter.
        statics: list[ComparativeStatic] = []
        bad_refs = []
        for sid in prop.supporting_static_ids:
            try:
                env = await self._store.get(sid)
                s = env.parse_payload(ComparativeStatic)
            except Exception:  # noqa: BLE001
                bad_refs.append(sid)
                continue
            if s.equilibrium_candidate_id != prop.equilibrium_candidate_id:
                bad_refs.append(sid)
                continue
            statics.append(s)
        if bad_refs:
            checks.append(
                PropositionCheck(
                    check_type=PropositionCheckType.equilibrium_consistency,
                    passed=False,
                    detail=(
                        f"supporting comparative statics invalid or from another "
                        f"equilibrium: {bad_refs[:3]}"
                    ),
                )
            )
            return checks, conditions, False
        if not statics and prop.claim_type == PropositionClaimType.monotonicity:
            checks.append(
                PropositionCheck(
                    check_type=PropositionCheckType.equilibrium_consistency,
                    passed=False,
                    detail="monotonicity proposition references no comparative static",
                )
            )
            return checks, conditions, False
        checks.append(
            PropositionCheck(
                check_type=PropositionCheckType.equilibrium_consistency,
                passed=True,
                detail=(
                    f"proposition grounded in {len(statics)} comparative static(s) "
                    f"of the same equilibrium candidate"
                ),
            )
        )

        # 2. Symbol validation of the proposition's math form
        if prop.mathematical_form is not None:
            try:
                if (
                    prop.claim_type == PropositionClaimType.equality
                    and "=" in (prop.mathematical_form.expression)
                    and "==" not in prop.mathematical_form.expression
                ):
                    lhs, _, rhs = prop.mathematical_form.expression.partition("=")
                    parse_sympy(lhs, {s: s for s in prop.mathematical_form.symbols_used})
                    parse_sympy(rhs, {s: s for s in prop.mathematical_form.symbols_used})
                else:
                    parse_sympy(
                        prop.mathematical_form.expression,
                        {s: s for s in prop.mathematical_form.symbols_used},
                    )
                checks.append(
                    PropositionCheck(
                        check_type=PropositionCheckType.symbol_validation,
                        passed=True,
                        detail="mathematical form parses",
                    )
                )
            except Exception as e:  # noqa: BLE001
                checks.append(
                    PropositionCheck(
                        check_type=PropositionCheckType.symbol_validation,
                        passed=False,
                        detail=f"mathematical form does not parse: {e}",
                    )
                )
                return checks, conditions, False
        else:
            checks.append(
                PropositionCheck(
                    check_type=PropositionCheckType.symbol_validation,
                    passed=True,
                    detail="no mathematical form declared; sign-based verification only",
                )
            )

        # 3. Claim-type-specific checks
        if prop.claim_type == PropositionClaimType.monotonicity:
            return self._check_monotonicity(prop, statics, checks, conditions, sympy)
        if prop.claim_type == PropositionClaimType.equality:
            return self._check_equality(prop, checks, conditions, sympy, candidate_map or {})
        checks.append(
            PropositionCheck(
                check_type=PropositionCheckType.algebraic_relation,
                passed=False,
                detail=f"claim_type {prop.claim_type.value!r} not supported by the verifier",
            )
        )
        return checks, conditions, False

    def _check_monotonicity(
        self,
        prop: Proposition,
        statics: list[ComparativeStatic],
        checks: list[PropositionCheck],
        conditions: list[str],
        sympy: Any,
        candidate_map: dict[str, Any] | None = None,
    ) -> tuple[list[PropositionCheck], list[str], bool]:
        matching = [
            s
            for s in statics
            if s.outcome_variable == prop.outcome_variable and s.parameter == prop.parameter
        ]
        if not matching:
            checks.append(
                PropositionCheck(
                    check_type=PropositionCheckType.derivative_sign,
                    passed=False,
                    detail=(
                        f"no comparative static for d{prop.outcome_variable}/"
                        f"d{prop.parameter} among supporting statics"
                    ),
                )
            )
            return checks, conditions, False
        static = matching[0]
        expected = prop.expected_sign
        if expected not in ("positive", "negative", "zero"):
            checks.append(
                PropositionCheck(
                    check_type=PropositionCheckType.derivative_sign,
                    passed=False,
                    detail=f"expected_sign must be positive|negative|zero, got {expected!r}",
                )
            )
            return checks, conditions, False

        if static.sign == StaticSign.ambiguous:
            # Sign depends on parameter restrictions: proposition MUST carry
            # explicit conditions, and is at best conditionally verified.
            if not prop.conditions:
                checks.append(
                    PropositionCheck(
                        check_type=PropositionCheckType.condition_requirement,
                        passed=False,
                        detail=(
                            "comparative static is ambiguous; proposition must "
                            "declare the conditions under which the sign holds"
                        ),
                    )
                )
                return checks, conditions, False
            conditions.extend(prop.conditions)
            # Symbol table: declared symbols map to themselves, and the
            # equilibrium candidate's variables are substituted so the
            # derivative is expressed in the model's parameters.
            table = {s: s for s in (static.derivative_expression.symbols_used or [])}
            for var, expr in (candidate_map or {}).items():
                table[var] = expr
            implied, explanation = self._sign_implied_by_conditions(
                static.derivative_expression.expression,
                list(prop.conditions),
                table,
                expected,
            )
            if implied is False:
                # The declared conditions rule the claim out -- this is a failed
                # proposition, not a conditional one.
                checks.append(
                    PropositionCheck(
                        check_type=PropositionCheckType.derivative_sign,
                        passed=False,
                        detail=(
                            f"static d{prop.outcome_variable}/d{prop.parameter} is "
                            f"ambiguous and the declared conditions contradict the "
                            f"claim: {explanation}"
                        ),
                        symbolic_detail=static.derivative_expression.expression,
                    )
                )
                return checks, conditions, False
            checks.append(
                PropositionCheck(
                    check_type=PropositionCheckType.derivative_sign,
                    passed=True,
                    detail=(
                        f"static d{prop.outcome_variable}/d{prop.parameter} is "
                        f"ambiguous; {explanation}"
                    ),
                    symbolic_detail=static.derivative_expression.expression,
                )
            )
            checks.append(
                PropositionCheck(
                    check_type=PropositionCheckType.condition_requirement,
                    passed=True,
                    detail="explicit conditions declared; preserved, not simplified",
                )
            )
            return checks, conditions, True

        if static.sign.value != expected:
            checks.append(
                PropositionCheck(
                    check_type=PropositionCheckType.derivative_sign,
                    passed=False,
                    detail=(
                        f"proposition claims d{prop.outcome_variable}/d{prop.parameter} "
                        f"{expected} but the verified comparative static is {static.sign.value}"
                    ),
                    symbolic_detail=static.derivative_expression.expression,
                )
            )
            return checks, conditions, False
        checks.append(
            PropositionCheck(
                check_type=PropositionCheckType.derivative_sign,
                passed=True,
                detail=(
                    f"proposition sign {expected} matches the verified comparative "
                    f"static d{prop.outcome_variable}/d{prop.parameter} = "
                    f"{static.derivative_expression.expression}"
                ),
                symbolic_detail=static.derivative_expression.expression,
            )
        )
        if prop.conditions:
            checks.append(
                PropositionCheck(
                    check_type=PropositionCheckType.condition_requirement,
                    passed=True,
                    detail="declared conditions compatible with the verified static",
                )
            )
        return checks, conditions, False

    def _check_equality(
        self,
        prop: Proposition,
        checks: list[PropositionCheck],
        conditions: list[str],
        sympy: Any,
        candidate_map: dict[str, Any],
    ) -> tuple[list[PropositionCheck], list[str], bool]:
        if prop.mathematical_form is None:
            checks.append(
                PropositionCheck(
                    check_type=PropositionCheckType.algebraic_relation,
                    passed=False,
                    detail="equality proposition requires a mathematical_form",
                )
            )
            return checks, conditions, False
        expr = prop.mathematical_form.expression
        if "=" in expr and "==" not in expr:
            lhs, _, rhs = expr.partition("=")
            lhs_sym = safe_sympify(lhs, auto_symbols=True).subs(candidate_map)
            rhs_sym = safe_sympify(rhs, auto_symbols=True).subs(candidate_map)
            diff = sympy.simplify(lhs_sym - rhs_sym)
            if diff == 0:
                checks.append(
                    PropositionCheck(
                        check_type=PropositionCheckType.algebraic_relation,
                        passed=True,
                        detail=(
                            f"algebraic equality holds at the equilibrium: "
                            f"{lhs.strip()} = {rhs.strip()}"
                        ),
                        symbolic_detail=str(diff),
                    )
                )
            else:
                checks.append(
                    PropositionCheck(
                        check_type=PropositionCheckType.algebraic_relation,
                        passed=False,
                        detail=(
                            f"algebraic equality does not hold at the equilibrium; "
                            f"difference = {diff}"
                        ),
                        symbolic_detail=str(diff),
                    )
                )
            return checks, conditions, False
        checks.append(
            PropositionCheck(
                check_type=PropositionCheckType.algebraic_relation,
                passed=False,
                detail="equality claim must be of the form 'lhs = rhs'",
            )
        )
        return checks, conditions, False


class PropositionVerifierPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.proposition_verifier",
            version="0.1.0",
            plugin_type="research",
            description="Deterministic symbolic proposition verification (Phase 3D)",
            provides=["proposition_verifier.default"],
            requires=["artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        store = ctx.require("artifact_store.default")
        self._service = PropositionVerifierService(artifact_store=store)
        ctx.register("proposition_verifier.default", self._service)
