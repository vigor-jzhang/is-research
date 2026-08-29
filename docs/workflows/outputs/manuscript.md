# Manuscript Architecture & Drafting — Phase 4B

Phase 4B transforms a completed `ResearchResultsPackage` into a structured,
evidence-grounded manuscript draft:

```text
ResearchResultsPackage
+ LiteratureSynthesis / EvidenceCorpus (via the gap's supporting artifacts)
→ ManuscriptOutline (deterministic, 12 sections)
→ ManuscriptSection (claims + citations, one LLM call per section)
→ ManuscriptDraft V1
→ ManuscriptCritique (separate artifact)
→ ManuscriptDraft V2 (immutable, supersedes V1)
```

Journal-specific formatting, bibliography formatting, LaTeX/DOCX/PDF export,
cover letter, and submission packaging are outside Phase 4B — implemented in
Phase 4C (see `docs/workflows/outputs/publication.md`).

## Schemas (`src/research_harness/research/schemas/manuscript.py`)

- `ManuscriptOutline` — results_package_id, title, ordered `SectionSpec`s
  (12 recommended sections), each with `allowed_artifact_types` and the
  resolved `artifact_ids` relevant to that section.
- `ManuscriptSection` — section_id, title, body (with `[CITE:<citation_id>]`
  placeholders), structured `ManuscriptClaim`s (grounding_type,
  grounding_artifact_id, citation_id, preserved conditions), embedded
  `CitationReference`s (paper_identity_id, evidence_item_id, page_locator).
- `CitationReference` — internal citation object linking claims to
  `PaperIdentity` + `EvidenceItem` + page locator. No final citation
  formatting is generated.
- `ManuscriptDraft` — immutable version (V1, V2, ...) with section_ids,
  `supersedes` pointer, status.
- `ManuscriptDraftExecution` — operational record (sections created/reused,
  claims, citations, novelty normalization count).
- `ManuscriptCritique` (+ issues, 9 categories, verdict
  approve/revise/reject) — persisted separately.

## Outline structure

Deterministic (no LLM): Introduction, Literature Review, Research Gap,
Theory / Mechanism, Analytical Model, Equilibrium Analysis, Propositions,
Numerical Analysis, Discussion, Contributions, Limitations, Conclusion.
Each spec resolves the relevant artifact ids from the package chain: the
gap's supporting evidence items + synthesis statements feed the Literature
Review / Research Gap sections; verified propositions feed Equilibrium +
Propositions; the experiment's results feed Numerical Analysis; findings +
contributions feed Introduction / Discussion / Contributions / Conclusion.

## Section drafting strategy

One `reasoning`-role call PER section; each prompt contains ONLY that
section's artifacts (summarized with ids), the verified propositions
available to it, and the result ids. The model returns `{title, body,
claims[], citations[]}`; claims are required (minItems 1). Deterministic
validation happens before persistence; on rejection the exact error is
appended to the prompt and the section is re-issued (bounded retry, max 4,
with 1s backoff — needed because the free-tier reasoning model intermittently
returns empty/invalid output).

## Citation / grounding validation (before persisting a section)

- every `grounding_artifact_id` must exist and its type must be allowed for
  the section; unknown ids are rejected;
- claims must carry either a grounding artifact or a citation; neither →
  rejected;
- literature claims (evidence/synthesis grounding) MUST include a
  `citation_id` (uncited factual literature claims are rejected);
- `[CITE:...]` placeholders in the body must reference declared citation ids
  (artifact ids are never valid placeholders);
- citations must reference existing evidence items + paper identities;
- global-novelty phrasing in body and claims is stripped (normalized) during
  drafting.

## Condition preservation

Every claim grounded in a verified proposition must include ALL of the
proposition's conditions. Matching is substring-based (deterministic): the
original condition text must appear within one of the claim's conditions
(e.g. "a > c" inside "a > c (interior solution)") — the live model
paraphrases, and exact-equality was too strict. Failed propositions can
never ground a claim ("not verified/conditionally verified" → rejected).

## Critic / revision flow

