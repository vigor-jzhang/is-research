"""Regression tests for CLI structure (round 28).

Batch: M72 (no typer.Choice anywhere), M73 (SQLite connections leak on error
paths), M76 (config helpers accept ``extra_plugins`` and ignore it), L33 (CLI
nits).

Where a defect sits in a command closure the test drives the real CLI through
CliRunner where that is cheap. Where driving the CLI would need a live model or
runtime — the store-leak fix in particular — the test asserts on the shape of
the source, which is weaker but still fails if the fix is reverted.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from research_harness.cli.main import (
    _live_quality_config,
    _routing_config,
    _tournament_config,
    app,
)

MAIN = (
    pathlib.Path(__file__).resolve().parents[2]
    / "src"
    / "research_harness"
    / "cli"
    / "main.py"
)

runner = CliRunner()


def _write_config(tmp_path: pathlib.Path) -> pathlib.Path:
    """A minimal config file.

    M76 only shows up on the config-file path: on the default path the caller's
    plugins were already in the generated dict. So the test must supply a file.
    """
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "plugins:\n"
        "  - storage.artifacts_sqlite\n"
        "artifacts:\n"
        "  store: sqlite\n"
        f"  path: {tmp_path / 'artifacts.db'}\n",
        encoding="utf-8",
    )
    return cfg


# ---------------------------------------------------------------------------
# M76 — extra_plugins accepted then dropped
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "helper", [_tournament_config, _routing_config, _live_quality_config]
)
def test_extra_plugins_survive_the_config_file_path(
    tmp_path: pathlib.Path, helper: object
) -> None:
    """A plugin a command asks for must reach the config even with a config file.

    Before the fix the helper took ``extra_plugins`` and then passed its own
    constant to ``_evaluation_config`` instead, so the parameter was dead and
    every one of the 23 call sites silently lost it.
    """
    cfg_path = _write_config(tmp_path)
    cfg = helper(cfg_path, ["probe.extra.plugin"])  # type: ignore[operator]
    assert "probe.extra.plugin" in cfg.plugins


def test_tournament_config_still_adds_its_own_plugin(tmp_path: pathlib.Path) -> None:
    """Guard: the dedicated plugin is still appended alongside the caller's."""
    cfg_path = _write_config(tmp_path)
    cfg = _tournament_config(cfg_path, ["probe.extra.plugin"])
    assert "evaluation.model_tournament" in cfg.plugins


# ---------------------------------------------------------------------------
# M72 — enumerated options accepted any string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv,flag",
    [
        (["routing", "decide", "--role", "bogus"], "--role"),
        (["routing", "decide", "--role", "fast", "--policy", "nope"], "--policy"),
        (["artifacts", "lineage", "x", "--direction", "sideways"], "--direction"),
        (["publication", "export", "--format", "rtf"], "--format"),
        (["publication", "profile-create", "--name", "n", "--style", "vancouver"], "--style"),
        (["run", "--role", "nope"], "--role"),
    ],
)
def test_invalid_enum_value_is_rejected_at_the_boundary(argv: list[str], flag: str) -> None:
    """A typo must be a usage error (exit 2), not an uncaught service error.

    Before the fix these options were ``str`` with the allowed values written
    only in the help text, so a bad value reached the service layer.
    """
    result = runner.invoke(app, argv)
    assert result.exit_code == 2, f"expected exit 2, got {result.exit_code}"
    assert "is not one of" in result.output, (
        f"{flag} was not validated as a choice:\n{result.output}"
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["artifacts", "lineage", "x", "--direction", "descendants"],
        ["publication", "profile-create", "--name", "n", "--style", "apa"],
    ],
)
def test_valid_enum_values_are_still_accepted(argv: list[str]) -> None:
    """Guard: the choice did not over-restrict a value that was always valid."""
    result = runner.invoke(app, argv)
    assert result.exit_code != 2, f"valid value rejected: {result.output}"


# ---------------------------------------------------------------------------
# M73 — SQLite connections leaked on every raising path
# ---------------------------------------------------------------------------


