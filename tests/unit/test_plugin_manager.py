import pytest

from research_harness.kernel.errors import PluginDependencyError, PluginError
from research_harness.kernel.events import EventBus
from research_harness.kernel.manager import PluginManager
from research_harness.kernel.plugin import Plugin, PluginMetadata
from research_harness.kernel.services import ServiceRegistry


def make_plugin(
    pid: str,
    provides: list[str] | None = None,
    requires: list[str] | None = None,
    optional: list[str] | None = None,
    record: list[str] | None = None,
    name: str | None = None,
) -> Plugin:
    _record = record
    _name = name or pid

    class P(Plugin):
        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(
                id=pid,
                version="0.1.0",
                plugin_type="test",
                description="test",
                provides=provides or [],
                requires=requires or [],
                optional_requires=optional or [],
            )

        async def setup(self, ctx):
            if _record is not None:
                _record.append(f"setup:{_name}")
            # register provides if any
            for svc in provides or []:
                ctx.register(svc, object())

        async def start(self):
            if _record is not None:
                _record.append(f"start:{_name}")

        async def stop(self):
            if _record is not None:
                _record.append(f"stop:{_name}")

        async def teardown(self):
            if _record is not None:
                _record.append(f"teardown:{_name}")

    return P()


@pytest.mark.asyncio
async def test_registration_and_order():
    order: list[str] = []
    reg = ServiceRegistry()
    bus = EventBus()
    mgr = PluginManager(services=reg, events=bus)
    # A provides svc.a, B requires svc.a
    a = make_plugin("a", provides=["svc.a"], record=order, name="a")
    b = make_plugin("b", provides=["svc.b"], requires=["svc.a"], record=order, name="b")
    c = make_plugin("c", provides=["svc.c"], requires=["svc.b"], record=order, name="c")
    mgr.register(c)
    mgr.register(a)
    mgr.register(b)
    resolved = mgr.resolve_order()
    assert resolved == ["a", "b", "c"]
    await mgr.start_all()
    # setup should be in dependency order
    assert order[:3] == ["setup:a", "setup:b", "setup:c"]
    assert order[3:6] == ["start:a", "start:b", "start:c"]
    await mgr.stop_all()
    # stop should be reverse, with teardown interleaved
    assert order[6:12] == ["stop:c", "teardown:c", "stop:b", "teardown:b", "stop:a", "teardown:a"]


@pytest.mark.asyncio
async def test_missing_dependency():
    mgr = PluginManager()
    p = make_plugin("p", requires=["svc.missing"])
    mgr.register(p)
    with pytest.raises(PluginDependencyError, match="requires service"):
        mgr.resolve_order()
    with pytest.raises(PluginDependencyError):
        await mgr.start_all()


@pytest.mark.asyncio
async def test_cycle_detection():
    mgr = PluginManager()
    a = make_plugin("a", provides=["svc.a"], requires=["svc.c"])
    b = make_plugin("b", provides=["svc.b"], requires=["svc.a"])
    c = make_plugin("c", provides=["svc.c"], requires=["svc.b"])
    mgr.register(a)
    mgr.register(b)
    mgr.register(c)
    with pytest.raises(PluginDependencyError, match="cycle"):
        mgr.resolve_order()


def test_duplicate_plugin_id():
    mgr = PluginManager()
    a1 = make_plugin("dup", provides=["svc.a"])
    a2 = make_plugin("dup", provides=["svc.b"])
    mgr.register(a1)
    with pytest.raises(PluginError, match="duplicate plugin id"):
        mgr.register(a2)


def test_duplicate_service():
    mgr = PluginManager()
    a = make_plugin("a", provides=["svc.x"])
    b = make_plugin("b", provides=["svc.x"])
    mgr.register(a)
    with pytest.raises(PluginError, match="already provided"):
        mgr.register(b)


@pytest.mark.asyncio
async def test_optional_requires_ordering():
    mgr = PluginManager()
    a = make_plugin("a", provides=["svc.a"])
    b = make_plugin("b", provides=["svc.b"], optional=["svc.a"])
    mgr.register(b)
    mgr.register(a)
    order = mgr.resolve_order()
    # a should come before b because b optionally requires a and a exists
    assert order.index("a") < order.index("b")


@pytest.mark.asyncio
async def test_optional_missing_not_error():
    mgr = PluginManager()
    b = make_plugin("b", provides=["svc.b"], optional=["svc.missing"])
    mgr.register(b)
    order = mgr.resolve_order()
    assert order == ["b"]
    await mgr.start_all()
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_deterministic_startup_order():
    # Two independent plugins should start in sorted order
    mgr = PluginManager()
    p1 = make_plugin("plugin.b", provides=["svc.b"])
    p2 = make_plugin("plugin.a", provides=["svc.a"])
    mgr.register(p1)
    mgr.register(p2)
    order = mgr.resolve_order()
    assert order == ["plugin.a", "plugin.b"]


@pytest.mark.asyncio
async def test_startup_and_shutdown_services_cleaned():
    reg = ServiceRegistry()
    bus = EventBus()
    mgr = PluginManager(services=reg, events=bus)
    a = make_plugin("a", provides=["svc.a"])
    mgr.register(a)
    await mgr.start_all()
    assert reg.has("svc.a")
    await mgr.stop_all()
    assert not reg.has("svc.a")


@pytest.mark.asyncio
async def test_event_emitted_on_lifecycle():
    bus = EventBus()
    mgr = PluginManager(events=bus)
    a = make_plugin("a", provides=["svc.a"])
    mgr.register(a)
    await mgr.start_all()
    types = [e.event_type for e in bus.history()]
    assert "plugin.loaded" in types
    assert "plugin.started" in types
    await mgr.stop_all()
    types2 = [e.event_type for e in bus.history()]
    assert "plugin.stopped" in types2


