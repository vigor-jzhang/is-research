"""Lightweight .env loader — no external dependency.

Loads `.env` from current working directory or project root if present.
Does not override already-set environment variables.
"""

from __future__ import annotations

import os
import pathlib


def load_dotenv(dotenv_path: str | pathlib.Path | None = None, override: bool = False) -> bool:
    """Load .env file into os.environ.

    Returns True if a file was loaded.
    """
    if dotenv_path is not None:
        p = pathlib.Path(dotenv_path)
        if p.is_file():
            _load_file(p, override=override)
            return True
        return False

    # M54: the old code walked a fixed 4 levels up, which can leave the project
    # entirely and load a .env that is not ours — a parent directory may hold
    # unrelated secrets. Stop at the first project root instead.
    cwd = pathlib.Path.cwd()
    bases: list[pathlib.Path] = [cwd]
    if not _is_project_root(cwd):
        for base in cwd.parents[:4]:
            bases.append(base)
            if _is_project_root(base):
                break
    candidates: list[pathlib.Path] = [base / ".env" for base in bases]

    # Deduplicate preserving order
    seen: set[pathlib.Path] = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        if c.is_file():
            _load_file(c, override=override)
            return True
    return False


def _is_project_root(path: pathlib.Path) -> bool:
    """True if `path` looks like the root of a project.

    M54: the boundary matters. Walking a fixed number of levels up can leave the
    project and read a .env that belongs to something else entirely.
    """
    return (path / "pyproject.toml").exists() or (path / ".git").exists()


def _load_file(path: pathlib.Path, override: bool = False) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    # L35: a non-UTF-8 .env file used to escape raw; skip it like an unreadable
    # one instead.
    except (OSError, UnicodeDecodeError):
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # M54: `export FOO=bar` defined a variable literally named "export FOO",
        # so the variable never appeared at all.
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        # Strip optional surrounding quotes
        if len(val) >= 2 and ((val[0] == val[-1] == '"') or (val[0] == val[-1] == "'")):
            val = val[1:-1]
        else:
            # M54: an inline comment was part of the value, so `FOO=bar # note`
            # set FOO to "bar # note". Only for unquoted values — a `#` inside
            # quotes is data.
            val = val.split(" #", 1)[0].rstrip()
        if not override and key in os.environ:
            continue
        if key:
            os.environ[key] = val
