# Research Domain

## Research Artifacts

Research outputs are typed **artifacts** — immutable, content-hashed, provenance-linked objects.

- **Domain payload** — the research content (e.g., `PaperRecord`, `EvidenceItem`)
- **Artifact envelope** — infrastructure metadata around the payload

```python
ArtifactEnvelope[T]
  artifact_id      # UUID, stable internal identity
  artifact_type    # e.g., "paper_record", "evidence_item"
  schema_version   # envelope version (currently 1)
  created_at       # UTC
  session_id, run_id
  producer         # "human", "plugin:storage.artifacts_sqlite", etc.
  payload: T       # domain model (Pydantic)
  content_hash     # SHA-256 of canonical payload JSON
  metadata         # non-domain, no secrets
  provenance       # optional inline hints; authoritative is store
```

`ArtifactEnvelope.create(payload, artifact_type, producer, ...)` computes the hash and assigns id/timestamp. The envelope is frozen (`frozen=True`) — revisions create new artifacts.

Payload types live in `src/research_harness/research/schemas/`:

- `ResearchQuestion` — question, motivation, scope, constraints, status
- `ResearchPlan` — objective, research_question_id, steps, search_concepts
- `SourceRecord` — generic source (title, url, external_identifiers, publisher)
- `PaperRecord` — canonical bibliographic record (title, authors: list[Author], year, venue, abstract, doi, external_identifiers, url, open_access_url, publication_type)
- `EvidenceItem` — statement + `source_artifact_id` (must exist), locator (page/pages), extraction_method, confidence, `category` (Phase 2F taxonomy)
- `ResearchClaim` — statement, claim_type (`fact`/`inference`/`hypothesis`/`assumption`/`recommendation`), evidence_refs, confidence
- `ScreeningProtocol/PaperScreeningView/ScreeningDecision/ScreeningReview` — Phase 2D screening (title/abstract, `include/exclude/uncertain`)
- `DocumentLocation/DocumentAcquisition/FullTextDocument` — Phase 2E documents (see `docs/documents.md`), `ScreeningExecution/ScreenedLiteratureSet`, `DocumentAcquisitionExecution/FullTextCorpus`
- `PaperResearchProfile/EvidenceExtractionExecution/EvidenceCorpus` — Phase 2F evidence extraction (see `docs/evidence.md`)
- `SynthesisStatement/SynthesisTheme/LiteratureSynthesis/SynthesisExecution` — Phase 2G cross-paper synthesis (see `docs/synthesis.md`)
- `ResearchGap/GapAnalysis/GapAnalysisExecution` — Phase 2H gap analysis (see `docs/gaps.md`)
- `GapSelection/MechanismCandidate/MechanismCritique/MechanismAnalysis/SelectedMechanism` — Phase 3A mechanism development (see `docs/mechanisms.md`)
- `FormalAnalyticalModel/ModelActor/ModelVariable/ModelParameter/ModelTimingStage/InformationStructure/ModelAssumption/PayoffFunction/ModelSpecificationCritique` — Phase 3B formal analytical model (see `docs/models.md`)
- `OptimizationProblem/FirstOrderCondition/BestResponse/EquilibriumCandidate/EquilibriumVerification/EquilibriumAnalysis/EquilibriumExecution` — Phase 3C equilibrium derivation (see `docs/equilibrium.md`)
- `ComparativeStatic/Proposition/PropositionVerification/PropositionCritique/EconomicInterpretation` — Phase 3D propositions (see `docs/propositions.md`)
- `ParameterSweep/NumericalResult/RobustnessCheck/WelfareAnalysis/NumericalExperiment/NumericalExperimentExecution` — Phase 3E numerical experiments (see `docs/numerical.md`)

Later phases add `ResearchGap`, `Mechanism`, etc., without kernel changes.

All schemas are Pydantic v2, round-trip via JSON, and use explicit `schema_version`.

## Session vs Artifact Persistence

Two stores, two responsibilities:

```
Session Store  (= trajectory)
  .research/sessions/<id>/events.jsonl
  What did the agent do?  Chronological events, append-only.

Artifact Store (= research state)
  .research/artifacts.db (SQLite)
  What research objects exist? Durable, immutable artifacts + provenance.
```

- **EventBus = transport** — in-memory pub/sub; `history()` is for tests, not replay.
- **Session subscriber** — every `Event` with a `session_id` is appended to `events.jsonl` by `session.jsonl` plugin.
- **ArtifactStore = authoritative** for artifact content; `artifact.created` events are observable but not authoritative.
- **Session trajectory = authoritative** for sequence; store is for lineage.

Do not replay from `EventBus` history — read `SessionStore` and `ArtifactStore`.

```
producer → EventBus → subscribers
                    └→ session.jsonl → events.jsonl (scrubbed)
producer → ArtifactStore.put() → artifacts table → artifact.created event
```

## Provenance

Small explicit lineage, not a full knowledge graph.

```python
ProvenanceLink
  relation: derived_from | extracted_from | generated_from | supersedes
  source_artifact_id  # upstream
  target_artifact_id  # downstream (derived)
  created_at, producer, metadata
```

