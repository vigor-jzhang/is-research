"""Phase 3A gap selection — selects one ResearchGap from a GapAnalysis.

Uses GapAnalysis.ranked_gap_ids as input but does NOT automatically assume
rank #1 must be selected: a model call makes the selection with a rationale,
validated against the analyzed gap set. The choice then passes through the
autonomy policy checkpoint (`research_gap`): interactive mode requests
approval, high-autonomy mode continues while recording the decision.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field, field_validator

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.gap import GapAnalysis, GapStatus, ResearchGap
from research_harness.research.schemas.mechanism import GapSelection, SelectionStatus

logger = logging.getLogger(__name__)


class _SelectionResponse(BaseModel):
    selected_gap_id: str
    evidence_synthesis_basis: str | None = None
    research_importance: float = Field(default=0.5, ge=0.0, le=1.0)
    theoretical_relevance: float = Field(default=0.5, ge=0.0, le=1.0)
    analytical_model_suitability: float = Field(default=0.5, ge=0.0, le=1.0)
    tractability: float = Field(default=0.5, ge=0.0, le=1.0)
    selection_rationale: str

    @field_validator("selected_gap_id", "selection_rationale")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must be non-empty")
        return v

    model_config = {"extra": "forbid"}


class GapSelectionService:
    def __init__(
        self,
        model_router: Any,
        artifact_store: Any,
        model_role: str = "reasoning",
        autonomy_mode: str = "high",
        autonomy: Any | None = None,
        max_alternatives: int = 5,
        events: Any | None = None,
    ) -> None:
        self._router = model_router
        self._store = artifact_store
        self._model_role = model_role
        self._autonomy_mode = autonomy_mode
        self._autonomy = autonomy
        self._max_alternatives = max_alternatives
        self._events = events

    @property
    def selection_id(self) -> str:
        return "research.gap_selection"

    async def select(self, gap_analysis_id: str, selected_gap_id: str | None = None) -> str:
        """Select a gap from a GapAnalysis. Returns the GapSelection artifact id."""
        # Idempotency: reuse an existing selection for the same analysis + autonomy mode
        existing = await self._store.list(artifact_type="gap_selection")
        for env in existing:
            try:
                sel = GapSelection.model_validate(env.payload)
                if (
                    sel.gap_analysis_id == gap_analysis_id
                    and sel.autonomy_mode == self._autonomy_mode
                    and sel.status != SelectionStatus.rejected
                ):
                    return env.artifact_id
            except Exception:
                continue

        a_env = await self._store.get(gap_analysis_id)
        analysis = a_env.parse_payload(GapAnalysis)
        gap_ids = analysis.ranked_gap_ids or analysis.gap_ids
        if not gap_ids:
            raise ValueError(f"GapAnalysis {gap_analysis_id} has no gaps to select from")

        # Load all gaps with their ranking/opportunity context
        gap_payloads: dict[str, ResearchGap] = {}
        for gid in gap_ids:
            try:
                gap_payloads[gid] = (await self._store.get(gid)).parse_payload(ResearchGap)
            except Exception:
                continue
        if not gap_payloads:
            raise ValueError(f"none of the gaps of {gap_analysis_id} could be loaded")

        selected_by = "model"
        if selected_gap_id is not None:
            if selected_gap_id not in gap_payloads:
                raise ValueError(
                    f"selected gap {selected_gap_id!r} not among gaps of {gap_analysis_id}"
                )
            choice = self._manual_selection(selected_gap_id, gap_payloads)
            selected_by = "operator"
        else:
            choice = await self._model_selection(gap_analysis_id, analysis, gap_payloads)

        chosen_gap_id: str = str(choice["selected_gap_id"])
        alternatives = [gid for gid in gap_ids if gid != chosen_gap_id][: self._max_alternatives]

        # Autonomy checkpoint: gap selection is a major research decision
        approval = await self._request_approval(chosen_gap_id, gap_payloads[chosen_gap_id])
        status = SelectionStatus.approved if approval["approved"] else SelectionStatus.rejected

        selection = GapSelection(
            gap_analysis_id=gap_analysis_id,
            selected_gap_id=chosen_gap_id,
            alternative_gap_ids=alternatives,
            evidence_synthesis_basis=choice.get("evidence_synthesis_basis"),
            research_importance=choice.get("research_importance"),
            theoretical_relevance=choice.get("theoretical_relevance"),
            analytical_model_suitability=choice.get("analytical_model_suitability"),
            tractability=choice.get("tractability"),
            selection_rationale=choice["selection_rationale"],
            status=status,
            autonomy_mode=self._autonomy_mode,
            approval_required=approval["required"],
            approval_decided_by=approval["decided_by"],
            approval_reason=approval["reason"],
            selected_by=selected_by,
            metadata={"model_role": self._model_role},
        )
        sel_env = ArtifactEnvelope.create(
            payload=selection,
            artifact_type="gap_selection",
            producer=f"research.gap_selection:{self._model_role}",
        )
        await self._store.put(sel_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=gap_analysis_id,
                target_artifact_id=sel_env.artifact_id,
                producer="research.gap_selection",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=chosen_gap_id,
                target_artifact_id=sel_env.artifact_id,
                producer="research.gap_selection",
            )
        )

        # Mark the selected gap as selected via a superseding ResearchGap artifact
        original = gap_payloads[chosen_gap_id]
        updated = original.model_copy(
            update={
                "status": GapStatus.selected,
                "metadata": {
                    **original.metadata,
                    "gap_selection_id": sel_env.artifact_id,
                },
            }
        )
        gap_env = ArtifactEnvelope.create(
            payload=updated,
            artifact_type="research_gap",
            producer="research.gap_selection",
        )
        await self._store.put(gap_env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.supersedes,
                source_artifact_id=chosen_gap_id,
                target_artifact_id=gap_env.artifact_id,
                producer="research.gap_selection",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=sel_env.artifact_id,
                target_artifact_id=gap_env.artifact_id,
                producer="research.gap_selection",
            )
        )

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="gap_selection.completed",
                        source="research.gap_selection",
                        payload={
                            "selection_id": sel_env.artifact_id,
                            "gap_analysis_id": gap_analysis_id,
                            "selected_gap_id": selected_gap_id,
                            "status": status.value,
                            "approval_required": approval["required"],
                        },
                    )
                )
            except Exception:
                pass

        return sel_env.artifact_id

    def _manual_selection(
        self, selected_gap_id: str, gap_payloads: dict[str, ResearchGap]
    ) -> dict[str, Any]:
        gap = gap_payloads[selected_gap_id]
        ranking = gap.ranking
        return {
            "selected_gap_id": selected_gap_id,
            "evidence_synthesis_basis": (
                f"{gap.supporting_papers} supporting papers, "
                f"{gap.supporting_evidence_items} supporting evidence items"
            ),
            "research_importance": ranking.research_importance if ranking else 0.5,
            "theoretical_relevance": ranking.theoretical_relevance if ranking else 0.5,
            "analytical_model_suitability": (
                ranking.analytical_model_potential if ranking else 0.5
            ),
            "tractability": ranking.tractability if ranking else 0.5,
            "selection_rationale": (f"Gap explicitly selected by operator: {gap.title}"),
        }

    async def _model_selection(
        self,
        gap_analysis_id: str,
        analysis: GapAnalysis,
        gap_payloads: dict[str, ResearchGap],
    ) -> dict[str, Any]:
        prompt = self._build_prompt(analysis, gap_payloads)
        from research_harness.contracts.model import Message, ModelRequest

        request = ModelRequest(
            messages=[
                Message(
                    role="system",
                    content=(
                        "You are a research project director choosing ONE research gap to develop "
                        "a theoretical mechanism for. Return valid JSON matching the schema. "
                        "Never include chain-of-thought."
                    ),
                ),
                Message(role="user", content=prompt),
            ],
            response_schema=self._build_schema(),
            temperature=0.0,
            metadata={"gap_analysis_id": gap_analysis_id},
        )
        try:
            response = await self._router.complete(self._model_role, request)
            data = json.loads(response.message.content or "")
            choice = _SelectionResponse.model_validate(data)
        except Exception:
            # Deterministic fallback: rank #1 (model may still have been overridden
            # by an operator in interactive mode; failures are recorded in rationale)
            ranked = analysis.ranked_gap_ids or analysis.gap_ids
            first = next((gid for gid in ranked if gid in gap_payloads), ranked[0])
            gap = gap_payloads[first]
            ranking = gap.ranking
            return {
                "selected_gap_id": first,
                "evidence_synthesis_basis": (
                    f"{gap.supporting_papers} supporting papers, "
                    f"{gap.supporting_evidence_items} supporting evidence items"
                ),
                "research_importance": ranking.research_importance if ranking else 0.5,
                "theoretical_relevance": ranking.theoretical_relevance if ranking else 0.5,
                "analytical_model_suitability": (
                    ranking.analytical_model_potential if ranking else 0.5
                ),
                "tractability": ranking.tractability if ranking else 0.5,
                "selection_rationale": (
                    "Model selection unavailable or invalid; deterministic fallback to "
                    f"the top-ranked gap: {gap.title}"
                ),
            }

        if choice.selected_gap_id not in gap_payloads:
            # Model picked an id outside the analyzed set — fall back deterministically
            ranked = analysis.ranked_gap_ids or analysis.gap_ids
            first = next((gid for gid in ranked if gid in gap_payloads), ranked[0])
            return {
                "selected_gap_id": first,
                "evidence_synthesis_basis": choice.evidence_synthesis_basis,
                "research_importance": choice.research_importance,
                "theoretical_relevance": choice.theoretical_relevance,
                "analytical_model_suitability": choice.analytical_model_suitability,
                "tractability": choice.tractability,
                "selection_rationale": (
                    f"Model proposed unknown gap id {choice.selected_gap_id!r}; "
                    f"deterministic fallback to top-ranked gap."
                ),
            }
        return choice.model_dump()

    async def _request_approval(self, gap_id: str, gap: ResearchGap) -> dict[str, Any]:
        if self._autonomy is not None:
            from research_harness.contracts.autonomy import ApprovalRequest

            request = ApprovalRequest(
                request_id=gap_id,
                checkpoint="research_gap",
                description=(
                    f"Approve selection of research gap {gap.title[:120]} for mechanism development"
                ),
                payload={"gap_id": gap_id, "gap_title": gap.title},
            )
            decision = await self._autonomy.request_approval(request)
            return {
                "approved": bool(decision.approved),
                "required": True,
                "decided_by": getattr(decision, "decided_by", "policy") or "policy",
                "reason": getattr(decision, "reason", None),
            }
        return {
            "approved": True,
            "required": False,
            "decided_by": f"policy:{self._autonomy_mode}",
            "reason": "auto-approved (no autonomy policy registered)",
        }

    def _build_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "selected_gap_id": {"type": "string"},
                "evidence_synthesis_basis": {"type": "string"},
                "research_importance": {"type": "number", "minimum": 0, "maximum": 1},
                "theoretical_relevance": {"type": "number", "minimum": 0, "maximum": 1},
                "analytical_model_suitability": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1,
                },
                "tractability": {"type": "number", "minimum": 0, "maximum": 1},
                "selection_rationale": {"type": "string"},
            },
            "required": ["selected_gap_id", "selection_rationale"],
            "additionalProperties": False,
        }

    def _build_prompt(self, analysis: GapAnalysis, gap_payloads: dict[str, ResearchGap]) -> str:
        lines = []
        for gid in analysis.ranked_gap_ids or analysis.gap_ids:
            gap = gap_payloads.get(gid)
            if gap is None:
                continue
            rank = gap.ranking
            lines.append(
                f"- {gid} | rank {rank.composite if rank else 0.0:.3f} | "
                f"importance {rank.research_importance if rank else 0.0:.2f} | "
                f"model-potential {rank.analytical_model_potential if rank else 0.0:.2f} | "
                f"tractability {rank.tractability if rank else 0.0:.2f}\n"
                f"  {gap.title}\n"
                f"  type {gap.gap_type.value}; {gap.supporting_papers} papers, "
                f"{gap.supporting_evidence_items} evidence items supporting; "
                f"{gap.contradicting_papers} contradicting\n"
                f"  {gap.description[:240]}"
            )
        return f"""Choose ONE research gap to develop a theoretical mechanism for.

