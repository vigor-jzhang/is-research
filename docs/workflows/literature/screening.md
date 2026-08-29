# Screening

Literature screening operates at the **PaperIdentity** level (not raw `PaperRecord`s). It is deterministic where possible, model-assisted where judgment is required, and approval-gated at the protocol boundary.

```
PaperIdentity ──► ScreeningViewBuilder (deterministic)
                      │
                      ▼
                PaperScreeningView  ──┐
                      │               │
                      ▼               │
ScreeningProtocol (draft → approved)  │
                      │               │
                      ▼               │
           TitleAbstractScreener (model)
                      │
                      ▼
              ScreeningDecision ──► maybe ScreeningReview
                      │
                      ▼
          ScreeningOrchestrator ──► ScreeningExecution
                                           │
                                           ▼
                                ScreenedLiteratureSet (included / excluded / uncertain)
```

## Key distinction

> `PaperRecord` is a normalized observation from a provider.
> `PaperIdentity` is the resolved scholarly work.
> `PaperScreeningView` is the **deterministic** candidate snapshot shown to the screener for one `PaperIdentity`.

Screening never creates or mutates `PaperRecord`/`PaperIdentity` — it reads them via `artifact_store.default`.

## PaperScreeningView (deterministic, no LLM)

Plugin `literature.screening_view_builder` provides `screening_view_builder.default`.

For a given `paper_identity_id`:

1. Load `PaperIdentity` and its `member_paper_artifact_ids`.
2. Load each `PaperRecord`. Provider is inferred from `ProviderRecordSnapshot` provenance (`generated_from`) with fallback to `metadata.crossref_publisher` / `metadata.semantic_scholar_paperId`.
3. Select canonical fields deterministically:
   - **title** — prefer non-empty, crossref priority, then smallest `artifact_id`; record `field_sources["title"]`.
   - **abstract** — prefer non-empty longest (richest), tie `semantic_scholar > crossref`; if none, `None`.
   - **year** — most common year among members; tie smallest `artifact_id`.
   - **authors / venue** — first non-empty by smallest `artifact_id`.
   - `all_titles`, `all_abstracts` preserved for audit; `metadata` records `title_conflicts`, `abstract_conflicts`, `year_conflicts`, `missing_abstract`.
4. Persist `PaperScreeningView` (`artifact_type="paper_screening_view"`).
5. Provenance: `view derived_from PaperIdentity` and `view derived_from each PaperRecord` (both via `ProvenanceRelation.derived_from`).

Properties:

- No model call, no hallucination risk.
- Idempotent: re-building for same `paper_identity_id` with unchanged members returns existing `artifact_id` (checked by `paper_identity_id` + exact `member_paper_artifact_ids` set).
- Superseded identities are filtered by the orchestrator, not the view builder — but building a view for a superseded identity is allowed (caller decides).

View does not infer beyond members: prompt to the screener says `do not infer beyond this deterministic view` and shows `No abstract available` when missing.

## ScreeningProtocol (model-assisted, approval-gated)

Plugin `literature.screening_protocol_builder` provides `screening_protocol_builder.default` (`requires=["model_router.default","artifact_store.default","autonomy_policy.default"]`).

```python
ScreeningProtocol
  research_question_id, research_plan_id?
  objective, inclusion_criteria[≤12], exclusion_criteria[≤12], decision_rules?
  screening_stage="title_abstract", model_role, status="draft"|"approved"|"rejected"|"superseded"
  ScreeningCriterion {criterion_id, kind: inclusion|exclusion, description, rationale?, required}
```

Flow (`ScreeningProtocolBuilderService.build(rq_id, rp_id?)`):

