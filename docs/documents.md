# Documents — Phase 2E Full-Text Acquisition and Extraction

Phase 2E answers: for every **included** scholarly work (`ScreenedLiteratureSet.included_identity_ids`), can we deterministically find, acquire, preserve, and extract page-level text while retaining complete provenance — without interpreting contents?

> **Academic relevance and document accessibility are independent.** Failure to obtain full text never turns an included paper into an excluded paper. A paper can be `academically included` + `full text unavailable` and remains so.

> **Phase 2E performs no research interpretation.** No `EvidenceItem`, `ResearchClaim`, section inference, reference parsing, or LLM calls. Output is **documents**, not evidence.

## Concepts

```
PaperIdentity          = scholarly-work identity (resolved from PaperRecords)
DocumentLocation       = a possible legitimate location of a document (candidate)
DocumentAcquisition    = an attempt/result of obtaining bytes (downloaded/imported/failed)
Blob                  = immutable stored binary/text content (content-addressed by sha256)
FullTextDocument       = extracted structured text representation (page-level)
DocumentAcquisitionExecution = operational record for processing a screened set
FullTextCorpus         = durable output artifact for Phase 2F (available/unavailable)
```

Do not collapse into a giant document object.

## BlobStore vs ArtifactStore

```
ArtifactStore (.research/artifacts.db SQLite)
  = structured research metadata (JSON, small, queryable)
  - ArtifactEnvelope[T] with content_hash, provenance

BlobStore (.research/blobs filesystem)
  = large immutable byte/text payloads (PDFs, extracted JSON)
  - content-addressed by sha256
  - BlobReference{algorithm,digest,size_bytes,media_type,storage_key}
  - not stored inside SQLite JSON
```

On-disk layout (example):

```
.research/blobs/sha256/ab/cd/abcd...ef  # 32-byte sha256 first 2+2 chars as dirs
```

Properties:

- `same bytes → same blob` (deduplicated by `sha256`)
- atomic write via temp file + `rename`
- SHA-256 verification on write and read
- safe filenames (hex only, no `..`, no absolute paths, escaped via `storage_key`)
- no absolute machine-specific paths in artifacts (logical `storage_key` only)
- reopen persistence (filesystem)

Contract `src/research_harness/contracts/blob.py:BlobStore` provides `put_bytes/get_bytes/exists/stat/delete`.

Plugin `storage.blobs_filesystem` provides `blob_store.default`, config `documents.blob_root: ".research/blobs"` `src/research_harness/config/schema.py:DocumentsConfig`.

Artifacts reference blobs via `BlobReference`, never via absolute path.

## DocumentLocation

`src/research_harness/research/schemas/document_location.py:DocumentLocation`

```
paper_identity_id
resolver: documents.locator.metadata | documents.locator.unpaywall
url: http/https direct candidate
landing_page_url: optional
media_type: application/pdf if direct
access_type: open_access | user_provided | unknown | restricted
host_type: publisher | repository | unknown
version: publishedVersion | acceptedVersion | submittedVersion | unknown
license: optional (Unpaywall)
is_direct_download: bool
discovered_at
provider_snapshot_id: for Unpaywall trace
metadata: {source_paper_artifact_id, unpaywall_host_type, ...}
```

A location is only a candidate; it does not imply success.

## DocumentLocator Contract

`src/research_harness/contracts/document.py:DocumentLocator`

```python
async resolve(paper_identity_id) -> list[str]  # DocumentLocation artifact_ids
```

Provider-neutral, orchestrator does not import Unpaywall/Semantic Scholar clients directly.

### Existing-Metadata Locator

Plugin `documents.locator.metadata` (`src/research_harness/plugins/documents/locator_metadata/plugin.py:10`)

Deterministically examines `PaperIdentity → member PaperRecords`:

- `PaperRecord.open_access_url`
- `PaperRecord.metadata.open_access_pdf_url` (Semantic Scholar Academic Graph `openAccessPdf` mapped in Phase 2B)
- `PaperRecord.url` if it ends with `.pdf` (conservative)

