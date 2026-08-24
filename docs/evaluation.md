# Evaluation Harness (Phase 6A–6E)

Plugin-based evaluation framework that measures research-agent quality
independently from the production pipeline:

```
given benchmark input
→ run research capability/workflow
→ compare output against reference/evidence
→ compute deterministic + model-assisted evaluations
→ persist evaluation artifacts
→ aggregate metrics
```

Evaluation reuses the existing plugin, artifact, provenance, model-role, and
execution infrastructure. Production research code does not depend on any
evaluator implementation. Benchmarks run the REAL production services
(NoveltyValidationService, LiteratureSearchOrchestratorService, Phase 4C
PublicationFormatterService) composed with deterministic fixtures — no
network, no paid models.

## Schemas (`research/schemas/evaluation.py`)

Immutable, versioned payloads; evaluation logic never lives in the schemas:

- `Benchmark` — id, version, name, category, evaluation config, `case_ids`
- `BenchmarkCase` — `case_id`, `input` (workflow input: manuscript, fixture
  sources, scripted model responses), `reference` (expected outputs),
  `evaluation_dimensions`, `tags`, `version`
- `EvaluationRun` — benchmark id/version/content hash, per-case hashes,
  evaluation config, evaluator ids/versions, model roles, produced artifact
  ids, evaluator/case result ids, report id, token usage, cost, latency,
  case counts, failures
- `EvaluatorResult` — case, evaluator id/version, category
  (deterministic | model_assisted), score (0..1), `value` (structured
  details), status (passed/failed/skipped/error), explanation,
  `evidence_artifact_ids`, `model_metadata` (role/model/tokens/latency when
  a model was used)
- `EvaluationCaseResult` — per-case status (passed/failed/error), evaluator
  result ids, produced artifact ids, per-dimension scores, error
- `EvaluationMetric` — metric id, dimension, kind (rate/quantity/cost/
  latency/score), value, count, definition
- `EvaluationReport` — case totals (passed/failed/error), metrics, embedded
  per-case results, false-positive/false-negative counts, execution cost and
  latency, evaluator versions, model roles

## Evaluator contract (`contracts/evaluator.py`)

`Evaluator` protocol: `evaluator_id`, `evaluator_version`, `category`,
`async evaluate(EvaluatorContext) -> EvaluatorResult`.

`EvaluatorContext` carries the case, the case envelope, produced artifacts,
evaluation config, and an optional model router (benchmarks inject their
fixture router so evaluation runs offline).

Evaluators are plugin services registered in the service registry:

| Evaluator | Category | Role |
|---|---|---|
| `evaluator.deterministic` | deterministic | Novelty-threat comparison (relationships, claim statuses, report status, false clear) or generic `reference_equality` mode |
| `evaluator.retrieval` | deterministic | Literature retrieval (Phase 6B): precision@k, recall@k, F1@k, MRR, duplicate_rate, missed/irrelevant papers |
| `evaluator.screening` | deterministic | Screening (Phase 6C): accuracy, include precision/recall/F1, false exclusion/inclusion, review triggers, technical failures |
| `evaluator.evidence` | deterministic | Evidence extraction (Phase 6C): precision/recall, category + locator accuracy, unsupported/duplicate rates, required-evidence recall |
| `evaluator.gap_analysis` | deterministic | Gap analysis (Phase 6D): type/precision/recall, grounding, corpus-bounded claims, support counts, ranking, hallucination |
| `evaluator.mechanism` | deterministic | Mechanism development (Phase 6D): validity, knowledge-basis discipline, grounding, critic recall, revision success |
| `evaluator.equilibrium` | deterministic | Equilibrium correctness (Phase 6E): symbolic expression/FOC/best-response equivalence, verification, order, conditions |
| `evaluator.numerical` | deterministic | Numerical analysis (Phase 6E): value/feasibility/condition/sweep/robustness/welfare/reproducibility |
| `evaluator.claim_grounding` | model-assisted | Whether candidate assessments are grounded in cited evidence (deterministic guard: no evidence → ungrounded without a model call) |
| `evaluator.citation_correctness` | deterministic | Placeholder check (6A) or full `manuscript_citation` mode (6B): resolution, map accuracy, dedup, leftovers, invented fields |
| `evaluator.llm_judge` | model-assisted | Generic rubric-driven judge with structured output |

