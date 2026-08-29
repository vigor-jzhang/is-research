"""Phase 3B model builder — formal analytical model specification from a
SelectedMechanism + supporting Phase 2 evidence.

Produces a strict structured model specification (actors, symbol table,
timing, information structure, grounded assumptions, payoff functions) and
validates it deterministically before persistence: all symbols defined,
domains valid, actor references valid, timing consistent, decision ownership
valid, payoff symbols valid, assumption references valid, no duplicate
symbols. No equilibrium/propositions/numerical experiments (Phase 3C+).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.mechanism import KnowledgeBasis, SelectedMechanism
from research_harness.research.schemas.model import (
    Expression,
    FormalAnalyticalModel,
    InformationItem,
    InformationStructure,
    ModelActor,
    ModelAssumption,
    ModelParameter,
    ModelSpecificationExecution,
    ModelStatus,
    ModelTimingStage,
    ModelVariable,
    PayoffFunction,
    SymbolKind,
    UncertaintyItem,
    Visibility,
)

logger = logging.getLogger(__name__)

_EXPR_ITEM_FIELDS = ("symbols_used", "latex")


class _ExpressionItem(BaseModel):
    expression: str
    latex: str | None = None
    symbols_used: list[str] = Field(default_factory=list)

    @field_validator("expression")
    @classmethod
    def validate_expression(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("expression must be non-empty")
        return v

    model_config = {"extra": "forbid"}


class _ActorItem(BaseModel):
    actor_id: str
    name: str
    role: str | None = None
    strategic: bool = True
    description: str | None = None

    model_config = {"extra": "forbid"}


class _VariableItem(BaseModel):
    symbol: str
    name: str
    meaning: str
    domain: str = "R"
    units: str | None = None
    kind: str = SymbolKind.state_variable.value
    owner_actor_id: str | None = None

    model_config = {"extra": "forbid"}


class _ParameterItem(BaseModel):
    symbol: str
    name: str
    meaning: str
    domain: str = "R"
    units: str | None = None

    model_config = {"extra": "forbid"}


class _AssumptionItem(BaseModel):
    statement: str
    mathematical_form: _ExpressionItem | None = None
    knowledge_basis: str = KnowledgeBasis.modeling_assumption.value
    source_ids: list[str] = Field(default_factory=list)
    purpose: str | None = None
    restrictiveness: str = "medium"

    model_config = {"extra": "forbid"}


class _TimingItem(BaseModel):
    stage_number: int = Field(ge=0)
    name: str
    description: str
    actor_ids: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class _InformationItem(BaseModel):
    actor_id: str
    variable_symbols: list[str] = Field(default_factory=list)
    available_at_stage: int = Field(default=0, ge=0)
    visibility: str = Visibility.public.value
    description: str | None = None

    model_config = {"extra": "forbid"}


class _UncertaintyItem(BaseModel):
    variable_symbol: str
    distribution: str
    belief_note: str | None = None

    model_config = {"extra": "forbid"}


class _InformationStructureItem(BaseModel):
    items: list[_InformationItem] = Field(default_factory=list)
    uncertainty: list[_UncertaintyItem] = Field(default_factory=list)
    summary: str | None = None

    model_config = {"extra": "forbid"}


class _PayoffItem(BaseModel):
    actor_id: str
    objective_type: str = "utility"
    expression: _ExpressionItem
    decision_variables: list[str] = Field(default_factory=list)
    parameters: list[str] = Field(default_factory=list)
    constraints: list[_ExpressionItem] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class ModelSpecificationResponse(BaseModel):
    title: str
    description: str
    game_type: str | None = None
    actors: list[_ActorItem] = Field(default_factory=list)
    variables: list[_VariableItem] = Field(default_factory=list)
    parameters: list[_ParameterItem] = Field(default_factory=list)
    assumptions: list[_AssumptionItem] = Field(default_factory=list)
    timing: list[_TimingItem] = Field(default_factory=list)
    information_structure: _InformationStructureItem = Field(
        default_factory=_InformationStructureItem
    )
    payoffs: list[_PayoffItem] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


def _free_symbols_of(expr: str, known: set[str]) -> tuple[set[str], str | None]:
    """Parse a SymPy-compatible expression; return (free symbol names, error)."""
    try:
        from research_harness.research.symbolic import safe_sympify

        parsed = safe_sympify(expr, {s: s for s in known})
        return {str(s) for s in parsed.free_symbols}, None
    except Exception as e:  # noqa: BLE001
        return set(), f"invalid expression {expr!r}: {e}"


class ModelBuilderService:
    def __init__(
        self,
        model_router: Any,
        artifact_store: Any,
        model_role: str = "reasoning",
        max_actors: int = 8,
        max_variables: int = 40,
        max_parameters: int = 40,
        max_assumptions: int = 20,
        max_stages: int = 20,
        max_payoffs: int = 10,
        events: Any | None = None,
    ) -> None:
        self._router = model_router
        self._store = artifact_store
        self._model_role = model_role
        self._max_actors = max_actors
        self._max_variables = max_variables
        self._max_parameters = max_parameters
        self._max_assumptions = max_assumptions
        self._max_stages = max_stages
        self._max_payoffs = max_payoffs
        self._events = events

    @property
    def builder_id(self) -> str:
        return "research.model_builder"

    async def build(self, selected_mechanism_id: str) -> str:
        """Build a FormalAnalyticalModel from a SelectedMechanism.

        Returns the ModelSpecificationExecution artifact id.
        """
        # Idempotency: reuse a completed successful build for same mechanism + role
        existing = await self._store.list(artifact_type="model_specification_execution")
        for env in existing:
            try:
                ex = ModelSpecificationExecution.model_validate(env.payload)
                if (
                    ex.selected_mechanism_id == selected_mechanism_id
                    and ex.model_role == self._model_role
                    and ex.completed_at is not None
                    and ex.model_created
                ):
                    return env.artifact_id
            except Exception:
                continue

        sm_env = await self._store.get(selected_mechanism_id)
        mechanism = sm_env.parse_payload(SelectedMechanism)
        context = await self.load_context(mechanism)

        started = datetime.now(UTC)
        exec_record = ModelSpecificationExecution(
            selected_mechanism_id=selected_mechanism_id,
            model_created=False,
            rejected=False,
            model_role=self._model_role,
            failures=[],
            counts={"model_calls": 0},
            started_at=started,
        )

        prompt = self._build_prompt(mechanism, context)
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You are a formal analytical model specifier in information systems. "
                        "Return valid JSON matching the schema. Never include chain-of-thought."
                    ),
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self.build_schema(),
            temperature=0.0,
            metadata={"selected_mechanism_id": selected_mechanism_id},
        )

        try:
            response = await self._router.complete(self._model_role, request)
            exec_record.counts["model_calls"] = 1
            data = json.loads(response.message.content or "")
            parsed = ModelSpecificationResponse.model_validate(data)
        except Exception as e:
            exec_record.failures.append({"error": f"model call failed: {e}"})
            exec_record.rejected = True
            exec_record.completed_at = datetime.now(UTC)
            exec_env = ArtifactEnvelope.create(
                payload=exec_record,
                artifact_type="model_specification_execution",
                producer="research.model_builder",
            )
            await self._store.put(exec_env)
            return exec_env.artifact_id

        # Deterministic structural validation; reject the whole spec on failure
        try:
            model = self.build_model(parsed, mechanism, sm_env.artifact_id, context)
        except ValueError as e:
            exec_record.failures.append({"error": f"structural validation failed: {e}"})
            exec_record.rejected = True
            exec_record.completed_at = datetime.now(UTC)
            exec_env = ArtifactEnvelope.create(
                payload=exec_record,
                artifact_type="model_specification_execution",
                producer="research.model_builder",
            )
            await self._store.put(exec_env)
            return exec_env.artifact_id

        m_env = ArtifactEnvelope.create(
            payload=model,
            artifact_type="formal_analytical_model",
            producer=f"research.model_builder:{self._model_role}",
        )
        await self._store.put(m_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=selected_mechanism_id,
                target_artifact_id=m_env.artifact_id,
                producer="research.model_builder",
            )
        )
        # Model assumptions that are literature-backed keep individual grounding:
        # add provenance edges from their source artifacts to the model.
        for a in model.assumptions:
            if a.knowledge_basis == KnowledgeBasis.literature_supported:
                for sid in a.source_ids:
                    try:
                        await self._store.add_provenance(
                            ProvenanceLink(
                                relation=ProvenanceRelation.derived_from,
                                source_artifact_id=sid,
                                target_artifact_id=m_env.artifact_id,
                                producer="research.model_builder",
                            )
                        )
                    except Exception:
                        pass

        exec_record.model_created = True
        exec_record.completed_at = datetime.now(UTC)
        exec_env = ArtifactEnvelope.create(
            payload=exec_record,
            artifact_type="model_specification_execution",
            producer="research.model_builder",
        )
        await self._store.put(exec_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=exec_env.artifact_id,
                target_artifact_id=m_env.artifact_id,
                producer="research.model_builder",
            )
        )
        return exec_env.artifact_id

    # ------------------------------------------------------------------
    # Deterministic structural validation
    # ------------------------------------------------------------------

    def build_model(
        self,
        parsed: ModelSpecificationResponse,
        mechanism: SelectedMechanism,
        mechanism_id: str,
        context: dict[str, Any],
    ) -> FormalAnalyticalModel:
        actors = [
            ModelActor(
                actor_id=a.actor_id,
                name=a.name,
                role=a.role,
                strategic=a.strategic,
                description=a.description,
            )
            for a in parsed.actors[: self._max_actors]
        ]
        actor_ids = {a.actor_id for a in actors}
        if not actor_ids:
            raise ValueError("model must define at least one actor")

        variables = [
            ModelVariable(
                symbol=v.symbol,
                name=v.name,
                meaning=v.meaning,
                domain=v.domain or "R",
                units=v.units,
                kind=SymbolKind(v.kind)
                if v.kind in SymbolKind.values()
                else SymbolKind.state_variable,
                owner_actor_id=v.owner_actor_id,
            )
            for v in parsed.variables[: self._max_variables]
        ]
        parameters = [
            ModelParameter(
                symbol=p.symbol,
                name=p.name,
                meaning=p.meaning,
                domain=p.domain or "R",
                units=p.units,
            )
            for p in parsed.parameters[: self._max_parameters]
        ]

        symbol_table: dict[str, ModelVariable | ModelParameter] = {v.symbol: v for v in variables}
        symbol_table.update({p.symbol: p for p in parameters})

        # No duplicate symbols (across variables + parameters)
        all_syms = [v.symbol for v in variables] + [p.symbol for p in parameters]
        if len(set(all_syms)) != len(all_syms):
            dups = sorted({sym for sym in all_syms if all_syms.count(sym) > 1})
            raise ValueError(f"duplicate symbol(s): {dups}")

        # Decision ownership: every decision_variable must be owned by an actor
        for v in variables:
            if v.kind == SymbolKind.decision_variable:
                if not v.owner_actor_id:
                    raise ValueError(f"decision variable {v.symbol!r} has no owner_actor_id")
                if v.owner_actor_id not in actor_ids:
                    raise ValueError(
                        f"decision variable {v.symbol!r} owned by unknown actor "
                        f"{v.owner_actor_id!r}"
                    )

        # Timing: unique sequential stages starting at 0
        timing = [
            ModelTimingStage(
                stage_number=t.stage_number,
                name=t.name,
                description=t.description,
                actor_ids=list(t.actor_ids),
            )
            for t in parsed.timing[: self._max_stages]
        ]
        if not timing:
            raise ValueError("model must define at least one timing stage")
        stage_nums = [t.stage_number for t in timing]
        if len(set(stage_nums)) != len(stage_nums):
            raise ValueError(f"duplicate timing stage numbers: {stage_nums}")
        if stage_nums != list(range(len(timing))):
            raise ValueError(
                f"timing stages must be sequential 0..{len(timing) - 1}, got {stage_nums}"
            )
        max_stage = len(timing) - 1
        for t in timing:
            for aid in t.actor_ids:
                if aid not in actor_ids:
                    raise ValueError(
                        f"timing stage {t.stage_number} references unknown actor {aid!r}"
                    )

        # Information structure
        info_items = [
            InformationItem(
                actor_id=i.actor_id,
                variable_symbols=list(i.variable_symbols),
                available_at_stage=i.available_at_stage,
                visibility=(
                    Visibility(i.visibility)
                    if i.visibility in Visibility.values()
                    else Visibility.public
                ),
                description=i.description,
            )
            for i in parsed.information_structure.items
        ]
        for i in info_items:
            if i.actor_id not in actor_ids:
                raise ValueError(f"information item references unknown actor {i.actor_id!r}")
            for sym in i.variable_symbols:
                if sym not in symbol_table:
                    raise ValueError(f"information item observes undefined symbol {sym!r}")
            if i.available_at_stage > max_stage:
                raise ValueError(
                    f"information available at stage {i.available_at_stage} beyond timing "
                    f"(max {max_stage})"
                )
        uncertainty = [
            UncertaintyItem(
                variable_symbol=u.variable_symbol,
                distribution=u.distribution,
                belief_note=u.belief_note,
            )
            for u in parsed.information_structure.uncertainty
        ]
        for u in uncertainty:
            if u.variable_symbol not in symbol_table:
                raise ValueError(f"uncertainty references undefined symbol {u.variable_symbol!r}")
            var = symbol_table[u.variable_symbol]
            if isinstance(var, ModelVariable) and var.kind != SymbolKind.random_variable:
                raise ValueError(
                    f"uncertainty variable {u.variable_symbol!r} is not a random_variable "
                    f"(kind {var.kind.value})"
                )
        info_struct = InformationStructure(
            items=info_items,
            uncertainty=uncertainty,
            summary=parsed.information_structure.summary,
        )

        # Assumptions with grounding
        assumptions = [
            ModelAssumption(
                statement=a.statement,
                mathematical_form=self._expr(a.mathematical_form) if a.mathematical_form else None,
                knowledge_basis=(
                    KnowledgeBasis(a.knowledge_basis)
                    if a.knowledge_basis in KnowledgeBasis.values()
                    else KnowledgeBasis.modeling_assumption
                ),
                source_ids=list(a.source_ids),
                purpose=a.purpose,
                restrictiveness=a.restrictiveness,
            )
            for a in parsed.assumptions[: self._max_assumptions]
        ]
        valid_sources = context["valid_source_ids"]
        for a in assumptions:
            if a.knowledge_basis == KnowledgeBasis.literature_supported:
                if not a.source_ids:
                    raise ValueError(
                        f"literature_supported assumption {a.statement[:60]!r} has no source_ids"
                    )
                bad = [sid for sid in a.source_ids if sid not in valid_sources]
                if bad:
                    raise ValueError(
                        f"literature_supported assumption {a.statement[:60]!r} cites "
                        f"unknown artifacts: {bad[:3]}"
                    )
            if a.mathematical_form is not None:
                self._validate_expr(
                    a.mathematical_form, symbol_table, where=f"assumption {a.statement[:40]!r}"
                )

        # Payoffs
        payoffs = [
            PayoffFunction(
                actor_id=p.actor_id,
                objective_type=p.objective_type,
                expression=self._expr(p.expression),
                decision_variables=list(p.decision_variables),
                parameters=list(p.parameters),
                constraints=[self._expr(c) for c in p.constraints],
            )
            for p in parsed.payoffs[: self._max_payoffs]
        ]
        for p in payoffs:
            if p.actor_id not in actor_ids:
                raise ValueError(f"payoff references unknown actor {p.actor_id!r}")
            owned = {
                v.symbol
                for v in variables
                if v.kind == SymbolKind.decision_variable and v.owner_actor_id == p.actor_id
            }
            bad_dv = [sym for sym in p.decision_variables if sym not in owned]
            if bad_dv:
                raise ValueError(
                    f"payoff of {p.actor_id!r} lists decision variables {bad_dv} not owned by them"
                )
            self._validate_expr(p.expression, symbol_table, where=f"payoff of {p.actor_id!r}")
            for sym in p.parameters:
                if sym not in symbol_table:
                    raise ValueError(f"payoff parameter {sym!r} undefined")
            for c in p.constraints:
                self._validate_expr(c, symbol_table, where=f"constraint of {p.actor_id!r}")

        return FormalAnalyticalModel(
            selected_mechanism_id=mechanism_id,
            gap_id=mechanism.gap_id,
            title=parsed.title,
            description=parsed.description,
            game_type=parsed.game_type,
            actors=actors,
            variables=variables,
            parameters=parameters,
            assumptions=assumptions,
            timing=timing,
            information_structure=info_struct,
            payoffs=payoffs,
            status=ModelStatus.draft,
            model_role=self._model_role,
        )

    def _expr(self, item: _ExpressionItem) -> Expression:
        return Expression(
            expression=item.expression,
            latex=item.latex,
            symbols_used=list(item.symbols_used),
        )

    def _validate_expr(self, expr: Expression, symbol_table: dict[str, Any], where: str) -> None:
        known = set(symbol_table)
        declared = set(expr.symbols_used)
        bad_declared = declared - known
        if bad_declared:
            raise ValueError(
                f"expression in {where} declares undefined symbols {sorted(bad_declared)[:5]}"
            )
        free, err = _free_symbols_of(expr.expression, known)
        if err is not None:
            raise ValueError(f"expression in {where}: {err}")
        unknown_free = free - known
        if unknown_free:
            raise ValueError(
                f"expression in {where} uses undefined symbols {sorted(unknown_free)[:5]}"
            )
        undeclared = free - declared
        if undeclared:
            raise ValueError(
                f"expression in {where} uses symbols {sorted(undeclared)[:5]} not listed in "
                f"symbols_used"
            )

    # ------------------------------------------------------------------
    # Context + prompt
    # ------------------------------------------------------------------

    async def load_context(self, mechanism: SelectedMechanism) -> dict[str, Any]:
        """Resolve the mechanism's grounded literature support for assumption checks."""
        valid_source_ids: set[str] = set()
        stmts: dict[str, str] = {}
        for g in mechanism.grounding:
            if g.basis == KnowledgeBasis.literature_supported:
                for sid in g.source_ids:
                    try:
                        env = await self._store.get(sid)
                        if env.artifact_type in ("synthesis_statement", "evidence_item"):
                            valid_source_ids.add(sid)
                            if env.artifact_type == "synthesis_statement":
                                from research_harness.research.schemas.synthesis import (
                                    SynthesisStatement,
                                )

                                s = env.parse_payload(SynthesisStatement)
                                stmts[sid] = s.statement
                    except Exception:
                        continue
        return {"valid_source_ids": valid_source_ids, "stmts": stmts}

    def build_schema(self) -> dict[str, Any]:
        expr_schema = {
            "type": "object",
            "properties": {
                "expression": {"type": "string"},
                "latex": {"type": "string"},
                "symbols_used": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["expression", "symbols_used"],
            "additionalProperties": False,
        }
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "description": {"type": "string"},
                "game_type": {"type": "string"},
                "actors": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "actor_id": {"type": "string"},
                            "name": {"type": "string"},
                            "role": {"type": "string"},
                            "strategic": {"type": "boolean"},
                            "description": {"type": "string"},
                        },
                        "required": ["actor_id", "name"],
                        "additionalProperties": False,
                    },
                },
                "variables": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "name": {"type": "string"},
                            "meaning": {"type": "string"},
                            "domain": {"type": "string"},
                            "units": {"type": "string"},
                            "kind": {"type": "string", "enum": SymbolKind.values()},
                            "owner_actor_id": {"type": "string"},
                        },
                        "required": ["symbol", "name", "meaning"],
                        "additionalProperties": False,
                    },
                },
                "parameters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "symbol": {"type": "string"},
                            "name": {"type": "string"},
                            "meaning": {"type": "string"},
                            "domain": {"type": "string"},
                            "units": {"type": "string"},
                        },
                        "required": ["symbol", "name", "meaning"],
                        "additionalProperties": False,
                    },
                },
                "assumptions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "statement": {"type": "string"},
                            "mathematical_form": expr_schema,
                            "knowledge_basis": {"type": "string", "enum": KnowledgeBasis.values()},
                            "source_ids": {"type": "array", "items": {"type": "string"}},
                            "purpose": {"type": "string"},
                            "restrictiveness": {
                                "type": "string",
                                "enum": ["low", "medium", "high"],
                            },
                        },
                        "required": ["statement"],
                        "additionalProperties": False,
                    },
                },
                "timing": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "stage_number": {"type": "integer", "minimum": 0},
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "actor_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["stage_number", "name", "description"],
                        "additionalProperties": False,
                    },
                },
                "information_structure": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "actor_id": {"type": "string"},
                                    "variable_symbols": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                    "available_at_stage": {"type": "integer", "minimum": 0},
                                    "visibility": {"type": "string", "enum": Visibility.values()},
                                    "description": {"type": "string"},
                                },
                                "required": ["actor_id"],
                                "additionalProperties": False,
                            },
                        },
                        "uncertainty": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "variable_symbol": {"type": "string"},
                                    "distribution": {"type": "string"},
                                    "belief_note": {"type": "string"},
                                },
                                "required": ["variable_symbol", "distribution"],
                                "additionalProperties": False,
                            },
                        },
                        "summary": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "payoffs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "actor_id": {"type": "string"},
                            "objective_type": {"type": "string"},
                            "expression": expr_schema,
                            "decision_variables": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "parameters": {"type": "array", "items": {"type": "string"}},
                            "constraints": {"type": "array", "items": expr_schema},
                        },
                        "required": ["actor_id", "expression"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["title", "description"],
            "additionalProperties": False,
        }

    def _build_prompt(self, mechanism: SelectedMechanism, context: dict[str, Any]) -> str:
        grounding_lines = (
            "\n".join(
                f"  - {g.element} [{g.basis.value}]{' (sources: ' + ', '.join(g.source_ids[:6]) + ')' if g.source_ids else ''}"
                for g in mechanism.grounding
            )
            or "  (none)"
        )
        stmt_lines = (
            "\n".join(f"  [{sid}] {s[:200]}" for sid, s in context["stmts"].items())
            or "  (no statements loaded)"
        )

        return f"""Specify a formal analytical model for the selected mechanism.

Mechanism: {mechanism.name}
Description: {mechanism.description[:400]}
Causal logic: {mechanism.causal_logic[:400]}
Actors: {", ".join(mechanism.actors)}
Strategic interactions: {", ".join(mechanism.strategic_interactions)}
Information structure: {mechanism.information_structure or "not specified"}
Incentives: {", ".join(mechanism.incentives)}
Boundary conditions: {", ".join(mechanism.boundary_conditions)}

Element-level knowledge basis of the mechanism:
{grounding_lines}

Grounded literature statements:
{stmt_lines}

Produce a structured model specification:
- title, description, game_type (e.g. 'static complete information', 'dynamic private information')
- actors: actor_id, name, role, strategic (optimizes their own payoff)
- variables: symbol, name, meaning, domain (e.g. R, R_+, [0,1], {{0,1}}), units if relevant,
  kind from: {", ".join(SymbolKind.values())}. decision_variable MUST have owner_actor_id.
  random_variable for exogenous uncertainty; private_information for hidden types;
  observable_signal for signals.
- parameters: symbol, name, meaning, domain, units if relevant
- assumptions: statement + mathematical_form (expression + symbols_used + latex) +
  knowledge_basis from: {", ".join(KnowledgeBasis.values())}. literature_supported
  assumptions MUST cite valid source ids from the mechanism's grounding (sources shown above).
- timing: sequential stages 0..N with explicit actor_ids per stage
  (Stage 0: parameters/types realized; ... final stage: payoffs realized).
- information_structure: who observes what (variable_symbols), at which stage,
  public/private visibility; uncertainty: random variables + distributions.
- payoffs: per strategic actor — objective_type, expression + symbols_used + latex,
  decision_variables (ONLY symbols of decision variables that actor owns),
  parameters, constraints.

Rules:
- Every symbol used in any expression must be declared in variables/parameters and
  listed in that expression's symbols_used.
- No duplicate symbols.
- Expressions MUST be SymPy-parseable arithmetic: numbers, symbols, + - * / **,
  parentheses, and elementary functions only (exp, log, sqrt, sin, cos, Abs, Min, Max).
  NEVER use Python conditionals (if/else), comparisons, lists, or piecewise
  definitions in expressions. 'p*q - c*q' good; '1 if x > 0 else 0' INVALID.
- Do NOT solve anything: no best responses, no equilibrium, no propositions,
  no numerical experiments.
- Return valid JSON only, no chain-of-thought.
"""


