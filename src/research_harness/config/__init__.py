"""Config package."""

from research_harness.config.loader import load_config
from research_harness.config.schema import AppConfig

__all__ = ["AppConfig", "load_config"]
