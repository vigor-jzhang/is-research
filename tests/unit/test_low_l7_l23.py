"""Regression tests for two Low-tier findings (round 34).

L7 — `proposition_generator` hardcoded `symbols_used=[]`, so every proposition
declared an empty symbol set while its expression parsed to several.
L23 — optional dependencies participated in cycle detection, so a legitimate
"A requires B, B optionally uses A" configuration refused to start.
"""

from __future__ import annotations

from typing import Any

import pytest

from research_harness.kernel.errors import PluginDependencyError
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata

# ---------------------------------------------------------------------------
# L7 — declared symbol set was empty
# ---------------------------------------------------------------------------


def test_proposition_declares_the_symbols_it_uses() -> None:
    """L7: `symbols_used=[]` declared an empty set for every proposition.

    The verifier builds a local symbol table from that declaration, so it parsed
    `pi*E` with no symbols declared and read `pi` as a free symbol rather than
    the constant.
    """
    from research_harness.plugins.research.proposition_generator.plugin import (
        PropositionGeneratorService,
    )

    svc = PropositionGeneratorService.__new__(PropositionGeneratorService)
    symbols = svc._free_symbols("alpha - beta*gamma")
    assert symbols == {"alpha", "beta", "gamma"}, f"declared {symbols}"


def test_unparseable_expression_declares_nothing() -> None:
    """Guard: a bad expression must not raise; it declares no symbols."""
    from research_harness.plugins.research.proposition_generator.plugin import (
        PropositionGeneratorService,
    )

    svc = PropositionGeneratorService.__new__(PropositionGeneratorService)
    assert svc._free_symbols("not a valid expression (((") == set()


def test_symbols_used_is_no_longer_hardcoded() -> None:
    """L7: the literal empty list must not come back."""
    import pathlib

    path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "src"
        / "research_harness"
        / "plugins"
        / "research"
        / "proposition_generator"
        / "plugin.py"
    )
    src = path.read_text(encoding="utf-8")
    assert "symbols_used=[]" not in src, "symbols_used is hardcoded empty again"
    assert "_free_symbols" in src


# ---------------------------------------------------------------------------
# L23 — optional dependencies must not stop the runtime
# ---------------------------------------------------------------------------


class _FakePlugin(Plugin):
    def __init__(
        self,
        pid: str,
        provides: list[str],
        requires: list[str],
        optional_requires: list[str] | None = None,
    ) -> None:
        self._pid = pid
        self._provides = provides
        self._requires = requires
        self._optional = optional_requires or []

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id=self._pid,
            version="0.1.0",
            plugin_type="tool",
            description="fake",
            provides=self._provides,
            requires=self._requires,
            optional_requires=self._optional,
        )

    async def setup(self, ctx: PluginContext) -> None:
        return None

    async def teardown(self) -> None:
        return None


def _manager(plugins: list[Plugin]) -> Any:
    from research_harness.kernel.events import EventBus
    from research_harness.kernel.manager import PluginManager
    from research_harness.kernel.services import ServiceRegistry

    mgr = PluginManager(
        services=ServiceRegistry(),
        events=EventBus(),
        plugin_configs={},
        runtime_meta={},
    )
    for p in plugins:
        mgr.register(p)
    return mgr


def test_optional_back_edge_does_not_refuse_to_start() -> None:
    """L23: A requires B while B optionally uses A is legitimate.

    Optional dependencies were added to the same graph used for cycle detection,
    so this pair looked like a cycle and the runtime refused to load.
    """
    a = _FakePlugin("a", provides=["svc.a"], requires=["svc.b"])
    b = _FakePlugin("b", provides=["svc.b"], requires=[], optional_requires=["svc.a"])
    order = _manager([a, b]).resolve_order()
    assert sorted(order) == ["a", "b"], f"got {order}"


def test_required_cycle_still_raises() -> None:
    """Guard: a real cycle among required deps must still be rejected."""
    a = _FakePlugin("a", provides=["svc.a"], requires=["svc.b"])
    b = _FakePlugin("b", provides=["svc.b"], requires=["svc.a"])
    with pytest.raises(PluginDependencyError, match="cycle"):
        _manager([a, b]).resolve_order()


def test_optional_ordering_still_applies_when_acyclic() -> None:
    """Guard: optional edges still order plugins when they can.

    `c` optionally uses `svc.d`, so `d` should come first even though nothing
    requires it.
    """
    d = _FakePlugin("d", provides=["svc.d"], requires=[])
    c = _FakePlugin("c", provides=["svc.c"], requires=[], optional_requires=["svc.d"])
    order = _manager([c, d]).resolve_order()
    assert order.index("d") < order.index("c"), f"got {order}"
