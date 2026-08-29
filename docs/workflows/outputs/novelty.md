# External Novelty Validation & Submission-Readiness Gate

Novelty validation adds an external literature-based novelty-validation layer on top of
the completed publication workflow; incremental revalidation adds incremental
revalidation when a manuscript is superseded; evidence enrichment enriches sparse
candidates with acquired evidence before assessment; bounded pre-acquisition moves that
acquisition earlier — before assessment — in a bounded, deterministic way:

```text
Formatted Manuscript
        │
        ▼
SubmissionPackage
        │
        ▼
Novelty Claim Extraction        (deterministic Layer A + model Layer B)
        ▼
Novelty Search Planning          (persisted BEFORE search, bounded queries)
        ▼
External Literature Search       (existing providers: Semantic Scholar, Crossref)
        ▼
Candidate normalization/deduplication (PaperIdentity)
        ▼
Bounded Evidence Pre-Acquisition (bounded pre-acquisition: sparse high-risk candidates)
        ▼
Candidate Prior-Art Assessment   (evidence-backed, independent critic pass)
        ▼
Claim-Level Novelty Assessment   (coverage-aware, deterministic)
        ▼
NoveltyValidationReport
        ▼
SubmissionReadinessGate

--- incremental revalidation (incremental, on manuscript supersession) ---
old manuscript ─► ManuscriptChangeSet ─► NoveltyRevalidationPlan
  ─► reuse unaffected claim assessments + revalidate affected claims
  ─► NEW NoveltyValidationReport ─► NEW SubmissionReadinessGate

--- evidence enrichment (evidence enrichment fallback) ---
sparse candidate (title_only / indexed_metadata)
  ─► EvidenceEnrichmentPlan ─► existing provider/document acquisition
  ─► EvidenceItem(s) ─► superseding candidate assessment
  ─► recomputed claim assessment ─► recomputed report/gate

--- bounded pre-acquisition (bounded pre-acquisition) ---
candidate set ─► deterministic selection (risk, budget, discovery breadth)
  ─► PreAcquisitionExecution ─► evidence enrichment acquisition path
  ─► evidence ready before assessment ─► 5C fallback only if still sparse
```

The central principle:

> Novelty validation is an evidence-backed search-and-comparison problem, not
> an LLM opinion.

This is not a system for proving global novelty. `0 search results` never
means "novel". Search scope is always declared; exhaustive coverage is never
claimed. `SubmissionPackage.status == ready` keeps its publication packaging meaning
(formatting/validation/exports); the new gate adds "external pre-submission
validation has also been performed".

## Schemas (`research/schemas/novelty.py`)

- `NoveltyClaim` — one manuscript claim requiring external validation:
  `claim_type` (absolute_priority, scoped_priority, literature_absence,
  mechanism/model/result_novelty, empirical_or_contextual_novelty,
  contribution_difference), `risk` (critical/high/medium/low),
  `importance`, `scope`, `extraction_method` (deterministic/hybrid),
  `source_quote` (verbatim grounding span), `locations` (all manuscript
  locations asserting the same claim), `source_artifact_ids`.
- `NoveltySearchPlan` — persisted BEFORE any external search: per-claim
  bounded queries with `query_type` (exact/mechanism/relationship/
  setting/theory/synonym), providers, `date_cutoff`, year window,
  `maximum_results`, declared `search_scope`, generation metadata
  (deterministic/model_assisted/hybrid).
- `NoveltySearchExecution` — durable per-search record: search-record ids,
  explicit `as_of_date`, planned/executed/succeeded counts, provider
  failures. `literature_search_record` artifacts (existing core platform contract)
  record provider/query/limit/timestamp/returned ids/errors.
- `NoveltyCandidateSet` — union of candidate papers per claim: references
  existing `PaperIdentity` artifacts (never duplicated metadata), which
  queries/providers found each paper, ranking metadata, `earliest_year`
  (None = unavailable), and deterministic `excluded` entries with reasons.
