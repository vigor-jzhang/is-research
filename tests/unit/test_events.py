import pytest

from research_harness.kernel.events import Event, EventBus


@pytest.mark.asyncio
async def test_publish_and_subscribe():
    bus = EventBus()
    received: list[Event] = []

    async def handler(e: Event) -> None:
        received.append(e)

    bus.subscribe("test.event", handler)
    evt = Event.create(event_type="test.event", source="unit", payload={"x": 1})
    await bus.publish(evt)
    assert len(received) == 1
    assert received[0].payload["x"] == 1


@pytest.mark.asyncio
async def test_multiple_subscribers():
    bus = EventBus()
    counts = [0, 0]

    async def h1(_: Event) -> None:
        counts[0] += 1

    async def h2(_: Event) -> None:
        counts[1] += 1

    bus.subscribe("a", h1)
    bus.subscribe("a", h2)
    await bus.publish(Event.create(event_type="a", source="s", payload={}))
    assert counts == [1, 1]


@pytest.mark.asyncio
async def test_wildcard():
    bus = EventBus()
    received: list[str] = []

    async def handler(e: Event) -> None:
        received.append(e.event_type)

    bus.subscribe("*", handler)
    await bus.publish(Event.create(event_type="x", source="s", payload={}))
    await bus.publish(Event.create(event_type="y", source="s", payload={}))
    assert received == ["x", "y"]


@pytest.mark.asyncio
async def test_ordering():
    bus = EventBus()
    order: list[int] = []

    async def handler(e: Event) -> None:
        order.append(e.payload["n"])

    bus.subscribe("ordered", handler)
    for i in range(5):
        await bus.publish(Event.create(event_type="ordered", source="s", payload={"n": i}))
    assert order == list(range(5))
    assert len(bus.history()) == 5


@pytest.mark.asyncio
async def test_subscriber_failure_isolation():
    bus = EventBus()
    ok_received: list[int] = []

    async def failing(_: Event) -> None:
        raise RuntimeError("boom")

    async def ok(e: Event) -> None:
        ok_received.append(1)

    bus.subscribe("evt", failing)
    bus.subscribe("evt", ok)
    # Should not raise despite failing handler
    await bus.publish(Event.create(event_type="evt", source="s", payload={}))
    assert ok_received == [1]


@pytest.mark.asyncio
async def test_unsubscribe():
    bus = EventBus()
    calls: list[int] = []

    async def handler(_: Event) -> None:
        calls.append(1)

    unsub = bus.subscribe("evt", handler)
    await bus.publish(Event.create(event_type="evt", source="s", payload={}))
    unsub()
    await bus.publish(Event.create(event_type="evt", source="s", payload={}))
    assert len(calls) == 1


def test_event_fields():
    e = Event.create(
        event_type="test", source="src", payload={"a": 1}, session_id="s1", run_id="r1"
    )
    assert e.event_type == "test"
    assert e.source == "src"
    assert e.session_id == "s1"
    assert e.run_id == "r1"
    assert e.payload["a"] == 1
    assert e.schema_version == "1.0"
    assert e.event_id


@pytest.mark.asyncio
async def test_history_is_bounded():
    bus = EventBus(history_limit=2)
    for i in range(3):
        await bus.publish(Event.create(event_type="bounded", source="unit", payload={"n": i}))
    assert [event.payload["n"] for event in bus.history()] == [1, 2]


def test_negative_history_limit_is_rejected():
    with pytest.raises(ValueError, match="history_limit"):
        EventBus(history_limit=-1)
