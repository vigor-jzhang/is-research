"""Regression tests for bootstrap/config (round 29).

Batch: M53 (plugin discovery: silent failures, no error isolation, re-scan per
plugin), M54 (dotenv parsing), M56 (contracts accept unknown fields), M59 (store
TOCTOU — already fixed, marked here), M61 (secret scrubbing).

M62 is deliberately not fixed this round — see the round-29 notes.
"""

from __future__ import annotations

import importlib.metadata
import os
import pathlib
from typing import Any
from unittest.mock import patch

import pytest

from research_harness.app.bootstrap import (
    create_plugin,
    discover_external_factories,
    get_all_plugin_factories,
)
from research_harness.config.dotenv import _load_file, load_dotenv
from research_harness.contracts.common import Usage
from research_harness.contracts.loop import LoopResult
from research_harness.contracts.model import Message, ModelResponse, ToolCall, ToolSpec
from research_harness.contracts.session import SessionEvent, SessionMetadata
from research_harness.plugins.sessions.jsonl.plugin import _scrub_sensitive


def _make_ep(name: str, obj: Any) -> Any:
    """A stand-in for an importlib EntryPoint."""

    class _EP:
        value = f"fake.module:{name}"

        def __init__(self) -> None:
            self.name = name

        def load(self) -> Any:
            return obj

    return _EP()


# ---------------------------------------------------------------------------
# M53 — discovery failures were silent, and one bad package broke everything
# ---------------------------------------------------------------------------


def _broken(cls: type) -> type:
    """A plugin whose metadata raises, standing in for a broken distribution."""
    return cls


def test_entry_point_failure_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """M53: a discovery failure used to return [] with no trace of why."""
    import logging

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("distribution metadata is corrupt")

    with (
        caplog.at_level(logging.WARNING, logger="research_harness.app.bootstrap"),
        patch.object(importlib.metadata, "entry_points", side_effect=boom),
    ):
        discover_external_factories()
    assert any("entry point discovery" in r.message for r in caplog.records), (
        f"discovery failure was not logged: {[r.message for r in caplog.records]}"
    )


def test_duplicate_external_id_does_not_break_other_plugins() -> None:
    """M53: two externals colliding made EVERY plugin uninstantiable.

    The old code raised here, and `create_plugin` builds the merged set on every
    call — so a collision between two third-party packages took down every
    built-in too.
    """
    from research_harness.plugins.registry import BUILTIN_PLUGINS

    class _Plugin:
        pass

    ep1 = _make_ep("tool.one", _Plugin)
    ep2 = _make_ep("tool.one", _Plugin)
    with patch.object(importlib.metadata, "entry_points", return_value=[ep1, ep2]):
        factories = discover_external_factories()
    assert "tool.one" in factories

    # and a built-in is still creatable while that collision exists
    builtin_id = next(iter(BUILTIN_PLUGINS))
    plugin = create_plugin(builtin_id)
    assert plugin.metadata.id == builtin_id


def test_duplicate_against_a_builtin_is_still_rejected() -> None:
    """Guard: skipping the duplicate must not hide a shadowing conflict."""
    from research_harness.kernel.errors import PluginError

    class _Plugin:
        pass

    ep = _make_ep("tool.echo", _Plugin)
    with (
        patch.object(importlib.metadata, "entry_points", return_value=[ep]),
        pytest.raises(PluginError, match="duplicate plugin id"),
    ):
        get_all_plugin_factories()


def test_create_plugin_accepts_a_precomputed_factory_set() -> None:
    """M53: entry points were re-scanned once per plugin in the config list."""
    from research_harness.plugins.registry import BUILTIN_PLUGINS

    calls = {"n": 0}
    real = importlib.metadata.entry_points

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        return real(*args, **kwargs)

    builtin_id = next(iter(BUILTIN_PLUGINS))
    with patch.object(importlib.metadata, "entry_points", side_effect=counting):
        factories = get_all_plugin_factories()
        for pid in list(BUILTIN_PLUGINS)[:3]:
            create_plugin(pid, factories=factories)
    # one scan for the whole batch, not one per plugin
    assert calls["n"] <= 1, f"entry points scanned {calls['n']} times for a 3-plugin batch"


# ---------------------------------------------------------------------------
# M54 — dotenv parsing
# ---------------------------------------------------------------------------


def test_export_prefix_is_stripped(tmp_path: pathlib.Path) -> None:
    """M54: `export FOO=bar` defined a variable literally named "export FOO"."""
    env = tmp_path / ".env"
    env.write_text("export MY_VAR=hello\n", encoding="utf-8")
    os.environ.pop("MY_VAR", None)
    try:
        _load_file(env)
        assert os.environ.get("MY_VAR") == "hello"
    finally:
        os.environ.pop("MY_VAR", None)


def test_inline_comment_is_not_part_of_the_value(tmp_path: pathlib.Path) -> None:
    """M54: `FOO=bar # note` set FOO to the whole rest of the line."""
    env = tmp_path / ".env"
    env.write_text("MY_VAR=hello # a note\nOTHER=2\n", encoding="utf-8")
    os.environ.pop("MY_VAR", None)
    os.environ.pop("OTHER", None)
    try:
        _load_file(env)
        assert os.environ.get("MY_VAR") == "hello"
        assert os.environ.get("OTHER") == "2"
    finally:
        os.environ.pop("MY_VAR", None)
        os.environ.pop("OTHER", None)