Semantics: `A derived_from B` means `A` is newer, `B` is source.

- `extracted_from` — EvidenceItem from PaperRecord/SourceRecord
- `derived_from` — Claim from Evidence, synthesis from papers
- `generated_from` — model-assisted generation
- `supersedes` — revision: new artifact supersedes previous (history preserved)

Constraints:

- No self-edges (validated in model and store)
- No cycles for lineage relations (store checks BFS from target)
- Both artifacts must exist

Not provenance:

- `paper A cites paper B`
- `claim A contradicts claim B`

are semantic relations, not lineage. Keep them out of this graph.

Diagram:

```
Source / PaperRecord
        │
        │ extracted_from
        ▼
   EvidenceItem
        │
        │ derived_from
        ▼
  ResearchClaim
```

Traversal:

```python
ancestors = await store.get_lineage(claim_id, direction="ancestors")  # [evidence, paper]
descendants = await store.get_lineage(paper_id, direction="descendants")
parents, children = await store.get_provenance(artifact_id)
```

## Immutability

Artifacts are immutable after `put`. Duplicates by `artifact_id` are rejected. Revisions are new artifacts with a `supersedes` edge:

```
q2 supersedes q1  (q1 → q2)
```

This preserves history for later revision analysis.

## External Identifiers

Internal identity is `artifact_id` (UUID). External ids are `ExternalIdentifier {scheme, value}`:

- `doi`, `semantic_scholar`, `crossref`, `arxiv`, `openalex`, `pmid`, `url`

Do not derive `artifact_id` from DOI/URL — metadata may change.

DOI normalization (`research/schemas/common.py:normalize_doi`) handles:

```
10.1234/abc
https://doi.org/10.1234/ABC
http://dx.doi.org/10.1234/abc
doi:10.1234/ABC
```

All normalize to lower-case bare `10.1234/abc`. No network calls. `PaperRecord.doi` field auto-normalizes; `Author` and `SourceRecord` use `ExternalIdentifier.normalized()`.

## Content Hashing

- **Input:** canonical payload JSON (`model_dump(mode="json")`, `sort_keys=True`, `separators=(",", ":")`)
- **Algorithm:** SHA-256 hex
- **Scope:** payload only — not `artifact_id`, `created_at`, `session_id`, `metadata`, or envelope wrappers

Two artifacts with same payload have same hash but distinct ids. Hash is for deduplication/detection, not identity.

## Producer Metadata

Every envelope records `producer` — e.g., `human`, `plugin:storage.artifacts_sqlite`, `tool:echo`. Model/provider details stay in session/model events, not duplicated in artifact payloads.

## Storage — ArtifactStore vs BlobStore

`contracts/artifact.py:ArtifactStore` is the contract (`artifact_store.default`). `plugins/storage/artifacts_sqlite` implements it with stdlib `sqlite3`:

Tables:

```sql
artifacts(artifact_id PK, artifact_type, schema_version, created_at, session_id, run_id, producer, content_hash, payload_json, metadata_json)
provenance(source_artifact_id, target_artifact_id, relation, created_at, producer, metadata_json)  PK(source, target, relation)
```

Indexes on `artifact_type`, `content_hash`, `session_id`, `source`, `target`. Payload stored as canonical JSON; SQLite provides identity/lookup/lineage, not full relational normalization.

Transactions: `put` + `add_provenance` are atomic per call (`BEGIN/COMMIT/ROLLBACK`). Combined multi-artifact creation should be handled by caller if needed.

Events: `artifact.created` and `provenance.created` are emitted via `EventBus` but store remains authoritative.

Large byte payloads (PDFs, extracted text) are **not** in SQLite JSON. `contracts/blob.py:BlobStore` (`blob_store.default`) is filesystem content-addressed by `sha256` (`BlobReference{algorithm,digest,size_bytes,media_type,storage_key}`) under `.research/blobs/sha256/ab/cd/...`, atomic temp-file + rename, deduplicated. Artifacts store `BlobReference` (logical `storage_key`, no absolute paths). See `docs/documents.md` for `DocumentLocation/DocumentAcquisition/FullTextDocument` lifecycle.

## CLI

```bash
uv run research-agent artifacts list [--type paper_record] [--session s1] [--limit 10]
uv run research-agent artifacts inspect <artifact_id>
uv run research-agent artifacts lineage <artifact_id> [--direction ancestors|descendants]
```

Never displays secrets; uses scrubbed store.

## Deterministic Demonstration

See `tests/integration/test_research_lineage.py:PaperRecord → EvidenceItem → ResearchClaim` — creates four artifacts and two `extracted_from`/`derived_from` edges, then walks ancestors/descendants with no network/OpenRouter.

Run: `uv run pytest` (offline) or `uv run --env-file .env pytest -m live` for optional live smoke (separate).

## Schema Versioning

`schema_version` is explicit in both envelope and payloads (currently 1). `artifacts` table stores it; `schema_version` table tracks DB version. Readers must handle future migrations; no migration framework yet beyond `CREATE TABLE IF NOT EXISTS` and version row.
