"""PaperScreeningView builder — deterministic."""

from __future__ import annotations

import logging
from typing import Any

from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.paper import PaperRecord
from research_harness.research.schemas.screening_view import FieldSource, PaperScreeningView

logger = logging.getLogger(__name__)


def _normalize_title(t: str) -> str:
    return " ".join(t.strip().lower().split())


def _select_title(
    papers: list[tuple[str, PaperRecord, str]],
) -> tuple[str | None, FieldSource | None, list[str]]:
    # papers: list of (artifact_id, PaperRecord, provider)
    # Prefer non-empty normalized title, deterministic by provider priority (crossref first) then artifact_id
    candidates: list[tuple[str, str, str]] = []  # (title, artifact_id, provider)
    for aid, p, provider in papers:
        if p.title and p.title.strip():
            candidates.append((p.title.strip(), aid, provider))
    if not candidates:
        return None, None, []
    # Group by normalized title
    norm_map: dict[str, list[tuple[str, str, str]]] = {}
    for title, aid, prov in candidates:
        norm = _normalize_title(title)
        norm_map.setdefault(norm, []).append((title, aid, prov))
    # If multiple titles with same normalized, they are equivalent — pick first deterministically
    # If titles materially differ, pick deterministic: prefer crossref, then smallest artifact_id
    # For now, pick the first by provider priority (crossref > semantic_scholar) then aid
    provider_priority = {"crossref": 0, "semantic_scholar": 1}
    # Sort candidates by priority then aid
    candidates_sorted = sorted(candidates, key=lambda x: (provider_priority.get(x[2], 99), x[1]))
    chosen_title, chosen_aid, chosen_prov = candidates_sorted[0]
    # Check for conflicts: collect all distinct titles
    distinct_titles = list({c[0] for c in candidates})
    return (
        chosen_title,
        FieldSource(paper_artifact_id=chosen_aid, provider=chosen_prov, field_name="title"),
        distinct_titles,
    )


def _select_abstract(
    papers: list[tuple[str, PaperRecord, str]],
) -> tuple[str | None, FieldSource | None, list[str | None]]:
    # Prefer non-empty abstract, deterministic by provider priority (semantic_scholar often has richer abstract) or by length?
    # Spec: "Prefer non-empty abstract over missing, use deterministic provider/source priority or richness"
    # We will prefer longest non-empty abstract (richness) with tie-breaker provider priority
    candidates: list[tuple[str, str, str]] = []  # (abstract, aid, provider)
    for aid, p, provider in papers:
        if p.abstract and p.abstract.strip():
            candidates.append((p.abstract.strip(), aid, provider))
    if not candidates:
        return None, None, [p.abstract for _, p, _ in papers]
    # Prefer longest
    # Sort by length desc, then provider priority, then aid
    provider_priority = {
        "semantic_scholar": 0,
        "crossref": 1,
    }  # prefer semantic_scholar for abstract
    candidates_sorted = sorted(
        candidates, key=lambda x: (-len(x[0]), provider_priority.get(x[2], 99), x[1])
    )
    chosen_abs, chosen_aid, chosen_prov = candidates_sorted[0]
    return (
        chosen_abs,
        FieldSource(paper_artifact_id=chosen_aid, provider=chosen_prov, field_name="abstract"),
        [c[0] for c in candidates],
    )


def _select_year(
    papers: list[tuple[str, PaperRecord, str]],
) -> tuple[int | None, FieldSource | None, list[int | None]]:
    candidates: list[tuple[int, str, str]] = []
    for aid, p, provider in papers:
        if p.year is not None:
            candidates.append((p.year, aid, provider))
    if not candidates:
        return None, None, [p.year for _, p, _ in papers]
    # Prefer most common year, or first by priority
    # For determinism, pick smallest aid among those with year
    # Also record conflicts
    # Choose the year that appears most frequently, tie-break by smallest aid
    from collections import Counter

    counter = Counter([c[0] for c in candidates])
    most_common_year = counter.most_common(1)[0][0]
    # Among candidates with that year, pick smallest aid
    candidates_with_year = [c for c in candidates if c[0] == most_common_year]
    candidates_with_year.sort(key=lambda x: x[1])
    chosen_year, chosen_aid, chosen_prov = candidates_with_year[0]
    return (
        chosen_year,
        FieldSource(paper_artifact_id=chosen_aid, provider=chosen_prov, field_name="year"),
        [c[0] for c in candidates],
    )


def _select_authors(
    papers: list[tuple[str, PaperRecord, str]],
) -> tuple[list[str], FieldSource | None]:
    # Prefer first non-empty authors list deterministically
    for aid, p, provider in sorted(papers, key=lambda x: x[0]):  # sorted by aid
        if p.authors:
            names = [a.name for a in p.authors]
            return names, FieldSource(
                paper_artifact_id=aid, provider=provider, field_name="authors"
            )
    return [], None


def _select_venue(
    papers: list[tuple[str, PaperRecord, str]],
) -> tuple[str | None, FieldSource | None]:
    for aid, p, provider in sorted(papers, key=lambda x: x[0]):
        if p.venue and p.venue.strip():
            return p.venue.strip(), FieldSource(
                paper_artifact_id=aid, provider=provider, field_name="venue"
            )
    return None, None


