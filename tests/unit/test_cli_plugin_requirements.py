"""Regression test for H13: every CLI command must ensure all required plugins.

Commands build a default config listing everything they need, but several
appended only a subset when a config file was present, so the command ran
without services it requires. The worst case was `model_build`, whose default
needs `model.openrouter` and `routing.role_router` but ensured neither.

This test does not execute commands. It parses `cli/main.py` and asserts that
each command's ensure call covers its whole default plugin list, so the defect
cannot come back silently.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

MAIN = (
    pathlib.Path(__file__).resolve().parents[2]
    / "src"
    / "research_harness"
    / "cli"
    / "main.py"
)


def _string_list(node: ast.AST) -> list[str] | None:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return None
    out: list[str] = []
    for e in node.elts:
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            out.append(e.value)
        else:
            return None
    return out


def _default_plugins(fn: ast.AST) -> tuple[int, list[str]] | None:
    """Nearest `load_config_from_dict({... "plugins": [...]})` inside fn."""
    best: tuple[int, list[str]] | None = None
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "load_config_from_dict":
            for a in n.args:
                if isinstance(a, ast.Dict):
                    for k, v in zip(a.keys, a.values, strict=False):
                        if isinstance(k, ast.Constant) and k.value == "plugins":
                            pl = _string_list(v)
                            if pl and (best is None or n.lineno > best[0]):
                                best = (n.lineno, pl)
    return best


def _ensured_plugins(fn: ast.AST) -> set[str]:
    """Every plugin id the function guarantees is present in cfg.plugins."""
    out: set[str] = set()
    for n in ast.walk(fn):
        # _ensure_plugins(cfg, "a", "b", ...)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_ensure_plugins":
            out.update(a.value for a in n.args[1:] if isinstance(a, ast.Constant))
        # for req in ["a", "b"]: if req not in cfg.plugins: cfg.plugins.append(req)
        elif isinstance(n, ast.For) and isinstance(n.target, ast.Name) and n.target.id == "req":
            listed = _string_list(n.iter)
            if listed:
                out.update(listed)
        # if "x" not in cfg.plugins: cfg.plugins.append/extend(...)
        elif isinstance(n, ast.If):
            for s in ast.walk(n):
                if isinstance(s, ast.Call) and getattr(s.func, "attr", None) in (
                    "append",
                    "extend",
                ):
                    for a in s.args:
                        listed = _string_list(a)
                        if listed:
                            out.update(listed)
                        elif isinstance(a, ast.Constant) and isinstance(a.value, str):
                            out.add(a.value)
    return out


def _functions(tree: ast.Module):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for s in ast.walk(node):
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield s


def test_every_command_ensures_all_of_its_default_plugins():
    tree = ast.parse(MAIN.read_text())
    failures: list[str] = []
    checked = 0
    for fn in _functions(tree):
        default = _default_plugins(fn)
        if default is None:
            continue
        _lineno, needed = default
        ensured = _ensured_plugins(fn)
        if not ensured:
            continue  # nothing to ensure (e.g. builds the config inline)
        missing = [p for p in needed if p not in ensured]
        checked += 1
        if missing:
            failures.append(f"{fn.name}: default needs {needed} but never ensures {missing}")

    assert checked >= 40, f"only {checked} command functions were inspected"
    assert not failures, "CLI commands that drop required plugins:\n" + "\n  ".join(failures)


@pytest.mark.parametrize(
    "plugin_id",
    ["storage.artifacts_sqlite", "model.openrouter", "routing.role_router", "autonomy.configurable"],
)
def test_ensure_plugins_helper_exists_and_appends(plugin_id: str):
    """The helper the commands rely on must append, not replace."""
    from research_harness.cli.main import _ensure_plugins

    class _Cfg:
        def __init__(self, plugins: list[str]) -> None:
            self.plugins = plugins

    cfg = _Cfg(["user.plugin"])
    _ensure_plugins(cfg, plugin_id, "user.plugin")
    assert cfg.plugins == ["user.plugin", plugin_id], "user plugins must be preserved"
    # Idempotent
    _ensure_plugins(cfg, plugin_id)
    assert cfg.plugins.count(plugin_id) == 1


def test_manuscript_config_keeps_extra_plugins_with_a_config_file(tmp_path: pathlib.Path):
    """H14: extra_plugins must survive the config-file path.

    They were only applied when building the default config, so any command
    using a config file silently lost its research plugin.
    """
    from research_harness.cli.main import _manuscript_config

    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(
        "plugins:\n  - storage.artifacts_sqlite\n"
        "artifacts:\n  store: sqlite\n  path: ':memory:'\n"
    )
    cfg = _manuscript_config(cfg_file, ["research.manuscript_drafter"])
    assert "research.manuscript_drafter" in cfg.plugins
    assert "storage.artifacts_sqlite" in cfg.plugins


def test_publication_config_keeps_extra_plugins_with_a_config_file(tmp_path: pathlib.Path):
    """H14, publication variant."""
    from research_harness.cli.main import _publication_config

    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(
        "plugins:\n  - storage.artifacts_sqlite\n"
        "artifacts:\n  store: sqlite\n  path: ':memory:'\n"
    )
    cfg = _publication_config(cfg_file, ["research.publication_formatter"])
    assert "research.publication_formatter" in cfg.plugins


def test_config_helpers_preserve_user_plugins(tmp_path: pathlib.Path):
    """Ensuring must append, never replace what the user configured."""
    from research_harness.cli.main import _manuscript_config

    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(
        "plugins:\n  - my.custom.plugin\n"
        "artifacts:\n  store: sqlite\n  path: ':memory:'\n"
    )
    cfg = _manuscript_config(cfg_file, ["research.manuscript_drafter"])
    assert "my.custom.plugin" in cfg.plugins


def test_cli_helpers_are_importable_without_a_runtime():
    """Guards against a typo in the helper name breaking every command."""
    from research_harness.cli.main import (  # noqa: F401
        _ensure_plugins,
        _manuscript_config,
        _publication_config,
    )
