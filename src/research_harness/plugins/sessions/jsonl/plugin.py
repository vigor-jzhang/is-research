"""JSONL session plugin - append-only session storage."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_harness.kernel.errors import SessionError
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata

logger = logging.getLogger(__name__)

# Keys that must never be persisted (lowercased for case-insensitive match)
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "api-key",
    "openrouter_api_key",
    "authorization",
    "password",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "bearer",
}


def _scrub_sensitive(obj: Any) -> Any:
    if isinstance(obj, dict):
        scrubbed: dict[str, Any] = {}
        for k, v in obj.items():
            if k.lower() in _SENSITIVE_KEYS:
                continue
            scrubbed[k] = _scrub_sensitive(v)
        return scrubbed
    if isinstance(obj, list):
        return [_scrub_sensitive(x) for x in obj]
    return obj


class JsonlSessionStore:
    """Append-only JSONL session store."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    async def create_session(self, metadata: dict[str, Any] | None = None) -> str:
        session_id = str(uuid.uuid4())
        session_dir = self.root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "session_id": session_id,
            "created_at": datetime.now(UTC).isoformat(),
            "metadata": _scrub_sensitive(metadata or {}),
        }
        (session_dir / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        # Ensure events file exists
        (session_dir / "events.jsonl").touch(exist_ok=True)
        logger.debug("created session %s at %s", session_id, session_dir)
        return session_id

    async def append(self, session_id: str, event: dict[str, Any]) -> None:
        scrubbed = _scrub_sensitive(event)
        session_dir = self.root / session_id
        if not session_dir.exists():
            raise SessionError(f"session {session_id!r} does not exist")
        events_path = session_dir / "events.jsonl"
        # Ensure event has required fields
        if "event_id" not in scrubbed:
            scrubbed["event_id"] = str(uuid.uuid4())
        if "timestamp" not in scrubbed:
            scrubbed["timestamp"] = datetime.now(UTC).isoformat()
        line = json.dumps(scrubbed, ensure_ascii=False)
        # Append-only write
        with events_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    async def read(self, session_id: str) -> list[dict[str, Any]]:
        session_dir = self.root / session_id
        events_path = session_dir / "events.jsonl"
        if not events_path.exists():
            raise SessionError(f"session {session_id!r} not found")
        events: list[dict[str, Any]] = []
        with events_path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise SessionError(f"corrupt event at {session_id}:{lineno}: {e}") from e
        return events

    async def get_metadata(self, session_id: str) -> dict[str, Any]:
        meta_path = self.root / session_id / "metadata.json"
        if not meta_path.exists():
            raise SessionError(f"metadata for session {session_id!r} not found")
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SessionError(f"corrupt metadata for {session_id!r}: {e}") from e

    def session_exists(self, session_id: str) -> bool:
        return (self.root / session_id).exists()


class JsonlSessionPlugin(Plugin):
    def __init__(self, root: str | Path | None = None) -> None:
        self._root_override = Path(root) if root else None
        self._store: JsonlSessionStore | None = None
        self._ctx: PluginContext | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="session.jsonl",
            version="0.1.0",
            plugin_type="session",
            description="JSONL append-only session store",
            provides=["session_store.default"],
            requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        self._ctx = ctx
        cfg = ctx.config or {}
        # cfg may be {"session": {"root": "..."}} or {"root": "..."}
        root_val: str | None = None
        if "session" in cfg and isinstance(cfg["session"], dict):
            root_val = cfg["session"].get("root")
        elif "root" in cfg:
            root_val = cfg["root"]  # type: ignore[assignment]

        if self._root_override is not None:
            root = self._root_override
        elif root_val:
            root = Path(root_val)
        else:
            root = Path(".research/sessions")

        root.mkdir(parents=True, exist_ok=True)
        self._store = JsonlSessionStore(root=root)
        ctx.register("session_store.default", self._store)

        # Subscribe to EventBus as persistence layer.
        # SessionStore is the persistent source of truth; EventBus is transport.
        # This subscriber ensures every event with a session_id is appended
        # append-only to the JSONL file. Replay must use SessionStore, not EventBus history.
        store = self._store

        async def _persist(event: Any) -> None:  # type: ignore[no-untyped-def]
            # Only persist events that are part of a session trajectory
            sid = getattr(event, "session_id", None)
            if not sid:
                return
            try:
                # Use model_dump with json mode to ensure datetime serialization
                payload = (
                    event.model_dump(mode="json") if hasattr(event, "model_dump") else dict(event)
                )
                await store.append(sid, payload)
            except Exception:
                logger.exception(
                    "failed to persist event %s for session %s",
                    getattr(event, "event_type", "?"),
                    sid,
                )

        # Subscribe to all events (wildcard). The EventBus will invoke this for every publish.
        ctx.subscribe("*", _persist)  # type: ignore[arg-type]