- `NoveltyCandidateAssessment` — comparison of one candidate against the
  claim: 9 dimensions (focal phenomenon, actors, setting, mechanism, key
  assumptions, strategic decision, causal/equilibrium relationship,
  theoretical result, claimed contribution) with categorical values
  (match/partial_match/different/unknown), derived `relationship`
  (direct_prior_art/strong_overlap/partial_overlap/adjacent/distinct/
  insufficient_evidence), `evidence_basis` (full_text/abstract/
  indexed_metadata/title_only), evidence artifact ids.
- `NoveltyCriticAssessment` — independent critic pass, persisted separately
  so disagreement is preserved, never erased.
- `NoveltyClaimAssessment` — status (threatened/weakened/
  not_threatened_within_search_scope/unverified) + explicit `NoveltyCoverage`
  record (planned/executed/successful queries, provider failures, candidate
  counts, candidates with evidence, date limitations).
- `NoveltyRevisionRecommendation` — structured rewording/scope-change
  suggestions only; the manuscript is never modified.
- `NoveltyValidationReport` — package/manuscript/draft ids + manuscript
  content hash (version safety), all sub-artifact ids, per-status claim
  lists, coverage summary, overall status (clear/revise/blocked/unverified),
  documented aggregation policy, `supersedes` chain.
- `SubmissionReadinessGate` — references the unchanged `SubmissionPackage`
  and the report; status ready/needs_revision/blocked/unverified.
- `NoveltyValidationExecution` — operational record.
- incremental revalidation: `ManuscriptChangeSet` (old/new manuscript ids + content hashes,
  per-section change types, added/removed/modified/unchanged claim lists,
  `claim_identity_map` for stable identity), `NoveltyRevalidationPlan`
  (affected claims, reusable assessments, explicit reuse + revalidation
  reasons, persisted before any search), `NoveltyRevalidationExecution`
  (reused/new assessments, resulting report + gate ids).
- `StalenessStatus` (current/stale) — derived, never stored: a report/gate is
  stale when its manuscript content hash no longer matches the current
  manuscript in the draft lineage.
- evidence enrichment: `EvidenceEnrichmentPlan` (paper identity, requested evidence
  types, ordered acquisition strategies, reason, service policy — persisted
  before any acquisition), `EvidenceEnrichmentAttempt` (strategy/provider/
  status success|not_found|restricted|rate_limited|failed|skipped, retrieved
  artifact ids, failure reason), `EvidenceEnrichmentExecution`
  (before/after evidence basis, outcome enriched|partially_enriched|
  no_improvement|failed).
- bounded pre-acquisition: `PreAcquisitionExecution` (considered/selected/skipped candidate
  ids, per-candidate selection reasons, budgets, deterministic metrics, the
  evidence enrichment enrichment executions used).

## Claim extraction

- **Layer A (deterministic)**: regex patterns over title, abstract, and all
  sections detect high-risk lexical forms (`first study`, `we are the first`,
  `no prior research`, `never been examined`, `unexplored`, `unprecedented`,
  `to our knowledge`, `novel mechanism/model`, …). Technical contexts
  (`first-order condition`, `first step`, `first stage`) are blacklisted.
  The risk classifier is purely lexical — an LLM is never asked to decide
  whether a phrase containing "first" is high risk. Same claim in multiple
  sections merges into one claim with all locations preserved.
- **Layer B (model-assisted)**: the `reasoning` role receives only the
  manuscript material needed (title, abstract, introduction/research gap/
  theory/discussion/contributions/conclusion, bounded); structured output
  requires a `source_quote` that must be a verbatim span of the manuscript
  (fabricated claims rejected); explicit priority language found by the model
  is upgraded to the deterministic classification; bounded retries with
  validation feedback; semantic dedup merges locations.
- Ordinary contribution statements are NOT automatically priority claims
  (contribution_difference → medium).

## Search planning