No network call, no new API. Duplicate URLs suppressed, deterministic ordering by `url` string, idempotent (reuses existing `DocumentLocation` if same `paper_identity_id+url+resolver`).

Does not re-query Semantic Scholar for already-persisted data.

### Unpaywall Locator

Plugin `documents.locator.unpaywall` (`src/research_harness/plugins/documents/locator_unpaywall/plugin.py:10`)

- `GET https://api.unpaywall.org/v2/{doi}?email=...` (DOI from `PaperIdentity.canonical_identifiers` or member `PaperRecord.doi`)
- Requires `UNPAYWALL_EMAIL` env (added to `.env.example`), never persisted in artifacts/session
- Handles `200` JSON with `best_oa_location` + `oa_locations[]`, maps `url_for_pdf`, `url_for_landing_page`, `host_type`, `version`, `license`; 404→`[]` with snapshot still preserved; 429/5xx/timeout/malformed JSON → `[]` no location
- Preserves raw via `ProviderRecordSnapshot(provider="unpaywall", request_kind="oa_lookup", provider_record_id=normalized DOI)` (`src/research_harness/research/schemas/provider_snapshot.py`), then `DocumentLocation derived_from Snapshot` and `derived_from PaperIdentity`
- Idempotent: existing snapshot for DOI reused, existing locations for same `url` deduped

### Location Resolution Priority (deterministic, no LLM)

Orchestrator `src/research_harness/plugins/documents/acquisition_orchestrator/plugin.py:20` merges:

```
1. existing direct OA PDF from stored metadata (metadata locator, is_direct_download true)
2. Unpaywall best OA direct PDF (best_oa_location url_for_pdf)
3. other Unpaywall direct OA PDFs (oa_locations url_for_pdf)
4. OA landing-page-only location (url_for_landing_page)
5. no automatic location → not_available
```

Exact ordering documented and tested; duplicate URLs across resolvers are suppressed (metadata wins). Config `documents.location.use_metadata/use_unpaywall` (default true) can disable, `documents.acquisition.max_locations_per_paper:5` bounds candidates.

Crossref `link` not automatically used for download (license varying, per Crossref guidance). Prefer `Semantic Scholar public PDF`, `Unpaywall OA`, `user-provided`.

## No Paywall Bypass

Strictly not implemented: no institutional-login automation, cookie theft, proxy bypass, Sci-Hub, CAPTCHA bypass, publisher auth scraping, browser automation to evade controls. If no legitimate OA and no user import, record `unavailable`/`access_restricted` and continue. Researcher may later `import --identity <id> --file paper.pdf`.

## HTTP Document Fetcher

Plugin `documents.fetcher.http` (`src/research_harness/plugins/documents/fetcher_http/plugin.py:10`) provides `document_fetcher.default`, generic:

- `HTTP GET` with streaming, bounded redirects (`max_redirects:5`), SSRF revalidation on each redirect
- Allow only `http/https`, reject `file://, ftp://, localhost, 127.0.0.0/8, ::1, 10/8, 172.16/12, 192.168/16, link-local, private IPv6, 169.254.169.254` via `ipaddress` + hostname string checks
- Bounded redirects revalidated
- **DNS limitation (documented):** current verification rejects only literal private-IP URLs and private hostnames (`localhost`, `10.*`, etc.). Public hostname that DNS-resolves to a private address is not resolved and could bypass the check. In practice OA publishers/Unpaywall are public; a future hardening would resolve `hostname → IP` and re-validate resolved addresses.
- Size limits: `max_bytes:52428800` (50 MiB) via `Content-Length` early fail and streaming enforcement; does not load arbitrary size into memory
- Timeout `30s`, respects `Retry-After` not needed (fetcher is not retrying 429 beyond one)
- Content validation: PDF signature `%PDF-` (first bytes after BOM/whitespace), not just `Content-Type`; HTML login page (`<!doctype html`, `<html`) → `invalid_content` not PDF
- SHA-256 hashing, `BlobStore.put_bytes` deduplication, `DocumentAcquisition` with `status:downloaded/not_available/access_restricted/invalid_content/too_large/failed`, `http_status`, `final_url`, `sha256`, `size_bytes`, provenance `derived_from DocumentLocation` + `PaperIdentity`
- Idempotent: same `document_location_id` with same `sha256` and `downloaded` reuses existing acquisition without re-storing blob; emits `document.acquisition.started/completed/failed` (concise, no blobs in payload)

