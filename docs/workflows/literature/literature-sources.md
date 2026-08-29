# Literature Sources

## LiteratureSource Contract

Provider-neutral async protocol in `src/research_harness/contracts/literature.py`:

```python
class LiteratureSource(Protocol):
    provider_name: str  # "crossref" | "semantic_scholar"

    async def search(request: LiteratureSearchRequest) -> LiteratureSearchPage
    async def get(identifier: str) -> LiteratureSearchHit
```

```python
LiteratureSearchRequest  # query, year_from, year_to, limit, page_token (opaque), metadata
LiteratureSearchPage  # provider, hits, total_estimate, next_page_token (opaque), metadata
LiteratureSearchHit  # paper: PaperRecord, raw_payload: dict, provider, provider_record_id, rank, score, metadata
```

`page_token` is opaque at the contract boundary — Crossref uses `offset` (rows), Semantic Scholar uses `offset` as well, but the caller only sees an opaque string. Pagination is tested by retrieving a second page in mocked tests (`tests/unit/test_crossref.py:pagination`, `tests/unit/test_semantic_scholar.py:pagination`).

Downstream code never sees provider pagination internals.

## Provider-Specific JSON Stops at the Adapter

> **Provider-specific JSON stops at the source-adapter boundary. Downstream research capabilities operate on canonical research artifacts.**

Each plugin contains a pure `mapper.py`:

```
Crossref JSON  → map_crossref_work() → PaperRecord
Semantic Scholar JSON → map_semantic_scholar_paper() → PaperRecord
```

`client.py` handles HTTP; `mapper.py` is pure and independently testable; no HTTP in mapper, no persistence in client.

`PaperRecord` is the canonical type consumed downstream:

```python
paper.title
paper.authors  # list[Author{name, external_ids}]
paper.year
paper.abstract
paper.doi  # normalized bare 10.xxxx/...
paper.external_identifiers  # list[ExternalIdentifier{scheme, value}]
```

No `CrossrefWork`, `SemanticScholarPaper`, or raw `dict` leaks beyond the adapter.

Provider IDs are preserved via `ExternalIdentifier`:

- Crossref: `doi` (normalized via `research/schemas/common.py:normalize_doi`)
- Semantic Scholar: `semantic_scholar` (paperId), `corpus_id`, `doi`, `arxiv`, etc.

DOI normalization is shared — both mappers call `normalize_doi`, handling `10.xxxx`, `https://doi.org/`, `dx.doi.org`, `doi:` → `10.xxxx` lower-case.

Search ranking is not bibliographic metadata — `LiteratureSearchHit.score` and `rank` live in the hit, not in `PaperRecord`. `score` is optional because not every provider exposes it.

## Crossref Adapter

- Plugin `literature.crossref` provides `literature_source.crossref` (`plugins/literature/crossref/plugin.py`)
- Client `plugins/literature/crossref/client.py` uses `httpx` directly to `https://api.crossref.org/works`
  - `search`: `GET /works?query=&rows=&offset=&filter=from-pub-date,until-pub-date&mailto=`
  - `get`: `GET /works/{doi}`
  - `User-Agent` includes `research-harness/0.1.0` + `mailto` if set; `mailto` also sent as query param for polite pool
  - Abstract markup cleaned conservatively: strips `<jats:...>` tags, collapses whitespace (`mapper.py:_clean_abstract`)
  - Year from `published-print`/`published-online`/`issued`/`created` date-parts; venue from `container-title`; authors from `given`+`family` + ORCID
  - Pagination via `offset` encoded as opaque `page_token`; `next_page_token = offset+limit` if more results
  - Errors: 400 → `LiteratureResponseError`, 404 → `LiteratureNotFoundError`, 429 → `LiteratureRateLimitError` with `Retry-After`, 5xx retried with exponential backoff + `Retry-After`, timeouts/connect retried bounded (`MAX_RETRIES=3`)
  - Mapper preserves `crossref_publisher`/`crossref_type` in `PaperRecord.metadata`, not full raw

## Semantic Scholar Adapter