For every claim: 2 deterministic base queries (exact distinctive phrase +
scope/prior-research) plus model expansion across exact/mechanism/
relationship/setting/theory/synonym perspectives. Bounded and configurable:
`queries_per_risk` (critical 10, high 6, medium 3, low 1) capped by
`max_queries_per_claim` (12). Queries are persisted as `literature_query`
artifacts (existing contract) linked to the claim; the plan is persisted
before execution. `--offline` runs deterministic queries only. Query
expansion never produces hundreds of queries.

## External search

Reuses the existing provider plugins (`literature_source.semantic_scholar`,
`literature_source.crossref`) through the existing `literature_ingestor`
contract — no new search architecture. Every hit resolves through
`PaperIdentity → PaperRecord` via the existing `paper_identity_resolver`;
duplicate discoveries (same DOI across queries/providers) collapse to one
candidate that preserves every discovery query/provider. Deterministic
filtering records exclusion reasons: post-cutoff publication, missing title,
unsupported document types. Missing publication dates are NOT excluded — they
are recorded as unavailable and handled conservatively.

## Candidate prior-art assessment

Evidence preference order: full-text evidence already in the repository →
abstract → indexed metadata → title only. Title-only evidence can NEVER
produce a strong semantic judgment (relationship forced to
`insufficient_evidence`); indexed-metadata-only candidates cannot yield
direct/strong/partial overlap (downgraded to `insufficient_evidence`).
Evidence artifact ids are preserved as provenance. For critical-risk claims
or direct/strong overlap, an independent `critic`-role pass evaluates the
candidate from the same evidence; its verdict (concurs/disputes/uncertain) is
persisted separately — disagreement survives aggregation.

## Coverage policy (deterministic)

`not_threatened_within_search_scope` requires: all planned searches
succeeded with zero provider failures, and (for critical/high claims) not all
candidates lack abstract/full-text evidence. Otherwise the claim is
`unverified` — a failed search can never become a false clear result.
Coverage records planned/executed/successful query counts, provider count,
failures, candidate counts, and date limitations (e.g. restricted window,
unavailable years). `0 search results` with successful searches only yields
`not_threatened_within_search_scope` — never "proven novel".

## Novelty aggregation (deterministic)

Claim level: coverage insufficient → `unverified`; any direct prior
art/strong overlap → `threatened`; any partial overlap → `weakened`; any
unassessable candidate → `unverified`; else → `not_threatened_within_search_scope`.

Report level: any critical-risk threatened claim → `blocked`; else any
threatened/weakened → `revise`; else any unverified → `unverified`; else →
`clear`. The policy string is stored in the report artifact.

## Submission readiness

`SubmissionReadinessGate.status`: package not ready → `blocked`; novelty
clear → `ready`; revise → `needs_revision`; blocked → `blocked`; unverified →
`unverified`. The original `SubmissionPackage` is never mutated: a package can
be technically publication-ready (`ready`) while scientifically risky to
submit (`needs_revision`/`blocked`/`unverified`).

## Provenance

```text
SubmissionReadinessGate → SubmissionPackage + NoveltyValidationReport
NoveltyValidationReport → manuscript, claims, claim assessments, recommendations
NoveltyClaimAssessment → claim, search plan, search execution, candidate set,
                         candidate assessments
NoveltyCandidateAssessment → candidate set, PaperIdentity, evidence items
NoveltyCandidateAssessment → NoveltyCriticAssessment
NoveltySearchExecution → search plan → literature queries → search records
NoveltyCandidateAssessment → PaperIdentity → PaperRecord

incremental revalidation:
New Gate → New Report → NoveltyRevalidationExecution → NoveltyRevalidationPlan
  → ManuscriptChangeSet → old + new manuscripts
New Report → reused old assessments + newly generated assessments

evidence enrichment:
NoveltyCandidateAssessment → EvidenceEnrichmentExecution → Plan → Attempt
  → acquired EvidenceItem / PaperRecord / FullTextDocument → PaperIdentity
New CandidateAssessment → supersedes old CandidateAssessment

bounded pre-acquisition:
NoveltySearchExecution → CandidateSet → PreAcquisitionExecution
  → EvidenceEnrichmentPlan/Attempt/Execution → EvidenceItem / FullTextDocument
  → CandidateAssessment
```

