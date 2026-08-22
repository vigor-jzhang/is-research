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
        m_env = await self._store.get(candidate.model_id)
        model = m_env.parse_payload(FormalAnalyticalModel)

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

        table = {v.symbol: v for v in model.variables}
        table.update({p.symbol: p for p in model.parameters})
        candidate_exprs: dict[str, Any] = {}
        for e in candidate.expressions:
            try:
                candidate_exprs[e.variable] = parse_sympy(e.expression.expression, table)
            except Exception as exc:  # noqa: BLE001
                exec_record.failures.append({"error": f"candidate {e.variable} unparseable: {exc}"})

        param_syms = [p.symbol for p in model.parameters]
        static_ids: list[str] = []
        import sympy

        for outcome, expr in candidate_exprs.items():
            for param in param_syms:
                try:
                    deriv = sympy.simplify(sympy.diff(expr, sympy.Symbol(param)))
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
            factors = list(sympy.Mul.make_args(num)) + list(sympy.Mul.make_args(den))
            const_factors = [f for f in factors if not f.free_symbols]
            pos_factors = [
                f for f in factors if f.free_symbols and sympy.ask(sympy.Q.positive(f)) is True
            ]
            if len(const_factors) + len(pos_factors) == len(factors) and const_factors:
                const_part = sympy.prod(const_factors)
                if const_part.is_number:
                    if const_part > 0:
                        return StaticSign.positive, []
                    if const_part < 0:
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
