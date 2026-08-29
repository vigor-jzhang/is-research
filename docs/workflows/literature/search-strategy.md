# Search Strategy

## Lifecycle

```
ResearchQuestion
      │
      ▼
Search Planner (model role: fast, structured output)
      │
      ▼
LiteratureSearchStrategy  ──►  LiteratureQuery artifacts (2-8 queries)
      │                            │
      └────────┬────────┘          │
               ▼                   │
        Search Orchestrator        │
               │                   │
     ┌─────────┴─────────┐        │
     ▼                   ▼        │
 Crossref Source   Semantic Scholar│
     │                   │        │
     └─────────┬─────────┘        │
               ▼                  │
        LiteratureIngestor        │
               │                  │
      ┌────────┼─────────┐       │
      ▼        ▼         ▼       │
SearchRecord Snapshot  PaperRecord │
      │        │         ▲       │
      └────────┴─────────┘       │
               │                 │
               └─► PaperIdentity (exact DOI/arXiv/content hash)
                       │
                       ▼
        LiteratureSearchExecution
```

- `LiteratureQuery` = intended search (query, purpose, concepts, target_sources, year_from/to)
- `LiteratureSearchRecord` = one provider search that actually occurred (provider, query, filters, paper_artifact_ids, snapshot ids, pagination, `query_artifact_id` for traceability)
- `PaperRecord` = normalized observation from provider
- `PaperIdentity` = resolved scholarly-work identity (members, canonical identifiers, evidence)
- `LiteratureSearchExecution` = aggregate execution of a strategy (strategy_artifact_id, query_artifact_ids, search_record_artifact_ids, paper_artifact_ids, paper_identity_artifact_ids, counts, provider_failures)

## PaperRecord vs PaperIdentity

> `PaperRecord` is a normalized observation from a literature provider.
> `PaperIdentity` is our system's determination that one or more PaperRecords refer to the same scholarly work.

- `PaperRecord`s are immutable and preserved, never superseded by each other.
- `PaperIdentity` groups `PaperRecord`s believed same work via strong identifiers.
- Identity membership belongs inside `PaperIdentity`; provenance is `PaperRecord --derived_from--> PaperIdentity`.
- Revisions: new identity supersedes old (`PI2 supersedes PI1`), old remains retrievable.

## Conservative Resolution

search and identity resolution merges only on strong deterministic evidence, in priority:

1. **Normalized DOI** exact match — strongest
2. **Shared strong external identifier** — allowlist `doi, arxiv, pmid, openalex, pmcid`; e.g., shared `arxiv:2101.00001`
3. **Exact content hash** — same canonical payload hash (useful for repeated ingestion)

Not merged:

- Similar titles alone (`"Algorithmic Pricing in Platforms"` vs `"on Platforms"`) — kept separate
- Author/venue/year similarity — not used yet
- Missing identifiers — kept separate

False merges are more dangerous than temporary duplicates. Future phases will add candidate detection and probabilistic resolution.

Each `PaperIdentity` records `resolution_method` (`exact_identifier`/`exact_content`/`manual`) and `resolution_evidence` (e.g., `matched DOI: 10.123/a` with member ids) and `confidence`. Merges are not silent.

Idempotency: re-running resolution on same unchanged paper set reuses existing current identity (same members + evidence) — no duplicate `PaperIdentity`s. Supersedes only when membership grows.

## Provenance

```
ResearchQuestion ──derived_from──► LiteratureQuery
ResearchQuestion ──derived_from──► LiteratureSearchStrategy
LiteratureQuery ──derived_from──► LiteratureSearchStrategy
LiteratureQuery ──derived_from──► LiteratureSearchRecord
ProviderRecordSnapshot ──generated_from──► PaperRecord
PaperRecord ──derived_from──► PaperIdentity
LiteratureSearchStrategy + Queries + SearchRecords ──derived_from──► LiteratureSearchExecution
```

