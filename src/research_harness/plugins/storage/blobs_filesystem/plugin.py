"""Filesystem content-addressed blob store."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from research_harness.contracts.blob import BlobReference, BlobStat
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _storage_key(digest: str) -> tuple[str, Path]:
    # Use ab/cd/abcdef... layout to avoid too many files in one dir
    # key = ab/cd/<digest>
    # Return both key string and relative path
    key = f"{digest[:2]}/{digest[2:4]}/{digest}"
    return key, Path(key)


class FilesystemBlobStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # Ensure not following symlinks etc - safe
        self.root = self.root.resolve()

    def _path_for_key(self, key: str) -> Path:
        # Prevent directory traversal
        # key must be like ab/cd/digest, only hex and slashes
        # Reject absolute paths, .. etc
        if os.path.isabs(key):
            raise ValueError(f"absolute blob key not allowed: {key!r}")
        if ".." in Path(key).parts:
            raise ValueError(f"blob key cannot contain ..: {key!r}")
        # Only allow hex chars and /
        cleaned = key.replace("/", "")
        if not all(c in "0123456789abcdef" for c in cleaned):
            raise ValueError(f"invalid blob key (must be hex): {key!r}")
        p = self.root / key
        # Ensure p is within root (prevent escapes)
        try:
            p.resolve().relative_to(self.root)
        except ValueError:
            raise ValueError(f"blob key escapes root: {key!r}") from None
        return p

    def _path_for_digest(self, digest: str) -> Path:
        _, rel = _storage_key(digest)
        return self.root / rel

    async def put_bytes(self, data: bytes, media_type: str | None = None) -> BlobReference:
        digest = _sha256(data)
        key, _ = _storage_key(digest)
        target = self._path_for_key(key)
        # If exists, verify and return reference (deduplicate)
        if target.exists():
            # Verify existing content matches digest (defensive)
            existing = target.read_bytes()
            if _sha256(existing) != digest:
                raise RuntimeError(f"blob digest mismatch for existing file {target}")
            # Still return reference
            return BlobReference(
                algorithm="sha256",
                digest=digest,
                size_bytes=len(data),
                media_type=media_type or "application/octet-stream",
                storage_key=key,
            )
        # Atomic write via temp file + rename
        target.parent.mkdir(parents=True, exist_ok=True)
        # Use tempfile in same directory for atomic rename
        fd, tmp_path = tempfile.mkstemp(dir=str(target.parent))
        try:
            os.write(fd, data)
            os.fsync(fd)
            os.close(fd)
            # Verify temp file digest
            written = Path(tmp_path).read_bytes()
            if _sha256(written) != digest:
                Path(tmp_path).unlink(missing_ok=True)
                raise RuntimeError("temp blob digest mismatch")
            # Atomic rename
            os.replace(tmp_path, target)
        finally:
            try:
                os.close(fd)
            except Exception:
                pass
            Path(tmp_path).unlink(missing_ok=True)
        return BlobReference(
            algorithm="sha256",
            digest=digest,
            size_bytes=len(data),
            media_type=media_type or "application/octet-stream",
            storage_key=key,
        )

    async def get_bytes(self, ref: BlobReference | str) -> bytes:
        if isinstance(ref, BlobReference):
            key = ref.storage_key
            digest = ref.digest
        else:
            # ref is str — could be storage_key or digest hex
            s = ref.strip()  # type: ignore[union-attr]
            if len(s) == 64 and all(c in "0123456789abcdef" for c in s.lower()):
                digest = s.lower()
                key, _ = _storage_key(digest)
            else:
                key = s
                digest = None

        target = self._path_for_key(key)
        if not target.exists():
            raise FileNotFoundError(f"blob not found: {key!r}")
        data = target.read_bytes()
        if isinstance(ref, BlobReference):
            # Verify digest if provided
            if _sha256(data) != ref.digest:
                raise RuntimeError(f"blob digest mismatch for {key!r}")
            if len(data) != ref.size_bytes:
                raise RuntimeError(f"blob size mismatch for {key!r}")
        elif digest is not None:
            if _sha256(data) != digest:
                raise RuntimeError(f"blob digest mismatch for {digest!r}")
        return data

    async def exists(self, ref: BlobReference | str) -> bool:
        try:
            if isinstance(ref, BlobReference):
                key = ref.storage_key
            else:
                s = ref.strip()  # type: ignore[union-attr]
                if len(s) == 64 and all(c in "0123456789abcdef" for c in s.lower()):
                    key, _ = _storage_key(s.lower())
                else:
                    key = s
            target = self._path_for_key(key)
            return target.exists()
        except Exception:
            return False

    async def stat(self, ref: BlobReference | str) -> BlobStat:
        try:
            if isinstance(ref, BlobReference):
                key = ref.storage_key
                target = self._path_for_key(key)
                if not target.exists():
                    return BlobStat(exists=False)
                data = target.read_bytes()
                return BlobStat(exists=True, size_bytes=len(data), digest=_sha256(data))
            else:
                s = ref.strip()  # type: ignore[union-attr]
                if len(s) == 64 and all(c in "0123456789abcdef" for c in s.lower()):
                    key, _ = _storage_key(s.lower())
                    target = self._path_for_key(key)
                    if target.exists():
                        data = target.read_bytes()
                        return BlobStat(exists=True, size_bytes=len(data), digest=s.lower())
                    return BlobStat(exists=False)
                else:
                    key = s
                    target = self._path_for_key(key)
                    if target.exists():
                        data = target.read_bytes()
                        return BlobStat(exists=True, size_bytes=len(data), digest=_sha256(data))
                    return BlobStat(exists=False)
        except Exception:
            return BlobStat(exists=False)

    async def delete(self, ref: BlobReference | str) -> None:
        if isinstance(ref, BlobReference):
            key = ref.storage_key
        else:
            s = ref.strip()  # type: ignore[union-attr]
            if len(s) == 64 and all(c in "0123456789abcdef" for c in s.lower()):
                key, _ = _storage_key(s.lower())
            else:
                key = s
        target = self._path_for_key(key)
        target.unlink(missing_ok=True)
        # Try to clean empty parent dirs (ab/cd)
        try:
            target.parent.rmdir()
            target.parent.parent.rmdir()
        except Exception:
            pass


class BlobsFilesystemPlugin(Plugin):
    def __init__(self, root: str | Path | None = None) -> None:
        self._root_override = Path(root) if root else None
        self._store: FilesystemBlobStore | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="storage.blobs_filesystem",
            version="0.1.0",
            plugin_type="storage",
            description="Filesystem content-addressed blob store",
            provides=["blob_store.default"],
            requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        root_val: str | None = None
        if "documents" in cfg and isinstance(cfg["documents"], dict):
            root_val = cfg["documents"].get("blob_root")
        elif "blob_root" in cfg:
            root_val = cfg["blob_root"]  # type: ignore[assignment]
        if self._root_override is not None:
            root = self._root_override
        elif root_val:
            root = Path(root_val)
        else:
            root = Path(".research/blobs")
        # Also support nested documents.blob_root from AppConfig plugin_config mapping
        # The bootstrap will pass {"documents": {"blob_root": ...}} or similar
        store = FilesystemBlobStore(root=root)
        self._store = store
        ctx.register("blob_store.default", store)

    async def stop(self) -> None:
        self._store = None

    async def teardown(self) -> None:
        self._store = None
