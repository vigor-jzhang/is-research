"""YAML config loader with Pydantic validation."""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

from research_harness.config.schema import AppConfig
from research_harness.kernel.errors import ConfigurationError


def load_config(path: str | pathlib.Path) -> AppConfig:
    p = pathlib.Path(path)
    if not p.exists():
        raise ConfigurationError(f"config file not found: {p}")
    try:
        text = p.read_text(encoding="utf-8")
        data: Any = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ConfigurationError(f"failed to parse YAML {p}: {e}") from e
    except OSError as e:
        raise ConfigurationError(f"failed to read config {p}: {e}") from e

    if not isinstance(data, dict):
        raise ConfigurationError(f"config top-level must be a mapping, got {type(data).__name__}")

    try:
        return AppConfig.model_validate(data)
    except Exception as e:
        # Pydantic validation error - present nicely
        raise ConfigurationError(f"configuration validation failed for {p}: {e}") from e


def load_config_from_dict(data: dict[str, Any]) -> AppConfig:
    try:
        return AppConfig.model_validate(data)
    except Exception as e:
        raise ConfigurationError(f"configuration validation failed: {e}") from e