1. Load `ResearchQuestion` (and optional `ResearchPlan`) for context.
2. Prompt `model_role` (default `reasoning`, config `literature.screening.protocol_model_role`) with structured output schema (`objective`, `inclusion_criteria`, `exclusion_criteria`, `decision_rules`).
3. Validate: non-empty `criterion_id` ≤20, description ≤1000, unique ids across both lists, ≤12 each, ≥1 inclusion, `objective` non-empty. Too many → error. Model hallucinated `criterion_id` caught here.
4. Persist `ScreeningProtocol` as **draft** (`artifact_type="screening_protocol"`), provenance `derived_from ResearchQuestion` (and `ResearchPlan` if given), emit `screening.protocol.started`.
5. Request approval via `autonomy_policy.default` (`checkpoint="screening_protocol"`).
   - `high` autonomy: auto-approved → new artifact with `status=approved`, provenance `supersedes` the draft + `derived_from ResearchQuestion`, emit `screening.protocol.completed`, return approved id.
   - `interactive` / rejected: new artifact `status=rejected` superseding draft, emit `screening.protocol.failed`, raise `ValueError` (caller should inspect / retry).
6. **Approved protocol is required for screening** — `TitleAbstractScreener` and `ScreeningOrchestrator` reject `draft/rejected`.

CLI: `research-agent literature screening protocol create --question <rq_id>` and `protocol inspect/approve`. Approval is explicit; no silent promotion to `approved` without `autonomy_policy`.

## Title / Abstract Screener (model-assisted, audited)

Plugin `literature.title_abstract_screener` provides `title_abstract_screener.default` (`requires=["model_router.default","artifact_store.default"]`, optional `events`).

```python
ScreeningDecision
  paper_identity_id, screening_view_id, screening_protocol_id
  decision: include|exclude|uncertain
  matched_inclusion_criteria, matched_exclusion_criteria, reason_codes
  rationale_summary, confidence ∈ [0,1], information_sufficiency: sufficient|insufficient
  model_assessed=True
```

Flow (`TitleAbstractScreenerService.screen(view_id, protocol_id)`):

1. Load view + protocol; reject if protocol not `approved`.
2. Idempotency: reuse existing `screening_decision` with same `paper_identity_id + view_id + protocol_id` (avoids paying for model twice).
3. Build prompt:
   ```
   objective, decision_rules, inclusion/exclusion criteria,
   deterministic view: title / abstract / authors / year / venue
   Task: include if all required inclusion + no exclusion + sufficient;
         exclude if explicit exclusion matched;
         uncertain (+ insufficient) if title/abstract insufficient.
         Missing abstract must not auto-exclude.
   ```
   Criterion ids in prompt: `inclusion: [I1,…], exclusion: [E1,…]`.
4. Call `model_router.complete(model_role)` (default `fast`, config `literature.screening.screening_model_role`) with **structured output** schema (decision, matched ids, reason_codes, rationale_summary, confidence, information_sufficiency). `temperature=0.0`.
5. Parse JSON, validate criterion ids against protocol (`hallucinated → ValueError`, not stored).
6. Validate enums / `rationale_summary` non-empty ≤2000 / confidence bounds.
7. Persist `ScreeningDecision` (`artifact_type="screening_decision"`), provenance `derived_from PaperIdentity + PaperScreeningView + ScreeningProtocol`, emit `screening.candidate.completed`.
8. **Deterministic semantics**: if `matched_exclusion_criteria` non-empty but `decision != exclude`, log warning (spec says explicit exclusion → exclude); no silent override in this capability (trust model, surface conflict).

Hallucination boundary: **before** calling `ArtifactStore.put`, all criterion ids are checked against `protocol.all_criterion_ids()` split by kind, and JSON schema is closed. Raw model output never becomes an artifact on failure.

`has_abstract` (`view.abstract` non-empty) is logged (`screening view %s has_abstract=%s`) — used by orchestrator review logic.

## ScreeningReview (optional override)

```python
ScreeningReview
  screening_decision_id, review_reason, original_decision, final_decision
  reviewer_type: human | autonomy_policy, approval_decision_id?, notes?
```

Created in two places:

- **Orchestrator** for `uncertain` or `confidence < review_low_confidence_below` (default `0.65`, config `literature.screening.review_low_confidence_below`; disable with `null`): calls `autonomy_policy.request_approval(checkpoint="screening_review")`, persists `ScreeningReview` with `derived_from ScreeningDecision`, emits `screening.review.requested → screening.review.completed`. In `high` autonomy the review preserves original decision (no override) but records audit.
- **CLI** `literature screening review --decision <id> --final include|exclude|uncertain --notes …` with `reviewer_type=human` and `ReviewerType.human` (always preserved alongside model decision; model artifact never mutated).

