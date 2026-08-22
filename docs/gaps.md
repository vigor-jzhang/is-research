# Research Gap Analysis — Phase 2H

Phase 2H transforms the frozen Phase 2G `LiteratureSynthesis` into evidence-grounded
research gap candidates:

```text
ResearchQuestion
+ EvidenceCorpus
+ LiteratureSynthesis
        ↓
evidence-grounded gap analysis
        ↓
ResearchGap candidates (GapAnalysis)
```

Phase 2H identifies candidate research opportunities. It does **not** generate
theories, mechanisms, or analytical models (later phases).

## Gap taxonomy (`src/research_harness/research/schemas/gap.py:GapType`)

```
theoretical_gap mechanism_gap empirical_gap context_gap
boundary_condition_gap contradiction_gap methodological_gap
integration_gap
```

Extensible `str, Enum`.

## Epistemic rule

A gap is an **inference from the reviewed literature**, not a proven fact that no
paper exists anywhere. The analyzer enforces corpus-bounded language:

- sweeping claims ("No research has studied X", "no studies exist", "nothing is known")
  are detected and automatically rewritten to bounded forms
  ("within the reviewed corpus...", "the reviewed literature provides limited evidence on...",
  "few included studies examine...")
- `EvidenceCorpus.documents_without_evidence` is exposed as a **coverage limitation**,
  never as evidence of a gap

## Schemas

- `ResearchGap` — title, gap_type, description, why_it_matters, supporting
  synthesis-statement IDs, supporting evidence IDs, contradiction-statement IDs,
  relevant paper IDs, deterministic counts (`supporting_papers`, `supporting_evidence_items`,
  `contradicting_papers`), strength (`strongly_supported` | `tentative`), confidence,
  scope, limitations, status (`candidate`), ranking dimensions, analytical-model
  opportunity, created_at
- `GapRankDimension` — raw 0..1 scores kept separate: evidence_strength,
  research_importance, theoretical_relevance, analytical_model_potential, tractability;
  `composite` is the simple mean (transparent, not a black-box single score)
- `AnalyticalModelOpportunity` — `suitable`, `domains` (strategic interaction,
  information asymmetry, platform behavior, pricing, technology adoption, incentives,
  competition, mechanism design), `rationale` — opportunity assessment only, no model
- `GapAnalysis` — literature_synthesis_id, evidence_corpus_id, research_question_id,
  gap_ids, ranked_gap_ids, coverage_limitations, summary (references the gaps, no
  unsupported new claims)
- `GapAnalysisExecution` — statements_processed, themes_processed, gaps_created,
  gaps_rejected, model_role, failures

## Grounding rules

Before persisting a gap:

- every `supporting_synthesis_statement_ids` / `contradiction_statement_ids` must exist
  in the persisted `synthesis_statement` artifacts of the analyzed synthesis
- every `supporting_evidence_ids` must resolve to a paper (via corpus profiles or
  FullTextDocument mapping)
- title/description non-empty; gap_type valid
- hallucinated IDs → gap rejected (recorded in `gaps_rejected` + `failures`); valid
  gaps in the same response are still persisted

## Gap signals analyzed

contradictory findings → `contradiction_gap`; weakly supported themes / repeated
limitations → supported gaps; missing mechanisms → `mechanism_gap`; unexplored
boundary conditions; limited contexts → `context_gap`; future-research
recommendations; methodological concentration; theoretical fragmentation.

## Deterministic support counts

Computed by the orchestrator (model never invents counts):

```text
supporting_papers         = distinct papers across supporting statements + evidence
supporting_evidence_items = distinct supporting evidence IDs
contradicting_papers      = papers on the conflicting side of cited contradiction statements
```

Strength is deterministic: `strongly_supported` if ≥2 papers or ≥3 evidence items,
else `tentative`.

## Ranking

Deterministic: composite = mean of the five raw dimension scores; `ranked_gap_ids`
sorted descending with stable tie-breaking. Raw scores remain on the gap artifact.

## Provenance

```text
GapAnalysis → ResearchGap → SynthesisStatement → EvidenceItem → FullTextDocument → PaperIdentity
```

Gaps are `derived_from` their supporting statements and evidence; the analysis is
`derived_from` gaps, execution, and synthesis. Verified after SQLite reopen.

## Idempotency

An existing `GapAnalysisExecution` is reused only when synthesis + corpus + model role
match **and** the run completed successfully with `gaps_created > 0`. Failed or empty
runs are never reused; a material model change creates a new execution.

## CLI

```bash
uv run --env-file .env research-agent literature gaps run --synthesis <id> --corpus <evidence-corpus-id> [--question <rq-id>]
uv run research-agent literature gaps list --analysis <id>
uv run research-agent literature gaps inspect <gap-id>
uv run research-agent literature gaps analysis inspect <analysis-id>
```

## Configuration

```yaml
literature:
  gap:
    model_role: reasoning
    max_statements: 200
    max_gaps: 50
    max_model_calls: 20
```

## Tests

Offline (fake models): `tests/unit/test_gap_analyzer.py` (contradiction→contradiction_gap,
repeated limitation→supported gap, hallucinated IDs rejected, sweeping-claim
normalization, deterministic counts, ranking, partial model failure, idempotency +
model change, provenance reopen) and `tests/integration/test_phase2h_gaps.py`
(4 papers → contradiction_gap + mechanism_gap, ranked, coverage limitation, reopen).

Live (opt-in, `live_gap_analysis`): `tests/live/test_gap_analysis_live.py` —
structural grounding only.