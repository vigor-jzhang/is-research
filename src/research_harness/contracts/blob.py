"""BlobStore contract — content-addressed immutable byte storage."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field


class BlobReference(BaseModel):
    """Logical reference to a blob, no absolute paths."""

    algorithm: str = Field(default="sha256", description="Hash algorithm")
    digest: str = Field(description="Hex digest")
    size_bytes: int = Field(ge=0)
    media_type: str = Field(default="application/octet-stream")
    storage_key: str = Field(description="Logical key, e.g. ab/cd/abcdef...")

    model_config = {"extra": "forbid"}


class BlobStat(BaseModel):
    exists: bool
    size_bytes: int | None = None
    digest: str | None = None

    model_config = {"extra": "forbid"}


class BlobStore(Protocol):
    """Content-addressed blob store."""

    async def put_bytes(self, data: bytes, media_type: str | None = None) -> BlobReference:
        """Store bytes, return reference (deduplicated by sha256)."""
        ...

    async def get_bytes(self, ref: BlobReference | str) -> bytes:
        """Retrieve bytes by reference or storage_key/digest."""
        ...

    async def exists(self, ref: BlobReference | str) -> bool: ...

    async def stat(self, ref: BlobReference | str) -> BlobStat: ...

    async def delete(self, ref: BlobReference | str) -> None:
        """Optional delete, cautious."""
        ...