All edges are `derived_from`; reassessment of the same package creates a
superseding report (`supersedes` edge, old artifacts untouched). Verified
after store reopen.

## Manuscript-version safety

The report stores the exact `submission_package_id`, `manuscript_id`,
`draft_id`, and the manuscript's content hash. `create_gate` refuses to gate
a package whose manuscript differs from the report's, and refuses to build a
gate from a STALE report. An old report can never silently validate a changed
manuscript.

## Incremental revalidation

`novelty revalidate <previous-report-id> <new-package-id> [--offline]
[--as-of] [--force-all]`:

1. **Change detection (deterministic)** — per-section diff
   (unchanged/changed/added/removed by body+title equality, sha256 hashes);
   claims are re-extracted from the new manuscript with a previous-claims
   context: exact normalized-text matches keep stable identity
   (`NoveltyClaim.equivalent_claim_id`); near-similar claims (token-jaccard
   ≥ 0.5 with equal type/risk/scope) use a bounded structured model
   comparison for ambiguous semantic changes, conservative default =
   changed. Type/risk/scope differences are ALWAYS material (e.g. scoped →
   absolute "first" ⇒ risk increases ⇒ mandatory revalidation). Removed
   claims need no new search and never appear in the new report.
2. **Reuse policy (deterministic)** — an unchanged claim's previous
   assessment is reused only when: the claim meaning is unchanged (exact
   identity), the previous assessment is complete (never an `unverified`
   status), the date cutoff/`as_of` equals the current one, providers,
   max-results and year window match the current search policy. Every reuse
   decision persists its reason in the plan.
3. **Revalidation** — affected claims (new, modified, risk-increased,
   scope-broadened, previously unverified, policy-invalidated, or
   `--force-all`) run through the novelty validation pipeline.
4. **New report + gate** — a fresh `NoveltyValidationReport` binding the new
   draft/manuscript/content-hash/package, referencing reused + new
   assessments, superseding the old report; a new `SubmissionReadinessGate`
   is produced. Old reports/gates are never mutated.

## Staleness

`staleness(report-or-gate-id)` follows the draft supersedes lineage to the
leaf draft and its latest formatted manuscript, and compares the content
hash with the report's. Mismatch ⇒ `stale`. A stale report cannot create a
gate; a stale `ready` gate is never presented as current — `novelty inspect`
prints the staleness status prominently for both reports and gates.

## Evidence enrichment

Trigger (deterministic): `title_only` candidates always; `indexed_metadata`
candidates for critical/high-risk claims — i.e., exactly when the assessment
lacks defensible evidence. Repository evidence is checked first and never
refetched.

Acquisition priority: repository full text → existing abstract →
external abstract acquisition (`literature_source.{provider}.get` + the
existing ingestor, identifiers in order DOI → provider paper id → canonical
URL, never invented) → document full text (existing `document_locator.*` →
`document_fetcher.default` → `document_extractor.pypdf`, bytes through the
BlobStore with content hashes) → indexed metadata.

- Abstract acquisition persists the provider PaperRecord AND an
  `EvidenceItem` (`provider_import`, DOI-matched) so the evidence is reused
  across claims without re-acquisition.
- Full-text acquisition reuses the existing extractor; novelty-relevant
  `EvidenceItem`s are extracted via the existing `evidence_extractor` service
  (model-assisted) with a deterministic claim-token fallback, keeping page
  locators; large bodies stay in the BlobStore, never in SQLite JSON.
- Every attempt is recorded with its status; rate limits/paywalls/outages are
  explicit failures. If evidence remains insufficient, novelty validation rules apply
  unchanged: candidate → `insufficient_evidence`, claim → `unverified`.
