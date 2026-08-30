"""SQLite artifact store plugin — generic, no payload type knowledge."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_harness.kernel.errors import ResearchHarnessError
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope, compute_content_hash
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation

logger = logging.getLogger(__name__)


class ArtifactStoreError(ResearchHarnessError):
    pass


class SQLiteArtifactStore:
    """SQLite-backed artifact store.

    Uses standard sqlite3, no ORM. Tables: artifacts, provenance.

    Threading model
    ---------------
    A single connection is shared and opened with ``check_same_thread=False``,
    so every operation is serialised through ``self._lock``. This matters
    because most operations are read-modify-write (``put`` checks existence
    then inserts; ``add_provenance`` validates, walks the graph, then
    inserts): without a lock those steps interleave across threads, which
    corrupts transactions and can report success for an artifact that was
    never persisted.

    The lock is a plain ``threading.RLock``, never an ``asyncio`` lock, so it
    is safe from both threads and a single event loop. The invariant that
    keeps it deadlock-free is: **no await ever happens while the lock is
    held.** All lock-protected sections are synchronous, and the public
    ``async`` methods perform their awaits (event publication) outside them.
    """

    def __init__(self, path: str | Path, events: Any | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events = events
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Enable foreign keys and WAL for durability
        self._conn.execute("PRAGMA foreign_keys = ON;")
        self._conn.execute("PRAGMA journal_mode = WAL;")
        # Wait rather than failing immediately when another writer holds the
        # database lock.
        self._conn.execute("PRAGMA busy_timeout = 5000;")
        self._init_schema()

    def _init_schema(self) -> None:
        cur = self._conn.cursor()
        # Simple version table for future migrations
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            """
        )
        # Artifacts table — authoritative for artifact content
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                artifact_type TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                session_id TEXT,
                run_id TEXT,
                producer TEXT,
                content_hash TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            );
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_type ON artifacts(artifact_type);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_hash ON artifacts(content_hash);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_created ON artifacts(created_at);")

        # Provenance table — lineage edges
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS provenance (
                source_artifact_id TEXT NOT NULL,
                target_artifact_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                created_at TEXT NOT NULL,
                producer TEXT,
                metadata_json TEXT NOT NULL,
                PRIMARY KEY (source_artifact_id, target_artifact_id, relation)
            );
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_provenance_source ON provenance(source_artifact_id);"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_provenance_target ON provenance(target_artifact_id);"
        )

        # Ensure schema_version entry
        cur.execute("SELECT count(*) as c FROM schema_version;")
        row = cur.fetchone()
        if row["c"] == 0:
            cur.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?);",
                (1, datetime.now(UTC).isoformat()),
            )
        self._conn.commit()

    async def put(self, envelope: ArtifactEnvelope[Any]) -> None:
        # Verify content_hash matches payload (defense)
        expected = compute_content_hash(envelope.payload)  # type: ignore[arg-type]
        if envelope.content_hash != expected:
            raise ArtifactStoreError(
                f"content_hash mismatch for {envelope.artifact_id!r}: envelope {envelope.content_hash!r} != computed {expected!r}"
            )

        # Serialize payload — ensure JSON round-trip
        try:
            if hasattr(envelope.payload, "model_dump"):
                payload_dict = envelope.payload.model_dump(mode="json")  # type: ignore[attr-defined]
            else:
                payload_dict = dict(envelope.payload)  # type: ignore[arg-type]
            payload_json = json.dumps(payload_dict, ensure_ascii=False, sort_keys=True)
            metadata_json = json.dumps(envelope.metadata, ensure_ascii=False, sort_keys=True)
        except Exception as e:
            raise ArtifactStoreError(
                f"failed to serialize payload for {envelope.artifact_id!r}: {e}"
            ) from e

        # Atomic transaction. The immutability check and the insert must be one
        # critical section: checked separately, two writers can both observe
        # "absent" and then both insert (or interleave their transactions).
        with self._lock:
            if self._exists_sync(envelope.artifact_id):
                raise ArtifactStoreError(
                    f"artifact {envelope.artifact_id!r} already exists (immutable)"
                )
            try:
                cur = self._conn.cursor()
                cur.execute("BEGIN IMMEDIATE;")
                cur.execute(
                    """
                    INSERT INTO artifacts (
                        artifact_id, artifact_type, schema_version, created_at,
                        session_id, run_id, producer, content_hash, payload_json, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        envelope.artifact_id,
                        envelope.artifact_type,
                        envelope.schema_version,
                        envelope.created_at.isoformat(),
                        envelope.session_id,
                        envelope.run_id,
                        envelope.producer,
                        envelope.content_hash,
                        payload_json,
                        metadata_json,
                    ),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as e:
                self._conn.rollback()
                raise ArtifactStoreError(
                    f"artifact {envelope.artifact_id!r} already exists: {e}"
                ) from e
            except Exception:
                self._conn.rollback()
                raise

        # Emit observable event (session trajectory)
        if self.events is not None:
            try:
                from research_harness.kernel.events import Event

                await self.events.publish(
                    Event.create(
                        event_type="artifact.created",
                        source="storage.artifacts_sqlite",
                        payload={
                            "artifact_id": envelope.artifact_id,
                            "artifact_type": envelope.artifact_type,
                            "content_hash": envelope.content_hash,
                            "producer": envelope.producer,
                            "session_id": envelope.session_id,
                            "run_id": envelope.run_id,
                        },
                        session_id=envelope.session_id,
                        run_id=envelope.run_id,
                    )
                )
            except Exception:
                logger.exception(
                    "failed to emit artifact.created event for %s", envelope.artifact_id
                )

    # ------------------------------------------------------------------
    # Synchronous primitives. Every public operation funnels through these so
    # that the multi-step read-modify-write sequences below can run inside a
    # single lock acquisition without awaiting.
    # ------------------------------------------------------------------

    def _exists_sync(self, artifact_id: str) -> bool:
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(
                    "SELECT 1 FROM artifacts WHERE artifact_id = ? LIMIT 1;", (artifact_id,)
                )
                return cur.fetchone() is not None
            finally:
                cur.close()

    def _get_sync(self, artifact_id: str) -> ArtifactEnvelope[Any]:
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("SELECT * FROM artifacts WHERE artifact_id = ?;", (artifact_id,))
                row = cur.fetchone()
            finally:
                cur.close()
        if row is None:
            raise ArtifactStoreError(f"artifact {artifact_id!r} not found")
        return self._row_to_envelope(row)

    _LINK_COLUMNS: dict[str, str] = {
        "parents": "target_artifact_id",
        "children": "source_artifact_id",
    }

    def _links_sync(self, which: str, artifact_id: str) -> list[ProvenanceLink]:
        # Column comes from a fixed whitelist, never from caller input.
        column = self._LINK_COLUMNS[which]
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(
                    f"SELECT * FROM provenance WHERE {column} = ?;",
                    (artifact_id,),
                )
                rows = cur.fetchall()
            finally:
                cur.close()
        return [self._row_to_link(r) for r in rows]

    async def get(self, artifact_id: str) -> ArtifactEnvelope[Any]:
        return self._get_sync(artifact_id)

    async def exists(self, artifact_id: str) -> bool:
        return self._exists_sync(artifact_id)

    async def list(
        self,
        *,
        artifact_type: str | None = None,
        session_id: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ArtifactEnvelope[Any]]:
        # Build query with filters
        query = "SELECT * FROM artifacts"
        clauses: list[str] = []
        params: list[Any] = []
        if artifact_type is not None:
            clauses.append("artifact_type = ?")
            params.append(artifact_type)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at ASC"
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        elif offset:
            query += " LIMIT -1 OFFSET ?"
            params.append(offset)

        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute(query, tuple(params))
                rows = cur.fetchall()
            finally:
                cur.close()
        return [self._row_to_envelope(r) for r in rows]

    async def find_by_type(self, artifact_type: str) -> list[ArtifactEnvelope[Any]]:
        return await self.list(artifact_type=artifact_type)

    async def add_provenance(self, link: ProvenanceLink) -> None:
        # Validate no self-edge (already in model, but double-check)
        if link.source_artifact_id == link.target_artifact_id:
            raise ArtifactStoreError("self-provenance edges are not permitted")

        # Validate existence and run cycle detection inside the same critical
        # section as the insert: validated separately, two writers can both
        # pass the checks and then insert a cycle.
        with self._lock:
            if not self._exists_sync(link.source_artifact_id):
                raise ArtifactStoreError(
                    f"source artifact {link.source_artifact_id!r} not found"
                )
            if not self._exists_sync(link.target_artifact_id):
                raise ArtifactStoreError(
                    f"target artifact {link.target_artifact_id!r} not found"
                )

            # Cycle detection for lineage relations
            if link.relation in (
                ProvenanceRelation.derived_from,
                ProvenanceRelation.extracted_from,
                ProvenanceRelation.generated_from,
                ProvenanceRelation.supersedes,
            ):
                if self._would_create_cycle_sync(
                    link.source_artifact_id, link.target_artifact_id
                ):
                    raise ArtifactStoreError(
                        f"adding provenance {link.relation.value} {link.source_artifact_id!r} -> {link.target_artifact_id!r} would create cycle"
                    )

            try:
                cur = self._conn.cursor()
                cur.execute("BEGIN IMMEDIATE;")
                cur.execute(
                    """
                    INSERT INTO provenance (
                        source_artifact_id, target_artifact_id, relation, created_at, producer, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (
                        link.source_artifact_id,
                        link.target_artifact_id,
                        link.relation.value,
                        link.created_at.isoformat(),
                        link.producer,
                        json.dumps(link.metadata, ensure_ascii=False, sort_keys=True),
                    ),
                )
                self._conn.commit()
            except sqlite3.IntegrityError as e:
                self._conn.rollback()
                raise ArtifactStoreError(
                    f"provenance edge already exists or constraint violation: {e}"
                ) from e
            except Exception:
                self._conn.rollback()
                raise

        if self.events is not None:
            try:
                from research_harness.kernel.events import Event

                await self.events.publish(
                    Event.create(
                        event_type="provenance.created",
                        source="storage.artifacts_sqlite",
                        payload={
                            "relation": link.relation.value,
                            "source_artifact_id": link.source_artifact_id,
                            "target_artifact_id": link.target_artifact_id,
                            "producer": link.producer,
                        },
                        session_id=None,
                        run_id=None,
                    )
                )
            except Exception:
                logger.exception(
                    "failed to emit provenance.created for %s -> %s",
                    link.source_artifact_id,
                    link.target_artifact_id,
                )

    async def get_parents(self, artifact_id: str) -> list[ProvenanceLink]:
        return self._links_sync("parents", artifact_id)

    async def get_children(self, artifact_id: str) -> list[ProvenanceLink]:
        return self._links_sync("children", artifact_id)

    async def get_provenance(
        self, artifact_id: str
    ) -> tuple[list[ProvenanceLink], list[ProvenanceLink]]:
        parents = await self.get_parents(artifact_id)
        children = await self.get_children(artifact_id)
        return (parents, children)

    async def get_lineage(
        self, artifact_id: str, direction: str = "ancestors"
    ) -> list[ArtifactEnvelope[Any]]:
        if direction not in ("ancestors", "descendants"):
            raise ArtifactStoreError(
                f"unknown direction {direction!r}, use ancestors/descendants"
            )
        # Walk ancestors or descendants transitively (BFS, closest first).
        # Held under the lock for the whole traversal so the graph cannot
        # change underneath and yield a half-updated lineage.
        with self._lock:
            visited: set[str] = {artifact_id}
            queue: deque[str] = deque([artifact_id])
            result: list[ArtifactEnvelope[Any]] = []
            # Don't include the starting artifact itself in result, only lineage
            while queue:
                current = queue.popleft()
                if direction == "ancestors":
                    # parents are sources where current is target
                    links = self._links_sync("parents", current)
                    next_ids = [link.source_artifact_id for link in links]
                else:
                    links = self._links_sync("children", current)
                    next_ids = [link.target_artifact_id for link in links]
                for nid in next_ids:
                    if nid in visited:
                        continue
                    visited.add(nid)
                    try:
                        result.append(self._get_sync(nid))
                    except ArtifactStoreError:
                        # Should not happen as we validated existence, but skip
                        continue
                    queue.append(nid)
            return result

    def _would_create_cycle_sync(self, source_id: str, target_id: str) -> bool:
        """True if adding source->target would close a cycle.

        Callers must already hold ``self._lock`` (or be inside a critical
        section): the BFS must see a stable graph, otherwise two concurrent
        inserts can each miss the other's edge and create a cycle.
        """
        # Check if target can reach source via existing edges (then adding source->target would cycle)
        # Perform BFS from target following children edges (outgoing)
        visited: set[str] = {target_id}
        queue: deque[str] = deque([target_id])
        while queue:
            cur = queue.popleft()
            # Get children of cur (where cur is source)
            for link in self._links_sync("children", cur):
                nxt = link.target_artifact_id
                if nxt == source_id:
                    return True
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)
        return False

    def _row_to_envelope(self, row: sqlite3.Row) -> ArtifactEnvelope[Any]:
        artifact_type = row["artifact_type"]
        payload_json = row["payload_json"]
        payload_dict = json.loads(payload_json)
        # Storage is generic: keep payload as dict. Typed reconstruction is done
        # outside via envelope.parse_payload(ModelClass). This keeps storage
        # unaware of domain types and allows external plugins to persist custom
        # artifact types without modifying this file.
        payload: Any = payload_dict

        created_at = datetime.fromisoformat(row["created_at"])
        # Ensure timezone aware
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        return ArtifactEnvelope[Any](
            artifact_id=row["artifact_id"],
            artifact_type=artifact_type,
            schema_version=row["schema_version"],
            created_at=created_at,
            session_id=row["session_id"],
            run_id=row["run_id"],
            producer=row["producer"],
            payload=payload,  # type: ignore[arg-type]
            content_hash=row["content_hash"],
            metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
            provenance=None,
        )

    def _row_to_link(self, row: sqlite3.Row) -> ProvenanceLink:
        # Use timezone-aware parsing
        raw = row["created_at"]
        try:
            created_at = datetime.fromisoformat(raw)
        except Exception:
            created_at = datetime.now(UTC)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        return ProvenanceLink(
            relation=ProvenanceRelation(row["relation"]),
            source_artifact_id=row["source_artifact_id"],
            target_artifact_id=row["target_artifact_id"],
            created_at=created_at,
            producer=row["producer"],
            metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
        )

    async def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                logger.exception("error closing artifact store connection")


class ArtifactsSqlitePlugin(Plugin):
    def __init__(self, path: str | Path | None = None) -> None:
        self._path_override = Path(path) if path else None
        self._store: SQLiteArtifactStore | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="storage.artifacts_sqlite",
            version="0.1.0",
            plugin_type="storage",
            description="SQLite artifact store",
            provides=["artifact_store.default"],
            requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        # cfg may be {"artifacts": {"path": "...", "store": "sqlite"}} or {"path": "..."}
        path_val: str | None = None
        if "artifacts" in cfg and isinstance(cfg["artifacts"], dict):
            path_val = cfg["artifacts"].get("path")
        elif "path" in cfg:
            path_val = cfg["path"]  # type: ignore[assignment]

        if self._path_override is not None:
            path = self._path_override
        elif path_val:
            path = Path(path_val)
        else:
            path = Path(".research/artifacts.db")

        path.parent.mkdir(parents=True, exist_ok=True)
        store = SQLiteArtifactStore(path=path, events=ctx.events)
        self._store = store
        ctx.register("artifact_store.default", store)

    async def stop(self) -> None:
        if self._store is not None:
            await self._store.close()

    async def teardown(self) -> None:
        self._store = None
