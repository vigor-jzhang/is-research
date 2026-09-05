"""Policy-constrained model router (Phase 7C, shadow mode only).

Decision support + shadow evaluation over persisted RoleLeaderboard evidence.
Consumes leaderboards from the artifact store, applies capability + quality +
reliability + constraint gates, and selects a model under an explicit
documented policy. Never uses an LLM to choose. Never replaces the configured
production role model: the decision is recorded (with a shadow comparison)
and production behavior is unchanged in Phase 7C.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from research_harness.kernel.errors import PluginError
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.routing.policies import (
    default_policy_id,
    get_policy,
    list_policies,
)
from research_harness.research.routing.roles import validate_role
from research_harness.research.routing.selection import (
    DEFAULT_REQUIRED_PASS_RATE,
    build_assessments,
    decide_status,
    filter_eligible,
    select,
)
from research_harness.research.schemas.routing import (
    RoutingDecision,
    RoutingDecisionStatus,
    RoutingRequest,
)
from research_harness.research.schemas.tournament import RoleLeaderboard

_PRODUCER = "routing.policy_router"


class PolicyModelRouterService:
    def __init__(
        self,
        *,
        artifact_store: Any,
        service_lookup: Any,
        current_roles: dict[str, dict[str, str]] | None = None,
        leaderboard_max_age_seconds: float | None = None,
        default_required_pass_rate: float = DEFAULT_REQUIRED_PASS_RATE,
        producer: str = _PRODUCER,
    ) -> None:
        self._store = artifact_store
        self._lookup = service_lookup
        self._current_roles = dict(current_roles or {})
        self._max_age = leaderboard_max_age_seconds
        self._default_rate = default_required_pass_rate
        self._producer = producer

    @property
    def service_id(self) -> str:
        return "policy_model_router.default"

    def list_policies(self) -> list[dict[str, Any]]:
        return [
            {
                "policy_id": spec.policy_id,
                "name": spec.name,
                "description": spec.description,
                "selection_rules": spec.selection_rules,
            }
            for spec in list_policies()
        ]

    async def decide(
        self,
        role: str,
        policy_id: str | None = None,
        request: RoutingRequest | None = None,
        *,
        use_fallback: bool = False,
    ) -> RoutingDecision:
        validate_role(role)
        policy = get_policy(policy_id or default_policy_id())
        req = (request or RoutingRequest(role=role)).model_copy(update={"role": role})
        decision = await self._decide(role, policy.policy_id, req, use_fallback=use_fallback)
        await self._persist(decision)
        return decision

    async def shadow(
        self,
        role: str,
        policy_id: str | None = None,
        request: RoutingRequest | None = None,
        *,
        use_fallback: bool = False,
    ) -> RoutingDecision:
        validate_role(role)
        policy = get_policy(policy_id or default_policy_id())
        req = (request or RoutingRequest(role=role)).model_copy(update={"role": role})
        decision = await self._decide(role, policy.policy_id, req, use_fallback=use_fallback)
        decision.shadow = self._shadow_comparison(role, decision)
        await self._persist(decision)
        return decision

    async def get_decision(self, decision_id: str) -> RoutingDecision:
        return (await self._store.get(decision_id)).parse_payload(RoutingDecision)

    # ------------------------------------------------------------------

    async def _decide(
        self,
        role: str,
        policy_id: str,
        request: RoutingRequest,
        *,
        use_fallback: bool,
    ) -> RoutingDecision:
        spec = get_policy(policy_id)
        leaderboard, age, too_old, insufficient_reps = await self._load_leaderboard(role, request)

        evidence_usable = leaderboard is not None and not too_old and not insufficient_reps
        eligible: list[Any] = []
        rejected: list[Any] = []
        if leaderboard is not None and evidence_usable:
            capability_map: dict[str, bool] = {}
            for entry in leaderboard.entries:
                provider = str((entry.model or {}).get("provider") or "openrouter")
                capability_map[entry.candidate_id] = self._capability_ok(provider, request)
            assessments = build_assessments(leaderboard, capability_map)
            eligible, rejected = filter_eligible(assessments, request)

        status, reason = decide_status(
            leaderboard,
            eligible,
            too_old,
            insufficient_reps,
            use_fallback=use_fallback,
        )
        primary, fallback = (None, None)
        if status in (RoutingDecisionStatus.selected, RoutingDecisionStatus.fallback):
            primary, fallback = select(eligible, spec, use_fallback=use_fallback)
        selected = primary if primary is not None else None

        return RoutingDecision(
            policy_id=spec.policy_id,
            policy_version="1",
            policy_rules=dict(spec.selection_rules),
            request=request,
            role=role,
            leaderboard_id=leaderboard.id if leaderboard is not None else None,
            leaderboard_age_seconds=age,
            status=status,
            selected_candidate_id=selected.candidate_id if selected else None,
            selected_model=selected.model if selected else None,
            eligible_candidates=eligible,
            rejected_candidates=rejected,
            fallback_candidate_id=fallback.candidate_id if fallback else None,
            expected_quality=selected.deterministic_pass_rate if selected else None,
            expected_latency_ms=selected.latency_ms_p50 if selected else None,
            expected_cost=selected.estimated_cost if selected else None,
            rationale={
                "gate": spec.selection_rules.get("gate"),
                "rank": spec.selection_rules.get("rank"),
                "reason": reason,
                "eligible_count": len(eligible),
                "rejected_count": len(rejected),
                "use_fallback": use_fallback,
                "required_pass_rate": (
                    self._default_rate
                    if request.required_deterministic_pass_rate is None
                    else request.required_deterministic_pass_rate
                ),
            },
        )

    def _capability_ok(self, provider: str, request: RoutingRequest) -> bool:
        """Check required capabilities against the provider's ModelCapabilities.
        Reuses provider capability metadata; no provider-specific logic here."""
        try:
            provider_obj = self._lookup(f"model_provider.{provider}")
        except Exception as e:  # noqa: BLE001
            # M29: a provider that cannot be resolved is a wiring fault, not a
            # model capability. Returning False here marked every candidate on
            # that provider "capability rejected", which can empty a whole role
            # while the recorded reason blames the models.
            raise PluginError(
                f"cannot resolve model provider {provider!r} to check capabilities: {e}"
            ) from e
        caps = getattr(provider_obj, "capabilities", None)
        if caps is None:
            return False
        if request.require_structured_output and not bool(
            getattr(caps, "structured_output", False)
        ):
            return False
        if request.required_context_tokens:
            context = getattr(caps, "context_length", None)
            if context is None or context < request.required_context_tokens:
                return False
        return True

    async def _load_leaderboard(
        self, role: str, request: RoutingRequest
    ) -> tuple[RoleLeaderboard | None, float | None, bool, bool]:
        def _evidence_ok(board: RoleLeaderboard) -> bool:
            if not request.evidence_types:
                return True
            return board.evidence_type in request.evidence_types

        if request.leaderboard_ids:
            boards: list[RoleLeaderboard] = []
            for lid in request.leaderboard_ids:
                try:
                    env = await self._store.get(lid)
                except Exception:  # noqa: BLE001
                    continue
                if env.artifact_type != "role_leaderboard":
                    continue
                board = env.parse_payload(RoleLeaderboard)
                if board.role != role:
                    continue  # role isolation: only the requested role's evidence
                if not _evidence_ok(board):
                    continue
                boards.append(board)
        else:
            boards = [
                env.parse_payload(RoleLeaderboard)
                for env in await self._store.list(artifact_type="role_leaderboard")
                if env.payload.get("role") == role
            ]
            boards = [b for b in boards if _evidence_ok(b)]
        if not boards:
            return None, None, False, False
        leaderboard = max(boards, key=lambda b: b.created_at)
        age = (datetime.now(UTC) - leaderboard.created_at).total_seconds()
        max_age = (
            self._max_age
            if request.leaderboard_max_age_seconds is None
            else request.leaderboard_max_age_seconds
        )
        too_old = max_age is not None and age > max_age
        repetitions = int(leaderboard.metadata.get("repetitions") or 1)
        insufficient_reps = repetitions < request.min_repetitions
        return leaderboard, age, too_old, insufficient_reps

    def _shadow_comparison(self, role: str, decision: RoutingDecision) -> dict[str, Any]:
        current = self._current_roles.get(role) or {}
        current_model = current.get("model")
        current_provider = current.get("provider")
        selected = next(
            (
                a
                for a in decision.eligible_candidates
                if a.candidate_id == decision.selected_candidate_id
            ),
            None,
        )
        would_switch = (
            selected is not None
            and current_model is not None
            and selected.requested_model != current_model
        )
        current_entry = None
        if current_model is not None:
            current_entry = next(
                (
                    a
                    for a in decision.eligible_candidates + decision.rejected_candidates
                    if a.requested_model == current_model
                ),
                None,
            )
        shadow: dict[str, Any] = {
            "routing_mode": "shadow",
            "current_provider": current_provider,
            "current_model": current_model,
            "would_switch": would_switch if selected is not None else None,
            "same_as_current": (not would_switch)
            if selected is not None and current_model is not None
            else None,
            "expected_quality_delta": None,
            "expected_cost_delta": None,
            "expected_latency_delta": None,
        }
        if selected is not None and current_entry is not None:
            quality = current_entry.deterministic_pass_rate
            cost = current_entry.estimated_cost
            latency = current_entry.latency_ms_p50
            if selected.deterministic_pass_rate is not None and quality is not None:
                shadow["expected_quality_delta"] = selected.deterministic_pass_rate - quality
            if selected.estimated_cost is not None and cost is not None:
                shadow["expected_cost_delta"] = selected.estimated_cost - cost
            if selected.latency_ms_p50 is not None and latency is not None:
                shadow["expected_latency_delta"] = selected.latency_ms_p50 - latency
        return shadow

    async def _persist(self, decision: RoutingDecision) -> None:
        await self._store.put(
            ArtifactEnvelope.create(
                payload=decision,
                artifact_type="routing_decision",
                producer=self._producer,
                artifact_id=decision.id,
            )
        )


class PolicyModelRouterPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="routing.policy_router",
            version="0.1.0",
            plugin_type="model_router",
            description=(
                "Policy-constrained model router (decision support + shadow "
                "evaluation, Phase 7C); never switches production models"
            ),
            provides=["policy_model_router.default"],
            requires=["artifact_store.default", "model_provider.openrouter"],
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

        def _lookup(name: str) -> Any:
            return ctx.require(name)

        service = PolicyModelRouterService(
            artifact_store=ctx.require("artifact_store.default"),
            service_lookup=_lookup,
            current_roles=current_roles,
            leaderboard_max_age_seconds=routing_cfg.get("leaderboard_max_age_seconds"),
            default_required_pass_rate=float(
                routing_cfg.get("default_required_pass_rate") or DEFAULT_REQUIRED_PASS_RATE
            ),
        )
        ctx.register("policy_model_router.default", service)
