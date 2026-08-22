"""Unpaywall document locator — DOI-based, preserves raw via ProviderRecordSnapshot."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.document_location import (
    AccessType,
    DocumentLocation,
    HostType,
    VersionType,
)
from research_harness.research.schemas.provider_snapshot import ProviderRecordSnapshot

logger = logging.getLogger(__name__)


def _normalize_doi(doi: str) -> str:
    from research_harness.research.schemas.common import normalize_doi

    return normalize_doi(doi)


def _map_host_type(raw: str | None) -> HostType:
    if not raw:
        return HostType.unknown
    low = raw.lower()
    if low in ("publisher",):
        return HostType.publisher
    if low in ("repository",):
        return HostType.repository
    return HostType.unknown


def _map_version(raw: str | None) -> VersionType:
    if not raw:
        return VersionType.unknown
    low = raw.lower()
    if low in ("publishedversion", "published_version", "published"):
        return VersionType.publishedVersion
    if low in ("acceptedversion", "accepted_version"):
        return VersionType.acceptedVersion
    if low in ("submittedversion", "submitted_version"):
        return VersionType.submittedVersion
    return VersionType.unknown


class UnpaywallLocatorService:
    def __init__(
        self,
        artifact_store: Any,
        http_client: httpx.AsyncClient | None = None,
        email: str | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._store = artifact_store
        self._email = email or os.getenv("UNPAYWALL_EMAIL")
        self._client = http_client
        self._timeout = timeout_seconds
        self._own_client = False

    @property
    def resolver_id(self) -> str:
        return "documents.locator.unpaywall"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        # Create lazy client
        self._client = httpx.AsyncClient(timeout=self._timeout)
        self._own_client = True
        return self._client

    async def close(self) -> None:
        if self._own_client and self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass

    async def resolve(self, paper_identity_id: str) -> list[str]:
        # Load identity and find DOI
        ident_env = await self._store.get(paper_identity_id)
        from research_harness.research.schemas.identity import PaperIdentity

        if isinstance(ident_env.payload, dict):
            identity = PaperIdentity.model_validate(ident_env.payload)
        else:
            identity = ident_env.parse_payload(PaperIdentity)  # type: ignore[attr-defined]

        doi: str | None = None
        # Try canonical identifiers
        for ext in identity.canonical_identifiers:
            if ext.scheme.lower() == "doi":
                doi = ext.value
                break
        # Fallback: member PaperRecords
        if not doi:
            for pid in identity.member_paper_artifact_ids:
                try:
                    p_env = await self._store.get(pid)
                    from research_harness.research.schemas.paper import PaperRecord

                    if isinstance(p_env.payload, dict):
                        paper = PaperRecord.model_validate(p_env.payload)
                    else:
                        paper = p_env.parse_payload(PaperRecord)  # type: ignore[attr-defined]
                    if paper.doi:
                        doi = paper.doi
                        break
                    for ext in paper.external_identifiers:
                        if ext.scheme.lower() == "doi":
                            doi = ext.value
                            break
                    if doi:
                        break
                except Exception:
                    continue

        if not doi:
            return []

        try:
            norm_doi = _normalize_doi(doi)
        except Exception:
            return []

        if not self._email:
            logger.warning("UNPAYWALL_EMAIL not set, skipping unpaywall lookup for %s", norm_doi)
            return []

        # Check if we already have locations for this DOI/identity (idempotency via snapshot)
        # We check for existing DocumentLocation with same url and resolver
        # First, try to find existing ProviderRecordSnapshot for this DOI
        # If snapshot exists and locations already derived, reuse
        existing_locations = await self._store.list(artifact_type="document_location")
        # Also check for existing snapshot to avoid extra call? But we still need to ensure locations are returned
        # If we have a snapshot for this DOI, we can reuse locations without new HTTP

        # Look for existing snapshot
        existing_snapshots = await self._store.list(artifact_type="provider_record_snapshot")
        snap_id_for_doi: str | None = None
        for env in existing_snapshots:
            try:
                if isinstance(env.payload, dict):
                    snap = ProviderRecordSnapshot.model_validate(env.payload)
                else:
                    snap = env.parse_payload(ProviderRecordSnapshot)  # type: ignore[attr-defined]
                if snap.provider == "unpaywall" and snap.provider_record_id == norm_doi:
                    snap_id_for_doi = env.artifact_id
                    break
            except Exception:
                continue

        # If we already have locations derived from that snapshot, return them without new call
        if snap_id_for_doi:
            loc_ids = []
            for env in existing_locations:
                try:
                    if isinstance(env.payload, dict):
                        loc = DocumentLocation.model_validate(env.payload)
                    else:
                        loc = env.parse_payload(DocumentLocation)  # type: ignore[attr-defined]
                    if (
                        loc.paper_identity_id == paper_identity_id
                        and loc.provider_snapshot_id == snap_id_for_doi
                    ):
                        loc_ids.append(env.artifact_id)
                except Exception:
                    continue
            if loc_ids:
                # Already have locations for this identity+doi, reuse (idempotency)
                # Return sorted for determinism
                loc_ids.sort()
                return loc_ids
            # If snapshot exists but no locations for this identity yet, we still need to create locations for this identity from snapshot's raw
            # So we should load snapshot raw and create locations
            if snap_id_for_doi:
                snap_env = await self._store.get(snap_id_for_doi)
                if isinstance(snap_env.payload, dict):
                    snap = ProviderRecordSnapshot.model_validate(snap_env.payload)
                else:
                    snap = snap_env.parse_payload(ProviderRecordSnapshot)  # type: ignore[attr-defined]
                raw = snap.raw_payload
                locations = self._extract_locations_from_raw(
                    raw, paper_identity_id, snap_id_for_doi
                )
                # Persist locations
                created_ids = []
                for loc in locations:
                    # Check duplicate url for this identity
                    duplicate = None
                    for env in existing_locations:
                        try:
                            if isinstance(env.payload, dict):
                                existing_loc = DocumentLocation.model_validate(env.payload)
                            else:
                                existing_loc = env.parse_payload(DocumentLocation)  # type: ignore[attr-defined]
                            if (
                                existing_loc.paper_identity_id == paper_identity_id
                                and existing_loc.url == loc.url
                            ):
                                duplicate = env.artifact_id
                                break
                        except Exception:
                            continue
                    if duplicate:
                        created_ids.append(duplicate)
                        continue
                    loc_env = ArtifactEnvelope.create(
                        payload=loc,
                        artifact_type="document_location",
                        producer="documents.locator.unpaywall",
                    )
                    await self._store.put(loc_env)
                    await self._store.add_provenance(
                        ProvenanceLink(
                            relation=ProvenanceRelation.derived_from,
                            source_artifact_id=snap_id_for_doi,
                            target_artifact_id=loc_env.artifact_id,
                            producer="documents.locator.unpaywall",
                        )
                    )
                    await self._store.add_provenance(
                        ProvenanceLink(
                            relation=ProvenanceRelation.derived_from,
                            source_artifact_id=paper_identity_id,
                            target_artifact_id=loc_env.artifact_id,
                            producer="documents.locator.unpaywall",
                        )
                    )
                    created_ids.append(loc_env.artifact_id)
                created_ids.sort()
                return created_ids

        # No snapshot or no locations yet, need to call Unpaywall
        client = await self._get_client()
        url = f"https://api.unpaywall.org/v2/{quote(norm_doi, safe='/:')}"
        params = {"email": self._email}
        try:
            resp = await client.get(url, params=params)
        except httpx.TimeoutException as e:
            logger.warning("Unpaywall timeout for %s: %s", norm_doi, e)
            return []
        except Exception as e:
            logger.warning("Unpaywall request failed for %s: %s", norm_doi, e)
            return []

        if resp.status_code == 404:
            # No data, create snapshot with empty? Or just return no locations but still preserve snapshot indicating not found?
            # For 404, we should still create a snapshot indicating not found to avoid repeated calls? But spec says preserve raw response, so for 404 there is no JSON
            # We'll create a snapshot with empty raw and is_oa false
            raw_payload: dict[str, Any] = {"error": "not found", "doi": norm_doi, "status": 404}
            snap = ProviderRecordSnapshot(
                provider="unpaywall",
                provider_record_id=norm_doi,
                retrieved_at=datetime.now(UTC),
                request_kind="oa_lookup",
                request_metadata={"doi": norm_doi, "status": "404"},
                raw_payload=raw_payload,
                metadata={},
            )
            snap_env = ArtifactEnvelope.create(
                payload=snap,
                artifact_type="provider_record_snapshot",
                producer="documents.locator.unpaywall",
            )
            try:
                await self._store.put(snap_env)
            except Exception:
                # Duplicate snapshot (race) - fetch existing
                for env in await self._store.list(artifact_type="provider_record_snapshot"):
                    try:
                        s = (
                            ProviderRecordSnapshot.model_validate(env.payload)
                            if isinstance(env.payload, dict)
                            else env.parse_payload(ProviderRecordSnapshot)
                        )  # type: ignore[attr-defined]
                        if s.provider == "unpaywall" and s.provider_record_id == norm_doi:
                            snap_env = env
                            break
                    except Exception:
                        continue
            return []
        if resp.status_code == 429:
            logger.warning("Unpaywall rate limited for %s", norm_doi)
            return []
        if resp.status_code >= 500:
            logger.warning("Unpaywall server error %s for %s", resp.status_code, norm_doi)
            return []
        if resp.status_code != 200:
            logger.warning("Unpaywall unexpected status %s for %s", resp.status_code, norm_doi)
            return []

        try:
            raw = resp.json()
        except Exception as e:
            logger.warning("Unpaywall malformed JSON for %s: %s", norm_doi, e)
            return []

        # Preserve raw as snapshot
        snap = ProviderRecordSnapshot(
            provider="unpaywall",
            provider_record_id=norm_doi,
            retrieved_at=datetime.now(UTC),
            request_kind="oa_lookup",
            request_metadata={"doi": norm_doi, "url": url},
            raw_payload=raw,
            metadata={},
        )
        snap_env = ArtifactEnvelope.create(
            payload=snap,
            artifact_type="provider_record_snapshot",
            producer="documents.locator.unpaywall",
        )
        try:
            await self._store.put(snap_env)
            snap_id = snap_env.artifact_id
        except Exception:
            # Duplicate, find existing
            for env in await self._store.list(artifact_type="provider_record_snapshot"):
                try:
                    s = (
                        ProviderRecordSnapshot.model_validate(env.payload)
                        if isinstance(env.payload, dict)
                        else env.parse_payload(ProviderRecordSnapshot)
                    )  # type: ignore[attr-defined]
                    if s.provider == "unpaywall" and s.provider_record_id == norm_doi:
                        snap_id = env.artifact_id
                        break
                except Exception:
                    continue
            else:
                snap_id = snap_env.artifact_id

        locations = self._extract_locations_from_raw(raw, paper_identity_id, snap_id)
        created_ids: list[str] = []
        for loc in locations:
            # Deduplicate same url for this identity
            duplicate = None
            for env in existing_locations:
                try:
                    if isinstance(env.payload, dict):
                        existing_loc = DocumentLocation.model_validate(env.payload)
                    else:
                        existing_loc = env.parse_payload(DocumentLocation)  # type: ignore[attr-defined]
                    if (
                        existing_loc.paper_identity_id == paper_identity_id
                        and existing_loc.url == loc.url
                    ):
                        duplicate = env.artifact_id
                        break
                except Exception:
                    continue
            if duplicate:
                created_ids.append(duplicate)
                continue
            loc_env = ArtifactEnvelope.create(
                payload=loc,
                artifact_type="document_location",
                producer="documents.locator.unpaywall",
            )
            await self._store.put(loc_env)
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=snap_id,
                    target_artifact_id=loc_env.artifact_id,
                    producer="documents.locator.unpaywall",
                )
            )
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=paper_identity_id,
                    target_artifact_id=loc_env.artifact_id,
                    producer="documents.locator.unpaywall",
                )
            )
            created_ids.append(loc_env.artifact_id)

        # Deterministic ordering: sort by priority already, but also sort ids for return stability
        # Our _extract_locations_from_raw already returns in priority order, so keep that order for orchestrator priority
        # But for idempotency, we should return in that order
        return created_ids

    def _extract_locations_from_raw(
        self, raw: dict[str, Any], paper_identity_id: str, snapshot_id: str
    ) -> list[DocumentLocation]:
        locations: list[DocumentLocation] = []
        # Unpaywall structure: best_oa_location, oa_locations[]
        # Each has url, url_for_pdf, url_for_landing_page, host_type, version, license, evidence etc.
        best = raw.get("best_oa_location")
        oa_locations = raw.get("oa_locations") or []
        # Priority: best_oa_location with direct PDF first
        candidates: list[dict[str, Any]] = []
        if best and isinstance(best, dict):
            candidates.append(best)
        for loc in oa_locations:
            if loc not in candidates and isinstance(loc, dict):
                # Avoid duplicate of best
                if (
                    best
                    and loc.get("url_for_pdf") == best.get("url_for_pdf")
                    and loc.get("url") == best.get("url")
                ):
                    continue
                candidates.append(loc)

        # Sort candidates by priority: direct PDF, then host_type/repository? Actually spec says:
        # existing direct OA PDF from stored metadata (handled by other locator)
        # Unpaywall best OA direct PDF
        # other Unpaywall direct OA PDFs
        # OA landing-page-only
        # So within unpaywall, we should prioritize direct PDF
        def _priority(loc: dict[str, Any]) -> tuple[int, str]:
            pdf = loc.get("url_for_pdf")
            url = loc.get("url") or ""
            # Direct PDF has url_for_pdf non-empty
            is_direct = 0 if pdf and pdf.strip() else 1
            # Prefer best_oa_location first (already at front, but sort stable)
            # Use url as tie-breaker for determinism
            return (is_direct, url or "")

        candidates.sort(key=_priority)

        seen_urls: set[str] = set()
        for raw_loc in candidates:
            pdf_url = raw_loc.get("url_for_pdf")
            landing = raw_loc.get("url_for_landing_page") or raw_loc.get("url")
            # Prefer pdf_url if available, else landing
            url = None
            is_direct = False
            if pdf_url and isinstance(pdf_url, str) and pdf_url.strip().startswith("http"):
                url = pdf_url.strip()
                is_direct = True
            elif landing and isinstance(landing, str) and landing.strip().startswith("http"):
                url = landing.strip()
                is_direct = False
            else:
                # Fallback to url field
                url_field = raw_loc.get("url")
                if (
                    url_field
                    and isinstance(url_field, str)
                    and url_field.strip().startswith("http")
                ):
                    url = url_field.strip()
                    # Check if url looks like pdf
                    is_direct = url.lower().endswith(".pdf")

            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            # Access type open_access if is_oa true
            is_oa = raw.get("is_oa") or raw_loc.get("is_oa")
            access = AccessType.open_access if is_oa else AccessType.unknown
            # Only create if OA? Spec says prioritize OA, but we should only create OA locations
            # If not OA, skip unless explicitly open
            # But we will create for any candidate that has url and is in oa_locations (which are OA)
            # So treat as open_access
            loc = DocumentLocation(
                paper_identity_id=paper_identity_id,
                resolver=self.resolver_id,
                url=url,
                landing_page_url=landing if landing != url else None,
                media_type="application/pdf" if is_direct else None,
                access_type=access,
                host_type=_map_host_type(raw_loc.get("host_type")),
                version=_map_version(raw_loc.get("version")),
                license=raw_loc.get("license"),
                is_direct_download=is_direct,
                provider_snapshot_id=snapshot_id,
                metadata={
                    "unpaywall_host_type": raw_loc.get("host_type"),
                    "unpaywall_version": raw_loc.get("version"),
                },
            )
            locations.append(loc)
            if len(locations) >= 5:
                break

        return locations


class UnpaywallLocatorPlugin(Plugin):
    def __init__(
        self, http_client: httpx.AsyncClient | None = None, email: str | None = None
    ) -> None:
        self._client_override = http_client
        self._email_override = email
        self._service: UnpaywallLocatorService | None = None

    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="documents.locator.unpaywall",
            version="0.1.0",
            plugin_type="document_locator",
            description="Unpaywall OA locator (DOI-based)",
            provides=["document_locator.unpaywall"],
            requires=["artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        cfg = ctx.config or {}
        # Try documents.location config or env
        email: str | None = self._email_override
        if not email and "documents" in cfg and isinstance(cfg["documents"], dict):
            # documents.location.use_unpaywall is bool, but email is env only
            pass
        if not email:
            email = os.getenv("UNPAYWALL_EMAIL")
        # Also check config documents.unpaywall.email if provided (but never persist)
        if not email and "unpaywall" in cfg and isinstance(cfg["unpaywall"], dict):
            email = cfg["unpaywall"].get("email")  # type: ignore[assignment]

        store = ctx.require("artifact_store.default")
        # Create client with timeout from config
        timeout = 20.0
        if "documents" in cfg and isinstance(cfg["documents"], dict):
            dl_cfg = (
                cfg["documents"].get("download", {})
                if isinstance(cfg["documents"].get("download"), dict)
                else {}
            )
            timeout = float(dl_cfg.get("timeout_seconds", 20.0))
        client = self._client_override
        svc = UnpaywallLocatorService(
            artifact_store=store, http_client=client, email=email, timeout_seconds=timeout
        )
        self._service = svc
        ctx.register("document_locator.unpaywall", svc)

    async def teardown(self) -> None:
        if self._service is not None:
            try:
                await self._service.close()
            except Exception:
                pass