Filtering for `ScreenedLiteratureSet` uses `final_decision` if a `ScreeningReview` exists for that decision, otherwise `decision`. This is applied both during orchestrator counting and during identity `inspect` filtered listing.

## ScreeningOrchestrator (PaperIdentity level, budgets, idempotency)

Plugin `literature.screening_orchestrator` provides `screening_orchestrator.default` (`requires=["artifact_store.default","screening_view_builder.default","title_abstract_screener.default"]`, optional `autonomy_policy.default` for reviews).

`screening_orchestrator.screen(search_execution_id, protocol_id)`:

1. Load approved protocol and `LiteratureSearchExecution`; `candidate_identity_ids = search_execution.paper_identity_artifact_ids`. If empty, fallback to listing **current** `paper_identity` artifacts (those **not** superseded via `ProvenanceRelation.supersedes` children) — preserves offline tests without a search execution.
2. **Superseded filtering**: `PaperIdentity` with a `supersedes` child edge is skipped (`derived_from` / `generated_from` lineage irrelevant). This ensures screening the old identity never shadows the resolved work.
3. **Budgets** (`config literature.screening`):
   ```yaml
   screening:
     max_candidates: 500          # candidates in the set, truncated with reason
     max_model_calls: 500         # model calls in this run, stop clean
     review_low_confidence_below: 0.65  # null to disable
     review_uncertain: true
   ```
   Orchestrator (not LLM) enforces budgets. `max_candidates` truncates the candidate list; `max_model_calls` breaks the sequential loop when reached (after storing successful decisions). `budget_stop_reason` stored in `ScreeningExecution`.
4. **Idempotency per candidate** (`_find_existing_decision`): if a `screening_decision` already exists for `paper_identity_id + protocol_id` (any view for that identity+protocol), reuse it — no model cost, `reused` counted. Counts are still derived from the reused decision (including any `ScreeningReview` final override).
5. **Sequential screening** per `PaperIdentity`:
   - `view_builder.build(pi_id)` → `view_id` (or record `failures: stage=view`).
   - `screener.screen(view_id, protocol_id)` → `decision_id` (counts model_calls; emits `screening.candidate.started`).
   - Handle `uncertain` / low confidence via autonomy + emit `screening.review.*`; create `ScreeningReview` if needed.
   - Resolve `final_for_counts = review.final_decision` if review exists else `decision.decision`.
   - `included / excluded / uncertain` partitioned by `final_for_counts`.
6. Persist:
   ```python
   ScreeningExecution {
     protocol_artifact_id, search_execution_artifact_id,
     candidate_identity_ids, screening_view_ids, decision_artifact_ids, review_artifact_ids,
     started_at, completed_at, counts {total_candidates, processed, included, excluded, uncertain, failed, reused},
     failures, budget_stop_reason
   }
   ScreenedLiteratureSet {
     screening_execution_id, screening_protocol_id,
     included_identity_ids, excluded_identity_ids, uncertain_identity_ids, decision_artifact_ids
   }
   ```
   Provenance: `ScreeningExecution derived_from ScreeningProtocol + LiteratureSearchExecution`; `ScreenedLiteratureSet derived_from ScreeningExecution`. Emits `screening.completed` with counts.
7. **Failure semantics**: `view` or `screen` exception → `failures` entry, not crash of whole run; partial success preserved with `processed = len(decision_ids)`.

Idempotency of the whole execution is **not** guaranteed by equality of the set (counts may differ on re-run due to new identities). Per-candidate decision reuse is guaranteed; a second `orchestrator.screen` on same inputs will reuse decisions and only screen new candidates.

## Provenance model for screening