def test_plugin_metadata_validation():
    with pytest.raises(Exception):
        PluginMetadata(id="Bad ID", version="0.1.0", plugin_type="t", provides=[])  # type: ignore
    with pytest.raises(Exception):
        PluginMetadata(id="good", version="bad", plugin_type="t", provides=[])


@pytest.mark.asyncio
async def test_setup_failure_rolls_back_services_and_subscriptions():
    events = EventBus()
    manager = PluginManager(events=events)
    received: list[str] = []

    class First(Plugin):
        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(id="a_first", version="0.1.0", plugin_type="test", provides=["svc.first"])

        async def setup(self, ctx):
            ctx.register("svc.first", object())
            ctx.subscribe("probe", lambda _: received.append("called"))

    class Failing(Plugin):
        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(id="z_failing", version="0.1.0", plugin_type="test")

        async def setup(self, ctx):
            raise RuntimeError("setup failed")

    manager.register(First())
    manager.register(Failing())
    with pytest.raises(RuntimeError, match="setup failed"):
        await manager.start_all()
    assert not manager.services.has("svc.first")
    assert manager.events.handler_count("probe") == 0


# ---------------------------------------------------------------------------
# H16 / H17 / H18 — plugin lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_rollback_calls_stop_before_teardown():
    """H16: rollback must stop plugins, not just tear them down.

    Plugins acquire resources in setup() and release them in stop(), so
    teardown-only rollback leaked them (the SQLite plugin opens its connection
    in setup and closes it only in stop).
    """
    events = EventBus()
    manager = PluginManager(events=events)
    record: list[str] = []

    class Failing(Plugin):
        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(id="z_failing", version="0.1.0", plugin_type="test")

        async def setup(self, ctx):
            raise RuntimeError("setup failed")

    manager.register(make_plugin("a_ok", provides=["svc.a"], record=record))
    manager.register(Failing())
    with pytest.raises(RuntimeError, match="setup failed"):
        await manager.start_all()

    assert "stop:a_ok" in record, f"rollback never stopped the plugin: {record}"
    assert record.index("stop:a_ok") < record.index("teardown:a_ok")


@pytest.mark.asyncio
async def test_setup_that_subscribes_then_raises_cleans_up():
    """H17: a plugin that subscribes and then fails must not leak the handler.

    The cleanup list was captured only after setup returned, so a plugin that
    subscribed and then raised left its handler registered forever, firing
    against dead plugin state.
    """
    events = EventBus()
    manager = PluginManager(events=events)
    received: list[str] = []

    class SubscribeThenFail(Plugin):
        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(id="p", version="0.1.0", plugin_type="test")

        async def setup(self, ctx):
            ctx.subscribe("probe", lambda _: received.append("called"))
            raise RuntimeError("setup failed after subscribing")

    manager.register(SubscribeThenFail())
    with pytest.raises(RuntimeError, match="failed after subscribing"):
        await manager.start_all()

    assert manager.events.handler_count("probe") == 0, "leaked subscription from failed setup"


@pytest.mark.asyncio
async def test_start_all_is_idempotent():
    """H18: a second start_all() must not start every plugin again."""
    events = EventBus()
    manager = PluginManager(events=events)
    record: list[str] = []
    manager.register(make_plugin("a", provides=["svc.a"], record=record))

    await manager.start_all()
    await manager.start_all()

    assert record.count("start:a") == 1, record


@pytest.mark.asyncio
async def test_manager_can_be_reused_after_stop_all():
    """H18: stop_all() must release service providers.

    The provider map was only populated by register(), so after a stop any new
    plugin offering a previously-provided service was rejected, and the manager
    could not be used for a second cycle.
    """
    events = EventBus()
    manager = PluginManager(events=events)
    manager.register(make_plugin("a", provides=["svc.shared"]))
    await manager.start_all()
    await manager.stop_all()

    # A different plugin may now provide the same service.
    manager.register(make_plugin("b", provides=["svc.shared"]))


@pytest.mark.asyncio
async def test_setup_all_after_stop_all_still_resolves_dependencies():
    """Rebuilding providers must not break dependency validation.

    Clearing the provider map in stop_all() is what allows re-registration;
    setup_all() rebuilds it so ordering and validation still work.
    """
    events = EventBus()
    manager = PluginManager(events=events)
    record: list[str] = []
    manager.register(make_plugin("a", provides=["svc.a"], record=record))
    manager.register(make_plugin("b", requires=["svc.a"], record=record))
    await manager.start_all()
    await manager.stop_all()

    record.clear()
    await manager.start_all()

    assert record.index("setup:a") < record.index("setup:b"), record


@pytest.mark.asyncio
async def test_setup_that_registers_a_service_then_raises_cleans_up():
    """A partially-completed setup must not leave its services registered.

    The rollback loop only covered plugins that finished setup, so a plugin
    that registered a service and then raised leaked it into the registry --
    where a later plugin providing the same service would then collide.
    """
    events = EventBus()
    manager = PluginManager(events=events)

    class RegisterThenFail(Plugin):
        @property
        def metadata(self) -> PluginMetadata:
            return PluginMetadata(
                id="p", version="0.1.0", plugin_type="test", provides=["svc.leaked"]
            )

        async def setup(self, ctx):
            ctx.register("svc.leaked", object())
            raise RuntimeError("setup failed after registering")

    manager.register(RegisterThenFail())
    with pytest.raises(RuntimeError, match="failed after registering"):
        await manager.start_all()

    assert not manager.services.has("svc.leaked"), "service leaked from failed setup"