`research.manuscript_critic` (critic role) merges deterministic pre-checks —
missing limitations section, unknown placeholders, unused citations,
ungrounded claims, novelty residue, gap/contribution mismatch — with the
LLM's qualitative critique (unsupported claims, citation gaps, overclaiming,
cross-section inconsistency, mathematical-result distortion, repetition,
weak logical flow, missing limitations). The critique is persisted
separately (derived_from the draft).

`revise(draft_id)` creates an immutable V2: sections flagged by the critique
(via issue `location` = section id) are re-drafted with the critique feedback
appended to their prompts; un-flagged sections are reused by id. V1 is never
mutated; V2 carries a `supersedes` edge.

## Provenance

```text
ManuscriptDraft → ManuscriptSection → ResearchResultsPackage
  → ResearchFinding / ContributionClaim → Proposition / NumericalResult
ManuscriptSection → CitationReference → EvidenceItem → FullTextDocument
  → PaperIdentity
```

Sections get derived_from edges to every grounding artifact and every cited
evidence/identity (deduplicated); drafts link outline + package + sections;
V2 supersedes V1. Verified after SQLite reopen.

## CLI

```bash
uv run research-agent manuscript outline --results <package-id>
uv run research-agent manuscript draft --outline <outline-id> [--sections intro,propositions]
uv run research-agent manuscript inspect <draft-id>
uv run research-agent manuscript critique <draft-id>
uv run research-agent manuscript revise <draft-id>
```

`--sections` restricts drafting to a subset (used by the live smoke test).

## Configuration

```yaml
research:
  manuscript:
    drafter_role: reasoning
    critic_role: critic
    max_llm_calls: 100
```

## Tests

Unit (`tests/unit/test_manuscript.py`, 12): outline generation; section-by-
section drafting; unsupported claim rejection; missing citation rejection;
condition preservation; failed proposition exclusion; novelty normalization;
citation provenance (section → evidence → document → identity); critic output
with deterministic checks; immutable revision (flagged section re-drafted,
others reused, V1 unchanged); idempotency (keyed by section subset); 
provenance after reopen.

Integration (`tests/integration/test_phase4b_manuscript.py`): full offline
chain — package → outline → 12 sections → draft V1 → critique → revise →
V2 (supersedes), provenance after reopen.

Live (`tests/live/test_manuscript_live.py`, opt-in `live_manuscript`, needs
OPENROUTER_API_KEY): drafts Introduction, Analytical Model, Propositions on
the live package; verifies structural grounding (every section has grounded
claims, ids exist, conditions preserved). Recorded live run: 3 sections, 18 claims,
all grounded in real verified artifacts (per-run figure, not asserted by the
live test). Hardening driven by live failures:
claims required in schema; substring condition preservation; bounded retry
with feedback (4 attempts, 1s backoff); JSON extraction from model output;
idempotency keyed by the requested section subset; prompt rules forbidding
artifact ids inside `[CITE:...]`.

## Limitations

- Orphaned section artifacts can remain if a later section fails after
  earlier ones persisted (no transaction across sections; harmless in the
  immutable store).
- Conditions are preserved by substring containment, not semantic
  equivalence — a paraphrase that keeps the text passes.
- Citation placeholders are validated structurally (declared + existing
  evidence), not semantically (whether the evidence actually supports the
  claim — that is the critic's qualitative job).
- Drafting is sequential and LLM-bound (12 sections ≈ 12+ calls); no
  parallelism yet.
- The free-tier reasoning model intermittently returns empty output; the
  retry loop absorbs this but can take minutes for a full draft.
- Journal-specific style sheets remain future work; formatting,
  bibliography, LaTeX/DOCX/PDF export, cover letter, and submission packaging
  are implemented in Phase 4C (see `docs/workflows/outputs/publication.md`).

## Phase 4C readiness

**Ready — Phase 4C is implemented** (see `docs/workflows/outputs/publication.md`). The
structured draft (sections with grounded claims + internal citations +
critiques + versioned drafts) is the direct input for the publication
formatter: citation resolution, bibliography generation, LaTeX/DOCX/PDF
export, and the immutable SubmissionPackage.