- Inline: `novelty validate` enriches automatically when configured
  (`research.novelty.evidence_enrichment`). Explicit: `novelty enrich
  <candidate-assessment-id>` runs enrichment + reassessment: a superseding
  candidate assessment, a recomputed (superseding) claim assessment, and a
  recomputed report + gate — the old artifacts are never mutated and the
  original search provenance is preserved.
- Enrichment-created reassessments keep the search plan/execution/candidate
  set of the original assessment (coverage is recomputed deterministically
  from the new evidence).

## Bounded evidence pre-acquisition

Moves acquisition earlier: after candidate normalization/deduplication and
before assessment. Trigger (deterministic): claims whose risk ∈
`evidence_preacquisition.risk_levels` (default `[critical, high]`); eligible
candidates are `indexed_metadata` or `title_only` with a usable external
identifier. Existing evidence is never refetched (cache hit, zero external
calls).

Selection is deterministic — no LLM ranking: candidates are prioritized by
discovery breadth (number of queries + providers finding the paper), provider
count, DOI availability, and available publication year. Budgets bound the
work: `max_candidates_per_claim` (per claim) and `max_total_candidates`
(global across the run, counted at selection time). Every considered,
selected, and skipped candidate carries a persisted selection reason.

Acquisition reuses the evidence enrichment path (`_enrich` → plan/attempts/execution,
abstract first via `prefer_abstract`, optional full text) — no duplicated
provider/fallback logic. Deduplication is at the `PaperIdentity` level: one
paper found by many queries is acquired once; the same paper across multiple
claims is acquired once and the DOI-keyed evidence items are reused by every
claim.

Failures (rate limits, restricted, not found, provider/parse failures) are
recorded in the metrics (`failures`, per-attempt statuses) and never abort
validation; the novelty validation and enrichment conservative rules stay authoritative — failed
pre-acquisition never implies novelty.

evidence enrichment remains the fallback: candidates still sparse after pre-acquisition
are enriched inline during assessment — except that strategies which already
failed for the same identity+claim in this run's pre-acquisition are not
repeated (recorded, non-fatal). Metrics make the win measurable:
`candidates_considered/selected`, `cache_hits`, `external_attempts`,
`abstracts_acquired`, `full_texts_acquired`, `failures`,
`candidates_upgraded`.

```yaml
research:
  novelty:
    evidence_preacquisition:
      enabled: true
      risk_levels: [critical, high]
      max_candidates_per_claim: 10
      max_total_candidates: 30
      prefer_abstract: true
      acquire_full_text: false
```

Disabled (`enabled: false`) leaves the evidence enrichment behavior exactly as before.

## CLI

```bash
uv run research-agent novelty validate <submission-package-id> \
    [--as-of 2026-08-23] [--max-results 10] [--max-claims 5] [--offline] \
    [--report-only]
uv run research-agent novelty revalidate <previous-report-id> <new-package-id> \
    [--as-of 2026-08-23] [--offline] [--force-all]
uv run research-agent novelty enrich <candidate-assessment-id> [--offline]
uv run research-agent novelty report <package-id>        # reassess (supersedes)
uv run research-agent novelty gate <package-id>          # gate from latest report
uv run research-agent novelty inspect <report-or-gate-id>  # incl. staleness
```

`novelty validate` runs evidence enrichment automatically when configured.

Inspection of report/gate artifacts is also available via `artifacts inspect`.

## Configuration

```yaml
research:
  novelty:
    extractor_role: reasoning
    critic_role: critic
    max_llm_calls: 40
    max_queries_per_claim: 12
    queries_per_risk: {critical: 10, high: 6, medium: 3, low: 1}
    max_results_per_query: 10
    providers: [semantic_scholar, crossref]
    search_year_window: 50
    require_all_searches_succeed: true
    require_candidate_evidence: true
    evidence_enrichment:
      enabled: true
      acquire_abstract: true
      acquire_full_text: true
      max_attempts_per_candidate: 3
      abstract_providers: [semantic_scholar, crossref]
    evidence_preacquisition:
      enabled: true
      risk_levels: [critical, high]
      max_candidates_per_claim: 10
      max_total_candidates: 30
      prefer_abstract: true
      acquire_full_text: false
```