Rules:

- Deterministic evaluators gate case pass/fail. Model-assisted evaluators are
  advisory and can never override a deterministic failure.
- Model-assisted evaluators require structured output (`response_schema`) and
  record `model_metadata` (role, model, tokens, latency).
- The judge role defaults to `critic` — independent from the role that
  generated the artifact (`evaluation.judge_role`).

## Benchmark architecture (`research/benchmarks/`)

Benchmarks are immutable, versioned definitions registered as artifacts
(`benchmark`, `benchmark_case`):

- Registration is idempotent for identical content; any semantic change
  raises `BenchmarkVersionError` and requires a new version — a benchmark
  change can never silently alter historical evaluation results.
- `EvaluationRun` records the benchmark content hash and per-case content
  hashes for reproducibility.
- Benchmarks execute the **production workflow** (e.g. the real
  `NoveltyValidationService`) composed with deterministic fixtures: a
  `FixtureModelRouter` (scripted JSON responses keyed by prompt markers) and
  `FixtureLiteratureSource`s (fixture paper records, or `fail_all` for
  provider-failure cases). No network is required.

## The first benchmark: `novelty-threat-v1`

Offline benchmark over novelty validation with 7 cases:

| Case | Reference expectation | Production outcome |
|---|---|---|
| `nt-direct-prior-art` | threatened → blocked | threatened → blocked |
| `nt-strong-overlap` | threatened → blocked | threatened → blocked |
| `nt-partial-overlap` | weakened → revise | weakened → revise |
| `nt-distinct-paper` | not threatened → clear | not threatened → clear |
| `nt-insufficient-evidence` | unverified | unverified (guard downgrades weak evidence) |
| `nt-provider-failure` | unverified | unverified (search failures → insufficient coverage) |
| `nt-missed-prior-art` | threatened → blocked | **clear (false clear)** — scripted model misjudgment |

Known expected metrics for a clean run:

```
candidate_relationship_accuracy  5/6
claim_status_accuracy            6/7
report_status_accuracy           6/7
false_clear_count                1      (claims expected threatened/unverified
                                         reported clear)
false_clear_rate                 1/5
case_pass_rate                   6/7
evaluator_error_count            0
```

`false-clear` is the critical safety metric: expected
threatened/unverified, but the system reports clear. It is measured at the
claim level by `evaluator.deterministic` and surfaced as
`false_clear_count` / `false_clear_rate` plus
`report.false_negative_counts["false_clear"]`.

## Execution flow

```
load benchmark (immutable artifacts)
→ per case: run production workflow (fixture-composed)
→ collect produced artifacts (store diff)
→ run configured evaluators (failure-isolated; errors persist as error results)
→ per-case status: any deterministic failure → failed; any evaluator error
  → error; else passed
→ aggregate EvaluationReport (metrics across cases)
→ persist EvaluationRun + EvaluationReport + provenance
```

## CLI

```bash
uv run research-agent eval run novelty-threat-v1        # register + run + print metrics
uv run research-agent eval inspect <run-or-report-id>   # per-evaluator detail
uv run research-agent eval list                         # recent runs
```

The benchmark runs fully offline; no API keys are needed.

## Provenance

```
EvaluationReport → EvaluationRun → Benchmark → BenchmarkCases
BenchmarkCases → produced research artifacts → EvaluatorResults
EvaluatorResults → evaluator/model/config metadata
```

Recorded as `derived_from` edges in the artifact store; survives store
reopen. Evaluation never rewrites, supersedes, or deletes production
artifacts — it only adds downstream evaluation artifacts and lineage edges.

## Reproducibility