Configuration `src/research_harness/config/schema.py:DocumentsDownloadConfig` (`timeout_seconds`, `max_redirects`, `max_bytes`).

## Local PDF Import

CLI `research-agent literature documents import --identity <paper-identity-id> --file paper.pdf` (`src/research_harness/cli/main.py:documents_import`)

- Validates file exists, `size ≤ max_bytes`, PDF signature
- SHA-256, `BlobStore.put_bytes`, `DocumentAcquisition` `status:imported` `source_type:user_provided`, `blob` reference, retains original bytes unchanged, `imported_file` in metadata
- Same bytes twice → same blob, second import reuses acquisition id
- Provenance `derived_from PaperIdentity`
- Optionally runs extractor immediately if available
- Does not infer licensing beyond `user_provided`

## DocumentAcquisition

`src/research_harness/research/schemas/document_acquisition.py:DocumentAcquisition`

```
paper_identity_id, document_location_id?
status: downloaded|imported|not_available|access_restricted|invalid_content|too_large|failed
attempted_at, completed_at, blob:BlobReference?, sha256?, size_bytes?, media_type?, http_status?, final_url?, source_type, failure_code?, failure_message?
```

Avoid ambiguous booleans.

## DocumentExtractor Contract

`src/research_harness/contracts/document.py:DocumentExtractor`

```python
async extract(acquisition_id) -> full_text_document_id
```

Consumes stored bytes via `BlobStore`, no HTTP.

## PDF Extractor `pypdf`

Plugin `documents.extractor.pypdf` (`src/research_harness/plugins/documents/extractor_pypdf/plugin.py:10`) using `pypdf`:

- Page-level extraction via `PdfReader`, 1-based page numbers (human-facing, documented), preserves boundaries (`page 1 text`, `page 2 text`, ...)
- No OCR: `Tesseract/OCRmyPDF/vision` not in Phase 2E; scanned/image-only PDFs yield little/no text → `text_status: insufficient_text` not `failed`
- Encrypted PDFs → `text_status: encrypted` (no password cracking)
- Quality metrics deterministic: `page_count`, `pages_with_text`, `pages_without_text`, `character_count`, `average_characters_per_page`, `empty_page_ratio`
- Threshold `character_count <200 or empty_page_ratio >0.5` → `insufficient_text` (documented, no LLM)
- Stores extracted pages as deterministic JSON blob:

```json
{"schema_version":1,"pages":[{"page":1,"text":"..."},{"page":2,"text":"..."}]}
```

Canonical `sort_keys=True`, `separators=(",",":")`, hashed via `sha256` of exact serialized bytes, stored as `text_blob` (media `application/json`), original PDF blob unchanged (`source_blob` separate)

- `FullTextDocument` `src/research_harness/research/schemas/full_text.py:FullTextDocument` fields: `paper_identity_id`, `document_acquisition_id`, `source_blob`, `text_blob?`, `extractor`, `extractor_version`, `page_count`, `pages_with_text`, `character_count`, `text_status:extracted/insufficient_text/encrypted/extraction_failed`, `language?`, `quality_metrics`, `metadata`

Extraction quality metrics do not claim accuracy.

## DocumentAcquisitionExecution & FullTextCorpus

`DocumentAcquisitionExecution` `src/research_harness/research/schemas/full_text.py:DocumentAcquisitionExecution`

```
screened_literature_set_id, paper_identity_ids, location_artifact_ids, acquisition_artifact_ids, full_text_document_ids,
started_at, completed_at, counts{total_included,locations_found,downloaded,imported,no_location,access_restricted,invalid_content,too_large,failed,text_extracted,insufficient_text,encrypted},
failures:[{paper_identity_id, error}], budget_stop_reason
```

`FullTextCorpus` `src/research_harness/research/schemas/full_text.py:FullTextCorpus` durable output for Phase 2F:

