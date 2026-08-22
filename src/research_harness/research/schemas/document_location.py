"""DocumentLocation — candidate location of a document."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class AccessType(str, Enum):
    open_access = "open_access"
    user_provided = "user_provided"
    unknown = "unknown"
    restricted = "restricted"


class HostType(str, Enum):
    publisher = "publisher"
    repository = "repository"
    unknown = "unknown"


class VersionType(str, Enum):
    publishedVersion = "publishedVersion"
    acceptedVersion = "acceptedVersion"
    submittedVersion = "submittedVersion"
    unknown = "unknown"


class DocumentLocation(BaseModel):
    paper_identity_id: str = Field(description="PaperIdentity artifact id")
    resolver: str = Field(description="Resolver id, e.g. documents.locator.metadata")
    url: str = Field(description="Direct URL candidate")
    landing_page_url: str | None = Field(default=None)
    media_type: str | None = Field(default=None, description="e.g. application/pdf")
    access_type: AccessType = Field(default=AccessType.unknown)
    host_type: HostType = Field(default=HostType.unknown)
    version: VersionType = Field(default=VersionType.unknown)
    license: str | None = Field(default=None)
    is_direct_download: bool = Field(default=False)
    discovered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provider_snapshot_id: str | None = Field(
        default=None, description="ProviderRecordSnapshot for Unpaywall"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("paper_identity_id", "resolver", "url")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must be non-empty")
        return v.strip()

    @field_validator("url", "landing_page_url")
    @classmethod
    def validate_url(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError(f"url must be http/https, got {v!r}")
        return v

    @property
    def schema_version(self) -> int:
        return 1

    model_config = {"extra": "forbid"}