def _store_assignments(tree: ast.AST) -> list[ast.FunctionDef]:
    """Every function that opens a store directly."""
    found: list[ast.FunctionDef] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Assign):
                continue
            src = ast.unparse(sub.value)
            if "SQLiteArtifactStore(" in src or "_get_artifact_store(" in src:
                found.append(node)
                break
    return found


def _closes_in_finally(fn: ast.FunctionDef) -> bool:
    for sub in ast.walk(fn):
        if not isinstance(sub, ast.Try) or not sub.finalbody:
            continue
        for stmt in sub.finalbody:
            if "store.close()" in ast.unparse(stmt):
                return True
    return False


def test_every_store_is_closed_in_a_finally() -> None:
    """Every command that opens a store must close it in a ``finally``.

    Before the fix each command called ``await store.close()`` at the end of the
    happy path (and sometimes before an early ``return``), so any exception in
    between — and there are 100+ ``typer.Exit`` sites — leaked the connection.
    """
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    owners = _store_assignments(tree)
    assert owners, "no store assignments found — the test is not looking anywhere"
    leaked = [fn.name for fn in owners if not _closes_in_finally(fn)]
    assert not leaked, f"store opened without a finally-close in: {leaked}"


def test_no_bare_close_left_on_the_happy_path() -> None:
    """Guard: the old bare ``await store.close()`` statements are gone.

    A bare close at the top level of a command body is the shape that leaked;
    after the fix the only closes are inside ``finally`` blocks.
    """
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    bare: list[int] = []
    for fn in _store_assignments(tree):
        for sub in ast.walk(fn):
            if not isinstance(sub, ast.Expr):
                continue
            text = ast.unparse(sub.value)
            if text == "await store.close()" and not _in_finalbody(fn, sub):
                bare.append(sub.lineno)
    assert not bare, f"bare store.close() outside a finally at lines {bare}"


def _in_finalbody(fn: ast.FunctionDef, target: ast.AST) -> bool:
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Try):
            for stmt in sub.finalbody:
                if stmt is target or any(x is target for x in ast.walk(stmt)):
                    return True
    return False


# ---------------------------------------------------------------------------
# L33 — CLI nits
# ---------------------------------------------------------------------------


def test_prompt_and_prompt_file_are_mutually_exclusive(tmp_path: pathlib.Path) -> None:
    """L33: --prompt was silently discarded when --prompt-file was also given."""
    pf = tmp_path / "p.txt"
    pf.write_text("from file", encoding="utf-8")
    result = runner.invoke(app, ["run", "--prompt", "inline", "--prompt-file", str(pf)])
    assert result.exit_code == 2
    assert "mutually exclusive" in result.output


def test_long_payload_is_marked_as_truncated(tmp_path: pathlib.Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """L33: `artifacts inspect` cut the payload at 4000 chars without saying so."""
    import asyncio


    monkeypatch.chdir(tmp_path)
    (tmp_path / ".research").mkdir(exist_ok=True)
    artifact_id = asyncio.run(_seed_large_artifact(tmp_path))

    result = runner.invoke(app, ["artifacts", "inspect", artifact_id])
    assert "truncated" in result.output, (
        f"no truncation notice in output:\n{result.output[:400]}"
    )


class _BigPayload(BaseModel):
    blob: str


async def _seed_large_artifact(tmp_path: pathlib.Path) -> str:
    from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
    from research_harness.research.envelope import ArtifactEnvelope

    store = SQLiteArtifactStore(path=tmp_path / ".research" / "artifacts.db")
    try:
        env = ArtifactEnvelope.create(
            payload=_BigPayload(blob="x" * 9000),
            artifact_type="paper_record",
            producer="test",
        )
        await store.put(env)
    finally:
        await store.close()
    return env.artifact_id


def test_status_pad_sits_inside_the_markup() -> None:
    """L33: `{mark:16s}` padded the markup, so columns lined up on invisible tags."""
    source = MAIN.read_text(encoding="utf-8")
    assert "[green]selected        [/green]" in source
    assert "[yellow]static_fallback  [/yellow]" in source
    assert "mark:16s" not in source