Traceability: `PaperIdentity` → `PaperRecord` → `ProviderRecordSnapshot` → `LiteratureSearchRecord` → `LiteratureQuery` → `SearchStrategy` → `ResearchQuestion`.

Every `LiteratureSearchRecord` has `query_artifact_id` field and `derived_from` provenance to its `LiteratureQuery`.

## Planning

`literature.search_planner` uses `model_router.default` (default role `fast`, configurable via `literature.planning.model_role`). Structured output schema:

```python
SearchStrategyProposal { objective, concepts, queries: list[SearchQueryProposal] }
SearchQueryProposal { query, purpose, concepts, target_sources, year_from, year_to }
```

Validation: non-empty query ≤500 chars, known sources, max 8 queries (configurable), valid year ranges. Truncates if model exceeds bounds. Persists `LiteratureQuery` artifacts (each `derived_from` RQ) and `LiteratureSearchStrategy` (derived_from RQ + queries).

No chain-of-thought stored — only `purpose`/`rationale_summary` if needed.

## Orchestration

`literature.search_orchestrator` executes a `LiteratureSearchStrategy`:

1. Load strategy + queries
2. For each query, for each target source (sequentially):
   - Resolve `literature_source.<name>`
   - `LiteratureSearchRequest` with `limit = max_results_per_query_per_source` (default 50)
   - `LiteratureIngestor.ingest_search` → snapshot/paper/search_record + `generated_from` provenance
   - Add `LiteratureQuery → LiteratureSearchRecord` provenance
   - Collect counts, respect budgets
3. Call `paper_identity_resolver` on all paper ids
4. Create `LiteratureSearchExecution` with `started_at/completed_at`, `provider_failures`, `counts` (`queries_planned/executed`, `provider_searches_*`, `raw_paper_records`, `unique_paper_identities`, `duplicate_records_collapsed`)
5. Provenance: execution `derived_from` strategy, queries, search records

Budgets (configurable via `literature.orchestration`):

```yaml
literature:
  orchestration:
    max_queries: 8
    max_results_per_query_per_source: 50
    max_total_provider_requests: 50
    max_total_papers: 500
```

Orchestrator, not LLM, enforces budgets. On budget hit, stops cleanly, records reason, preserves completed results. Sequential execution for deterministic rate-limit behavior.

Failure semantics: each provider search is independent; partial success preserves successful results, records failures explicitly (`provider_failures` list). Execution status `complete_success` / `partial_success` / `complete_failure` derived from counts.

## CLI

```bash
# Plan from ResearchQuestion (requires OpenRouter)
uv run --env-file .env research-agent literature plan --question <rq_artifact_id> [--research-plan <rp_id>]

# Execute strategy (uses real providers via ingestion, no LLM)
uv run --env-file .env research-agent literature execute --strategy <strategy_id>

# Convenience: plan + execute
uv run --env-file .env research-agent literature discover --question <rq_id>

# Inspect
uv run research-agent literature identities list
uv run research-agent literature identities inspect <identity_id>
uv run research-agent artifacts inspect <paper_id>
uv run research-agent artifacts lineage <paper_id> --direction ancestors
```

All commands are thin wrappers over services; no HTTP/SQL in CLI.

## Deterministic Offline Test

`tests/integration/test_phase2c_integration.py` demonstrates:

```
ResearchQuestion → fake model → SearchStrategy (2 queries) → fake Crossref (A, B) + fake Semantic Scholar (A duplicate)
→ ingestion → 3 PaperRecords (A, A duplicate, B) → 2 PaperIdentities (A group, B singleton) → LiteratureSearchExecution
```

Asserts `raw=3, unique=2, duplicate_collapsed=1`, provenance, and DB reopen persistence — no network, no OpenRouter.

## Query Safety

Query text is data, never shell/SQL/Python. Validated ≤500 chars, non-empty, passed only via typed `LiteratureSearchRequest`. No code execution.