class ModelBuilderPlugin(Plugin):
    def __init__(self, model_role: str | None = None) -> None:
        self._model_role_override = model_role
        self._service: ModelBuilderService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.model_builder",
            version="0.1.0",
            plugin_type="research",
            description="Formal analytical model specification (Phase 3B)",
            provides=["analytical_model_builder.default"],
            requires=["model_router.default", "artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        research_cfg: dict[str, Any] = {}
        if "research" in cfg and isinstance(cfg["research"], dict):
            research_cfg = (
                cfg["research"].get("model", {})
                if isinstance(cfg["research"].get("model"), dict)
                else {}
            )
        model_role = (
            self._model_role_override
            or research_cfg.get("builder_role")
            or research_cfg.get("model_role")
            or "reasoning"
        )
        max_actors = int(research_cfg.get("max_actors", 8))
        max_variables = int(research_cfg.get("max_variables", 40))
        max_parameters = int(research_cfg.get("max_parameters", 40))
        max_assumptions = int(research_cfg.get("max_assumptions", 20))
        max_stages = int(research_cfg.get("max_stages", 20))
        max_payoffs = int(research_cfg.get("max_payoffs", 10))

        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        self._service = ModelBuilderService(
            model_router=router,
            artifact_store=store,
            model_role=str(model_role),
            max_actors=max_actors,
            max_variables=max_variables,
            max_parameters=max_parameters,
            max_assumptions=max_assumptions,
            max_stages=max_stages,
            max_payoffs=max_payoffs,
            events=ctx.events,
        )
        ctx.register("analytical_model_builder.default", self._service)
