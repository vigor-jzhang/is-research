"""Routing role validation (Phase 7C) — routing is strictly role-specific:
evidence for one role is never used to route another."""

from __future__ import annotations

from research_harness.research.tournament.roles import (
    SUPPORTED_ROLES,
    validate_role,
)

__all__ = ["SUPPORTED_ROLES", "validate_role"]