`EvaluationRun` persists: benchmark version + content hash, per-case
hashes, evaluation config, model roles, produced artifact ids, evaluator
versions, scores/results, token usage, cost, and timestamps. Registration
immutability guarantees historical runs cannot be silently invalidated.
Fixture corpora are part of the immutable case content (case hashes cover
them), so changing a corpus requires a new benchmark version. Re-running a
benchmark produces a new run with identical outcomes; explicit-id fixture
artifacts re-persist idempotently and a content change raises instead of
silently altering history.

## Literature retrieval benchmark (`literature-retrieval-v1`, Phase 6B)

Drives the REAL `literature.search_orchestrator` pipeline:

```
fixture queries → strategy artifact → search orchestration → ingestion
→ PaperIdentity deduplication → LiteratureSearchExecution
```

Cases (6): exact terminology, synonym terminology, multiple relevant papers,
duplicate provider results (same DOI from both providers), irrelevant
high-keyword-overlap paper + sparse metadata, no relevant result.

**Ranking limitation (documented):** the persisted search contract has no
explicit rank field. The ranked result set is the deduplicated first-hit
order of `LiteratureSearchRecord.paper_artifact_ids`; `FixtureLiteratureSource`
returns hits in fixture order, which is the intended rank order. MRR and
precision@k are meaningful under that interpretation only.

Reference `relevant` entries are stable paper keys (DOI or title) resolved to
produced `PaperIdentity` ids by `evaluator.retrieval` — no benchmark-specific
identity store is maintained; dedup runs through the production resolver.

### Retrieval metrics

Per case and aggregated (`k = [5, 10]` from the benchmark config):

- `precision@k`, `recall@k`, `f1@k` — pooled per case (denominator k / relevant set)
- `mrr` — mean reciprocal rank of the first relevant identity (1/rank)
- `duplicate_rate` — `duplicate_records_collapsed / raw_paper_records`
- `relevant_papers_missed`, `irrelevant_papers_retrieved`
- case pass = no relevant paper missed (irrelevant retrieval degrades
  precision but is not a hard failure)

Known expected aggregate metrics (clean run): precision@5 = 1.4/6,
precision@10 = 0.7/6, recall@5 = recall@10 = 5/6, mrr = 5/6,
duplicate_rate = 0.5/6, missed = 0, irrelevant = 3, pass rate 1.0.

## Citation correctness benchmark (`citation-correctness-v1`, Phase 6B)

Drives the REAL Phase 4C formatter:

```
ManuscriptSection → CitationReference → format() → rendered inline citations
→ bibliography → citation_map
```

Cases (10): valid citation; same paper via multiple citation ids; missing
citation id (declared but never cited in the text); citation pointing to a
missing PaperIdentity (the formatter must refuse, not fabricate); sparse
metadata without invention; page locator; anonymous manuscript; multiple
sections citing the same paper; leftover `[CITE:*]` placeholder; citation
mapped to the wrong PaperIdentity.

### Citation metrics

- `citation_resolution_accuracy` — expected citations that received a
  bibliography entry (pooled)
- `unresolved_citation_count` — citation in the map but not in the bibliography
- `bibliography_coverage` — entries with a supporting citation in the text
- `bibliography_deduplication_accuracy` — expected citation-id groups per
  paper identity
- `citation_map_accuracy` — citation id → paper identity matches
- `leftover_placeholder_count`, `unsupported_bibliography_entry_count`
- `invented_bibliographic_field_count` — bibliography fields not present in
  the source PaperRecord; **any invented field is a deterministic failure**
- `inline_citation_accuracy`, `anonymous_review_ok`, `formatter_failure_ok`

Explicit detections: citation in text but not bibliography (leftover /
unresolved), bibliography entry without supporting citation
(`unsupported_bibliography_entry_count`), citation mapped to the wrong
PaperIdentity (`citation_map_accuracy`), invented bibliographic field
(deterministic failure).

Known expected aggregate metrics (clean run): resolution = 11/12, map =
11/12, dedup = 8/9, coverage = 9/9, unresolved = 1, leftovers = 1,
invented = 0, unsupported = 0, pass rate 7/10 (missing-citation-id,
leftover-placeholder, and wrong-mapping cases fail by design).

