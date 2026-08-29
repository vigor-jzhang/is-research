import json
import pathlib

import pytest

from research_harness.kernel.errors import SessionError
from research_harness.plugins.sessions.jsonl.plugin import JsonlSessionStore


@pytest.mark.asyncio
async def test_create_and_append(tmp_path: pathlib.Path):
    store = JsonlSessionStore(root=tmp_path)
    sid = await store.create_session(metadata={"purpose": "test"})
    assert store.session_exists(sid)
    await store.append(sid, {"event_type": "test.event", "payload": {"x": 1}, "source": "unit"})
    events = await store.read(sid)
    assert len(events) == 1
    assert events[0]["event_type"] == "test.event"


@pytest.mark.asyncio
async def test_append_only(tmp_path: pathlib.Path):
    store = JsonlSessionStore(root=tmp_path)
    sid = await store.create_session()
    for i in range(3):
        await store.append(sid, {"event_type": f"e{i}", "source": "t", "payload": {}})
    events = await store.read(sid)
    assert len(events) == 3
    assert [e["event_type"] for e in events] == ["e0", "e1", "e2"]
    # Check file has 3 lines
    lines = (tmp_path / sid / "events.jsonl").read_text().strip().splitlines()
    assert len(lines) == 3


@pytest.mark.asyncio
async def test_corrupt_input(tmp_path: pathlib.Path):
    store = JsonlSessionStore(root=tmp_path)
    sid = await store.create_session()
    # Manually write corrupt line
    (tmp_path / sid / "events.jsonl").write_text("not json\n")
    with pytest.raises(SessionError, match="corrupt event"):
        await store.read(sid)


@pytest.mark.asyncio
async def test_secrets_not_persisted(tmp_path: pathlib.Path):
    store = JsonlSessionStore(root=tmp_path)
    sid = await store.create_session(
        metadata={"api_key": "secret123", "OPENROUTER_API_KEY": "sk-xxx"}
    )
    meta = await store.get_metadata(sid)
    assert "secret123" not in json.dumps(meta)
    assert "sk-xxx" not in json.dumps(meta)

    await store.append(
        sid,
        {"event_type": "x", "source": "t", "payload": {"token": "should_not_appear", "data": "ok"}},
    )
    events = await store.read(sid)
    assert "should_not_appear" not in json.dumps(events)


@pytest.mark.asyncio
async def test_secrets_nested_scrubbing(tmp_path: pathlib.Path):
    store = JsonlSessionStore(root=tmp_path)
    sid = await store.create_session()

    # Nested structures with various secret key casings
    payload = {
        "headers": {"Authorization": "Bearer sk-secret-123", "authorization": "Bearer xxx"},
        "config": {"api_key": "top-secret", "nested": {"access_token": "acc-123", "data": "ok"}},
        "list": [{"token": "tok-1"}, {"bearer": "bear-1"}, {"safe": "keep"}],
        "OPENROUTER_API_KEY": "sk-should-not-persist",
        "password": "pwd123",
        "secret": "s3cr3t",
        "refresh_token": "refresh-abc",
        "Authorization": "Bearer should-remove",
    }
    await store.append(sid, {"event_type": "nested", "source": "t", "payload": payload})
    events = await store.read(sid)
    dumped = json.dumps(events)
    for secret in [
        "sk-secret-123",
        "top-secret",
        "acc-123",
        "tok-1",
        "bear-1",
        "sk-should-not-persist",
        "pwd123",
        "s3cr3t",
        "refresh-abc",
        "should-remove",
    ]:
        assert secret not in dumped, f"secret {secret!r} was not scrubbed"
    # Safe data should remain
    assert "keep" in dumped
    assert "ok" in dumped


@pytest.mark.asyncio
async def test_invalid_session(tmp_path: pathlib.Path):
    store = JsonlSessionStore(root=tmp_path)
    with pytest.raises(SessionError):
        await store.append("nonexistent", {"event_type": "x", "source": "t"})


@pytest.mark.asyncio
async def test_metadata(tmp_path: pathlib.Path):
    store = JsonlSessionStore(root=tmp_path)
    sid = await store.create_session(metadata={"foo": "bar"})
    meta = await store.get_metadata(sid)
    assert meta["session_id"] == sid
    assert meta["metadata"]["foo"] == "bar"


@pytest.mark.asyncio
async def test_events_have_ids_and_timestamps(tmp_path: pathlib.Path):
    store = JsonlSessionStore(root=tmp_path)
    sid = await store.create_session()
    await store.append(sid, {"event_type": "x", "source": "t"})
    events = await store.read(sid)
    assert "event_id" in events[0]
    assert "timestamp" in events[0]


@pytest.mark.asyncio
async def test_session_id_cannot_escape_root(tmp_path: pathlib.Path):
    store = JsonlSessionStore(root=tmp_path / "sessions")
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(SessionError, match="invalid session id"):
        await store.append("../outside", {"event_type": "x", "source": "t"})
    assert not (outside / "events.jsonl").exists()