```
ResearchQuestion ──derived_from──► ScreeningProtocol
ResearchPlan    ──derived_from──► ScreeningProtocol
ScreeningProtocol ──supersedes──► ScreeningProtocol (draft → approved/rejected)

PaperIdentity ──derived_from──► PaperScreeningView
PaperRecord ──derived_from──► PaperScreeningView

PaperIdentity ──derived_from──► ScreeningDecision
PaperScreeningView ──derived_from──► ScreeningDecision
ScreeningProtocol ──derived_from──► ScreeningDecision

ScreeningDecision ──derived_from──► ScreeningReview
ScreeningProtocol ──derived_from──► ScreeningExecution
LiteratureSearchExecution ──derived_from──► ScreeningExecution
ScreeningExecution ──derived_from──► ScreenedLiteratureSet
```

Traceability: `ScreenedLiteratureSet → ScreeningExecution → ScreeningDecision → PaperScreeningView → PaperIdentity → PaperRecord → ProviderRecordSnapshot → LiteratureSearchRecord → LiteratureQuery → LiteratureSearchStrategy → ResearchQuestion`.

## CLI

```bash
# Protocol (requires OpenRouter; approval via autonomy policy)
uv run --env-file .env research-agent literature screening protocol create --question <rq_id> [--research-plan <rp_id>]
uv run research-agent literature screening protocol inspect <protocol_id>
uv run research-agent literature screening protocol approve <draft_protocol_id>   # interactive override

# Screening
uv run --env-file .env research-agent literature screening run --search-execution <exec_id> --protocol <approved_protocol_id>
uv run research-agent literature screening decisions list [--execution <screening_execution_id>]
uv run research-agent literature screening decisions inspect <decision_id>
uv run research-agent literature screening review --decision <decision_id> --final include --notes "human override"

# Identity views
uv run research-agent artifacts list --type paper_screening_view
uv run research-agent artifacts inspect <view_id>
uv run research-agent artifacts list --type screening_execution
uv run research-agent artifacts lineage <decision_id> --direction ancestors

# Identities filtered by final disposition (CLI applies ScreeningReview final override)
uv run research-agent literature identities list   # not screened
uv run research-agent literature screening decisions list --execution <exec_id>
```

All CLI commands are thin wrappers over services; no HTTP/SQL in CLI except direct `SQLiteArtifactStore` reads for inspection.

## Configuration (configs/example.yaml)

```yaml
literature:
  screening:
    protocol_model_role: reasoning
    screening_model_role: fast
    max_candidates: 500
    max_model_calls: 500
    max_inclusion_criteria: 12
    max_exclusion_criteria: 12
    review_uncertain: true
    review_low_confidence_below: 0.65
```

`max_*` are enforced by the orchestrator, not the model. Setting `review_low_confidence_below: null` disables low-confidence reviews.

## Budgets and determinism summary

| Concern | Enforced by | LLM touches? |
|---|---|---|
| Which fields are shown to screener | `ScreeningViewBuilder` deterministic selection, no model | No |
| Which criteria exist | `ScreeningProtocolBuilder` model proposal → schema validation → approval | Model proposes, system validates + approves |
| Which decision for a view | `TitleAbstractScreener` model structured output → id validation → provenance | Model |
| How many candidates / calls | `ScreeningOrchestrator` budgets (`max_candidates`, `max_model_calls`) | No (orchestrator) |
| Whether a view screens as superseded | Orchestrator `supersedes` filtering | No |

## Deterministic offline test

`tests/integration/test_phase2d_screening.py`:

```
ResearchQuestion → fake model → ScreeningProtocol (approved via FakeAutonomy)
3 PaperRecords (2 with same DOI → 2 PaperIdentities) → mock search execution
Fake screener view builder → deterministic views (title/abstract conflicts recorded)
Fake screener (include / exclude / uncertain) → decisions + reviews for uncertain
→ ScreeningOrchestrator.screen → ScreeningExecution + ScreenedLiteratureSet
Asserts: counts (1/1/1), provenance, superseded filtering, budgets respected,
second run reuses decisions (idempotency), DB reopen persistence.
```

No network, no OpenRouter.

## Quality gates for screening

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

Plugin contracts covered: `screening_protocol_builder.default`, `screening_view_builder.default`, `title_abstract_screener.default`, `screening_orchestrator.default` (`contracts/screening.py`).