## Tests

Unit (`tests/unit/test_novelty.py`, 31): deterministic extraction (first/
absence/scoped/technical-context blacklist/duplicate locations), hybrid
upgrade and fabricated-quote rejection, bounded planning with preserved
query types, provider dedup through `PaperIdentity` with preserved discovery
queries/providers, post-cutoff exclusion + conservative missing-date
handling, title-only and indexed-metadata guards, direct prior art with
critic pass preserved, offline determinism (zero router calls), provider
failure → unverified, evidence-less high-risk candidates → unverified,
0-results coverage → not_threatened (never "proven novel"), every claim
status, every report status, all four gate mappings, revision
recommendations without manuscript modification, conservative rewording for
absolute claims under weak coverage, reassessment supersession +
immutability, gate manuscript-mismatch rejection, and the full provenance
chain after reopen.

Unit (`tests/unit/test_revalidation.py`, 11, incremental revalidation): no manuscript change
→ all eligible assessments reused with zero new searches; non-novelty section
change → assessments reused; one novelty claim wording change → only that
claim revalidated; new claim → new validation; removed claim → absent from
the new report (no search, no assessment); scoped → absolute "first" → risk
increase → mandatory revalidation with no reuse of the old assessment;
unchanged claim + changed search policy (as_of) → no reuse with explicit
reason; staleness current → stale after manuscript change; stale `ready` gate
never treated as current (gate creation refused, old gate untouched); and
`--force-all` → zero reuse. Reuse reasons and revalidation reasons are
persisted and asserted.

Integration (`tests/integration/test_phase5a_novelty.py`, 1): a real publication packaging
`SubmissionPackage` → extraction → plans → fake providers (one threatening
paper + duplicate, one partial overlap, one unrelated) → `PaperIdentity`
dedup → assessments with critic pass → blocked report → gate; provenance
graph verified after close/reopen, including search record → query and
recommendation → supporting candidates edges.

Integration (`tests/integration/test_phase5b_revalidation.py`, 1, incremental revalidation):
real publication packaging package → 5A report (clear) → draft V2 supersession with one
novelty claim modified → staleness flips to stale → incremental revalidation
(one claim reused, one revalidated, threat found → blocked) → new report +
gate → close/reopen → full incremental provenance: gate → report → execution
→ plan → change set → old + new manuscripts; reused assessment linkage;
supersedes edge; old artifacts untouched and stale.

Unit (`tests/unit/test_enrichment.py`, 10, evidence enrichment): title-only candidate →
abstract acquired via provider `get` → reassessed with abstract evidence;
metadata-only candidate → full text acquired through fake locator/fetcher/
extractor → stronger (non-downgraded) reassessment with deterministic
EvidenceItems; existing adequate evidence → zero acquisition calls; the same
paper under two claims → acquired exactly once, evidence reused across
claims; DOI acquisition failure → paper-id fallback attempted; all strategies
fail → `insufficient_evidence` preserved and claim `unverified`; rate limit →
explicit `rate_limited` attempt, never a clear result; `enrich_candidate`
reveals strong overlap → superseding candidate assessment → recomputed claim
assessment (threatened) → recomputed blocked report + gate with old artifacts
untouched; enrichment reveals a distinct candidate → conservative update to
`clear`; `enrich_candidate` no-op when evidence already sufficient.

Integration (`tests/integration/test_phase5c_enrichment.py`, 1, evidence enrichment):
full chain — a title-only candidate (DOI via external identifier) is enriched
inline during `novelty validate`: abstract acquired → EvidenceItem persisted
→ candidate assessed as direct prior art on abstract evidence → blocked
report + gate → reopen → enrichment provenance verified: assessment →
execution → plan → attempt → evidence item → paper identity.

