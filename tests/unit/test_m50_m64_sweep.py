"""Regression tests for the M50-M64 sweep (round 30).

Ten findings that were never triaged into a batch: M50 (SSRF pinning silently
off), M51 (kernel exports that cannot be imported), M52 (shallow config merge),
M55 (dead, diverged helper), M57 (partial write + double close), M58 (existence
checks swallow I/O errors), M60 (fabricated timestamps), M63 (unreadable payload
scored as empty), M64 (inconsistent error type).

M62 is not fixed this round — see the round-29 notes.
"""

from __future__ import annotations

import os
import pathlib

import pytest

from research_harness.config.schema import AppConfig
from research_harness.contracts.evaluator import EvaluatorError, envelope_payload_dict
from research_harness.kernel.errors import PluginError
from research_harness.plugins.storage.blobs_filesystem.plugin import FilesystemBlobStore

# ---------------------------------------------------------------------------
# M51 — kernel exports that cannot actually be imported
# ---------------------------------------------------------------------------


def test_runtime_is_importable() -> None:
    """M51: `Runtime` was in __all__ but imported only under TYPE_CHECKING."""
    from research_harness.kernel import Runtime  # noqa: F401

    assert Runtime is not None


def test_autonomy_error_is_exported() -> None:
    """M51: AutonomyError was missing from the kernel package entirely."""
    import research_harness.kernel as k

    assert hasattr(k, "AutonomyError")
    assert "AutonomyError" in k.__all__


# ---------------------------------------------------------------------------
# M52 — per-plugin config overrides were shallow-merged
# ---------------------------------------------------------------------------


def test_override_keeps_the_rest_of_the_section() -> None:
    """M52: an override supplying one key discarded the rest of the section.

    `{"research": {"max_steps": 3}}` replaced the whole derived "research"
    section, so every other research setting silently vanished.
    """
    from research_harness.app.bootstrap import _derived_plugin_configs

    cfg = AppConfig.model_validate(
        {
            "plugins": [],
            "models": {
                "roles": {
                    "fast": {"provider": "openrouter", "model": "m"},
                    "reasoning": {"provider": "openrouter", "model": "m"},
                    "critic": {"provider": "openrouter", "model": "m"},
                }
            },
        }
    )
    overrides = {"research.mechanism_generator": {"research": {"max_steps": 3}}}
    pc = _derived_plugin_configs(cfg, overrides)
    section = pc["research.mechanism_generator"]["research"]
    assert section["max_steps"] == 3, "the override did not apply"
    assert len(section) > 1, (
        f"the override replaced the whole section; only {sorted(section)} survived"
    )


def test_override_still_wins() -> None:
    """Guard: merging deeper must not stop an override from taking effect."""
    from research_harness.app.bootstrap import _derived_plugin_configs

    cfg = AppConfig.model_validate(
        {
            "plugins": [],
            "models": {
                "roles": {
                    "fast": {"provider": "openrouter", "model": "m"},
                    "reasoning": {"provider": "openrouter", "model": "m"},
                    "critic": {"provider": "openrouter", "model": "m"},
                }
            },
        }
    )
    pc = _derived_plugin_configs(cfg, {"session.jsonl": {"session": {"root": "/tmp/x"}}})
    assert pc["session.jsonl"]["session"]["root"] == "/tmp/x"


# ---------------------------------------------------------------------------
# M55 — dead, diverged helper
# ---------------------------------------------------------------------------


def test_dead_plugin_config_helper_is_gone() -> None:
    """M55: `plugin_config()` had zero callers and had drifted ~22 ids."""
    assert not hasattr(AppConfig, "plugin_config"), (
        "the dead helper is back; it will drift from _derived_plugin_configs again"
    )


