"""Phase 3D comparative statics — ∂outcome/∂parameter at a verified equilibrium.

Fully deterministic (SymPy). Signs are only inferred when provable without
parameter restrictions; otherwise the sign is `ambiguous` and the conditions
are recorded explicitly. No LLM involvement.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.equilibrium import (
    EquilibriumAnalysis,
    EquilibriumCandidate,
)
from research_harness.research.schemas.model import Expression, FormalAnalyticalModel
from research_harness.research.schemas.proposition import (
    ComparativeStatic,
    ComparativeStaticsAnalysis,
    ComparativeStaticsExecution,
    StaticSign,
)
from research_harness.research.symbolic import parse_sympy

logger = logging.getLogger(__name__)


class ComparativeStaticsService:
    def __init__(
        self,
        artifact_store: Any,
        max_statics: int = 200,
        model_role: str = "reasoning",
    ) -> None:
        self._store = artifact_store
        self._max_statics = max_statics
        self._model_role = model_role

    # H9: statics/numerics must not run on an equilibrium that was never
    # verified. The CLI help promises "of a verified equilibrium", but both
    # services took analysis.selected_candidate_id without inspecting it.
    _VERIFIED_STATUSES = frozenset({"verified", "partially_verified"})

    async def _require_verified_equilibrium(
        self, analysis: Any, candidate_id: str, candidate: Any
    ) -> None:
        """Raise unless the selected candidate is verified.

        Two sources, because both occur in practice: the candidate carries its
        own ``verification_status`` (benchmark fixtures set this without
        producing a verification artifact), and a separate
        ``EquilibriumVerification`` artifact exists when the deriver actually
        ran. The artifact wins when present, since it supersedes.
        """
        from research_harness.research.schemas.equilibrium import EquilibriumVerification

        def _value(v: Any) -> str | None:
            if v is None:
                return None
            return getattr(v, "value", str(v))

        status = _value(getattr(candidate, "verification_status", None))
        for vid in list(getattr(analysis, "verification_ids", None) or []):
            try:
                v = (await self._store.get(vid)).parse_payload(EquilibriumVerification)
            except Exception:  # noqa: BLE001
                continue
            if getattr(v, "candidate_id", None) == candidate_id:
                status = _value(v.status)
                break
        if status not in self._VERIFIED_STATUSES:
            raise ValueError(
                f"equilibrium candidate {candidate_id} is not verified "
                f"(status={status!r}); refusing to run on an unverified equilibrium"
            )

    @property
    def service_id(self) -> str:
        return "research.comparative_statics"

    async def run(self, equilibrium_analysis_id: str) -> str:
        """Derive comparative statics for the selected candidate of an analysis.

        Returns the ComparativeStaticsExecution artifact id.
        """
        # Idempotency: reuse a completed successful run for same analysis + role
        existing = await self._store.list(artifact_type="comparative_statics_execution")
        for env in existing:
            try:
                ex = ComparativeStaticsExecution.model_validate(env.payload)
                if (
                    ex.model_role == self._model_role
                    and ex.completed_at is not None
                    and ex.statics_created > 0
                ):
                    a_env = await self._store.get(equilibrium_analysis_id)
                    a = a_env.parse_payload(EquilibriumAnalysis)
                    if ex.equilibrium_candidate_id == a.selected_candidate_id:
                        return env.artifact_id
            except Exception:
                continue

        a_env = await self._store.get(equilibrium_analysis_id)
        analysis = a_env.parse_payload(EquilibriumAnalysis)


        candidate_id = analysis.selected_candidate_id
        if candidate_id is None:
            raise ValueError(
                f"EquilibriumAnalysis {equilibrium_analysis_id} has no selected candidate"
            )
        c_env = await self._store.get(candidate_id)
        candidate = c_env.parse_payload(EquilibriumCandidate)
        await self._require_verified_equilibrium(analysis, candidate_id, candidate)
        m_env = await self._store.get(candidate.model_id)
        model = m_env.parse_payload(FormalAnalyticalModel)

        import sympy

        started = datetime.now(UTC)
        exec_record = ComparativeStaticsExecution(
            model_id=candidate.model_id,
            equilibrium_candidate_id=candidate_id,
            statics_created=0,
            status="derived",
            failures=[],
            counts={"max_statics": self._max_statics},
            model_role=self._model_role,
            started_at=started,
        )

        # H8: declared domains become SymPy assumptions. Without them
        # `sympy.ask(Q.positive(a))` is None for every parameter, so the
        # sign inference below could never fire and nearly every static came
        # back "ambiguous".
        table = self._symbols_with_domains(model, sympy)
        candidate_exprs: dict[str, Any] = {}
        for e in candidate.expressions:
            try:
                candidate_exprs[e.variable] = parse_sympy(e.expression.expression, table)
            except Exception as exc:  # noqa: BLE001
                exec_record.failures.append({"error": f"candidate {e.variable} unparseable: {exc}"})

        param_syms = [p.symbol for p in model.parameters]
        static_ids: list[str] = []

        for outcome, expr in candidate_exprs.items():
            for param in param_syms:
                try:
                    deriv = sympy.simplify(sympy.diff(expr, table[param]))
                except Exception as exc:  # noqa: BLE001
                    exec_record.failures.append(
                        {"error": f"derivative d{outcome}/d{param} failed: {exc}"}
                    )
                    continue
                sign, conditions = self._sign_of(deriv, param, sympy)
                static = ComparativeStatic(
                    model_id=candidate.model_id,
                    equilibrium_candidate_id=candidate_id,
                    analysis_id=None,
                    outcome_variable=outcome,
                    parameter=param,
                    derivative_expression=Expression(
                        expression=str(deriv),
                        symbols_used=sorted({str(s) for s in deriv.free_symbols}),
                    ),
                    sign=sign,
                    conditions=conditions,
                    interpretation=(
                        f"d{outcome}/d{param} = {deriv}; sign {sign.value}"
                        + (f" under: {'; '.join(conditions)}" if conditions else "")
                    ),
                    derived_by="sympy",
                )
                s_env = ArtifactEnvelope.create(
                    payload=static,
                    artifact_type="comparative_static",
                    producer="research.comparative_statics",
                )
                await self._store.put(s_env)
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=candidate_id,
                        target_artifact_id=s_env.artifact_id,
                        producer="research.comparative_statics",
                    )
                )
                static_ids.append(s_env.artifact_id)
                if len(static_ids) >= self._max_statics:
                    break
            if len(static_ids) >= self._max_statics:
                break

        exec_record.statics_created = len(static_ids)
        exec_record.completed_at = datetime.now(UTC)
        exec_env = ArtifactEnvelope.create(
            payload=exec_record,
            artifact_type="comparative_statics_execution",
            producer="research.comparative_statics",
        )
        await self._store.put(exec_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=candidate_id,
                target_artifact_id=exec_env.artifact_id,
                producer="research.comparative_statics",
            )
        )

        cs_analysis = ComparativeStaticsAnalysis(
            model_id=candidate.model_id,
            equilibrium_candidate_id=candidate_id,
            static_ids=static_ids,
            status="derived",
            summary=(
                f"Derived {len(static_ids)} comparative static(s) for "
                f"{len(candidate_exprs)} outcome(s) x {len(param_syms)} parameter(s)"
            ),
        )
        a2_env = ArtifactEnvelope.create(
            payload=cs_analysis,
            artifact_type="comparative_statics_analysis",
            producer="research.comparative_statics",
        )
        await self._store.put(a2_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=equilibrium_analysis_id,
                target_artifact_id=a2_env.artifact_id,
                producer="research.comparative_statics",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=candidate_id,
                target_artifact_id=a2_env.artifact_id,
                producer="research.comparative_statics",
            )
        )
        return exec_env.artifact_id

    def _symbols_with_domains(self, model: Any, sympy: Any) -> dict[str, Any]:
        """Build a symbol table whose symbols carry the declared domain assumptions.

        H8: `sympy.ask(Q.positive(a))` only succeeds if `a` was created with
        `positive=True`. The declared domain (`R_+`, `R_-`, `R`, ...) was never
        translated into assumptions, so the sign inference below was dead code.
        """
        table: dict[str, Any] = {}
        for obj in list(getattr(model, "variables", None) or []) + list(
            getattr(model, "parameters", None) or []
        ):
            symbol = str(getattr(obj, "symbol", "") or "")
            if not symbol:
                continue
            domain = str(getattr(obj, "domain", "R") or "R").strip()
            if domain in ("R_+", "R+", "> 0"):
                table[symbol] = sympy.Symbol(symbol, positive=True)
            elif domain in ("R_-", "R-", "< 0"):
                table[symbol] = sympy.Symbol(symbol, negative=True)
            else:
                # Default to real: sign queries are meaningless over complexes,
                # and an unknown domain must not silently imply positivity.
                table[symbol] = sympy.Symbol(symbol, real=True)
        return table

    def _factor_sign(self, expr: Any, sympy: Any) -> int | None:
        """Sign of a product: +1/-1/0, or None when it cannot be determined.

        Handles provably-negative factors explicitly. The previous code only
        recognised *positive* symbolic factors, so a negative factor simply
        fell through to "ambiguous".
        """
        if not expr.free_symbols:
            if expr.is_number:
                if expr > 0:
                    return 1
                if expr < 0:
                    return -1
                return 0
            return None
        sign = 1
        for f in sympy.Mul.make_args(expr):
            if not f.free_symbols:
                if f.is_number:
                    if f > 0:
                        continue
                    if f < 0:
                        sign = -sign
                        continue
                    return 0
                continue
            if sympy.ask(sympy.Q.positive(f)) is True:
                continue
            if sympy.ask(sympy.Q.negative(f)) is True:
                sign = -sign
                continue
            return None
        return sign

    def _sign_of_expression(self, expr: Any, sympy: Any) -> int | None:
        """Sign of a numerator or denominator, including the Add case.

        `Mul.make_args` on an Add (e.g. `-2*x**2 - 2`) returns the Add whole, so
        a constant sign hidden inside it is invisible to factor analysis. Ask
        about the whole expression as a fallback.
        """
        sign = self._factor_sign(expr, sympy)
        if sign is not None:
            return sign
        if sympy.ask(sympy.Q.positive(expr)) is True:
            return 1
        if sympy.ask(sympy.Q.negative(expr)) is True:
            return -1
        return None

    def _sign_of(self, deriv: Any, param: str, sympy: Any) -> tuple[StaticSign, list[str]]:
        """Decide the sign deterministically; ambiguous with conditions otherwise."""
        if deriv == 0:
            return StaticSign.zero, []
        free = deriv.free_symbols
        if not free:
            if sympy.ask(sympy.Q.positive(deriv)) is True:
                return StaticSign.positive, []
            if sympy.ask(sympy.Q.negative(deriv)) is True:
                return StaticSign.negative, []
            return StaticSign.ambiguous, []
        # Factor into constant * provably-positive factors (e.g. squares, even
        # powers): if every non-constant factor is provably positive, the sign
        # is the sign of the constant part.
        try:
            num, den = sympy.fraction(sympy.together(deriv))
            # H8: the previous code pooled numerator and denominator factors
            # into one list. A denominator like `-2*x**2 - 2` is an Add, so
            # `Mul.make_args` returns it whole and its negative sign was lost,
            # flipping the reported sign (e.g. 1/(-2*(x**2+1)) was reported
            # positive). Sign(num/den) == Sign(num) * Sign(den), so evaluate
            # each side separately instead of pooling.
            num_sign = self._sign_of_expression(num, sympy)
            den_sign = self._sign_of_expression(den, sympy)
            if num_sign and den_sign is not None:
                overall = num_sign * den_sign
                if overall > 0:
                    return StaticSign.positive, []
                if overall < 0:
                    return StaticSign.negative, []
        except Exception:  # noqa: BLE001
            pass
        # Sign depends on parameter values -> ambiguous, conditions recorded
        conditions = [f"sign of d{param} depends on: {', '.join(sorted(str(s) for s in free)[:6])}"]
        return StaticSign.ambiguous, conditions

    async def resolve_analysis(self, execution_id: str) -> str:
        """Map an execution back to its ComparativeStaticsAnalysis artifact id."""
        ex_env = await self._store.get(execution_id)
        ex = ex_env.parse_payload(ComparativeStaticsExecution)
        for env in await self._store.list(artifact_type="comparative_statics_analysis"):
            try:
                a = env.parse_payload(ComparativeStaticsAnalysis)
            except Exception:  # noqa: BLE001
                continue
            if a.equilibrium_candidate_id == ex.equilibrium_candidate_id:
                return env.artifact_id
        raise ValueError("no comparative_statics_analysis found for execution")


class ComparativeStaticsPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.comparative_statics",
            version="0.1.0",
            plugin_type="research",
            description="Deterministic comparative statics of a verified equilibrium (Phase 3D)",
            provides=["comparative_statics.default"],
            requires=["artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        store = ctx.require("artifact_store.default")
        self._service = ComparativeStaticsService(artifact_store=store)
        ctx.register("comparative_statics.default", self._service)