class ScreeningViewBuilderService:
    def __init__(self, artifact_store: Any) -> None:
        self._store = artifact_store

    async def build(self, paper_identity_id: str) -> str:
        # Load identity
        ident_env = await self._store.get(paper_identity_id)
        from research_harness.research.schemas.identity import PaperIdentity

        if isinstance(ident_env.payload, dict):
            identity = PaperIdentity.model_validate(ident_env.payload)
        else:
            identity = ident_env.parse_payload(PaperIdentity)  # type: ignore[attr-defined]

        # Check if view already exists for this identity (idempotency: same members + same identity version)
        # For now, we will check if there's already a view for this identity id
        # Search for existing views with same paper_identity_id
        existing_views = await self._store.list(artifact_type="paper_screening_view")
        for env in existing_views:
            try:
                if isinstance(env.payload, dict):
                    view = PaperScreeningView.model_validate(env.payload)
                else:
                    view = env.parse_payload(PaperScreeningView)  # type: ignore[attr-defined]
                if view.paper_identity_id == paper_identity_id:
                    # Check if members are same? The view's member_paper_artifact_ids should match identity's current members
                    # If members match, reuse
                    if set(view.member_paper_artifact_ids) == set(
                        identity.member_paper_artifact_ids
                    ):
                        return env.artifact_id
            except Exception:
                continue

        # Load member PaperRecords
        papers: list[tuple[str, PaperRecord, str]] = []  # (artifact_id, PaperRecord, provider)
        for pid in identity.member_paper_artifact_ids:
            try:
                p_env = await self._store.get(pid)
                # Payload may be dict
                if isinstance(p_env.payload, dict):
                    paper = PaperRecord.model_validate(p_env.payload)
                else:
                    paper = p_env.parse_payload(PaperRecord)  # type: ignore[attr-defined]
                # Provider from paper's external_identifiers or from snapshot? We can infer from paper's metadata or from provenance
                # For now, try to get provider from paper's metadata or from snapshot provenance
                # Simplistic: check if paper has doi from crossref vs semantic_scholar via external_ids
                # We'll try to determine provider by looking at provenance: paper generated_from snapshot, snapshot provider
                provider = None
                # Look at parents of paper: should be snapshot
                try:
                    parents = await self._store.get_parents(pid)
                    for link in parents:
                        if link.relation.value == "generated_from":
                            # Fetch snapshot
                            snap_env = await self._store.get(link.source_artifact_id)
                            from research_harness.research.schemas.provider_snapshot import (
                                ProviderRecordSnapshot,
                            )

                            snap = (
                                snap_env.parse_payload(ProviderRecordSnapshot)
                                if hasattr(snap_env, "parse_payload")
                                else ProviderRecordSnapshot.model_validate(snap_env.payload)
                            )  # type: ignore[attr-defined]
                            provider = snap.provider
                            break
                except Exception:
                    pass
                if provider is None:
                    # Fallback: check external_identifiers for semantic_scholar vs crossref
                    # Crossref papers have crossref_publisher in metadata, semantic have semantic_scholar_paperId
                    if paper.metadata.get("crossref_publisher"):
                        provider = "crossref"
                    elif paper.metadata.get("semantic_scholar_paperId"):
                        provider = "semantic_scholar"
                    else:
                        provider = "unknown"
                papers.append((pid, paper, provider))
            except Exception as e:
                logger.warning(
                    "Failed to load PaperRecord %s for identity %s: %s", pid, paper_identity_id, e
                )
                continue

        if not papers:
            raise ValueError(f"No member papers found for identity {paper_identity_id!r}")

        # Deterministic selection
        title, title_src, all_titles = _select_title(papers)
        abstract, abs_src, all_abstracts = _select_abstract(papers)
        year, year_src, all_years = _select_year(papers)
        authors, authors_src = _select_authors(papers)
        venue, venue_src = _select_venue(papers)

        field_sources: dict[str, FieldSource] = {}
        if title_src:
            field_sources["title"] = title_src
        if abs_src:
            field_sources["abstract"] = abs_src
        if year_src:
            field_sources["year"] = year_src
        if authors_src:
            field_sources["authors"] = authors_src
        if venue_src:
            field_sources["venue"] = venue_src

        metadata: dict[str, Any] = {}
        if len(set(all_titles)) > 1:
            metadata["title_conflicts"] = all_titles
        if len([a for a in all_abstracts if a]) > 1 and len({a for a in all_abstracts if a}) > 1:
            metadata["abstract_conflicts"] = True
        if len({y for y in all_years if y is not None}) > 1:
            metadata["year_conflicts"] = all_years
        if abstract is None:
            metadata["missing_abstract"] = True

        view = PaperScreeningView(
            paper_identity_id=paper_identity_id,
            title=title,
            abstract=abstract,
            authors=authors,
            year=year,
            venue=venue,
            field_sources=field_sources,
            member_paper_artifact_ids=identity.member_paper_artifact_ids,
            metadata=metadata,
            all_titles=all_titles,
            all_abstracts=all_abstracts,
        )

        env = ArtifactEnvelope.create(
            payload=view,
            artifact_type="paper_screening_view",
            producer="literature.screening_view_builder",
        )
        await self._store.put(env)
        # Provenance: view derived_from identity and each member paper
        await self._store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=paper_identity_id,
                target_artifact_id=env.artifact_id,
                producer="literature.screening_view_builder",
            )
        )
        for pid, _, _ in papers:
            try:
                await self._store.add_provenance(
                    ProvenanceLink(
                        relation=ProvenanceRelation.derived_from,
                        source_artifact_id=pid,
                        target_artifact_id=env.artifact_id,
                        producer="literature.screening_view_builder",
                    )
                )
            except Exception:
                pass

        return env.artifact_id


class ScreeningViewBuilderPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="literature.screening_view_builder",
            version="0.1.0",
            plugin_type="literature",
            description="Deterministic PaperScreeningView builder",
            provides=["screening_view_builder.default"],
            requires=["artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        store = ctx.require("artifact_store.default")
        service = ScreeningViewBuilderService(artifact_store=store)
        ctx.register("screening_view_builder.default", service)