The ranked gap candidates from the analysis are (IDs are authoritative; select
ONLY one of these IDs):

{chr(10).join(lines)}

Selection criteria:
- research importance, theoretical relevance, analytical-model suitability,
  and tractability for a parsimonious analytical model
- Do NOT simply pick rank #1; choose the gap where mechanism development adds
  the most theoretical value
- evidence_synthesis_basis: brief summary of the evidence/synthesis grounding
- Score each dimension 0..1 in your answer
- Return valid JSON only, no chain-of-thought.
"""


class GapSelectionPlugin(Plugin):
    def __init__(self, model_role: str | None = None) -> None:
        self._model_role_override = model_role
        self._service: GapSelectionService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="research.gap_selection",
            version="0.1.0",
            plugin_type="research",
            description="Gap selection with autonomy checkpoint (Phase 3A)",
            provides=["gap_selection.default"],
            requires=[
                "model_router.default",
                "artifact_store.default",
                "autonomy_policy.default",
            ],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        research_cfg: dict[str, Any] = {}
        if "research" in cfg and isinstance(cfg["research"], dict):
            research_cfg = (
                cfg["research"].get("mechanism", {})
                if isinstance(cfg["research"].get("mechanism"), dict)
                else {}
            )
        model_role = (
            self._model_role_override
            or research_cfg.get("generator_role")
            or research_cfg.get("model_role")
            or "reasoning"
        )
        autonomy_mode = str(cfg.get("autonomy_mode") or "high")
        if autonomy_mode not in ("high", "interactive"):
            autonomy_mode = "high"

        router = ctx.require("model_router.default")
        store = ctx.require("artifact_store.default")
        autonomy = ctx.try_get("autonomy_policy.default")
        self._service = GapSelectionService(
            model_router=router,
            artifact_store=store,
            model_role=str(model_role),
            autonomy_mode=autonomy_mode,
            autonomy=autonomy,
            events=ctx.events,
        )
        ctx.register("gap_selection.default", self._service)
