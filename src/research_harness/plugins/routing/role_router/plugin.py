"""Role-based model router plugin."""

from __future__ import annotations

import logging
from typing import Any

from research_harness.contracts.model import ModelRequest, ModelResponse
from research_harness.kernel.errors import ModelError, ServiceError
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata

logger = logging.getLogger(__name__)


class RoleRouter:
    """Maps logical roles to provider/model and delegates to provider."""

    def __init__(
        self,
        roles: dict[str, dict[str, str]],
        service_lookup: Any,
    ) -> None:
        # roles: role -> {provider, model}
        self._roles = roles
        self._service_lookup = service_lookup

    def resolve(self, role: str) -> dict[str, str]:
        cfg = self._roles.get(role)
        if cfg is None:
            available = sorted(self._roles.keys())
            raise ModelError(f"unknown model role {role!r}. Available roles: {available}")
        return dict(cfg)

    async def complete(self, role: str, request: ModelRequest) -> ModelResponse:
        mapping = self.resolve(role)
        provider_name = mapping.get("provider", "openrouter")
        model_slug = mapping.get("model")
        if not model_slug:
            raise ModelError(f"role {role!r} has no model configured")

        service_name = f"model_provider.{provider_name}"
        try:
            provider = self._service_lookup(service_name)
        except ServiceError as e:
            raise ModelError(f"no provider for role {role!r} ({service_name}): {e}") from e

        # Inject model into request metadata without mutating original
        new_metadata = dict(request.metadata)
        new_metadata["model"] = model_slug
        # Keep provider hint if needed
        routed_request = request.model_copy(update={"metadata": new_metadata})

        logger.debug("routing role %s -> %s/%s", role, provider_name, model_slug)
        return await provider.complete(routed_request)


class RoleRouterPlugin(Plugin):
    """Provides model_router.default via role-based routing."""

    def __init__(self) -> None:
        self._ctx: PluginContext | None = None
        self._router: RoleRouter | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="routing.role_router",
            version="0.1.0",
            plugin_type="model_router",
            description="Role-based model router",
            provides=["model_router.default"],
            requires=["model_provider.openrouter"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        cfg = ctx.config or {}
        # ctx.config may contain {"models": {"roles": {...}}} from Runtime
        # or directly {"roles": {...}}
        models_cfg: dict[str, Any] = {}
        if "models" in cfg:
            models_cfg = cfg["models"]  # type: ignore[assignment]
        elif "roles" in cfg:
            models_cfg = {"roles": cfg["roles"]}

        roles: dict[str, dict[str, str]] = {}
        # models_cfg expected shape {"roles": {"fast": {"provider":"openrouter","model":"..."}, ...}}
        raw_roles = models_cfg.get("roles", {})  # type: ignore[union-attr]
        if isinstance(raw_roles, dict):
            for role, rcfg in raw_roles.items():
                if isinstance(rcfg, dict):
                    provider = rcfg.get("provider", "openrouter")
                    model = rcfg.get("model", "")
                    if model:
                        roles[role] = {"provider": str(provider), "model": str(model)}
                    else:
                        # Allow empty model for testing; still register but will error on use
                        roles[role] = {"provider": str(provider), "model": ""}

        if not roles:
            # Provide defaults that will fail gracefully if used without config
            logger.warning(
                "role router has no roles configured; routing will fail until configured"
            )

        def lookup(name: str) -> Any:
            return ctx.require(name)

        self._router = RoleRouter(roles=roles, service_lookup=lookup)
        ctx.register("model_router.default", self._router)

    async def teardown(self) -> None:
        self._router = None
