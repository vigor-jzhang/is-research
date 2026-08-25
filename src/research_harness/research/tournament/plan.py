"""Tournament plan loading (Phase 7B).

Plans are standalone YAML files — defining candidates and configuration never
requires code changes and never mutates the global user config.
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

from research_harness.kernel.errors import ConfigurationError
from research_harness.research.schemas.tournament import TournamentPlan


def load_tournament_plan(path: str | pathlib.Path) -> TournamentPlan:
    p = pathlib.Path(path)
    if not p.exists():
        raise ConfigurationError(f"tournament plan not found: {p}")
    try:
        data: Any = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigurationError(f"failed to parse tournament plan {p}: {e}") from e
    except OSError as e:
        raise ConfigurationError(f"failed to read tournament plan {p}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigurationError(
            f"tournament plan top-level must be a mapping, got {type(data).__name__}"
        )
    try:
        return TournamentPlan.model_validate(data)
    except Exception as e:  # noqa: BLE001
        raise ConfigurationError(f"tournament plan validation failed for {p}: {e}") from e


def plan_from_dict(data: dict[str, Any]) -> TournamentPlan:
    try:
        return TournamentPlan.model_validate(data)
    except Exception as e:  # noqa: BLE001
        raise ConfigurationError(f"tournament plan validation failed: {e}") from e


def plan_hash(plan: TournamentPlan) -> str:
    import hashlib
    import json

    payload = plan.model_dump(mode="json")
    payload.pop("plan_id", None)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
