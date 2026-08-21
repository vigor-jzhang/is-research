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

    # Search in cwd and then walk up to find repo root (where pyproject.toml lives)
    candidates: list[pathlib.Path] = []
    cwd = pathlib.Path.cwd()
    # Check cwd and parents up to 4 levels
    for base in [cwd, *cwd.parents[:4]]:
        candidates.append(base / ".env")

    # Also check alongside pyproject.toml discovery
    # Find pyproject.toml upwards
    for base in [cwd, *cwd.parents[:4]]:
        if (base / "pyproject.toml").exists():
            candidates.append(base / ".env")
            break

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


def _load_file(path: pathlib.Path, override: bool = False) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        # Strip optional surrounding quotes
        if len(val) >= 2 and ((val[0] == val[-1] == '"') or (val[0] == val[-1] == "'")):
            val = val[1:-1]
        if not override and key in os.environ:
            continue
        if key:
            os.environ[key] = val