# ---------------------------------------------------------------------------
# M57 — partial write and double close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_bytes_survives_a_partial_write(tmp_path: pathlib.Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """M57: put_bytes issued a single os.write, which may write less than asked.

    With os.write forced to write only a few bytes per call, the old code
    produced a truncated blob (and then a digest mismatch). A buffered writer
    keeps writing until the data is drained.
    """
    store = FilesystemBlobStore(root=tmp_path)
    data = bytes(range(256)) * 500  # 128 000 bytes
    real_write = os.write

    def partial(fd: int, buf: bytes) -> int:
        return real_write(fd, buf[:16])

    monkeypatch.setattr(os, "write", partial)
    ref = await store.put_bytes(data, media_type="application/octet-stream")
    got = await store.get_bytes(ref)
    assert len(got) == len(data), f"wrote {len(got)} of {len(data)} bytes"
    assert got == data


@pytest.mark.asyncio
async def test_put_bytes_does_not_close_the_fd_twice(tmp_path: pathlib.Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """M57: the fd was closed inline and again in the finally (always failing)."""
    store = FilesystemBlobStore(root=tmp_path)
    real_close = os.close
    calls: list[int] = []

    def tracking_close(fd: int) -> None:
        calls.append(fd)
        try:
            real_close(fd)
        except OSError:
            pass

    monkeypatch.setattr(os, "close", tracking_close)
    await store.put_bytes(b"payload", media_type="application/octet-stream")
    doubled = [fd for fd in calls if calls.count(fd) > 1]
    assert not doubled, f"fd(s) closed more than once: {doubled}"


# ---------------------------------------------------------------------------
# M58 — existence checks swallowed I/O errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exists_does_not_report_an_io_error_as_absent(
    tmp_path: pathlib.Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """M58: `exists()` returned False for any exception, including OSError."""
    store = FilesystemBlobStore(root=tmp_path)
    ref = await store.put_bytes(b"x", media_type="application/octet-stream")

    def boom(self: object) -> bool:
        raise OSError("disk on fire")

    monkeypatch.setattr(pathlib.Path, "exists", boom)
    with pytest.raises(OSError):
        await store.exists(ref)


@pytest.mark.asyncio
async def test_stat_does_not_report_an_io_error_as_absent(
    tmp_path: pathlib.Path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """M58: same for stat(), which is what callers use to decide to re-fetch."""
    store = FilesystemBlobStore(root=tmp_path)
    ref = await store.put_bytes(b"x", media_type="application/octet-stream")

    def boom(self: object) -> bool:
        raise OSError("disk on fire")

    monkeypatch.setattr(pathlib.Path, "exists", boom)
    with pytest.raises(OSError):
        await store.stat(ref)


@pytest.mark.asyncio
async def test_malformed_reference_is_still_absent(tmp_path: pathlib.Path) -> None:
    """Guard: only I/O errors raise; a bad reference is still simply absent."""
    store = FilesystemBlobStore(root=tmp_path)
    assert await store.exists(12345) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# M60 — fabricated timestamps
# ---------------------------------------------------------------------------


def test_unreadable_link_timestamp_raises() -> None:
    """M60: `_row_to_link` substituted "now" for a timestamp it could not parse."""
    from research_harness.plugins.storage.artifacts_sqlite.plugin import (
        ArtifactStoreError,
        SQLiteArtifactStore,
    )

    store = SQLiteArtifactStore(path=pathlib.Path(":memory:"))
    row = {
        "source_artifact_id": "a",
        "target_artifact_id": "b",
        "relation": "derived_from",
        "created_at": "not-a-timestamp",
        "producer": "p",
        "metadata_json": "{}",
    }
    with pytest.raises(ArtifactStoreError, match="unreadable created_at"):
        store._row_to_link(row)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# M63 — an unreadable payload was scored as empty
# ---------------------------------------------------------------------------


def test_unreadable_payload_is_not_scored_as_empty() -> None:
    """M63: `{}` made an unreadable artifact look genuinely empty."""

    class _Env:
        artifact_id = "art-1"

        def __init__(self, payload: object) -> None:
            self.payload = payload

    with pytest.raises(EvaluatorError, match="cannot be read as a dict"):
        envelope_payload_dict(_Env(object()))  # type: ignore[arg-type]


def test_dict_and_model_payloads_still_work() -> None:
    """Guard: the raise must not affect the two payload shapes that do work."""
    from pydantic import BaseModel

    class _M(BaseModel):
        a: int = 1

    class _Env:
        artifact_id = "art-1"

        def __init__(self, payload: object) -> None:
            self.payload = payload

    assert envelope_payload_dict(_Env({"a": 1})) == {"a": 1}  # type: ignore[arg-type]
    assert envelope_payload_dict(_Env(_M())) == {"a": 1}  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# M64 — inconsistent error type
# ---------------------------------------------------------------------------


def test_registry_raises_plugin_error() -> None:
    """M64: registry.create_plugin raised ValueError where bootstrap raises PluginError."""
    from research_harness.plugins.registry import create_plugin

    with pytest.raises(PluginError, match="unknown plugin"):
        create_plugin("definitely.not.a.plugin")


def test_plugin_error_is_a_research_harness_error() -> None:
    """Guard: callers catching ResearchHarnessError must catch this one too."""
    from research_harness.kernel.errors import ResearchHarnessError

    assert issubclass(PluginError, ResearchHarnessError)


# ---------------------------------------------------------------------------
# M50 — SSRF pinning silently disabled
# ---------------------------------------------------------------------------


def test_injected_client_warns_that_pinning_is_off(caplog: pytest.LogCaptureFixture) -> None:
    """M50: with an injected client, DNS pinning is off — say so.

    We cannot install our pinned transport on a client we did not build, so the
    protection is genuinely unavailable. It used to disappear without a word.
    """
    import logging

    import httpx

    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService

    with caplog.at_level(
        logging.WARNING, logger="research_harness.plugins.documents.fetcher_http.plugin"
    ):
        HttpFetcherService(
            artifact_store=None,
            blob_store=None,
            http_client=httpx.AsyncClient(),
        )
    assert any("DNS pinning is inactive" in r.message for r in caplog.records), (
        f"no warning about lost SSRF protection: {[r.message for r in caplog.records]}"
    )


def test_own_client_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    """Guard: the warning is only for the case where protection is actually lost."""
    import logging

    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherService

    with caplog.at_level(
        logging.WARNING, logger="research_harness.plugins.documents.fetcher_http.plugin"
    ):
        HttpFetcherService(artifact_store=None, blob_store=None)
    assert not [r for r in caplog.records if "DNS pinning" in r.message]
