"""Configurable autonomy policy plugin."""

from __future__ import annotations

from typing import Any

from research_harness.contracts.autonomy import ApprovalDecision, ApprovalRequest
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata


class ConfigurableAutonomyPolicy:
    """Decides approval based on mode.

    - high: never requires approval, auto-approves
    - interactive: requires approval for important checkpoints
    """

    # Checkpoints that are considered important
    IMPORTANT_CHECKPOINTS = {
        "research_question",
        "literature_set",
        "research_gap",
        "proposed_mechanism",
        "model_assumptions",
        "major_model_revision",
        "final_contribution_claim",
        "screening_protocol",
        "screening_review",
    }

    def __init__(
        self, mode: str = "high", events: Any | None = None, source: str = "autonomy.configurable"
    ) -> None:
        if mode not in ("high", "interactive"):
            raise ValueError(f"autonomy mode must be high or interactive, got {mode!r}")
        self._mode = mode
        self._events = events
        self._source = source

    @property
    def mode(self) -> str:
        return self._mode

    async def requires_approval(self, checkpoint: str) -> bool:
        if self._mode == "high":
            return False
        # interactive: require approval for important checkpoints
        return checkpoint in self.IMPORTANT_CHECKPOINTS

    async def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        needs = await self.requires_approval(request.checkpoint)
        if not needs:
            decision = ApprovalDecision(
                request_id=request.request_id,
                approved=True,
                reason="auto-approved (high autonomy or non-critical checkpoint)",
                decided_by=f"policy:{self._mode}",
            )
        else:
            # In interactive mode, for Phase 1 we auto-approve but mark as interactive
            # Real interactive would prompt human; Phase 1 records intent.
            # We emit an approval.requested event and auto-resolve for now.
            decision = ApprovalDecision(
                request_id=request.request_id,
                approved=True,
                reason="auto-approved in Phase 1 interactive stub; future would require human",
                decided_by=f"policy:{self._mode}",
            )

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="approval.requested",
                        source=self._source,
                        payload={
                            "request": request.model_dump(),
                            "requires_approval": needs,
                        },
                    )
                )
                await self._events.publish(
                    Event.create(
                        event_type="approval.resolved",
                        source=self._source,
                        payload={"decision": decision.model_dump()},
                    )
                )
            except Exception:
                pass
        return decision


class ConfigurableAutonomyPlugin(Plugin):
    def __init__(self, mode: str | None = None) -> None:
        self._mode_override = mode
        self._policy: ConfigurableAutonomyPolicy | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="autonomy.configurable",
            version="0.1.0",
            plugin_type="autonomy_policy",
            description="Configurable autonomy policy (high / interactive)",
            provides=["autonomy_policy.default"],
            requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        mode = self._mode_override
        if mode is None:
            # cfg may be {"autonomy": "high"} or {"mode": "high"}
            if "autonomy" in cfg:
                mode = cfg["autonomy"]  # type: ignore[assignment]
            elif "mode" in cfg:
                mode = cfg["mode"]  # type: ignore[assignment]
            else:
                mode = "high"
        assert isinstance(mode, str)
        policy = ConfigurableAutonomyPolicy(mode=mode, events=ctx.events, source=ctx.plugin_id)
        self._policy = policy
        ctx.register("autonomy_policy.default", policy)
