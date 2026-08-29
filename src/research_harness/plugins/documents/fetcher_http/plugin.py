"""HTTP document fetcher — DNS-pinned SSRF-safe, bounded, PDF-validating."""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import socket
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.document_acquisition import (
    AcquisitionStatus,
    DocumentAcquisition,
)

logger = logging.getLogger(__name__)

# Private networks to reject
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local + cloud metadata 169.254.169.254
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),  # unique local
    ipaddress.ip_network("fe80::/10"),  # link-local
]

_CLOUD_METADATA_IPS = {"169.254.169.254", "fd00:ec2::254"}  # simplified


def _is_private_hostname(host: str) -> bool:
    if not host:
        return True
    low = host.lower().strip()
    if low in ("localhost", "localhost.", "::1"):
        return True
    # Try ip literal
    try:
        ip = ipaddress.ip_address(low.strip("[]"))
        # ``is_global`` rejects private, loopback, link-local, reserved,
        # multicast, documentation, and carrier-grade NAT ranges.
        return not ip.is_global
    except ValueError:
        return low == "localhost" or low.endswith(".localhost")


def _validate_url(url: str) -> tuple[str, int, tuple[str, ...]]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme must be http/https, got {parsed.scheme!r} for {url!r}")
    host = parsed.hostname or ""
    if not host:
        raise ValueError(f"URL must have hostname: {url!r}")
    if _is_private_hostname(host):
        raise ValueError(f"URL host is private/local and rejected: {host!r} in {url!r}")
    # Hostname strings are not sufficient: DNS can map a public-looking name
    # to loopback, private, or metadata addresses. Validate every resolved IP
    # immediately before the client is allowed to connect.
    try:
        addresses = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError as e:
        raise ValueError(f"URL hostname could not be resolved: {host!r}") from e
    if not addresses:
        raise ValueError(f"URL hostname did not resolve: {host!r}")
    resolved = tuple(dict.fromkeys(sockaddr[0] for _, _, _, _, sockaddr in addresses))
    for address in resolved:
        if _is_private_hostname(address):
            raise ValueError(f"URL hostname resolves to private or non-global address: {host!r}")
    if parsed.username or parsed.password:
        raise ValueError(f"URL with userinfo rejected: {url!r}")
    return host, parsed.port or (443 if parsed.scheme == "https" else 80), resolved


def _is_pdf_bytes(data: bytes) -> bool:
    # PDF signature: %PDF- at start, allow leading whitespace/BOM?
    # Check first 1k for %PDF-
    head = data.lstrip(b"\x00\x20\x09\x0a\x0d\xef\xbb\xbf")  # strip whitespace and BOM
    return head.startswith(b"%PDF-")


def _looks_like_html(data: bytes) -> bool:
    head = data[:4096].lstrip().lower()
    return (
        head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<head" in head[:1024]
    )


