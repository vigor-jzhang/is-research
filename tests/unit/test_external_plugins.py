"""Tests for external plugin discovery via entry_points."""

import importlib.metadata
from unittest.mock import MagicMock, patch

import pytest

from research_harness.app.bootstrap import (
    create_plugin,
    discover_external_factories,
    get_all_plugin_factories,
    list_available_plugins,
)
from research_harness.config.loader import load_config_from_dict
from research_harness.kernel.errors import PluginError
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata


class FakeExternalToolPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="tool.external_echo",
            version="0.1.0",
            plugin_type="tool",
            description="external echo",
            provides=["tool.external_echo"],
            requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        class Tool:
            name = "external_echo"
            description = "external"
            input_schema = {"type": "object", "properties": {}}

            async def execute(self, args):  # type: ignore[no-untyped-def]
                return {"ok": True}

        ctx.register("tool.external_echo", Tool())


class FakeExternalDuplicatePlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="tool.echo",  # duplicates built-in
            version="0.1.0",
            plugin_type="tool",
            description="duplicate",
            provides=["tool.echo"],
            requires=[],
        )


def _make_ep(name: str, obj: object, value: str | None = None):  # type: ignore[no-untyped-def]
    ep = MagicMock()
    ep.name = name
    ep.value = value or f"fake.module:{name}"
    ep.load.return_value = obj
    return ep


def test_external_discovered_and_loaded():
    ep = _make_ep("tool.external_echo", FakeExternalToolPlugin)
    with patch.object(importlib.metadata, "entry_points", return_value=[ep]):
        factories = discover_external_factories()
        assert "tool.external_echo" in factories
        plugin = factories["tool.external_echo"]()
        assert plugin.metadata.id == "tool.external_echo"
        assert isinstance(plugin, Plugin)


def test_external_service_registered_via_runtime(tmp_path):
    ep = _make_ep("tool.external_echo", FakeExternalToolPlugin)
    with patch.object(importlib.metadata, "entry_points", return_value=[ep]):
        from research_harness.app.bootstrap import build_runtime

        cfg = load_config_from_dict(
            {
                "plugins": ["tool.external_echo"],
                "models": {"roles": {}},
                "session": {"root": str(tmp_path)},
            }
        )
        runtime = build_runtime(cfg)
        # Should have created plugin and registered service
        assert "tool.external_echo" in [p.metadata.id for p in runtime.plugins.list_plugins()]
        # Check that plugin's service is not yet registered until start
        # After start, service should be available
        import asyncio

        async def _check():
            async with runtime:
                svc = runtime.services.require("tool.external_echo")
                assert svc is not None

        asyncio.run(_check())


def test_builtin_and_external_coexist(tmp_path):
    ep = _make_ep("tool.external_echo", FakeExternalToolPlugin)
    with patch.object(importlib.metadata, "entry_points", return_value=[ep]):
        factories = get_all_plugin_factories()
        assert "tool.echo" in factories  # builtin
        assert "tool.external_echo" in factories  # external
        # Also test list_available_plugins
        lst = list_available_plugins()
        ids = {pid for pid, _ in lst}
        assert "tool.echo" in ids
        assert "tool.external_echo" in ids
        # Check sources
        src_map = dict(lst)
        assert src_map["tool.echo"] == "builtin"
        assert src_map["tool.external_echo"] == "external"


def test_duplicate_plugin_id_rejected():
    ep = _make_ep("tool.echo", FakeExternalDuplicatePlugin)
    with patch.object(importlib.metadata, "entry_points", return_value=[ep]):
        with pytest.raises(PluginError, match="duplicate plugin id"):
            get_all_plugin_factories()
        # Also via create_plugin should fail when trying to create duplicate?
        # create_plugin will call get_all_plugin_factories internally
        with pytest.raises(PluginError, match="duplicate"):
            create_plugin("tool.echo")


def test_malformed_external_plugin_rejected():
    # Factory returns object that is not a Plugin
    class NotAPlugin:
        pass

    ep = _make_ep("tool.bad", NotAPlugin)
    with patch.object(importlib.metadata, "entry_points", return_value=[ep]):
        factories = discover_external_factories()
        # Factory exists but calling it should raise PluginError
        with pytest.raises(PluginError, match="Plugin"):
            factories["tool.bad"]()
        # Via create_plugin
        with pytest.raises(PluginError, match="Plugin"):
            create_plugin("tool.bad")


def test_external_plugin_id_mismatch_rejected():
    class MismatchedPlugin(Plugin):
        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(
                id="tool.actual_id",
                version="0.1.0",
                plugin_type="tool",
                description="mismatch",
                provides=["tool.actual_id"],
                requires=[],
            )

    ep = _make_ep("tool.entry_name", MismatchedPlugin)
    with patch.object(importlib.metadata, "entry_points", return_value=[ep]):
        factories = discover_external_factories()
        with pytest.raises(PluginError, match="id mismatch"):
            factories["tool.entry_name"]()


def test_external_plugin_loading_error_reported():
    ep = _make_ep("tool.broken", MagicMock())
    ep.load.side_effect = ImportError("module not found")
    ep.value = "broken.module:Plugin"
    with patch.object(importlib.metadata, "entry_points", return_value=[ep]):
        factories = discover_external_factories()
        with pytest.raises(PluginError, match="failed to load external plugin"):
            factories["tool.broken"]()
        with pytest.raises(PluginError, match="failed to load"):
            create_plugin("tool.broken")


def test_external_factory_function():
    # Entry point points to a factory function returning Plugin instance
    def factory_func():
        return FakeExternalToolPlugin()

    ep = _make_ep("tool.external_echo", factory_func)
    with patch.object(importlib.metadata, "entry_points", return_value=[ep]):
        factories = discover_external_factories()
        plugin = factories["tool.external_echo"]()
        assert isinstance(plugin, Plugin)
        assert plugin.metadata.id == "tool.external_echo"


def test_deterministic_ordering():
    # Two externals plus builtins should be sorted deterministically
    ep1 = _make_ep("tool.z_external", FakeExternalToolPlugin)

    # Need second plugin with different id
    class OtherPlugin(Plugin):
        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(
                id="tool.a_external",
                version="0.1.0",
                plugin_type="tool",
                description="a",
                provides=["tool.a_external"],
                requires=[],
            )

    ep2 = _make_ep("tool.a_external", OtherPlugin)
    with patch.object(importlib.metadata, "entry_points", return_value=[ep1, ep2]):
        lst = list_available_plugins()
        # Should be sorted by id
        ids = [pid for pid, _ in lst]
        assert ids == sorted(ids)
