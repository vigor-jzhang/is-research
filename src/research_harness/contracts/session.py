"""Session store contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field


class SessionEvent(BaseModel):
    event_id: str
    event_type: str
    timestamp: datetime
    source: str
    session_id: str | None = None
    run_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class SessionMetadata(BaseModel):
    session_id: str
    created_at: datetime
    config_ref: dict[str, Any] | None = None


class SessionStore(Protocol):
    """Append-only session store."""

    async def create_session(self, metadata: dict[str, Any] | None = None) -> str:
        """Create a new session and return its id."""
        ...

    async def append(self, session_id: str, event: dict[str, Any]) -> None:
        """Append an event to the session (append-only)."""
        ...

    async def read(self, session_id: str) -> list[dict[str, Any]]:
        """Read all events for a session."""
        ...

    async def get_metadata(self, session_id: str) -> dict[str, Any]: ...