## Screening benchmark (`literature-screening-v1`, Phase 6C)

Drives the REAL Phase 2D pipeline:

```
ScreeningProtocol (real builder + real approval gate)
→ ScreeningViewBuilderService
→ TitleAbstractScreenerService
→ ScreeningOrchestratorService
→ ScreeningDecision + ScreeningReview + ScreenedLiteratureSet
```

Cases (9): clear include; clear exclude; ambiguous → uncertain (with
review); missing abstract (uncertain, never excluded); conflicting
metadata (2-member identity screened once); duplicate provider identity
screened once; irrelevant keyword overlap; low-confidence review trigger;
technical/model failure not treated as exclusion.

Reference keys are paper titles resolved to produced identities. The
protocol is rebuilt per run with the real approval gate (high-autonomy
`ConfigurableAutonomyPolicy`), so re-runs produce fresh decisions.

### Screening metrics (`evaluator.screening`)

- `screening_accuracy` — correct decisions / expected decisions
- `include_precision` (pooled TP/(TP+FP)), `include_recall`, `include_f1`
- `exclude_accuracy`, `uncertain_accuracy`
- `false_exclusion_rate`, `false_inclusion_rate`
- `review_trigger_accuracy`, `technical_failure_count`

Critical deterministic failures (fail the case): expected include
incorrectly excluded; expected uncertain forced to exclude; a technical
failure counted as an academic exclusion; an identity screened more than
once; unexpected technical failures.

Known expected aggregate metrics: accuracy 8/8, include precision/recall/f1
4/4, exclude 2/2, uncertain 2/2, false rates 0, review accuracy 8/8,
failures 1, pass rate 9/9.

## Evidence extraction benchmark (`evidence-extraction-v1`, Phase 6C)

Drives the REAL Phase 2F pipeline:

```
FullTextCorpus (blob-backed fixture pages)
→ EvidenceExtractorService (real chunking + deterministic page grounding)
→ EvidenceOrchestratorService
→ EvidenceItem + PaperResearchProfile + EvidenceCorpus
```

Cases (9): single-page finding; multi-page evidence; multiple categories in
one document; similar text on wrong page (locator grounding refusal);
unsupported claim (deterministic failure); missing evidence (required
statement missed — deterministic failure); duplicate evidence (production
dedup); partial chunk/model failure (other chunks preserved);
insufficient-text document.

References are semantic: `expected_statements` keyed by statement with
`category`, `valid_pages`, `required`; plus `expected_unsupported`,
`expected_chunk_failures`, `expected_documents_without_evidence`.
Fixture documents are run-unique because the production evidence dedup is
global per document.

### Evidence metrics (`evaluator.evidence`)

- `evidence_precision`, `evidence_recall`, `evidence_f1` (mean per-case)
- `category_accuracy`, `locator_accuracy`
- `required_evidence_recall`
- `unsupported_evidence_rate` — extracted statements not contained in the
  source page text (statement grounding via the blob store)
- `duplicate_evidence_rate`
- `documents_with_required_evidence_missed`, `chunk_failure_count`

Deterministic gating: required evidence missed, unsupported evidence beyond
`expected_unsupported`, chunk failures beyond `expected_chunk_failures`,
category/locator mismatches, and duplicate evidence all fail the case.
Model-assisted evaluators remain advisory and never override these.

Known expected aggregate metrics: precision 7/8, recall 7/8, f1 5/7,
category 7/7, locator 7/7, unsupported rate 1/8, duplicates 0/8, missed 1,
chunk failures 2, pass rate 7/9.

## Gap-analysis benchmark (`research-gap-analysis-v1`, Phase 6D)

Drives the REAL Phase 2H analyzer:

```
LiteratureSynthesis + EvidenceCorpus
→ GapAnalyzerService (one bounded model call + deterministic guards)
→ ResearchGap + GapAnalysis
```

Cases (10): contradiction gap; mechanism gap; context gap; boundary-condition
gap; methodological gap; repeated limitation (strongly supported); weak
evidence → tentative; unsupported/global-novelty claim (fails by design —
the analyzer normalizes sweeping phrasing, the evaluator flags the
unsupported gap); hallucinated evidence/synthesis ID (deterministically
rejected); no defensible gap.

