"""Deterministic paper identity resolver — exact identifiers only."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from research_harness.contracts.identity import IdentityMatch, IdentityResolutionResult
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.provenance.relations import ProvenanceLink, ProvenanceRelation
from research_harness.research.schemas.common import normalize_doi
from research_harness.research.schemas.identity import (
    IdentityEvidence,
    PaperIdentity,
    ResolutionMethod,
)
from research_harness.research.schemas.paper import PaperRecord

logger = logging.getLogger(__name__)

# Allowlist of external identifier schemes considered identity-bearing
IDENTITY_SCHEMES = {"doi", "arxiv", "pmid", "openalex", "pmcid"}


class PaperIdentityResolverService:
    def __init__(self, artifact_store: Any, events: Any | None = None) -> None:
        self._store = artifact_store
        self._events = events

    async def resolve(self, paper_artifact_ids: list[str]) -> IdentityResolutionResult:
        if not paper_artifact_ids:
            return IdentityResolutionResult()

        # Deduplicate input
        unique_ids = list(dict.fromkeys(paper_artifact_ids))

        # Fetch PaperRecords
        id_to_paper: dict[str, PaperRecord] = {}
        id_to_hash: dict[str, str] = {}
        id_to_doi: dict[str, str | None] = {}
        id_to_ext_ids: dict[str, dict[str, str]] = {}  # scheme -> value
        unresolved: list[str] = []

        for pid in unique_ids:
            try:
                env = await self._store.get(pid)
                # Payload may be dict; use parse
                if isinstance(env.payload, dict):
                    paper = PaperRecord.model_validate(env.payload)
                elif isinstance(env.payload, PaperRecord):
                    paper = env.payload
                else:
                    # Try via envelope
                    paper = env.parse_payload(PaperRecord)  # type: ignore[attr-defined]
                id_to_paper[pid] = paper
                id_to_hash[pid] = env.content_hash
                # Normalized DOI
                doi = paper.doi
                if doi:
                    doi = normalize_doi(doi)
                id_to_doi[pid] = doi
                # Build scheme->value map for identity schemes
                ext_map: dict[str, str] = {}
                for eid in paper.external_identifiers:
                    scheme = eid.scheme.lower()
                    if scheme in IDENTITY_SCHEMES:
                        # Normalize value
                        val = eid.value.strip().lower() if scheme == "doi" else eid.value.strip()
                        if scheme == "doi":
                            val = normalize_doi(val)
                        ext_map[scheme] = val
                # Also add doi from PaperRecord.doi if present
                if doi and "doi" not in ext_map:
                    ext_map["doi"] = doi
                id_to_ext_ids[pid] = ext_map
            except Exception as e:
                logger.warning("Failed to fetch PaperRecord %s: %s", pid, e)
                unresolved.append(pid)

        # Filter to only successfully fetched
        valid_ids = [pid for pid in unique_ids if pid not in unresolved]

        # Build groups via union-find
        parent: dict[str, str] = {pid: pid for pid in valid_ids}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        # Evidence for each union
        # Map root -> list of evidence
        # For simplicity, we track evidence per pair
        pair_evidence: dict[tuple[str, str], IdentityEvidence] = {}

        # Rule 1: normalized DOI exact match
        doi_groups: dict[str, list[str]] = defaultdict(list)
        for pid, doi in id_to_doi.items():
            if doi:
                doi_groups[doi].append(pid)
        for doi, members in doi_groups.items():
            if len(members) > 1:
                # Union all members with this DOI
                first = members[0]
                for other in members[1:]:
                    union(first, other)
                    # Record evidence for first-other pair
                    key = (first, other) if first < other else (other, first)
                    if key not in pair_evidence:
                        pair_evidence[key] = IdentityEvidence(
                            identifier_scheme="doi",
                            normalized_value=doi,
                            member_artifact_ids=[first, other],
                        )

        # Rule 2: shared strong external identifier (allowlist)
        # For each scheme, group by value
        for scheme in IDENTITY_SCHEMES:
            if scheme == "doi":
                continue  # already handled
            groups: dict[str, list[str]] = defaultdict(list)
            for pid, ext_map in id_to_ext_ids.items():
                val = ext_map.get(scheme)
                if val:
                    groups[val].append(pid)
            for val, members in groups.items():
                if len(members) > 1:
                    first = members[0]
                    for other in members[1:]:
                        # Check if already united via DOI; still record evidence if new
                        if find(first) != find(other):
                            union(first, other)
                            key = (first, other) if first < other else (other, first)
                            if key not in pair_evidence:
                                pair_evidence[key] = IdentityEvidence(
                                    identifier_scheme=scheme,
                                    normalized_value=val,
                                    member_artifact_ids=[first, other],
                                )

        # Rule 3: exact content hash (for exact duplicate payloads)
        hash_groups: dict[str, list[str]] = defaultdict(list)
        for pid, h in id_to_hash.items():
            hash_groups[h].append(pid)
        for h, members in hash_groups.items():
            if len(members) > 1:
                first = members[0]
                for other in members[1:]:
                    if find(first) != find(other):
                        union(first, other)
                        key = (first, other) if first < other else (other, first)
                        if key not in pair_evidence:
                            pair_evidence[key] = IdentityEvidence(
                                identifier_scheme="content_hash",
                                normalized_value=h,
                                member_artifact_ids=[first, other],
                            )

        # Build groups from union-find
        groups: dict[str, list[str]] = defaultdict(list)
        for pid in valid_ids:
            root = find(pid)
            groups[root].append(pid)

        # For each group, determine if it represents a single paper or merged
        # Groups of size 1 are singletons — still create identity for them? Spec says resolver should create identities for all papers, even singletons?
        # The spec's identity resolution is for deduplication: papers with same DOI should be one identity.
        # For singletons, we should still create an identity with that single member, unless that member already has a current identity that is exactly that singleton.
        # For now, create identities for all groups.

        # Fetch existing PaperIdentity artifacts to handle idempotency and supersedes
        existing_identities: list[ArtifactEnvelope[Any]] = await self._store.list(
            artifact_type="paper_identity"
        )  # type: ignore[arg-type]
        # Map from frozenset(member ids) -> envelope
        existing_by_members: dict[frozenset[str], ArtifactEnvelope[Any]] = {}
        for env in existing_identities:
            try:
                identity = env.parse_payload(PaperIdentity)  # type: ignore[attr-defined]
                key = frozenset(identity.member_paper_artifact_ids)
                existing_by_members[key] = env
            except Exception:
                continue

        # Determine which existing identities are current (not superseded)
        # Find all superseded ids: those that are source of a supersedes edge
        superseded_ids: set[str] = set()
        for env in existing_identities:
            # Check if this identity is superseded by another
            # need to look at provenance where source is this id and relation supersedes
            children = await self._store.get_children(env.artifact_id)
            for link in children:
                if link.relation == ProvenanceRelation.supersedes:
                    # Actually supersedes edge is source=old, target=new, so old is source
                    # So if env is source of supersedes, it is superseded
                    superseded_ids.add(env.artifact_id)
                # Also if env is target of supersedes, it supersedes old, but that's not superseded itself
            # Also check parents where env is target of supersedes? That's the new one, not superseded
            # The above covers: old -> new via supersedes, so old is source

        # More accurate: check provenance where source == env.id and relation supersedes => env is superseded
        # We already did.

        # Build result
        result = IdentityResolutionResult()

        # For each new group, check for reuse or supersede
        for _root, members in groups.items():
            members_sorted = sorted(members)
            key = frozenset(members_sorted)
            # Collect evidence for this group: all pair evidences where both members in group
            group_evidence: list[IdentityEvidence] = []
            # Find a representative evidence: if group size >1, we need at least one evidence that justifies merging
            # For singletons, no evidence needed
            if len(members_sorted) > 1:
                # Collect all pair evidences that are within this group
                for (a, b), ev in pair_evidence.items():
                    if a in members_sorted and b in members_sorted:
                        group_evidence.append(ev)
                # If no pair evidence but group was formed via transitive union, we still need evidence
                # For transitive, we can synthesize: pick first DOI that all share?
                if not group_evidence:
                    # This can happen if group formed via transitive but we didn't record every pair
                    # For now, use the first member's DOI as evidence
                    first_doi = id_to_doi.get(members_sorted[0])
                    if first_doi:
                        group_evidence.append(
                            IdentityEvidence(
                                identifier_scheme="doi",
                                normalized_value=first_doi,
                                member_artifact_ids=members_sorted,
                            )
                        )

            # Determine method
            method = "exact_identifier"
            if group_evidence and any(
                e.identifier_scheme == "content_hash" for e in group_evidence
            ):
                # If only content hash, mark as exact_content
                if all(e.identifier_scheme == "content_hash" for e in group_evidence):
                    method = "exact_content"
            if len(members_sorted) == 1:
                method = "exact_identifier"  # singletons

            # Check for exact reuse: existing current identity with same members
            existing_env = existing_by_members.get(key)
            if existing_env is not None and existing_env.artifact_id not in superseded_ids:
                # Reuse
                result.identities_reused.append(existing_env.artifact_id)
                # Also need to create match entry for reporting
                result.matches.append(
                    IdentityMatch(
                        member_paper_artifact_ids=members_sorted,
                        method=method,
                        evidence=group_evidence,
                        confidence=1.0 if group_evidence else None,
                    )
                )
                continue

            # Check for supersedes: find existing current identities that are subsets of new group
            superseded_found: list[str] = []
            for existing_key, existing_env in existing_by_members.items():
                if existing_env.artifact_id in superseded_ids:
                    continue
                if existing_key.issubset(key) and existing_key != key:
                    # New group is superset of existing — should supersede
                    superseded_found.append(existing_env.artifact_id)

            # Create new identity
            # Canonical identifiers: collect unique DOIs etc from members
            canonical_ids: list[Any] = []
            seen_values: set[str] = set()
            for pid in members_sorted:
                paper = id_to_paper[pid]
                # M34: normalize before comparing, and key the dedupe set by
                # "scheme:value" in both branches so the two sources of DOIs
                # collapse together. PaperRecord normalizes its own `doi` field
                # but NOT the values in `external_identifiers`, so one merged
                # identity emitted the same DOI twice — once bare, once as a
                # URL — which downstream consumers read as two different papers.
                doi = normalize_doi(paper.doi) if paper.doi else None
                if doi:
                    doi_key = f"doi:{doi}"
                    if doi_key not in seen_values:
                        canonical_ids.append({"scheme": "doi", "value": doi})
                        seen_values.add(doi_key)
                for eid in paper.external_identifiers:
                    # M34: this rebound the loop's `key` — a frozenset of member
                    # ids the supersede check above depends on — to a str.
                    value = normalize_doi(eid.value) if eid.scheme == "doi" else eid.value
                    ident = f"{eid.scheme}:{value}"
                    if ident not in seen_values and eid.scheme in IDENTITY_SCHEMES:
                        canonical_ids.append({"scheme": eid.scheme, "value": value})
                        seen_values.add(ident)

            # Convert canonical_ids to ExternalIdentifier objects
            from research_harness.research.schemas.common import ExternalIdentifier

            canonical_objs = [
                ExternalIdentifier(scheme=c["scheme"], value=c["value"]) for c in canonical_ids
            ]

            identity = PaperIdentity(
                member_paper_artifact_ids=members_sorted,
                canonical_identifiers=canonical_objs,
                resolution_method=ResolutionMethod(method)
                if method in ResolutionMethod._value2member_map_
                else ResolutionMethod.exact_identifier,
                resolution_evidence=group_evidence,
                confidence=1.0 if group_evidence else 0.9,
                status="active",
            )
            # Determine artifact_type
            envelope = ArtifactEnvelope.create(
                payload=identity,
                artifact_type="paper_identity",
                producer="literature.identity_resolver",
            )
            await self._store.put(envelope)
            # Provenance: identity derived_from each member PaperRecord
            for pid in members_sorted:
                link = ProvenanceLink(
                    relation=ProvenanceRelation.derived_from,
                    source_artifact_id=pid,
                    target_artifact_id=envelope.artifact_id,
                    producer="literature.identity_resolver",
                )
                await self._store.add_provenance(link)

            # If superseded, add supersedes edges
            for old_id in superseded_found:
                sup_link = ProvenanceLink(
                    relation=ProvenanceRelation.supersedes,
                    source_artifact_id=old_id,
                    target_artifact_id=envelope.artifact_id,
                    producer="literature.identity_resolver",
                )
                await self._store.add_provenance(sup_link)
                result.identities_superseded.append(old_id)

            result.identities_created.append(envelope.artifact_id)
            result.matches.append(
                IdentityMatch(
                    member_paper_artifact_ids=members_sorted,
                    method=method,
                    evidence=group_evidence,
                    confidence=1.0 if group_evidence else None,
                )
            )

        # Unresolved are those not in any group? But we grouped all valid_ids, so unresolved is just those that failed fetch
        result.unresolved_paper_ids = unresolved

        # Emit event
        if self._events is not None:
            try:
                from research_harness.kernel.events import Event

                await self._events.publish(
                    Event.create(
                        event_type="paper_identity.resolved",
                        source="literature.identity_resolver",
                        payload={
                            "created": result.identities_created,
                            "reused": result.identities_reused,
                            "superseded": result.identities_superseded,
                            "matches": [m.model_dump() for m in result.matches],
                        },
                    )
                )
            except Exception:
                logger.exception("failed to emit paper_identity.resolved")

        return result


class PaperIdentityResolverPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="literature.identity_resolver",
            version="0.1.0",
            plugin_type="literature",
            description="Deterministic paper identity resolver",
            provides=["paper_identity_resolver.default"],
            requires=["artifact_store.default"],
        )

    async def setup(self, ctx: PluginContext) -> None:
        store = ctx.require("artifact_store.default")
        resolver = PaperIdentityResolverService(artifact_store=store, events=ctx.events)
        ctx.register("paper_identity_resolver.default", resolver)
