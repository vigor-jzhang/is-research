# Cross-Paper Evidence Synthesis

literature synthesis transforms the frozen evidence extraction `EvidenceCorpus` into a grounded, structured
representation of what the literature says:

```text
EvidenceCorpus
→ PaperResearchProfiles
→ small profile/evidence batches
→ candidate themes (structured model output)
→ cross-batch consolidation
→ LiteratureSynthesis
```

literature synthesis summarizes; it does **not** identify research gaps, generate new theory,
or build analytical models (gap analysis+).

> Every substantive synthesis statement must reference supporting `EvidenceItem` IDs.
> No unsupported literature claim may be persisted.

## Evidence-only synthesis

The synthesizer consumes only:

- `EvidenceCorpus`
- `PaperResearchProfile`
- `EvidenceItem`

Every persisted `SynthesisStatement` references `supporting_evidence_ids` (and for
contradictions, `conflicting_evidence_ids`) that must exist in the supplied corpus.

## Schemas (`src/research_harness/research/schemas/synthesis.py`)

- `SynthesisStatementType` — consensus, mixed, contradiction, pattern, boundary_condition,
  methodological_pattern, theoretical_pattern, limitation_pattern, future_research_pattern
- `SupportType` — single_paper | multi_paper (computed deterministically)
- `SynthesisStatement` — statement, type, supporting_evidence_ids, conflicting_evidence_ids,
  supporting_paper_identity_ids, conflicting_paper_identity_ids,
  `papers_supporting`, `evidence_items_supporting`, `papers_conflicting`,
  `evidence_items_conflicting` (all deterministic counts), support_type, confidence
- `SynthesisTheme` — title, dimension, statements, evidence_item_ids, paper_identity_ids
- `LiteratureSynthesis` — evidence_corpus_id, theme_ids, statement_ids, counts
- `SynthesisExecution` — profiles_processed, evidence_items_processed, batches_processed,
  batches_failed, themes_created, statements_created, statements_rejected, model_role, failures

All Pydantic v2, `extra="forbid"`, `schema_version`, immutable after persistence.

## Synthesis strategy (hierarchical, bounded)

1. Load `EvidenceCorpus` → profiles + evidence items; build `evidence_id → paper_identity_id` map.
2. Group profiles into batches (`literature.synthesis.batch_profiles`, default 3).
3. Per batch: one strict structured model call → candidate themes with statements.
   Evidence IDs in the prompt are authoritative; model may only cite those.
4. Cross-batch consolidation: themes with the same normalized title are merged;
   statements deduplicated by normalized text.
5. Deterministic support metrics computed per statement (never model-invented).
6. Persist `SynthesisStatement` → `SynthesisTheme` → `LiteratureSynthesis`.

Bounded by `batch_profiles`, `max_batches`, `max_model_calls` (defaults 3/20/100).

## Grounding validation

Before persisting a statement:

- every `supporting_evidence_ids` / `conflicting_evidence_ids` entry must exist in the corpus (`ev_by_id`)
- every evidence ID must map to a paper (`paper_by_evidence`)
- statement non-empty; type valid; `contradiction` requires `conflicting_evidence_ids`
- hallucinated evidence IDs → statement rejected (recorded in `statements_rejected` + `failures`)

No chain-of-thought requested.

## Consensus / contradiction handling

- `consensus` = supporting evidence from ≥2 distinct papers → `support_type=multi_paper`
- `contradiction` preserves both sides: supporting + conflicting evidence/paper IDs both stored
- `mixed` for partial agreement; disagreement is never forced into consensus
- single-paper evidence is never presented as literature-wide consensus: `support_type`
  is computed deterministically from the count of distinct supporting papers

## Deterministic support metrics

Computed by the orchestrator from actual evidence references (no LLM):

```text
papers_supporting         = distinct paper ids of supporting evidence
evidence_items_supporting = len(supporting_evidence_ids)
papers_conflicting        = distinct paper ids of conflicting evidence
evidence_items_conflicting = len(conflicting_evidence_ids)
```

## Synthesis dimensions

Themes may target: theories, constructs, mechanisms, assumptions, methods, data/context,
variables, findings/results, boundary conditions, limitations, future research.
Not every corpus must contain every dimension.

## Provenance

```text
LiteratureSynthesis
→ SynthesisTheme
→ SynthesisStatement
→ EvidenceItem
→ FullTextDocument
→ PaperIdentity
```

Every synthesis statement is auditable back to pages through its EvidenceItems
(`derived_from` edges persisted; page locators survive SQLite + BlobStore reopen).

## Idempotency

An existing `SynthesisExecution` for the same corpus is reused only when `model_role`,
`batch_profiles`, and `max_batches` match. A material model/config change creates a new
execution; unchanged reruns make zero additional model calls.

## CLI

```bash
uv run --env-file .env research-agent literature synthesis run --corpus <evidence-corpus-id>
uv run research-agent literature synthesis inspect <synthesis-id>
uv run research-agent literature synthesis themes list --synthesis <synthesis-id>
uv run research-agent literature synthesis themes inspect <theme-id>
```

## Configuration

```yaml
literature:
  synthesis:
    model_role: reasoning
    batch_profiles: 3
    max_evidence_per_profile: 12
    max_batches: 20
    max_model_calls: 100
```

## Tests

Offline (fake models): `tests/unit/test_synthesis.py` (evidence-ID validation,
hallucinated rejection, multi-paper consensus, contradiction, single-vs-multi distinction,
partial batch failure, cross-batch consolidation, deterministic counts, idempotency +
model change, provenance reopen) and `tests/integration/test_phase2g_synthesis.py`
(4 profiles → 3 themes, consensus + contradiction, 5 statements, reopen provenance).

Live (opt-in, `live_synthesis`): `tests/live/test_synthesis_live.py` — structural only.