References are semantic (`expected_gaps` by title with type, support counts,
tentativeness; `expected_rank_order`; `expected_hallucinated`;
`expected_unsupported`). Statement/evidence/themes are stable fixture
artifacts; the LiteratureSynthesis is run-unique so analyzer idempotency
never stales re-runs.

### Gap metrics

- `gap_type_accuracy`, `gap_precision`, `gap_recall`, `gap_f1` (mean per-case)
- `grounding_accuracy` (cited ids exist in the fixture sets)
- `corpus_bounded_claim_accuracy` (no sweeping/global-novelty phrasing)
- `support_count_accuracy`, `ranking_accuracy`
- `unsupported_gap_rate`, `hallucinated_reference_count`

Critical failures: hallucinated references; unsupported gap persisted; global
novelty claim as established fact; support-count mismatches; missing/extra
gaps; rank-order mismatches.

Known expected: precision 8/9, recall 8/8, f1 9/10, type 8/8, grounding 8/9,
corpus-bounded 9/9, support 8/8, ranking 2/2, unsupported 1/9, hallucinated 1,
pass rate 9/10.

## Mechanism-development benchmark (`mechanism-development-v1`, Phase 6D)

Drives the REAL Phase 3A pipeline:

```
GapAnalysis → GapSelectionService (model + real approval gate)
→ MechanismGeneratorService (deterministic candidate validation)
→ MechanismCriticService (critique + revision selection)
→ MechanismCandidate + MechanismCritique + SelectedMechanism
```

Cases (10): multiple plausible mechanisms; literature-supported mechanism
(with deterministic support counts); genuinely new hypothesis; modeling
assumption; hallucinated literature support (candidate rejected);
weak gap alignment (quality, not validity); incoherent causal direction
(critic flags, revision fixes); missing actor/incentive; critic requests
revision; invalid candidate rejected while valid candidates survive.

Fixture statements/evidence/gap are run-unique per run and the scripted
responses' ids are rewritten to match, so re-runs are fully fresh.

### Mechanism metrics

- `gap_alignment_accuracy` (candidates trace to the selected gap)
- `knowledge_basis_accuracy` (valid bases + structural discipline)
- `grounding_accuracy`, `candidate_validity_rate`, `candidate_validity_f1`
- `unsupported_support_rate`, `critic_issue_recall`, `revision_success_rate`
- `selected_mechanism_validity`, `invalid_candidates_rejected`

Structural properties always verified: literature_supported elements have
valid source ids; new_hypothesis is never labeled literature_supported;
modeling assumptions remain explicit; the selected mechanism traces to the
selected gap; the original candidate remains immutable after revision.

Known expected: validity 11/11, knowledge basis 11/11, grounding 11/11,
alignment 11/11, unsupported 0/11, critic recall 3/3, revision 11/11,
selected validity 11/11, rejected 2, pass rate 10/10.

## Deterministic vs advisory

`evaluator.gap_analysis` and `evaluator.mechanism` gate pass/fail
deterministically. `evaluator.llm_judge` remains available only for advisory
dimensions (theoretical coherence, mechanism clarity, IS relevance, novelty
within the reviewed corpus) and can never override deterministic failures.

## Equilibrium benchmark (`equilibrium-correctness-v1`, Phase 6E)

Drives the REAL Phase 3C pipeline:

```
FormalAnalyticalModel → EquilibriumDeriverService (SymPy FOCs/BRs,
simultaneous solve or backward induction, symbolic verification, bounded
LLM revision) → EquilibriumAnalysis
```

Cases (10): monopoly/interior optimum `(a-c)/2`; Cournot simultaneous
`(a-c)/3`; sequential leader–follower `(a-c)/2` / `(a-c)/4` with solution
order `[follower, leader]`; parameter-conditioned `(a-c)/(2b)`; incorrect
LLM candidate rejected; zero-payoff/unsolvable model (never treated as
solved); invalid FOC (nonzero residual rejection); partial symbolic
verification (SOC unproven → partially_verified); bounded revision (2
rounds exhausted); denominator/positivity conditions `(ab-c)/(2b)`.

