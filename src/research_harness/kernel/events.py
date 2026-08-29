"""Lightweight asynchronous event bus."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class Event(BaseModel):
    """Research harness event."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    schema_version: str = Field(default="1.0")
    event_type: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str
    session_id: str | None = None
    run_id: str | None = None
    parent_event_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        event_type: str,
        source: str,
        payload: dict[str, Any] | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        parent_event_id: str | None = None,
        event_id: str | None = None,
    ) -> Event:
        return cls(
            event_id=event_id or str(uuid.uuid4()),
            event_type=event_type,
            source=source,
            payload=payload or {},
            session_id=session_id,
            run_id=run_id,
            parent_event_id=parent_event_id,
        )

    model_config = {"extra": "forbid"}


EventHandler = Callable[[Event], Awaitable[None]]


class EventBus:
    """In-process asynchronous publish/subscribe bus."""

    def __init__(self, history_limit: int = 1_000) -> None:
        if history_limit < 0:
            raise ValueError("history_limit must be non-negative")
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._wildcard_handlers: list[EventHandler] = []
        self._history: list[Event] = []
        self._history_limit = history_limit

    def subscribe(self, event_type: str, handler: EventHandler) -> Callable[[], None]:
        """Subscribe to an event type. Use '*' for all events."""
        if event_type == "*":
            self._wildcard_handlers.append(handler)

            def _unsub() -> None:
                if handler in self._wildcard_handlers:
                    self._wildcard_handlers.remove(handler)

            return _unsub

        self._handlers[event_type].append(handler)

        def _unsub2() -> None:
            lst = self._handlers.get(event_type)
            if lst is not None and handler in lst:
                lst.remove(handler)

        return _unsub2

    async def publish(self, event: Event) -> None:
        """Publish an event to all matching subscribers."""
        if self._history_limit:
            self._history.append(event)
            if len(self._history) > self._history_limit:
                del self._history[: len(self._history) - self._history_limit]
        # Collect handlers: exact match + wildcard
        handlers: list[EventHandler] = []
        handlers.extend(self._handlers.get(event.event_type, []))
        handlers.extend(self._wildcard_handlers)

        for handler in list(handlers):
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("event handler failed for %s", event.event_type)

    def history(self) -> list[Event]:
        return list(self._history)

    def clear_history(self) -> None:
        self._history.clear()

    def handler_count(self, event_type: str | None = None) -> int:
        if event_type is None:
            return sum(len(v) for v in self._handlers.values()) + len(self._wildcard_handlers)
        if event_type == "*":
            return len(self._wildcard_handlers)
        return len(self._handlers.get(event_type, []))
