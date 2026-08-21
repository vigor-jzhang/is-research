"""Pydantic schema for runtime configuration."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class RuntimeSection(BaseModel):
    autonomy: str = Field(default="high", description="Autonomy mode: high or interactive")

    @field_validator("autonomy")
    @classmethod
    def validate_autonomy(cls, v: str) -> str:
        if v not in ("high", "interactive"):
            raise ValueError(f"autonomy must be 'high' or 'interactive', got {v!r}")
        return v


class ModelRoleConfig(BaseModel):
    provider: str = Field(description="Provider id, e.g. openrouter")
    model: str = Field(description="Model slug")

    model_config = {"extra": "forbid"}


class ModelsConfig(BaseModel):
    roles: dict[str, ModelRoleConfig] = Field(
        default_factory=dict, description="Logical role -> provider/model"
    )

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, v: dict[str, ModelRoleConfig]) -> dict[str, ModelRoleConfig]:
        if not v:
            return v
        for role in v:
            if not role:
                raise ValueError("role name must be non-empty")
        return v


class SessionConfig(BaseModel):
    root: str = Field(default=".research/sessions", description="Session storage root dir")

    model_config = {"extra": "forbid"}


class LoopConfig(BaseModel):
    max_steps: int = Field(default=8, ge=1, le=100, description="Max agent loop steps")

    model_config = {"extra": "forbid"}


class AppConfig(BaseModel):
    runtime: RuntimeSection = Field(default_factory=RuntimeSection)
    plugins: list[str] = Field(default_factory=list, description="List of plugin ids to load")
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    loop: LoopConfig = Field(default_factory=LoopConfig)

    @field_validator("plugins")
    @classmethod
    def validate_plugins(cls, v: list[str]) -> list[str]:
        if len(v) != len(set(v)):
            raise ValueError(f"duplicate plugin ids in config: {v}")
        return v

    model_config = {"extra": "forbid"}

    def plugin_config(self, plugin_id: str) -> dict[str, Any]:
        """Return dict config for a given plugin id (derived)."""
        mapping: dict[str, dict[str, Any]] = {
            "routing.role_router": {"models": self.models.model_dump()},
            "session.jsonl": {"session": self.session.model_dump()},
            "loop.simple_tool_loop": {"loop": self.loop.model_dump()},
            "autonomy.configurable": {"autonomy": self.runtime.autonomy},
        }
        return mapping.get(plugin_id, {})