class _PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect only to IPs validated for each requested host and port."""

    def __init__(self) -> None:
        self._backend = AutoBackend()
        self._addresses: dict[tuple[str, int], tuple[str, ...]] = {}

    def pin(self, host: str, port: int, addresses: tuple[str, ...]) -> None:
        self._addresses[(host.lower(), port)] = addresses

    async def connect_tcp(
        self, host: str, port: int, timeout: float | None = None, local_address: str | None = None, socket_options: Any = None
    ) -> Any:
        addresses = self._addresses.get((host.lower(), port))
        if not addresses:
            raise httpcore.ConnectError(f"no validated address pinned for {host}:{port}")
        # The HTTP origin retains the hostname for TLS SNI and certificate checks.
        return await self._backend.connect_tcp(
            addresses[0], port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options: Any = None) -> Any:
        raise httpcore.ConnectError("Unix sockets are not permitted for document fetches")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class _PinnedAsyncTransport(httpx.AsyncHTTPTransport):
    def __init__(self, backend: _PinnedNetworkBackend) -> None:
        super().__init__(trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(network_backend=backend)


class HttpFetcherService:
    def __init__(
        self,
        artifact_store: Any,
        blob_store: Any,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
        max_redirects: int = 5,
        max_bytes: int = 52428800,
        events: Any | None = None,
    ) -> None:
        self._store = artifact_store
        self._blobs = blob_store
        self._client = http_client
        self._timeout = timeout_seconds
        self._max_redirects = max_redirects
        self._max_bytes = max_bytes
        self._events = events
        self._own_client = False
        self._pinned_backend = _PinnedNetworkBackend()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=False,
            trust_env=False,
            transport=_PinnedAsyncTransport(self._pinned_backend),
        )
        self._own_client = True
        return self._client

    async def fetch(self, document_location_id: str) -> str:
        # Load location
        loc_env = await self._store.get(document_location_id)
        from research_harness.research.schemas.document_location import DocumentLocation

        if isinstance(loc_env.payload, dict):
            loc = DocumentLocation.model_validate(loc_env.payload)
        else:
            loc = loc_env.parse_payload(DocumentLocation)  # type: ignore[attr-defined]

        url = loc.url
        paper_identity_id = loc.paper_identity_id

        # Check if acquisition already exists for this location with same blob (idempotency)
        # We will search existing acquisitions for same location and if successful with blob, we could reuse
        # But we need to fetch first to know blob. Instead, check if there's already a successful acquisition for same location
        # and if its final_url still reachable? For now, we check if existing acquisition for same location with status downloaded/imported exists,
        # and if blob exists, reuse without refetch? However to know if blob changed, we'd need to fetch.
        # Simpler: always check existing acquisitions for same location before fetching and reuse if recent and blob exists
        # This satisfies "same location already produced successful acquisition with same immutable blob -> reuse"
        # We will after fetch compare digest.

        # Validate initial URL
        try:
            _validate_url(url)
        except ValueError as e:
            return await self._create_failed_acquisition(
                paper_identity_id,
                document_location_id,
                AcquisitionStatus.failed,
                "invalid_url",
                str(e),
            )

        started = datetime.now(UTC)
        client = await self._get_client()
        current_url = url
        redirect_count = 0
        final_url = url
        final_status: int | None = None
        data_bytes: bytes | None = None
        error_code: str | None = None
        error_msg: str | None = None

        # Emit started
        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="document.acquisition.started",
                        source="documents.fetcher.http",
                        payload={
                            "document_location_id": document_location_id,
                            "paper_identity_id": paper_identity_id,
                            "url": current_url,
                        },
                    )
                )
            except Exception:
                pass

        try:
            while redirect_count <= self._max_redirects:
                # Validate each redirect url
                host, port, addresses = _validate_url(current_url)
                if self._own_client:
                    self._pinned_backend.pin(host, port, addresses)
                try:
                    # Use streaming to enforce size limits
                    req = client.build_request(
                        "GET", current_url, headers={"User-Agent": "research-harness/0.1.0"}
                    )
                    resp = await client.send(req, stream=True)
                except httpx.TimeoutException as e:
                    error_code = "timeout"
                    error_msg = str(e)
                    final_status = None
                    break
                except Exception as e:
                    error_code = "network_error"
                    error_msg = str(e)
                    break

                final_status = resp.status_code
                final_url = str(resp.url)

                # Handle redirects manually
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc_header = resp.headers.get("location")
                    await resp.aclose()
                    if not loc_header:
                        error_code = "redirect_no_location"
                        error_msg = f"redirect {resp.status_code} without location"
                        break
                    # Resolve relative redirect
                    next_url = httpx.URL(loc_header)
                    if not next_url.is_absolute_url:
                        next_url = resp.url.join(next_url)
                    current_url = str(next_url)
                    redirect_count += 1
                    if redirect_count > self._max_redirects:
                        error_code = "too_many_redirects"
                        error_msg = f"exceeded max_redirects {self._max_redirects}"
                        break
                    # Validate redirect target
                    try:
                        _validate_url(current_url)
                    except ValueError as e:
                        error_code = "private_redirect"
                        error_msg = str(e)
                        break
                    continue

                # Not redirect, check status
                if resp.status_code == 429:
                    await resp.aclose()
                    error_code = "rate_limited"
                    error_msg = "429 Too Many Requests"
                    break
                if resp.status_code == 404:
                    await resp.aclose()
                    error_code = "not_found"
                    error_msg = "404 Not Found"
                    break
                if resp.status_code >= 400:
                    await resp.aclose()
                    error_code = "http_error"
                    error_msg = f"HTTP {resp.status_code}"
                    break

                # Check Content-Length early
                clen = resp.headers.get("content-length")
                if clen:
                    try:
                        clen_int = int(clen)
                        if clen_int > self._max_bytes:
                            await resp.aclose()
                            error_code = "too_large"
                            error_msg = (
                                f"Content-Length {clen_int} exceeds max_bytes {self._max_bytes}"
                            )
                            final_status = resp.status_code
                            # Create too_large acquisition
                            return await self._create_failed_acquisition(
                                paper_identity_id,
                                document_location_id,
                                AcquisitionStatus.too_large,
                                error_code,
                                error_msg,
                                http_status=resp.status_code,
                                final_url=final_url,
                            )
                    except ValueError:
                        pass

                # Stream with size enforcement
                chunks: list[bytes] = []
                total = 0
                try:
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > self._max_bytes:
                            await resp.aclose()
                            return await self._create_failed_acquisition(
                                paper_identity_id,
                                document_location_id,
                                AcquisitionStatus.too_large,
                                "too_large",
                                f"stream exceeded max_bytes {self._max_bytes}",
                                http_status=resp.status_code,
                                final_url=final_url,
                            )
                        chunks.append(chunk)
                    await resp.aclose()
                except Exception as e:
                    await resp.aclose()
                    error_code = "stream_error"
                    error_msg = str(e)
                    break

                data_bytes = b"".join(chunks)
                break  # success, exit loop

            else:
                # Loop exhausted due to redirects
                if error_code is None:
                    error_code = "too_many_redirects"
                    error_msg = "redirect loop"

        except ValueError as e:
            # SSRF validation failure
            return await self._create_failed_acquisition(
                paper_identity_id,
                document_location_id,
                AcquisitionStatus.failed,
                "invalid_url",
                str(e),
            )
        except Exception as e:
            error_code = "unknown_error"
            error_msg = str(e)

        if data_bytes is None:
            # Failed to fetch
            # Map error_code to status
            if error_code in ("not_found",):
                status = AcquisitionStatus.not_available
            elif error_code in ("too_large",):
                status = AcquisitionStatus.too_large
            elif error_code in ("private_redirect", "invalid_url") or error_code == "rate_limited":
                status = AcquisitionStatus.failed
            else:
                status = AcquisitionStatus.failed
            return await self._create_failed_acquisition(
                paper_identity_id,
                document_location_id,
                status,
                error_code or "failed",
                error_msg or "fetch failed",
                http_status=final_status,
                final_url=final_url,
            )

        # Validate PDF content
        # Check content-type header? Not trusted, but check if HTML
        # Use data_bytes to verify PDF signature
        if not _is_pdf_bytes(data_bytes):
            # Could be HTML login page
            if _looks_like_html(data_bytes):
                return await self._create_failed_acquisition(
                    paper_identity_id,
                    document_location_id,
                    AcquisitionStatus.invalid_content,
                    "invalid_content",
                    "URL returned HTML instead of PDF",
                    http_status=final_status,
                    final_url=final_url,
                )
            # Check if maybe PDF but missing signature? Treat as invalid
            return await self._create_failed_acquisition(
                paper_identity_id,
                document_location_id,
                AcquisitionStatus.invalid_content,
                "invalid_content",
                "PDF signature missing (%PDF-)",
                http_status=final_status,
                final_url=final_url,
            )

        # Hash and store blob
        sha = hashlib.sha256(data_bytes).hexdigest()
        size = len(data_bytes)

        # Check idempotency: existing acquisition for same location with same sha
        existing = await self._store.list(artifact_type="document_acquisition")
        for env in existing:
            try:
                if isinstance(env.payload, dict):
                    from research_harness.research.schemas.document_acquisition import (
                        DocumentAcquisition as DA,
                    )

                    acq = DA.model_validate(env.payload)
                else:
                    acq = env.parse_payload(DocumentAcquisition)  # type: ignore[attr-defined]
                if (
                    acq.document_location_id == document_location_id
                    and acq.sha256 == sha
                    and acq.status == AcquisitionStatus.downloaded
                ):
                    # Check blob exists
                    if acq.blob and await self._blobs.exists(acq.blob):
                        # Reuse
                        if self._events is not None:
                            try:
                                from research_harness.kernel.events import Event

                                await self._events.publish(
                                    Event.create(
                                        event_type="document.acquisition.completed",
                                        source="documents.fetcher.http",
                                        payload={
                                            "acquisition_id": env.artifact_id,
                                            "paper_identity_id": paper_identity_id,
                                            "status": acq.status.value,
                                            "reused": True,
                                        },
                                    )
                                )
                            except Exception:
                                pass
                        return env.artifact_id
            except Exception:
                continue

        # Store blob
        blob_ref = await self._blobs.put_bytes(data_bytes, media_type="application/pdf")

        # Create acquisition
        acq = DocumentAcquisition(
            paper_identity_id=paper_identity_id,
            document_location_id=document_location_id,
            status=AcquisitionStatus.downloaded,
            attempted_at=started,
            completed_at=datetime.now(UTC),
            blob=blob_ref,
            sha256=sha,
            size_bytes=size,
            media_type="application/pdf",
            http_status=final_status,
            final_url=final_url,
            source_type="http",
            metadata={},
        )
        env = ArtifactEnvelope.create(
            payload=acq, artifact_type="document_acquisition", producer="documents.fetcher.http"
        )
        await self._store.put(env)
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=document_location_id,
                target_artifact_id=env.artifact_id,
                producer="documents.fetcher.http",
            )
        )
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=paper_identity_id,
                target_artifact_id=env.artifact_id,
                producer="documents.fetcher.http",
            )
        )

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="document.acquisition.completed",
                        source="documents.fetcher.http",
                        payload={
                            "acquisition_id": env.artifact_id,
                            "paper_identity_id": paper_identity_id,
                            "status": "downloaded",
                            "sha256": sha[:8],
                            "size_bytes": size,
                        },
                    )
                )
            except Exception:
                pass

        return env.artifact_id

    async def _create_failed_acquisition(
        self,
        paper_identity_id: str,
        document_location_id: str | None,
        status: AcquisitionStatus,
        failure_code: str,
        failure_message: str,
        http_status: int | None = None,
        final_url: str | None = None,
    ) -> str:
        acq = DocumentAcquisition(
            paper_identity_id=paper_identity_id,
            document_location_id=document_location_id,
            status=status,
            attempted_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            blob=None,
            sha256=None,
            size_bytes=None,
            media_type=None,
            http_status=http_status,
            final_url=final_url,
            source_type="http" if document_location_id else "none",
            failure_code=failure_code,
            failure_message=failure_message,
            metadata={},
        )
        env = ArtifactEnvelope.create(
            payload=acq, artifact_type="document_acquisition", producer="documents.fetcher.http"
        )
        await self._store.put(env)
        if document_location_id:
            try:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=document_location_id,
                        target_artifact_id=env.artifact_id,
                        producer="documents.fetcher.http",
                    )
                )
            except Exception:
                pass
        try:
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=paper_identity_id,
                    target_artifact_id=env.artifact_id,
                    producer="documents.fetcher.http",
                )
            )
        except Exception:
            pass

        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="document.acquisition.failed",
                        source="documents.fetcher.http",
                        payload={
                            "paper_identity_id": paper_identity_id,
                            "document_location_id": document_location_id,
                            "failure_code": failure_code,
                            "status": status.value,
                        },
                    )
                )
            except Exception:
                pass
        return env.artifact_id

    async def close(self) -> None:
        if self._own_client and self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass


class HttpFetcherPlugin(Plugin):
    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._client_override = http_client
        self._service: HttpFetcherService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="documents.fetcher.http",
            version="0.1.0",
            plugin_type="document_fetcher",
            description="HTTP document fetcher with SSRF/size/PDF validation",
            provides=["document_fetcher.default"],
            requires=["artifact_store.default", "blob_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        docs_cfg: dict[str, Any] = {}
        if "documents" in cfg and isinstance(cfg["documents"], dict):
            docs_cfg = cfg["documents"]  # type: ignore[assignment]
        dl_cfg = docs_cfg.get("download", {}) if isinstance(docs_cfg.get("download"), dict) else {}
        timeout = float(dl_cfg.get("timeout_seconds", 30))
        max_redirects = int(dl_cfg.get("max_redirects", 5))
        max_bytes = int(dl_cfg.get("max_bytes", 52428800))

        store = ctx.require("artifact_store.default")
        blobs = ctx.require("blob_store.default")
        svc = HttpFetcherService(
            artifact_store=store,
            blob_store=blobs,
            http_client=self._client_override,
            timeout_seconds=timeout,
            max_redirects=max_redirects,
            max_bytes=max_bytes,
            events=ctx.events,
        )
        self._service = svc
        ctx.register("document_fetcher.default", svc)

    async def teardown(self) -> None:
        if self._service is not None:
            try:
                await self._service.close()
            except Exception:
                pass