Unit (`tests/unit/test_preacquisition.py`, 12, bounded pre-acquisition): critical sparse
candidate → pre-acquired before assessment (abstract basis, upgraded metrics);
low-risk claim → no pre-acquisition at all; existing abstract → cache hit
with zero provider calls; same paper found by multiple queries → one
identity, one acquisition; same paper across two claims → acquired once,
second claim is a cache hit; candidate count above `max_candidates_per_claim`
→ deterministic bounded selection with persisted reasons; global
`max_total_candidates` cap across claims; rate limit → recorded in metrics,
assessment continues and stays conservative (never a clear); successful
pre-acquisition → evidence enrichment fallback not invoked (exactly one enrichment
execution); insufficient/budget-limited pre-acquisition → the uncovered
candidate is still enriched by the evidence enrichment fallback; failed pre-acquisition
→ the same strategies are NOT repeated inline during assessment; disabled
config → evidence enrichment behavior unchanged.

Integration (`tests/integration/test_phase5d_preacquisition.py`, 1,
bounded pre-acquisition): three sparse candidates (one also duplicated via same DOI) →
identity normalization → bounded pre-acquisition (3 abstracts acquired) →
all candidates assessed on abstract evidence → blocked report + gate → zero
inline fallback enrichments (3 total enrichment executions = 3
pre-acquisitions) → reopen → provenance verified: PreAcquisitionExecution →
claim, candidate set, enrichment executions → plans → evidence items →
candidate assessments.

Live (`tests/live/test_novelty_validation_live.py`, opt-in
`live_novelty_validation`): a controlled known claim (ride-hailing platform
competition) plus a bounded full report over the live package. Failure-safe:
if providers rate-limit, the claim becomes `unverified` with failures
recorded — never a false clear. Live run (2026-08-23): controlled claim →
`unverified` (provider failure recorded), package report → `unverified`
(2 claims, 2 search executions), gate → `unverified`; package status
unchanged. Live revalidation smoke: incremental run on the live package
completed with new report + gate, honest `unverified` status under provider
rate limits.

## Limitations

- The system cannot prove global novelty and never claims to.
- Coverage depends on provider availability/rate limits; failures produce
  `unverified`, not conclusions.
- Abstract/metadata quality limits semantic comparison; title-only evidence
  is always `insufficient_evidence`.
- Query quality for less-explicit claims depends on the model expansion step.
- Dates: post-cutoff work is excluded; unknown dates are handled
  conservatively but cannot be resolved.
- Full-text reuse is limited to evidence items already in the repository.
- The critic pass is prompt-bounded by the same evidence; it cannot see
  beyond it.
- Conservative wording suggestions are recommendations only — human
  approval controls manuscript revision.
- incremental revalidation change detection: near-identical wording relies on a bounded model
  comparison (conservative default = changed); re-extraction may produce
  claim-text variance across model runs, which invalidates exact identity and
  forces revalidation (never silently reuses).
- Staleness is content-hash-based: two manuscripts with identical content
  but different artifacts are considered current for one another.
- evidence enrichment enrichment depends on provider `get` support and open-access
  availability; paywalls/rate limits are recorded as explicit failures, never
  as evidence. Abstract reuse across claims requires a DOI match; candidates
  without any usable identifier cannot be enriched. Deterministic
  claim-token evidence extraction is used when the model extractor is
  unavailable/offline.
- bounded pre-acquisition pre-acquisition is bounded per claim and globally; budget-skipped
  candidates fall back to evidence enrichment inline enrichment. Title-only candidates
  without usable identifiers are never pre-acquired (correctly skipped with a
  recorded reason). Global budget accounting is per validation run (reset on
  each `create_report`/`revalidate`).

## Next steps

The single next increment: **open-access full-text prioritization** — rank
pre-acquisition selection by open-access likelihood (Unpaywall data already
in the repository) so `acquire_full_text: true` runs target fetchable
documents first, reducing restricted/failed attempts. Do not implement yet.