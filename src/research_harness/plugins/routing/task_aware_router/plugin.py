"""Task-aware shadow router (Phase 7D.4, shadow mode only).

Decision support over persisted TaskQualificationMatrix evidence: for a
(role, task) it selects an advisory task-specialized model using exact-task
qualification (never transferred across tasks). Uncovered tasks keep the
configured static role model (`static_fallback`, reason
`no_qualified_task_model`) and are never dynamically switched. Production
continues executing the configured static model; the decision is recorded as an
immutable `task_aware_routing_decision` artifact. No LLM is used to choose.
"""

from __future__ import annotations

from typing import Any

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.routing.roles import validate_role
from research_harness.research.routing.task_aware import build_task_aware_decision
from research_harness.research.routing.tasks import tasks_for_role
from research_harness.research.schemas.qualification import TaskQualificationMatrix
from research_harness.research.schemas.routing import (
    TaskAwareRoutingDecision,
    TaskAwareShadowCampaign,
)

_PRODUCER = "routing.task_aware_router"


class TaskAwareRouterService:
    def __init__(
        self,
        *,
        artifact_store: Any,
        current_roles: dict[str, dict[str, str]] | None = None,
        max_qualification_age_seconds: float | None = None,
        producer: str = _PRODUCER,
    ) -> None:
        self._store = artifact_store
        self._current_roles = dict(current_roles or {})
        self._max_age = max_qualification_age_seconds
        self._producer = producer

    @property
    def service_id(self) -> str:
        return "task_aware_router.default"

    # ------------------------------------------------------------------

    async def latest_matrix(self, role: str) -> TaskQualificationMatrix | None:
        """Latest task-qualification matrix for a role (immutable evidence)."""
        validate_role(role)
        matrices = [
            env.parse_payload(TaskQualificationMatrix)
            for env in await self._store.list(artifact_type="task_qualification_matrix")
            if env.payload.get("role") == role
        ]
        if not matrices:
            return None
        return max(matrices, key=lambda m: m.created_at)

    async def decide(
        self,
        role: str,
        task: str,
        *,
        max_qualification_age_seconds: float | None = None,
    ) -> TaskAwareRoutingDecision:
        """One immutable task-aware shadow routing decision for (role, task)."""
        validate_role(role)
        if task not in tasks_for_role(role):
            raise ValueError(f"unknown task {task!r} for role {role!r}")
        matrix = await self.latest_matrix(role)
        if matrix is None:
            raise ValueError(
                f"no task_qualification_matrix for role {role!r}; run `routing qualify` first"
            )
        current = self._current_roles.get(role) or {}
        decision = build_task_aware_decision(
            role=role,
            task=task,
            matrix=matrix,
            static_model=current.get("model"),
            static_provider=current.get("provider"),
            max_qualification_age_seconds=max_qualification_age_seconds
            if max_qualification_age_seconds is not None
            else self._max_age,
        )
        await self._persist(decision)
        return decision

    async def shadow_campaign(
        self,
        *,
        max_qualification_age_seconds: float | None = None,
    ) -> TaskAwareShadowCampaign:
        """Run the task-aware shadow router over the current qualification
        matrices (every role/task). Records a batch; no production changes."""
        decision_ids: list[str] = []
        roles_with_matrices: list[str] = []
        for role in ("fast", "reasoning", "critic"):
            matrix = await self.latest_matrix(role)
            if matrix is None:
                continue
            roles_with_matrices.append(role)
            for task in tasks_for_role(role):
                decision = await self.decide(
                    role,
                    task,
                    max_qualification_age_seconds=max_qualification_age_seconds,
                )
                decision_ids.append(decision.id)
        campaign = TaskAwareShadowCampaign(
            decision_ids=decision_ids,
            metadata={
                "roles": roles_with_matrices,
                "policy_id": "task_aware_shadow_v1",
                "max_qualification_age_seconds": max_qualification_age_seconds,
            },
        )
        await self._store.put(
            ArtifactEnvelope.create(
                payload=campaign,
                artifact_type="task_aware_shadow_campaign",
                producer=self._producer,
                artifact_id=campaign.id,
            )
        )
        return campaign

    async def get_decision(self, decision_id: str) -> TaskAwareRoutingDecision:
        return (await self._store.get(decision_id)).parse_payload(TaskAwareRoutingDecision)

    async def list_decisions(self, role: str | None = None) -> list[TaskAwareRoutingDecision]:
        decisions = [
            env.parse_payload(TaskAwareRoutingDecision)
            for env in await self._store.list(artifact_type="task_aware_routing_decision")
            if role is None or env.payload.get("role") == role
        ]
        decisions.sort(key=lambda d: d.created_at, reverse=True)
        return decisions

    # ------------------------------------------------------------------

    async def _persist(self, decision: TaskAwareRoutingDecision) -> None:
        await self._store.put(
            ArtifactEnvelope.create(
                payload=decision,
                artifact_type="task_aware_routing_decision",
                producer=self._producer,
                artifact_id=decision.id,
            )
        )


class TaskAwareRouterPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="routing.task_aware_router",
            version="0.1.0",
            plugin_type="model_router",
            description=(
                "Task-aware shadow router (Phase 7D.4): per-(role, task) advisory "
                "selection from TaskQualificationMatrix evidence; uncovered tasks "
                "stay on the static configured role model. Never switches production."
            ),
            provides=["task_aware_router.default"],
            requires=["artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        models_cfg: dict[str, Any] = {}
        if "models" in cfg:
            models_cfg = cfg["models"]
        elif "roles" in cfg:
            models_cfg = {"roles": cfg["roles"]}
        current_roles: dict[str, dict[str, str]] = {}
        for role, rcfg in (models_cfg.get("roles") or {}).items():
            if isinstance(rcfg, dict) and rcfg.get("model"):
                current_roles[str(role)] = {
                    "provider": str(rcfg.get("provider") or "openrouter"),
                    "model": str(rcfg["model"]),
                }
        routing_cfg = cfg.get("routing") or {}
        service = TaskAwareRouterService(
            artifact_store=ctx.require("artifact_store.default"),
            current_roles=current_roles,
            max_qualification_age_seconds=routing_cfg.get("max_qualification_age_seconds"),
        )
        ctx.register("task_aware_router.default", service)