- Plugin `literature.semantic_scholar` provides `literature_source.semantic_scholar`
- Client `plugins/literature/semantic_scholar/client.py` uses `httpx` to `https://api.semanticscholar.org/graph/v1`
  - `search`: `GET /paper/search?query=&offset=&limit=&fields=paperId,corpusId,externalIds,title,abstract,year,venue,publicationTypes,publicationDate,authors,url,openAccessPdf`
  - `get`: `GET /paper/{paperId}?fields=...` where `paperId` may be `DOI:10.xxx`
  - Fields requested are minimal for `PaperRecord`; no citation graph/embeddings yet
  - Authentication: `x-api-key` header if `SEMANTIC_SCHOLAR_API_KEY` env var present; still works unauthenticated for open endpoints
  - Year may fall back to `publicationDate`; `openAccessPdf.url` → `PaperRecord.open_access_url`; `externalIds` → `doi` + `arxiv` + others
  - Pagination same opaque `offset` strategy
  - Same error/rate-limit/retry handling as Crossref, plus 401/403 → `LiteratureAuthenticationError`

## Provider Snapshots and Provenance

Raw provider JSON is **not** put into `PaperRecord.metadata`. It is stored separately as:

```python
ProviderRecordSnapshot
  provider, provider_record_id, retrieved_at, request_kind, request_metadata, raw_payload, metadata
```

Ingested via `literature.ingestion` (`plugins/literature/ingestion/plugin.py:literature.ingestion` → `literature_ingestor.default`):

```
LiteratureSource.search() → hits → Ingestor
                                      ├─► ProviderRecordSnapshot artifact
                                      ├─► PaperRecord artifact
                                      └─► provenance: Paper generated_from Snapshot
                                      └─► LiteratureSearchRecord artifact
```

`LiteratureSearchRecord` captures reproducibility: provider, query, filters, executed_at, requested_limit, returned_count, total_estimate, `paper_artifact_ids`, `provider_snapshot_artifact_ids`, pagination metadata. No credentials.

Provenance uses Phase 2A `ProvenanceRelation.generated_from` with `source=snapshot, target=paper`.

Result for 3 hits:

```
SearchRecord S1 ──► [P1, P2, P3] + [X1, X2, X3]
X1 ──generated_from──► P1
X2 ──generated_from──► P2
X3 ──generated_from──► P3
```

Later `PaperRecord → EvidenceItem → ResearchClaim` lineage can be walked backward to the snapshot:

```
Claim → Evidence → Paper → Snapshot
```

No automatic deduplication — two `PaperRecord`s with same DOI from different providers coexist; `content_hash` and `external_identifiers` are signals for Phase 2C.

## Rate Limiting and Retries

- Only transient errors retried: `429`, `5xx`, timeouts, connect failures
- Respect `Retry-After` header when present
- Bounded exponential backoff: `2^attempt + jitter`, `MAX_RETRIES=3`
- `sleep` is injectable for tests (`sleep=lambda` is async no-op in unit tests)
- Never retry `400, 401, 403, 404`, schema validation, or malformed JSON — failed requests are not converted to empty results

## Credentials

- `CROSSREF_MAILTO` (env or `literature.crossref.mailto` in YAML) → polite pool `mailto` and `User-Agent`
- `SEMANTIC_SCHOLAR_API_KEY` (env) → `x-api-key` header, optional
- Never stored in `ArtifactEnvelope`, `ProviderRecordSnapshot`, `LiteratureSearchRecord`, `session` events, or CLI output (`tests/unit/test_ingestion.py` asserts no `API_KEY` in artifacts)

## Configuration

```yaml
plugins:
  - storage.artifacts_sqlite
  - literature.crossref
  - literature.semantic_scholar
  - literature.ingestion

literature:
  crossref:
    enabled: true
    timeout_seconds: 20
  semantic_scholar:
    enabled: true
    timeout_seconds: 20
```

System works with `crossref` only, `semantic_scholar` only, or both — no code change, just `plugins` list.

## Live Testing

Normal `uv run pytest` is offline (respx mocked). Opt-in:

```bash
uv run --env-file .env pytest -m live_literature -v
# or both live suites
uv run --env-file .env pytest -m "live or live_literature" -v
```

Tests in `tests/live/test_literature_live.py` perform one small lookup/search per provider (known DOI `10.1038/nature12373`, query `information systems`, limit 2) and assert structural success (hit exists, title, DOI) without relying on ordering or citation counts. Crossref respects `CROSSREF_MAILTO`; Semantic Scholar reports authenticated vs unauthenticated clearly and skips if key unavailable.

No OpenRouter/LLM calls are made in Phase 2B; all normalization is deterministic.