```
document_acquisition_execution_id, screened_literature_set_id,
available_document_ids: list[FullTextDocument ids with extracted text],
unavailable_identity_ids, restricted_identity_ids, failed_identity_ids
```

Only `included_identity_ids` processed by default; `excluded`/`uncertain` not fetched (future option for uncertain). Failure to obtain text does not mutate screening (`include` stays `include`).

Idempotency: same `ScreenedLiteratureSet` with same blob/extractor version reuses existing `FullTextDocument` (checked via `document_acquisition_id+extractor+version+source_blob.digest`), new extractor version creates new doc preserving old.

## Provenance

```
ScreenedLiteratureSet --derived_from--> DocumentAcquisitionExecution --derived_from--> FullTextCorpus
PaperIdentity --derived_from--> DocumentLocation --derived_from--> DocumentAcquisition --derived_from--> FullTextDocument
PaperIdentity --derived_from--> ProviderRecordSnapshot(unpaywall) --derived_from--> DocumentLocation (Unpaywall)
ScreenedLiteratureSet → PaperIdentity (via included_identity_ids typed reference, also provenance Execution derived_from Set)
FullTextDocument → DocumentAcquisition → DocumentLocation → PaperIdentity → ScreenedLiteratureSet → ScreeningExecution → ScreeningProtocol → PaperScreeningView → PaperIdentity → PaperRecord → ProviderRecordSnapshot → LiteratureSearchRecord → LiteratureQuery → ResearchQuestion
```

Original literature provenance `PaperIdentity → PaperRecord → ProviderRecordSnapshot(crossref/semantic_scholar) → LiteratureSearchRecord → LiteratureQuery → ResearchQuestion` remains; Phase 2F can trace quotation via `FullTextDocument.text_blob` pages and `DocumentAcquisition.source_blob` original bytes.

## Events (concise, no blobs)

```
document.location.completed   {paper_identity_id, location_count}
document.acquisition.started  {document_location_id, paper_identity_id, url}
document.acquisition.completed {acquisition_id, paper_identity_id, status, sha256}
document.acquisition.failed   {paper_identity_id, failure_code}
document.extraction.started   {acquisition_id, paper_identity_id}
document.extraction.completed {document_id, acquisition_id, text_status, page_count}
document.extraction.failed    {acquisition_id, reason}
document.corpus.completed     {corpus_id, execution_id, available, counts}
document.location.completed (orchestrator) etc.
```

Via `EventBus`, session persistence; payloads carry ids not PDFs/texts.

## CLI

```bash
research-agent literature documents locate --set <screened-literature-set-id> [--config ...]
research-agent literature documents acquire --set <screened-literature-set-id> [--config ...]
research-agent literature documents run --set <screened-literature-set-id> [--config ...]  # alias for acquire
research-agent literature documents list [--set <id>] [--execution <id>] [--config ...]
research-agent literature documents inspect <full-text-document-id> [--config ...]
research-agent literature documents import --identity <paper-identity-id> --file <pdf-path> [--config ...]
research-agent literature documents text <full-text-document-id> [--page 5] [--limit 4000] [--config ...]
```

All thin: delegate to `document_acquisition_orchestrator.default`, `blob_store.default`, `artifact_store.default`.

## Configuration

```yaml
documents:
  blob_root: ".research/blobs"
  location:
    use_metadata: true
    use_unpaywall: true
  download:
    timeout_seconds: 30
    max_redirects: 5
    max_bytes: 52428800
  acquisition:
    max_locations_per_paper: 5
  extraction:
    extractor: pypdf

# env
UNPAYWALL_EMAIL=
# openrouter not required for 2E
```

No OpenRouter config required for Phase 2E.

## No LLM/Interpretation

Phase 2E makes **no** `model_router.default` calls, no `EvidenceItem`/`ResearchClaim`, no section inference, no reference parsing, no OCR. Page-level text is sufficient.

## For Developers

See `src/research_harness/contracts/blob.py`, `contracts/document.py`, `research/schemas/document_*.py`, `plugins/storage/blobs_filesystem`, `plugins/documents/*`, `tests/unit/test_blob_store.py` etc.