def test_quoted_hash_is_kept(tmp_path: pathlib.Path) -> None:
    """Guard: a `#` inside quotes is data, not a comment."""
    env = tmp_path / ".env"
    env.write_text('MY_VAR="a#b"\n', encoding="utf-8")
    os.environ.pop("MY_VAR", None)
    try:
        _load_file(env)
        assert os.environ.get("MY_VAR") == "a#b"
    finally:
        os.environ.pop("MY_VAR", None)


def test_discovery_stops_at_the_project_root(tmp_path: pathlib.Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """M54: walking a fixed 4 levels up could load a .env from outside the project.

    The project root is `outer`; `outer/.env` is legitimately ours. A .env in the
    directory *above* the project is not, and must not be loaded.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    # No .env inside the project; one directly above it. The project root is the
    # cwd itself, so nothing above it belongs to us.
    (tmp_path / ".env").write_text("OUTSIDE=1\n", encoding="utf-8")

    os.environ.pop("OUTSIDE", None)
    monkeypatch.chdir(proj)
    try:
        load_dotenv()
        assert "OUTSIDE" not in os.environ, (
            "loaded a .env from above the project root"
        )
    finally:
        os.environ.pop("OUTSIDE", None)


def test_project_env_is_still_found_from_a_subdirectory(
    tmp_path: pathlib.Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """Guard: stopping at the root must not stop us finding our own .env."""
    proj = tmp_path / "proj"
    sub = proj / "a" / "b"
    sub.mkdir(parents=True)
    (proj / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (proj / ".env").write_text("INSIDE=1\n", encoding="utf-8")

    os.environ.pop("INSIDE", None)
    monkeypatch.chdir(sub)
    try:
        load_dotenv()
        assert os.environ.get("INSIDE") == "1", "the project's own .env was not loaded"
    finally:
        os.environ.pop("INSIDE", None)


# ---------------------------------------------------------------------------
# M56 — contracts silently discarded unknown fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    [LoopResult, Message, ToolCall, ToolSpec, ModelResponse, Usage, SessionEvent, SessionMetadata],
)
def test_contract_forbids_unknown_fields(model: Any) -> None:
    """M56: a misspelled field was silently dropped.

    Asserted on the config rather than by constructing each model: most of these
    have required fields, so `Model(unknown=1)` raises for the wrong reason
    (missing field) even when extra fields are allowed.
    """
    assert model.model_config.get("extra") == "forbid", (
        f"{model.__name__} still accepts unknown fields"
    )


def test_model_response_rejects_a_misspelled_field() -> None:
    """M56, concretely: the case that matters most.

    `ModelResponse` with a misspelled usage field used to construct happily with
    the usage missing, and every cost report downstream silently read zero.
    """
    with pytest.raises(Exception, match="extra"):
        ModelResponse(  # type: ignore[call-arg]
            content="hi",
            tool_calls=[],
            usage=None,
            raw=None,
            usagge={"prompt_tokens": 1},
        )


# ---------------------------------------------------------------------------
# M59 — store TOCTOU (fixed earlier, never marked)
# ---------------------------------------------------------------------------


def test_put_checks_and_inserts_in_one_critical_section() -> None:
    """M59: the immutability check and the insert must not be separable."""
    import ast

    path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "src"
        / "research_harness"
        / "plugins"
        / "storage"
        / "artifacts_sqlite"
        / "plugin.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "put":
            for sub in ast.walk(node):
                if isinstance(sub, ast.With):
                    body = ast.unparse(sub)
                    if "_exists_sync" in body and "INSERT INTO artifacts" in body:
                        return
    pytest.fail("put() no longer checks existence and inserts under one lock")


# ---------------------------------------------------------------------------
# M61 — secret scrubbing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key", ["credentials", "Credentials", "client_secret", "private_key", "cookie"]
)
def test_newly_recognised_key_names_are_scrubbed(key: str) -> None:
    """M61: `credentials` was the obvious omission — no sensitive name matched."""
    assert _scrub_sensitive({key: "s3cr3t"}) == {}


def test_secret_in_free_text_is_redacted() -> None:
    """M61: an error echoing `Bearer sk-or-v1-…` has no sensitive key to match."""
    out = _scrub_sensitive({"detail": "upstream said 401: Bearer sk-or-v1-abc123def"})
    assert "sk-or-v1-abc123def" not in str(out)
    assert "REDACTED" in str(out)


def test_read_scrubs_too(tmp_path: pathlib.Path) -> None:
    """M61: read() applied no scrubbing, so anything already on disk leaked."""
    import asyncio

    from research_harness.plugins.sessions.jsonl.plugin import JsonlSessionStore

    async def _run() -> list[dict[str, Any]]:
        store = JsonlSessionStore(root=tmp_path)
        sid = await store.create_session()
        events_path = tmp_path / sid / "events.jsonl"
        # write as a previous version would have: unscrubbed
        events_path.write_text(
            '{"event_type": "x", "api_key": "leaked-key"}\n', encoding="utf-8"
        )
        return await store.read(sid)

    events = asyncio.run(_run())
    assert "leaked-key" not in str(events), "read() did not scrub a secret already on disk"