References are known-answer mathematical expectations (e.g. `q* = (a-c)/3`),
compared with **SymPy symbolic equivalence** — never string equality.

### Equilibrium metrics

- `equilibrium_expression_accuracy`, `foc_accuracy` (recomputed residuals),
  `best_response_accuracy` (vs model FOC solutions)
- `verification_accuracy`, `solution_order_accuracy`, `condition_accuracy`
- `unsolvable_detection_accuracy`, `incorrect_candidate_rejection_rate`

Critical failures: incorrect equilibrium marked verified; nonzero FOC
residual at a (partially) verified candidate; wrong sequential order;
required conditions dropped; unsolvable model treated as solved.

Known expected: expression 8/8, FOC 12/12, best response 8/8, verification
10/10, order 11/11, conditions 3/3, unsolvable 10/10, rejection 5/12, pass
rate 10/10.

## Numerical benchmark (`numerical-analysis-v1`, Phase 6E)

Drives the REAL Phase 3E pipeline:

```
verified equilibrium → NumericalAnalysisService (deterministic sweeps,
probes, grids; feasibility + equilibrium-condition enforcement; robustness;
welfare) → NumericalExperiment
```

Cases (9): baseline evaluation (a=10, c=1 → q=3); 1-D sweep (monotonic, 7
points); two-parameter grid (16 points); infeasible domain point (parameter
domain violations, never fabricated); violated equilibrium condition
(`2*b != 0` at b=0 → infeasible); proposition numerically supported;
proposition numerically violated (wrong sign); welfare calculation (2 × 9 =
18); deterministic rerun (engine `sympy+python`, seed 0).

References are explicit parameter values and expected outputs; comparisons
use deterministic floating-point tolerances (1e-6), never rendered strings.

### Numerical metrics

- `numerical_value_accuracy`, `feasibility_classification_accuracy`
- `condition_enforcement_accuracy`, `sweep_accuracy`
- `robustness_classification_accuracy`, `welfare_accuracy`
- `reproducibility_accuracy`

Known expected: values 4/4, feasibility 2/2, condition 9/9, sweep 9/9,
robustness 2/2, welfare 9/9, reproducibility 9/9, pass rate 9/9.

## Known limitations (Phase 6A–6E)

- Benchmarks so far: novelty threat, retrieval, citation, screening,
  evidence extraction, gap analysis, mechanism development, equilibrium
  correctness, numerical analysis. Proposition / manuscript-grounding
  benchmarks are not yet implemented.
- Case pass/fail is gated only by deterministic evaluators; a benchmark with
  only model-assisted evaluators cannot fail a case (by design — LLM judges
  never override deterministic verdicts).
- Retrieval ranking = provider hit order (no explicit rank in the persisted
  contract); sparse papers without identifiers are singletons (production
  behavior) and can only be evaluated by title key.
- The screening benchmark asserts the model-facing behavior through scripted
  decisions (the model itself is not under test); the production refusal and
  review gates are exercised for real.
- The evidence benchmark measures the extractor's locator grounding
  (page-range validation); statement-containment is not enforced by the
  production extractor, so unsupported claims are detected by the evaluator
  (the `ev-unsupported-claim` case fails by design).
- Citation benchmark covers the author-year style path of the formatter
  (abstract generation is disabled via `abstract_required=False`).
- Cost is estimated from token usage × `evaluation.cost_per_million_tokens`
  (0 for offline fixtures).
- No live scholarly corpus, leaderboard, model comparison, auto model
  routing, embedding-based relevance, human annotation UI, or CI quality
  thresholds.

## Recommended next increment

Phase 6F: a **proposition/comparative-statics benchmark** over the Phase 3D
pipeline (verified equilibria → symbolic comparative statics → proposition
generation/verification with deterministic sign checks), reusing the
harness, evaluator contract, and generic aggregation end to end.