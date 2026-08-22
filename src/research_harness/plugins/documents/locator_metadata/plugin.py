"""Existing-metadata document locator — deterministic, no network."""

from __future__ import annotations

from typing import Any

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.document_location import (
    AccessType,
    DocumentLocation,
    HostType,
    VersionType,
)


def _is_pdf_url(url: str) -> bool:
    low = url.lower().split("?")[0].split("#")[0]
    return low.endswith(".pdf")


class MetadataLocatorService:
    def __init__(self, artifact_store: Any) -> None:
        self._store = artifact_store

    @property
    def resolver_id(self) -> str:
        return "documents.locator.metadata"

    async def resolve(self, paper_identity_id: str) -> list[str]:
        # Load identity
        ident_env = await self._store.get(paper_identity_id)
        from research_harness.research.schemas.identity import PaperIdentity

        if isinstance(ident_env.payload, dict):
            identity = PaperIdentity.model_validate(ident_env.payload)
        else:
            identity = ident_env.parse_payload(PaperIdentity)  # type: ignore[attr-defined]

        candidates: list[tuple[str, str, str | None]] = []  # (url, artifact_id, media_type)
        seen_urls: set[str] = set()

        for pid in sorted(identity.member_paper_artifact_ids):
            try:
                p_env = await self._store.get(pid)
                from research_harness.research.schemas.paper import PaperRecord

                if isinstance(p_env.payload, dict):
                    paper = PaperRecord.model_validate(p_env.payload)
                else:
                    paper = p_env.parse_payload(PaperRecord)  # type: ignore[attr-defined]
            except Exception:
                continue

            # Check open_access_url
            if paper.open_access_url and paper.open_access_url.strip():
                url = paper.open_access_url.strip()
                if url not in seen_urls and url.startswith("http"):
                    seen_urls.add(url)
                    # Determine if direct PDF
                    is_pdf = _is_pdf_url(url) or (paper.metadata.get("open_access_pdf_url") == url)
                    candidates.append((url, pid, "application/pdf" if is_pdf else None))

            # Check PaperRecord metadata open_access_pdf_url (from Semantic Scholar mapping)
            # Semantic Scholar mapper stores openAccessPdf url in open_access_url, but also check metadata
            for key in ("open_access_pdf_url", "openAccessPdf_url", "pdf_url"):
                val = paper.metadata.get(key)
                if (
                    val
                    and isinstance(val, str)
                    and val.strip() not in seen_urls
                    and val.strip().startswith("http")
                ):
                    url = val.strip()
                    seen_urls.add(url)
                    candidates.append((url, pid, "application/pdf"))

            # Check external identifiers that are URLs? Not needed

            # Check PaperRecord url if it looks like PDF (but avoid arbitrary publisher URLs)
            # Only consider if open_access_url missing and url is direct pdf from known repository
            # For safety, only add if url ends with pdf and is not same as seen
            if paper.url and paper.url.strip() not in seen_urls and _is_pdf_url(paper.url.strip()):
                url = paper.url.strip()
                if url.startswith("http"):
                    seen_urls.add(url)
                    candidates.append((url, pid, "application/pdf"))

        # Deterministic ordering: sorted by url
        candidates.sort(key=lambda x: x[0])

        location_ids: list[str] = []
        for url, pid, media_type in candidates:
            # Deduplicate already existing locations for this identity+url (idempotency)
            existing = await self._store.list(artifact_type="document_location")
            found = None
            for env in existing:
                try:
                    if isinstance(env.payload, dict):
                        from research_harness.research.schemas.document_location import (
                            DocumentLocation as DL,
                        )

                        loc = DL.model_validate(env.payload)
                    else:
                        loc = env.parse_payload(DocumentLocation)  # type: ignore[attr-defined]
                    if (
                        loc.paper_identity_id == paper_identity_id
                        and loc.url == url
                        and loc.resolver == self.resolver_id
                    ):
                        found = env.artifact_id
                        break
                except Exception:
                    continue
            if found:
                location_ids.append(found)
                continue

            loc = DocumentLocation(
                paper_identity_id=paper_identity_id,
                resolver=self.resolver_id,
                url=url,
                landing_page_url=None,
                media_type=media_type or "application/pdf",
                access_type=AccessType.open_access,
                host_type=HostType.repository
                if "semanticscholar" in url or "arxiv" in url.lower()
                else HostType.publisher
                if "doi.org" in url
                else HostType.unknown,
                version=VersionType.unknown,
                license=None,
                is_direct_download=_is_pdf_url(url),
                provider_snapshot_id=None,
                metadata={"source_paper_artifact_id": pid},
            )
            env = ArtifactEnvelope.create(
                payload=loc,
                artifact_type="document_location",
                producer="documents.locator.metadata",
            )
            await self._store.put(env)
            await self._store.add_provenance(
                ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=paper_identity_id,
                    target_artifact_id=env.artifact_id,
                    producer="documents.locator.metadata",
                )
            )
            # Also link from paper
            try:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=pid,
                        target_artifact_id=env.artifact_id,
                        producer="documents.locator.metadata",
                    )
                )
            except Exception:
                pass
            location_ids.append(env.artifact_id)

        return location_ids


class MetadataLocatorPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="documents.locator.metadata",
            version="0.1.0",
            plugin_type="document_locator",
            description="Metadata-based document locator (existing OA URLs)",
            provides=["document_locator.metadata"],
            requires=["artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        store = ctx.require("artifact_store.default")
        svc = MetadataLocatorService(artifact_store=store)
        ctx.register("document_locator.metadata", svc)
