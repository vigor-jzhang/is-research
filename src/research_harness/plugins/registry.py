"""Registry of built-in plugins."""

from __future__ import annotations

from collections.abc import Callable

from research_harness.kernel.plugin import Plugin


def _create_openrouter() -> Plugin:
    from research_harness.plugins.models.openrouter.plugin import OpenRouterPlugin

    return OpenRouterPlugin()


def _create_role_router() -> Plugin:
    from research_harness.plugins.routing.role_router.plugin import RoleRouterPlugin

    return RoleRouterPlugin()


def _create_echo() -> Plugin:
    from research_harness.plugins.tools.echo.plugin import EchoToolPlugin

    return EchoToolPlugin()


def _create_loop() -> Plugin:
    from research_harness.plugins.loops.simple_tool_loop.plugin import SimpleToolLoopPlugin

    return SimpleToolLoopPlugin()


def _create_session() -> Plugin:
    from research_harness.plugins.sessions.jsonl.plugin import JsonlSessionPlugin

    return JsonlSessionPlugin()


def _create_autonomy() -> Plugin:
    from research_harness.plugins.autonomy.configurable.plugin import ConfigurableAutonomyPlugin

    return ConfigurableAutonomyPlugin()


BUILTIN_PLUGINS: dict[str, Callable[[], Plugin]] = {
    "model.openrouter": _create_openrouter,
    "routing.role_router": _create_role_router,
    "tool.echo": _create_echo,
    "loop.simple_tool_loop": _create_loop,
    "session.jsonl": _create_session,
    "autonomy.configurable": _create_autonomy,
}


def create_plugin(plugin_id: str) -> Plugin:
    """Create a built-in plugin by id.

    For merged builtin+external discovery, use research_harness.app.bootstrap.create_plugin.
    """
    factory = BUILTIN_PLUGINS.get(plugin_id)
    if factory is None:
        available = sorted(BUILTIN_PLUGINS.keys())
        raise ValueError(f"unknown plugin {plugin_id!r}. Available built-ins: {available}")
    return factory()


def list_builtin_ids() -> list[str]:
    return sorted(BUILTIN_PLUGINS.keys())
