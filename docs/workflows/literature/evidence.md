# Evidence Extraction

evidence extraction transforms the frozen document acquisition `FullTextCorpus` into structured, page-grounded evidence:

```text
FullTextCorpus
→ FullTextDocument
→ page-bounded reading (chunks)
→ structured evidence extraction (model-assisted, strict schema)
→ EvidenceItems (page-grounded)
→ PaperResearchProfile (references EvidenceItems)
→ EvidenceCorpus (input to literature synthesis)
```

> Every extracted research fact or finding is traceable to exact source page(s) in the `FullTextDocument`.

evidence extraction performs **no** cross-paper synthesis, gap detection, theory
generation, embeddings, or OCR. Synthesis and gap detection are later
implemented phases (`docs/workflows/literature/synthesis.md`, `docs/workflows/literature/gaps.md`); embeddings and OCR
remain out of scope.

## Concepts

```
EvidenceExtractionChunk = page-bounded unit of text sent to the model
EvidenceCandidate       = strict structured model output (validated before persistence)
EvidenceItem            = durable page-grounded evidence artifact
PaperResearchProfile    = paper-level profile referencing EvidenceItems
EvidenceExtractionExecution = operational record of a corpus run
EvidenceCorpus          = durable output artifact (input to literature synthesis)
```

## Chunking strategy

Deterministic page-bounded chunks preserve page boundaries (`src/research_harness/plugins/literature/evidence_extractor/plugin.py:chunk_pages`):

```text
pages 1–4 → chunk 0 (start_page 1, end_page 4)
pages 5–8 → chunk 1 (start_page 5, end_page 8)
pages 9–12 → chunk 2
```

- `pages_per_chunk` default `4`, configurable (`literature.evidence.pages_per_chunk`).
- Chunk metadata retains `document_id`, `chunk_index`, `start_page`, `end_page`, `page_texts`.
- No whole-paper uncontrolled requests; `max_chunks_per_document` bounds chunks per paper.

## Evidence categories

Typed, extensible taxonomy (`src/research_harness/research/schemas/evidence.py:EvidenceCategory`):

```
research_question theory construct mechanism assumption
method data variable finding result boundary_condition
limitation future_research
```

## Structured output only

The model receives a strict JSON schema (enum categories, required fields, no extra properties) and must return:

```json
{"items": [{"category": "finding", "statement": "...", "page_numbers": [2, 3], "confidence": 0.9, "excerpt": "..."}]}
```

Rejected:

- missing `page_numbers`
- pages outside the supplied chunk
- invalid categories
- empty statements
- malformed / non-JSON responses

No chain-of-thought is requested.

## Grounding validation

Before persistence (`EvidenceExtractorService.extract_chunk`):

1. cited pages exist in the chunk (`contains_page`)
2. those pages were actually supplied to the model (chunk bounds)
3. `document_id` is bound per chunk and each candidate is persisted against the chunk's `FullTextDocument`
4. impossible locators rejected

A short `excerpt` is optional validation support; the durable representation is the statement, not the excerpt.

## Extractor plugin

`literature.evidence_extractor` provides `evidence_extractor.default`:

- depends on `model_router.default`, `artifact_store.default`, `blob_store.default`
- default logical role `reasoning`, configurable via `literature.evidence.model_role`
- no OpenRouter/model slug hard-coded

## Orchestrator plugin

`literature.evidence_orchestrator` provides `evidence_orchestrator.default` (`src/research_harness/plugins/literature/evidence_orchestrator/plugin.py`):

- reads each `FullTextDocument` in `FullTextCorpus.available_document_ids`
- reads extracted page text from `BlobStore` (never re-parses the PDF)
- skips `insufficient_text` / `encrypted` / `extraction_failed` docs → recorded as `documents_without_evidence`; screening disposition untouched
- persists `EvidenceItem`s incrementally — a failed chunk never discards earlier evidence
- conservative dedup: normalized exact statement collapse within a paper (no embeddings)
- budgets enforced: `max_chunks_per_document`, `max_model_calls`
- idempotent rerun: existing `EvidenceExtractionExecution` for the same corpus is reused

## Profile construction

Deterministic aggregation of the paper's `EvidenceItem`s into `PaperResearchProfile` (`src/research_harness/research/schemas/research_profile.py`):

- one `ProfileClaim` per evidence item, grouped by category into sections (`theories`, `main_findings`, …)
- each claim references its `evidence_item_ids`; no unsupported statement without an evidence reference
- `inference=True` reserved for future model-organized claims (currently unused — all claims are grounded)
- profile provenance: `derived_from` each EvidenceItem + the FullTextDocument

## Schemas

- `EvidenceItem` extended (backward compatible): `category`, `Locator.pages` (`src/research_harness/research/schemas/evidence.py`)
- `PaperResearchProfile` (`research/schemas/research_profile.py`)
- `EvidenceExtractionExecution`, `EvidenceCorpus` (`research/schemas/evidence_extraction.py`)

All Pydantic v2, `extra="forbid"`, `schema_version`, immutable after persistence.

## Provenance

```text
EvidenceCorpus → EvidenceExtractionExecution → FullTextCorpus → (document acquisition chain)
PaperResearchProfile → EvidenceItem → FullTextDocument → DocumentAcquisition → PaperIdentity → ResearchQuestion
```

Evidence page locators (`locator.pages`) survive SQLite + BlobStore reopen (verified in tests).

## Budgets

```yaml
literature:
  evidence:
    model_role: reasoning
    pages_per_chunk: 4
    max_chunks_per_document: 50
    max_model_calls: 500
```

## CLI

```bash
uv run --env-file .env research-agent literature evidence run --corpus <full-text-corpus-id>
uv run research-agent literature evidence profiles list
uv run research-agent literature evidence profiles inspect <profile-id>
uv run research-agent literature evidence items list --profile <profile-id>
uv run research-agent literature evidence items inspect <evidence-id>
```

Thin wrappers over `evidence_orchestrator.default`, `artifact_store.default`.

## Tests

Offline (fake models, no network): `tests/unit/test_evidence_extraction.py`, `tests/integration/test_phase2f_evidence.py` cover page-bounded chunking, grounded/ungrounded candidates, malformed output, partial chunk failure, dedup, profile construction, insufficient-text skip, budgets, idempotent rerun, provenance reopen.

Live (opt-in, marker `live_evidence`): `tests/live/test_evidence_live.py` — requires `OPENROUTER_API_KEY` and `EVIDENCE_LIVE_CORPUS_ID` (a real `FullTextCorpus`); structural assertions only.