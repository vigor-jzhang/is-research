# Evaluation Harness (Phase 6A–7A.1)

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
| `evaluator.comparative_statics` | deterministic | Comparative statics (Phase 6F): derivative/sign/condition/coverage/ambiguous-sign accuracy, overclaim detection |
| `evaluator.proposition` | deterministic | Proposition correctness (Phase 6F): verification, monotonicity/equality recomputation, condition/support integrity, rejection justification |
| `evaluator.results_grounding` | deterministic | Results assembly (Phase 6G): finding/condition/proposition/numerical support, gap alignment, novelty, contradiction detection |
| `evaluator.manuscript_grounding` | deterministic | Manuscript grounding (Phase 6G): claim/citation grounding, conditions, critique recall, revision success |
| `evaluator.pipeline_integrity` | deterministic | End-to-end pipeline integrity (Phase 6H): stage completion, provenance, grounding, conditions, citations, bibliography fidelity |
| `evaluator.synthesis` | deterministic | Literature synthesis (Phase 7A): statement grounding, consensus/contradiction/mixed accuracy, multi-paper support, support counts, unsupported-statement rate, hallucinated references |
| `evaluator.model_specification` | deterministic | Analytical model specification (Phase 7A): symbol table, payoff completeness, decision ownership, timing, information structure, assumption grounding, structural validity, critic issue recall |
| `evaluator.document_acquisition` | deterministic | Document acquisition (Phase 7A): acquisition/extraction success, failure classification, fallback usage, duplicate-blob reuse, corpus availability |
| `evaluator.revalidation` | deterministic | Incremental revalidation (Phase 7A): stale-reuse rate, required recomputation, unchanged reuse, provenance-version accuracy |
| `evaluator.identity_resolution` | deterministic | Ingestion + identity resolution (Phase 7A.1): canonical mapping, duplicate collapse, false-merge/false-split detection, DOI normalization, supersession, partial ingestion |
| `evaluator.gap_selection` | deterministic | Gap selection (Phase 7A.1): selection validity, rationale grounding, alternatives, deterministic fallback, autonomy decision, operator override, rerun reuse |
| `evaluator.novelty_revalidation` | deterministic | Novelty revalidation (Phase 7A.1): revalidation trigger, stale-reuse rate, threat detection, irrelevant-update, supersession, provenance version |
| `evaluator.publication_packaging` | deterministic | Publication packaging (Phase 7A.1): package validation, exports, bibliography integrity, placeholder removal, anonymization, blob persistence, deterministic render |
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

## Comparative statics benchmark (`comparative-statics-v1`, Phase 6F)

Drives the REAL Phase 3D service:

```
verified equilibrium → ComparativeStaticsService (SymPy derivatives,
deterministic sign inference, recorded ambiguity conditions) →
ComparativeStatic artifacts
```

Cases (8): positive derivative `dq/da = 1/2`; negative derivative
`dq/dc = -1/2`; zero derivative (fixed cost drops out of `q* = a/2`);
ambiguous sign (`dq/da = 1/(2b)` — never a definite overclaim); derivative
requiring conditions (`dq/db = -(a-c)/(2b²)` with the sign dependency
recorded); multiple outcomes/parameters (Cournot: 4 statics); incorrect
expected derivative (reference asserts `dq/da = 2` — fails by design);
unused parameter (zero static derived anyway).

References are known closed forms, compared with **SymPy symbolic
equivalence**; the evaluator re-derives every derivative from the produced
candidate itself (defensive recomputation, never trusting the static's
string).

### Comparative statics metrics

- `derivative_accuracy`, `sign_accuracy`
- `condition_preservation_accuracy` (dropped or spurious conditions)
- `outcome_parameter_coverage`
- `ambiguous_sign_accuracy`

Critical failures: wrong derivative; wrong sign; definite sign asserted
when the derivative is ambiguous; conditions dropped (or spurious);
expected outcome/parameter pair missing.

Known expected: derivatives 11/12 (1 by-design wrong reference), signs
12/12, conditions 12/12, coverage 12/12, ambiguous 2/2, pass rate 7/8.

## Proposition benchmark (`proposition-correctness-v1`, Phase 6F)

Drives the REAL Phase 3D proposition pipeline:

```
verified equilibrium → ComparativeStaticsService → PropositionGeneratorService
(scripted responses) → PropositionVerifierService (symbolic checks) +
PropositionCriticService
```

Cases (10): correct positive monotonicity; correct negative monotonicity;
zero-effect proposition; conditional proposition (ambiguous static with
declared `b > 0` → conditionally_verified); wrong-sign proposition rejected;
missing-condition proposition rejected; valid equilibrium equality
(`q1 = q2`); invalid equality (`q1 = 2*q2`) rejected; hallucinated
comparative-static ID rejected on equilibrium consistency; unsupported
threshold claim rejected.

The evaluator recomputes monotonicity signs and equality differences from
the produced candidate expressions and re-checks every supporting id
against the produced statics of the same candidate.

### Proposition metrics

- `proposition_verification_accuracy`, `monotonicity_accuracy`
- `equality_accuracy`, `condition_accuracy`
- `support_reference_accuracy`
- `incorrect_proposition_rejection_rate`

Critical failures: incorrect proposition marked verified; wrong symbolic
derivative/sign; missing required conditions; hallucinated support ids;
invalid equality accepted; valid equality rejected.

Known expected: verification 10/10, monotonicity 7/7, equality 2/2,
conditions 1/1, support 10/10, rejection 5/10 (pooled), pass rate 10/10.

## Results benchmark (`results-assembly-v1`, Phase 6G)

Drives the REAL Phase 4A pipeline:

```
verified Phase 3 outputs → ResultsAssemblerService (deterministic validation:
failed-proposition rejection, unsupported-id rejection, condition
preservation, global-novelty normalization) → ResearchResultsPackage →
ResultsCriticService (deterministic contradiction + novelty + gap checks)
```

Cases (10): correctly grounded analytical finding; conditional proposition
with conditions preserved; numerical robustness support; symbolic/numerical
contradiction surfaced by the critic's deterministic check; failed
proposition rejected as support (validation + retry); unsupported artifact
id rejected (validation + retry); valid theoretical contribution; weak
gap/contribution link (out-of-range finding reference rejected; critic
surfaces the concern); global novelty claim normalized; unsupported
managerial implication persisted (fails by design).

The evaluator recomputes ground truth: support ids are re-checked against
the produced verified propositions/statics/results, required conditions are
re-derived from the cited supports, novelty phrasing is re-scanned, and
implication grounding is re-verified.

### Results metrics

- `finding_grounding_accuracy`, `condition_preservation_accuracy`
- `proposition_support_accuracy`, `numerical_support_accuracy`
- `contribution_gap_alignment_accuracy`, `implication_grounding_accuracy`
- `novelty_claim_accuracy`, `contradiction_detection_accuracy`
- `unsupported_claim_rate`

Critical failures: failed proposition used as valid support; unsupported
finding persisted; required conditions dropped; contradiction hidden;
global novelty persisted as fact; contribution without valid gap/finding
support; unsupported implication persisted.

Known expected: grounding 10/10, conditions 10/10, proposition support 9/9,
numerical support 1/1, alignment 10/10, implications 9/10, novelty 10/10,
contradiction 1/10 (pooled), unsupported 1/20, pass rate 9/10.

## Manuscript benchmark (`manuscript-grounding-v1`, Phase 6G)

Drives the REAL Phase 4B pipeline:

```
ResearchResultsPackage + literature artifacts → ManuscriptDrafterService
(deterministic outline; scripted section drafts; validation rejects
unsupported claims, missing/hallucinated citations, failed-proposition
grounding; normalizes novelty) → ManuscriptCriticService (deterministic
checks) → optional revision (flagged sections re-drafted, others reused)
```

Cases (11): grounded literature claim with citation; grounded mathematical
claim; proposition condition preserved in prose; unsupported literature
claim rejected; missing citation rejected; hallucinated citation id
rejected; failed proposition presented as result rejected; novelty overclaim
normalized; gap/contribution inconsistency flagged by the critic; omitted
limitations flagged by the critic; revision re-drafts the flagged section
(unused citation repaired) while reusing unaffected sections by id.

The citation boundary stays clear of Phase 6B: 6G verifies
`ManuscriptClaim → CitationReference → EvidenceItem/research artifact`
referential integrity only; Phase 6B remains the author-year formatting
benchmark.

### Manuscript metrics

- `claim_grounding_accuracy`, `literature_citation_coverage`
- `mathematical_claim_accuracy`, `condition_preservation_accuracy`
- `unsupported_claim_rate`, `citation_reference_accuracy`
- `novelty_claim_accuracy`, `section_consistency_accuracy`
- `critique_issue_recall`, `revision_success_rate`

Critical failures: hallucinated artifact/citation references; unsupported
literature claim; failed proposition presented as verified; missing
proposition conditions; unsupported novelty; revision that fails to repair
a required deterministic issue.

Known expected: grounding 15/15, citations 6/6, mathematical 5/5,
conditions 5/5, unsupported 0/15, citation references 6/6, novelty 15/15,
consistency 15/15, critique recall 3/3, revision 1/1, pass rate 11/11.

## End-to-end benchmark (`research-pipeline-e2e-v1`, Phase 6H)

Drives the real production chain across every representative stage:

```
literature retrieval → screening → evidence extraction → synthesis →
gap analysis → mechanism → analytical model → equilibrium →
propositions → numerical analysis → results assembly →
manuscript grounding → citation formatting
```

over a small deterministic fixture corpus with scripted model responses.
The pipeline-integrity evaluator re-derives ground truth from the produced
artifacts and gates on: missing stages; broken provenance links; unsupported
evidence/claims; invalid equilibrium expressions; conditions lost
downstream; wrong citation identity; invented bibliography metadata; and
numerical disagreement with known expectations.

### Pipeline metrics

- `stage_completion_rate` 13/13, `provenance_integrity_rate` 16/16
- `grounding_integrity_rate` 14/14, `condition_preservation_rate` 2/2
- `citation_integrity_rate` 1/1, `bibliography_fidelity_rate` 1/1
- `deterministic_failure_count` 0, `end_to_end_pass` 1.0

## Literature synthesis benchmark (`literature-synthesis-v1`, Phase 7A)

Drives the real Phase 2G synthesizer over a fixture `EvidenceCorpus` (papers +
`EvidenceItem` + `PaperResearchProfile`):

```
EvidenceCorpus → LiteratureSynthesizerService → SynthesisStatement →
SynthesisTheme → LiteratureSynthesis
```

8 cases: multi-paper consensus; contradiction preserved with both sides;
mixed evidence; single-paper observation not treated as consensus;
boundary-condition pattern; methodological pattern; hallucinated evidence id
rejected; unsupported (no-paper-mapping) statement rejected. The synthesizer
deterministically rejects hallucinated ids and paper-less statements, and
computes support metrics (`support_type`, `papers_supporting`) itself.

### Synthesis metrics (`evaluator.synthesis`)

- `statement_grounding_accuracy` — every supporting/conflicting evidence id exists in the corpus
- `consensus_accuracy`, `contradiction_accuracy` — expected typed statements produced with the expected type
- `multi_paper_support_accuracy` — ≥2-paper statements marked `multi_paper`
- `support_count_accuracy` — `papers_supporting` / `papers_conflicting` match
- `unsupported_statement_rate` — statements referencing missing evidence ids
- `hallucinated_reference_count` — distinct missing ids referenced

## Analytical model specification benchmark (`analytical-model-specification-v1`, Phase 7A)

Drives the real Phase 3B pipeline:

```
SelectedMechanism → ModelBuilderService → FormalAnalyticalModel →
ModelSpecificationCriticService
```

9 cases: valid strategic model; undefined symbol; duplicate symbol; invalid
decision ownership; invalid timing; invalid information structure;
unsupported literature-backed assumption; missing payoff for a strategic
actor (by-design deterministic failure detected by `payoff_completeness`);
critic-detected mechanism/model mismatch.

### Model-specification metrics (`evaluator.model_specification`)

- `symbol_table_accuracy`, `payoff_completeness`, `decision_ownership_accuracy`
- `timing_accuracy`, `information_structure_accuracy`, `assumption_grounding_accuracy`
- `structural_validity_accuracy` — produced created/rejected matches the reference
- `critic_issue_recall` — expected critique issue categories detected

## Document acquisition benchmark (`document-acquisition-v1`, Phase 7A)

Drives the real Phase 2E pipeline with a mocked `httpx` transport (no
network): metadata locator → HTTP fetcher → blob store → pypdf extractor →
acquisition orchestrator → `FullTextCorpus`. 8 cases: valid OA PDF; fallback
location; no location; HTML masquerading as PDF; oversized document;
restricted/unavailable document; duplicate-blob reuse; insufficient extracted
text.

### Acquisition metrics (`evaluator.document_acquisition`)

- `acquisition_success_rate`, `extraction_success_rate`
- `failure_classification_accuracy` — per-paper status matches the reference
- `fallback_usage_accuracy` — successful acquisition used a non-first location
- `duplicate_blob_reuse_accuracy` — same location+bytes reuse one acquisition
- `corpus_availability_accuracy` — available/unavailable/restricted/failed

## Incremental revalidation benchmark (`incremental-revalidation-v1`, Phase 7A)

Drives REAL production services twice per stage (baseline + changed upstream)
and records whether the downstream execution was recomputed or deterministically
reused. 7 cases: new ScreeningProtocol → new decisions; superseding
PaperIdentity → new screening view; model role/config change → new evidence
execution; changed EvidenceCorpus → new synthesis; changed synthesis → new gap
analysis; changed model specification → new equilibrium analysis; unchanged
inputs → deterministic reuse. Any stale reuse of incompatible upstream state is
a deterministic failure.

### Revalidation metrics (`evaluator.revalidation`)

- `stale_reuse_rate` — changed-upstream stages that reused incompatible state
- `required_recomputation_accuracy` — changed stages that produced new artifacts
- `unchanged_reuse_accuracy` — identical stages that reused deterministically
- `provenance_version_accuracy` — new downstream derived (transitively) from the changed upstream

## Ingestion + identity benchmark (`literature-ingestion-identity-v1`, Phase 7A.1)

Drives the real Phase 2B/2C pipeline over fixture provider sources:

```
ProviderRecordSnapshot → PaperRecord → LiteratureSearchRecord → PaperIdentityResolver
```

8 cases: same DOI across providers; normalized DOI variants
(`https://doi.org/…`, `doi:…`, bare); shared strong identifier (arXiv); exact-content
duplicate; similar-title without a strong identifier stays separate; sparse metadata;
provider failure with partial ingestion; identity supersession when a new member
appears. False semantic merges deterministically fail.

### Identity-resolution metrics (`evaluator.identity_resolution`)

- `canonical_mapping_accuracy`, `duplicate_collapse_accuracy`
- `false_merge_rate`, `false_split_rate` — false semantic merges are failures
- `identifier_normalization_accuracy`, `supersession_accuracy`, `partial_ingestion_accuracy`

## Gap-selection benchmark (`gap-selection-v1`, Phase 7A.1)

Drives the real Phase 3A `GapSelectionService` over a fixture `GapAnalysis` + ranked
`ResearchGap`s (model selection or operator override + autonomy checkpoint). 8 cases:
select rank #1 when clearly strongest; valid non-rank-1 selection; operator override;
invalid model-selected gap → deterministic fallback; autonomy approval; autonomy
rejection; unsupported gap id rejected; deterministic rerun reuse. No subjective
"best gap" is scored unless the fixture defines the expected decision.

### Gap-selection metrics (`evaluator.gap_selection`)

- `selected_gap_validity`, `selection_rationale_grounding`
- `alternative_consideration_accuracy`, `fallback_accuracy`
- `autonomy_decision_accuracy`, `operator_override_accuracy`, `reuse_accuracy`

## Novelty revalidation benchmark (`novelty-revalidation-v1`, Phase 7A.1)

Runs the real Phase 5A/5B `NoveltyValidationService.create_report` twice — once
against baseline fixture sources, once against changed sources — and compares the
two reports. 7 cases: unchanged literature → prior novelty reusable; new directly
relevant paper → revalidation required; new contradictory evidence; new paper
covering the claimed mechanism/gap; irrelevant new paper does not invalidate
novelty; stale novelty artifact is not silently reused; superseding assessment
preserves history. Any incompatible stale novelty reuse is a deterministic failure.

### Novelty-revalidation metrics (`evaluator.novelty_revalidation`)

- `revalidation_trigger_accuracy`, `stale_reuse_rate`
- `novelty_threat_detection_accuracy`, `irrelevant_update_accuracy`
- `supersession_accuracy`, `provenance_version_accuracy`

## Publication / packaging benchmark (`publication-packaging-v1`, Phase 7A.1)

Drives the real Phase 4C pipeline: `ManuscriptDraft → formatter → bibliography →
validate → exporters (Markdown/LaTeX/DOCX/PDF → BlobStore) → SubmissionPackage`.
8 cases: correct citation resolution; unresolved citation blocks readiness;
bibliography dedup; missing metadata not invented; anonymous-review mode; leftover
internal placeholder; Markdown/LaTeX/DOCX/PDF artifact generation; export BlobStore
persistence; deterministic rerender; invalid package not marked publication-ready.
Reuses Phase 6B citation expectations conceptually without duplicating that
evaluator.

### Packaging metrics (`evaluator.publication_packaging`)

- `package_validation_accuracy`, `export_success_accuracy`, `bibliography_integrity`
- `placeholder_removal_accuracy`, `anonymization_accuracy`
- `blob_persistence_accuracy`, `deterministic_render_accuracy`

## Coverage matrix

`research_harness/research/evaluation_coverage.py` maps every production
capability → benchmark → evaluator → metrics → deterministic/advisory gating
→ covered edge cases → known gaps, across all of 6A-7A.1 (22 benchmarks). Missing
coverage is explicit: `uncovered_capabilities()` lists capabilities with no
dedicated benchmark (live provider connectors, evidence enrichment/
pre-acquisition standalone, advisory proposition-critique quality).

## Evaluation readiness

`research_harness/research/evaluation_readiness.py` generates a report with
deterministic criteria (benchmark inventory, evaluator inventory, metric
coverage, deterministic gating per benchmark, by-design failing cases, known
untested behaviors, uncovered capabilities, reproducibility status,
provenance/reopen coverage, live-test coverage, model-assisted evaluator
usage) and a deterministic verdict: `ready` / `ready_with_gaps` /
`not_ready` — never an LLM judgment. Current verdict: `ready_with_gaps`
(all 22 benchmark families deterministically gated; the four targeted
deterministic gaps from 7A are closed; residual gaps are non-blocking:
live provider/publisher behavior, evidence-enrichment standalone, and
advisory LLM-quality judging, so `ready` is not forced).

## Known limitations (Phase 6A–7A.1)

- Benchmarks: novelty threat, retrieval, citation, screening, evidence
  extraction, gap analysis, mechanism development, equilibrium correctness,
  numerical analysis, comparative statics, proposition correctness, results
  assembly, manuscript grounding, research-pipeline e2e, literature
  synthesis, analytical model specification, document acquisition,
  incremental revalidation, literature ingestion/identity resolution, gap
  selection, novelty revalidation, and publication packaging. Leaderboards,
  live corpora, model tournaments, automated model selection, and
  publication-quality scoring are not implemented.
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
- No live scholarly corpus, model comparison, embedding-based relevance,
  human annotation UI, or CI quality thresholds.

## Model tournaments + role leaderboards (Phase 7B)

Phase 7B adds a reproducible model-comparison layer on top of the frozen
harness. It answers "which model is best for each logical role" without
changing a single benchmark definition or evaluator and without automatic
routing.

### Execution model

```
TournamentPlan (YAML, no code changes)
  -> for each candidate model:
       CandidateModelRouter binds role -> candidate model
         (all other roles fall back to the production role router)
       -> run_benchmark over the existing frozen benchmarks x repetitions
          (generic harness: workflow -> evaluators -> EvaluationReport)
       -> collect per-call usage/latency at the model boundary + reports
  -> aggregate TournamentModelResult (dimensions stay visible)
  -> deterministic RoleLeaderboard (lexicographic, documented ranking)
```

Candidate binding exists only inside the router instance for the duration of
the run — the global user config is never modified.

### Schemas

- `TournamentPlan` — plan_id, role, benchmark_ids, candidate models
  (provider / requested model / temperature / max tokens / structured-output
  mode / optional pricing), repetitions, timeout/retry policy, deterministic
  pass threshold, optional evaluator + advisory evaluator ids, ranking rules.
- `TournamentModelConfig` — exact model identity + config + `TournamentPricing`
  (pricing source/version, input/output per-million rates).
- `ModelCallRecord` — one model call measured at the model boundary: resolved
  model id, latency, prompt/completion/total tokens, cost (+ source), status,
  failure classification, retries.
- `TournamentModelResult` — aggregated per candidate across
  benchmarks × repetitions: every rate plus latency mean/p50/p95, tokens,
  estimated cost, cost-per-successful case/benchmark, advisory score,
  failure counts, and the exact `BenchmarkRunRef`s (EvaluationRun ids).
- `TournamentRun` — immutable record: plan hash + full snapshot, benchmark
  ids/versions, model ids/config, repetitions, ranking rules, all
  EvaluationRun ids, runtime/usage metadata.
- `RoleLeaderboard` / `LeaderboardEntry` — immutable leaderboard exposing every
  raw dimension (model, eligibility, pass rates, reliability, latency, tokens,
  cost, advisory score).

### Benchmark → role mapping

| Role | Benchmarks |
| --- | --- |
| `fast` | literature-screening-v1 (screener decisions) |
| `reasoning` | evidence-extraction-v1, literature-synthesis-v1, research-gap-analysis-v1, mechanism-development-v1, analytical-model-specification-v1, proposition-correctness-v1, results-assembly-v1, manuscript-grounding-v1 |
| `critic` | mechanism-development-v1, analytical-model-specification-v1, proposition-correctness-v1, results-assembly-v1, manuscript-grounding-v1 (the critique passes) |

A benchmark can belong to several roles; a tournament binds the candidate to
exactly one role and holds every other role at the configured defaults.

### Metrics

- Deterministic: `deterministic_pass_rate` (passed / (passed + failed)),
  `benchmark_pass_rate`, `case_pass_rate`.
- Reliability: `structured_output_success_rate`, `model_error_rate`
  (timeout/provider_error/rate_limit/validation_failure),
  `retry_rate`.
- Latency (model boundary only, never fixture/setup time):
  `latency_ms_mean`, `latency_ms_p50`, `latency_ms_p95`.
- Tokens: `input_tokens`, `output_tokens`, `total_tokens`.
- Cost (never invented): provider-returned usage cost wins; else computed from
  configured pricing (source/version/rates recorded); else `None` + caveat.
  `estimated_cost`, `cost_per_successful_case`, `cost_per_successful_benchmark`.
- Advisory: `advisory_score` from optional advisory evaluators.

### Ranking (deterministic, lexicographic)

1. eligibility (deterministic_pass_rate ≥ plan threshold)
2. deterministic_pass_rate, 3. benchmark_pass_rate
4. model_error_rate (asc), structured_output_success_rate (desc), retry_rate (asc)
5. latency_ms_p50 (asc)
6. cost_per_successful_case (asc), total_tokens (asc)
7. advisory_score (desc); tie-break by candidate_id.

Cost/latency never outrank deterministic correctness. None = unknown = worst.
The hierarchy is persisted with every leaderboard as `ranking_rules`.

### Reproducibility

Every TournamentRun preserves the plan id/hash/snapshot, benchmark ids +
versions, model ids/config, exact EvaluationRun ids, repetitions, runtime and
usage metadata, and the ranking rules. Historical runs and leaderboards are
immutable; a later tournament creates a new leaderboard.

### CLI

```bash
uv run research-agent evaluation tournament run --plan <plan.yaml>
uv run research-agent evaluation tournament inspect <run-id>
uv run research-agent evaluation leaderboard show --role reasoning
uv run research-agent evaluation leaderboard list
uv run research-agent evaluation leaderboard inspect <leaderboard-id>
```

Candidates and pricing are defined in the plan YAML — no code changes. See
`configs/tournament/example-reasoning.yaml`.

## Evidence enrichment benchmark (`evidence-enrichment-v1`, Phase 7C)

Closes the Phase 5C-5D coverage gap. Drives the real `NoveltyValidationService`
with `enrichment_enabled=True` and `preacquisition_enabled=True` over fixture
sources whose `get()` serves acquired abstracts. 7 cases:

- title-only candidate enriched to abstract
- indexed-metadata candidate enriched to abstract
- unsupported enrichment rejected (never fabricated evidence)
- rate-limited source rejected without invention
- pre-acquisition selects and upgrades sparse candidates
- original sparse source preserved
- changed source set does not reuse stale enrichment

### Enrichment metrics (`evaluator.evidence_enrichment`)

- `enrichment_grounding_accuracy`, `enrichment_outcome_accuracy`
- `source_preservation_accuracy`, `unsupported_rejection_accuracy`
- `stale_reuse_rate`, `preacquisition_accuracy`, `provenance_version_accuracy`

## Policy-constrained model routing (Phase 7C, shadow mode)

The router answers "given a logical role, quality requirement, budget, latency
target, and leaderboard evidence, which model should be selected?" It is
decision support + shadow evaluation only — it never replaces the configured
production role model and never uses an LLM to choose.

### Selection hierarchy

1. capability compatibility (structured output / context) — reuses provider
   `ModelCapabilities`
2. deterministic eligibility threshold
3. reliability requirements (structured-output success, model-error rate)
4. explicit constraints (allowed models/providers, max cost, latency limit,
   min repetitions, leaderboard freshness)
5. quality → 6. latency → 7. cost → 8. deterministic tie-break.

Correctness is never traded for lower cost unless the request explicitly
lowers the required quality threshold.

### Policies

`quality_first`, `balanced`, `cost_constrained`, `latency_constrained` — each is
an explicit, documented policy (gate + rank) persisted with every decision via
`RoutingDecision.policy_rules`. `cost_constrained` first filters by minimum
deterministic quality (reliability mandatory) then chooses the lowest expected
cost among eligible candidates.

### Evidence handling

Missing/stale evidence is never a silent choice: no leaderboard, stale
(older than `leaderboard_max_age_seconds`), candidate absent, unknown cost,
insufficient repetitions, or all-below-threshold return
`insufficient_evidence` / `no_eligible_model` with structured rationale.

### Fallbacks

Every production-capable decision defines a deterministic fallback: the
next-best eligible candidate satisfying the same gate (or `None` when none
exists), reported as `fallback_candidate_id`.

### Shadow mode

`routing_mode = shadow` computes the model the router would select, records the
decision, and compares it against the configured role model: `would_switch`,
`same_as_current`, `expected_quality_delta`, `expected_cost_delta`,
`expected_latency_delta`. No production behavior changes.

### Routing schemas

`RoutingPolicy`, `RoutingRequest`, `RoutingCandidateAssessment`,
`RoutingDecision` (selected/rejected/fallback candidates, leaderboard ids,
policy id/version + rules, expected quality/latency/cost, structured rationale,
shadow comparison), `RoutingExecution`.

### Routing benchmark (`model-routing-policy-v1`)

12 cases over synthetic RoleLeaderboard fixtures: quality-first choice,
cheap-failing rejection, cost-constrained, latency-constrained,
structured-output capability rejection, missing-cost, stale leaderboard,
insufficient repetitions, no eligible candidate, deterministic tie-break,
fallback selection, role isolation. Metrics (`evaluator.model_routing`):
`routing_decision_accuracy`, `eligibility_filter_accuracy`,
`constraint_satisfaction_accuracy`, `fallback_accuracy`,
`role_isolation_accuracy`, `stale_evidence_handling_accuracy`,
`deterministic_tiebreak_accuracy`, `unsafe_selection_rate` (any selection of a
deterministically ineligible model is a critical failure).

## Live-quality validation + production-routing readiness (Phase 7D.0)

Scripted-fixture benchmarks are not valid live-quality proxies. Phase 7D.0 adds
trustworthy live-quality evidence for routing readiness before any production
activation (which remains off).

### Live-quality benchmarks

- `live-quality-reasoning-v1` — evidence extraction, synthesis, gap analysis,
  mechanism generation, model specification, proposition generation over the
  REAL production pipelines with realistic, model-agnostic inputs and
  structural references. `evaluator.live_quality_reasoning` measures
  structured-output success, grounding correctness, unsupported-reference rate,
  instruction adherence, required-field completeness, deterministic downstream
  validation (e.g. proposition verification), and task completion.
- `live-quality-critic-v1` — mechanism/model/proposition/results/manuscript
  critique over the REAL critic services with reference cases containing known
  INJECTED DEFECTS. `evaluator.live_quality_critic` measures defect_recall,
  false_positive_rate, severity_accuracy, required-category coverage,
  actionable_revision_rate, structured-output success.
- `live-quality-fast-v1` — real screening pipeline fast-role decisions.
  `evaluator.live_quality_fast` measures decision accuracy, uncertain-case
  handling, false-exclusion rate, structured-output success (latency/cost are
  aggregated by the service from call records).

Inputs live in the immutable benchmark definitions (small frozen realistic
corpus); references are semantic/structural (valid ids/pages, required
concepts, allowed gap types, injected defects, expected decision class,
required mathematical structure) — never exact natural-language matching.

### Artifacts (`research/schemas/live_quality.py`)

`LiveQualityTaskResult` (per repetition), `LiveQualityModelResult` (mean/worst/
variance pass rates, structured-output failure frequency, provider-error
frequency, critical grounding failures, latency/tokens/cost),
`LiveQualityRun`, `QualificationCriteria`, `RoutingReadinessAssessment`
(qualified, reasons, qualified/fallback models, configured model,
`unsafe_production_qualification` always False).

### Repetitions / reliability

Configurable repetitions (default 3). Mean/worst-case pass rate + variance,
structured-output failure frequency and provider-error frequency are tracked.
A single successful call never qualifies production routing.

### Production qualification rules (`research/routing/readiness.py`)

Role-specific `QualificationCriteria` (fast is stricter than reasoning/critic):
min deterministic quality, min structured-output success, max provider-error
rate, min repetitions, no critical grounding failures, min cases, evidence
freshness. Live-quality evidence (`RoleLeaderboard.evidence_type ==
live_quality_evidence`) is REQUIRED — offline fixture tournaments alone can
never authorize routing.

### Routing readiness benchmark (`production-routing-readiness-v1`)

9 offline cases: no live evidence, below threshold, qualified, critical
grounding failure, high provider-error rate, stale live evidence, role evidence
mismatch, qualified primary + fallback, no qualified fallback.
`evaluator.routing_readiness` with the critical metric
`unsafe_production_qualification_rate = 0`.

### CLI

```bash
uv run research-agent evaluation live-quality run --role reasoning --repetitions 3
uv run research-agent evaluation live-quality inspect <run-id>
uv run research-agent routing readiness --role reasoning
uv run research-agent eval run production-routing-readiness-v1
```

Production routing is never enabled automatically: each role reports
ready/not_ready; Phase 7D (controlled activation) is a separate future phase.

## Live-model qualification campaigns (Phase 7D.1)

Identifies production-qualified primary/fallback per role from live-quality
evidence. Production switching stays disabled.

### Campaign flow

```
config candidate set (live_quality.candidates[role])   # no slugs in service logic
  -> for each candidate: live-quality benchmark (>=3 reps) -> LiveQualityModelResult
  -> RoleLeaderboard (live_quality_evidence)
  -> qualify_candidate (reuses Phase 7D.0 QualificationCriteria exactly, never loosened)
  -> primary/fallback among qualified models (quality/reliability first, then
     latency/cost per policy; an unqualified cheap model can never win)
  -> RoleQualificationSummary + QualificationCampaign (persisted)
```

### Artifacts (`research/schemas/qualification.py`)

`QualificationCampaign`, `QualificationCandidateResult` (structured
`rejection_kinds`), `RoleQualificationSummary` (status = `qualified` |
`qualified_without_fallback` | `no_qualified_model`; primary/fallback).

### Rejection kinds

`below_quality_threshold`, `structured_output_failure`,
`critical_grounding_failure`, `provider_error_rate`,
`insufficient_repetitions`, `stale_evidence`, `capability_mismatch`. Aggregate
rejection counts are reported per campaign.

### Stability

>=3 repetitions required; mean/worst/variance pass rates + critical failure
frequency reported. A single lucky run never qualifies.

### Qualification benchmark (`model-qualification-policy-v1`)

10 offline cases over the real qualification algorithm: qualified primary +
fallback, primary-without-fallback, none qualified, critical-grounding despite
mean pass, insufficient repetitions, stale evidence, role mismatch, cheaper
unqualified loses, deterministic tie, borderline stays unqualified.
`evaluator.model_qualification` with critical metric
`unsafe_model_qualification_rate = 0`.

### CLI

```bash
uv run research-agent routing qualify --role reasoning --repetitions 3
uv run research-agent routing qualification inspect <campaign-id>
uv run research-agent routing qualification summary
uv run research-agent eval run model-qualification-policy-v1
```

## Qualification expansion + benchmark calibration (Phase 7D.2)

Expands candidate pools per role and verifies that failures reflect genuine
model/provider capability rather than benchmark defects. Production switching
stays disabled; thresholds are never loosened.

### Candidate pools (`live_quality.candidates` in config)

fast 4 candidates, reasoning 5, critic 4 (config-driven, no slugs in service
logic). Campaigns run >=3 repetitions per candidate (5 supported for borderline
candidates); a single run never qualifies.

### Benchmark calibration audit

`research-agent eval calibration` runs a model-independent audit per live-quality
benchmark (persisted as `benchmark_calibration_audit`): valid model-agnostic
reference, achievable structured schema, no fixture leakage, no impossible
evidence requirement, deterministic evaluator correctness, realistic context
size, valid grounding ids/pages, and no provider-specific assumptions.
Confirmed benchmark/evaluator defects are excluded from qualification (only
genuine model/provider outcomes count).

### Structured failure attribution

Each failed case is attributed to `model_reasoning_failure`,
`structured_output_failure`, `grounding_failure`,
`instruction_following_failure`, `provider_error`, `timeout`, `rate_limit`,
`benchmark_reference_defect`, `evaluator_defect`, or `infrastructure_failure`.
`benchmark_reference_defect`/`evaluator_defect` are excluded from the
critical-grounding gate.

### Per-task diagnostics

`LiveQualityModelResult.task_performance` records per-task (per-case) pass
rate mean/worst/variance, critical grounding failures, and failure attribution
across repetitions — a single aggregate score is insufficient. Reasoning
task-level breakdown: evidence extraction, literature synthesis, gap analysis,
mechanism generation, model specification, proposition generation. Fast-role
diagnostics: decision accuracy, false exclusions, uncertain handling,
structured-output reliability.

### Stability + ProductionQualificationMatrix

Qualified candidates get `stable` | `borderline` | `unstable` (worst
repetition below the threshold, critical grounding, or provider-error above the
cap -> unstable). Unstable candidates are never primary/fallback eligible.
`routing qualification matrix` persists the `ProductionQualificationMatrix`
(role, candidate, qualified, stability, primary_eligible, fallback_eligible,
repetitions, rejection reasons, live-quality run ids) — the Phase 7D activation
input. Qualified candidates are compared only among themselves (quality/
reliability first, then latency/cost); unqualified candidates are never ranked
above qualified ones; raw dimensions are preserved.

### Qualification benchmark (`model-qualification-policy-v1`, 16 cases)

Adds to the Phase 7D.1 cases: benchmark defect excluded from model failure,
evaluator defect excluded from model failure, borderline candidate requiring
extra repetitions, unstable candidate rejected (not eligible), qualified
primary + qualified fallback, and role-specific partial qualification via the
cross-role matrix. `evaluator.model_qualification` v0.2 adds
`stability_classification_accuracy` and `eligibility_accuracy`; the critical
`unsafe_model_qualification_rate` stays 0.

### CLI

```bash
uv run research-agent eval calibration
uv run research-agent routing qualify --role fast --repetitions 3
uv run research-agent routing qualification matrix
uv run research-agent routing qualification inspect <campaign-id>
uv run research-agent eval run model-qualification-policy-v1
```

## Strong-model expansion + task-specific qualification (Phase 7D.3)

Expands the candidate pools (reasoning 8, critic 6, fast 6 — including paid
models where configured) and adds task-specific qualification that reuses the
exact role thresholds (never relaxed). A model may be `qualified_for_task`
without being qualified for the whole role. Production switching stays disabled.

### Task qualification

Canonical tasks per role (see `research/routing/tasks.py`): reasoning —
evidence_extraction, synthesis, gap_analysis, mechanism_generation,
model_specification, proposition_generation; critic — mechanism_critique,
model_specification_critique, proposition_critique, results_critique,
manuscript_critique; fast — screening. Each model gets a
`TaskQualificationResult` per task (repetitions, det mean/worst/variance,
structured-output, provider-error, critical grounding, latency/tokens/cost,
qualified, rejection reasons) and the role gets a `TaskQualificationMatrix`
(qualified_models_by_task, ranked_models_by_task, qualified_tasks_by_model,
role_qualified_models). Per-task ranking considers only qualified models:
correctness, reliability (worst), structured-output, latency, cost,
deterministic tie-break.

### Evidence-extraction diagnostics

`evidence_extraction_diagnostics` persists per-model counts of hallucinated
evidence IDs, wrong page locators, unsupported claims, invalid categories,
missing required evidence, and malformed structured output — diagnostic only,
never changes the evidence benchmark to make models pass.

### Calibration audit extension

The 7D.2 audit now also validates every critic fixture against its artifact
schema. This exposed three genuine fixture defects (results_critique
`finding_type` value, research_results_package `status` value, manuscript
citation missing `evidence_item_id`) that had silently errored
lq-results-critique and lq-manuscript-critique for every model since 7D.0 and
inflated the critic deterministic pass rate (errors were excluded from the
denominator). All three were repaired; the audit now passes with zero confirmed
defects, and the honest critic result is no_qualified_model.

### CLI

```bash
uv run research-agent routing qualify --role reasoning --repetitions 3
uv run research-agent routing qualify-task --role reasoning --task evidence_extraction
uv run research-agent routing qualification tasks --role reasoning
uv run research-agent routing capability-profile <model-id>
uv run research-agent eval run task-specific-model-qualification-v1
```

## Focused model qualification (Phase 7D.3A)

Resolves the remaining capability blockers before task-aware routing.

### Evidence-ID interface audit (conclusion: option B — no change needed)

The production evidence extractor returns `EvidenceCandidate`
(category/statement/page_numbers/confidence/excerpt) with **no artifact IDs**;
`EvidenceOrchestratorService._persist_evidence` assigns `source_artifact_id`
(the FullTextDocument id) and the EvidenceItem id deterministically after
validation. So the model never invents internal UUIDs — the interface is
correct.

### Genuine evaluator defect found and repaired

The live-quality reasoning evaluator iterated scalar reference fields
(`source_artifact_id`, `gap_id`, `selected_mechanism_id`, `model_id`, ...)
character-by-character (a string was treated as a list of characters), so every
model since 7D.0 looked like it hallucinated evidence IDs (e.g. 296
"unsupported reference 'l','q','-',..."). Repaired: scalar fields are treated as
single references; only list fields iterate. With the fix, evidence grounding
failures collapse (gemini-2.5-pro: 558 -> 3) and **evidence_extraction now
qualifies for nemotron, deepseek, and gemini-2.5-pro (det 1.0)**; nemotron also
qualifies for synthesis.

### Critic requalification (repaired fixtures, 5 repetitions)

google/gemini-2.5-pro: det 0.40 < 0.85 (rejected); nemotron: provider-failed;
claude-3.7-sonnet and gpt-4o: provider-failed on this gateway. **critic:
no_qualified_model** — no primary, no fallback.

### Fast/screening

All candidates below the 0.9 threshold: gpt-4o det 0.333 (decision-accuracy
failures, model_reasoning_failure), deepseek det 0.5. **fast:
no_qualified_model.**

### Corrected reasoning task matrix (fixed evaluator, 3 reps)

| task | qualified models |
|---|---|
| evidence_extraction | **nemotron, deepseek, gemini-2.5-pro** (det 1.0) |
| synthesis | **nemotron** |
| gap_analysis / mechanism_generation / model_specification / proposition_generation | none |

## Remaining-task qualification + provider preflight (Phase 7D.3B)

Only candidates that PASSED the lightweight capability preflight entered the
qualification campaigns; provider-unavailable models were never interpreted as
incapable and never qualified.

### Provider/model preflight (routing preflight)

| candidate | reasoning | critic | fast |
|---|---|---|---|
| nvidia/nemotron-3-ultra-550b-a55b:free | available | available | transient upstream 502 |
| deepseek/deepseek-v4-flash-0731 | available | available | available |
| google/gemini-2.5-pro | available | available | — |
| openai/gpt-4o | available | — | available |
| meta-llama/llama-3.3-70b-instruct:free | provider_error (404 free tier) | — | — |
| mistralai/mistral-small-3.1-24b-instruct:free | provider_error (404 free tier) | — | — |
| google/gemini-2.5-flash:free | provider_error (404 free tier) | — | — |
| anthropic/claude-3.7-sonnet | provider_error (no endpoints) | — | — |

`model_preflight` artifacts record the exact requested/resolved model id and
each probe (reachability / structured JSON / context size / timeout-retry).

### Evaluator sanity audit (live-quality-evaluator-sanity-v1, 13/13)

Known-good responses pass, known-bad responses fail, scalar reference ids vs
list ids handled correctly, provider errors (no artifacts) never count as
successes. Genuine defects found and repaired:
1. Proposition verification: production writes `status: verified` (enum
   vocabulary); the evaluator required the literal `passed` — every correct
   model was falsely grounded. Now accepts verified/passed.
2. Critic evaluator emitted `severity_accuracy: None` (and the fast evaluator
   per-class accuracies `None` for empty classes), which made the whole
   critic/fast benchmarks ERROR instead of evaluating. Now emits 0.0.
3. Three live-quality reasoning fixtures silently errored at the workflow
   level: empty `supporting_evidence_ids` (SynthesisStatement min_length=1),
   payoff `expression` as a dict, equilibrium `variable` vs `label`, and
   `method: symbolic` outside the enum. gap/mechanism/model-spec/proposition
   had therefore evaluated ZERO models. Repaired in the workflow drivers.

### Corrected task matrix (3-5 reps, preflight-passing only)

| task | qualified models | primary / fallback |
|---|---|---|
| evidence_extraction (frozen) | nemotron, deepseek, gemini-2.5-pro | — |
| synthesis (frozen) | nemotron | — |
| mechanism_critique | **nemotron, gemini-2.5-pro** (det 1.0) | gemini / nemotron |
| model_specification_critique | **nemotron, gemini-2.5-pro** (det 1.0) | gemini / nemotron |
| proposition_critique | **nemotron, gemini-2.5-pro** (det 1.0) | gemini / nemotron |
| results_critique | none (gemini det 0.8) | — |
| manuscript_critique | none (gemini det 0.4) | — |
| gap_analysis / mechanism_generation / model_specification / proposition_generation | none | — |
| screening | none (gpt-4o det 0.667) | — |

Critic role remains no_qualified_model because results_critique and
manuscript_critique are uncovered. No role has a qualified primary+fallback.

## Recommended next increment

Post-Phase-7D.3B: the evaluation program covers 32 benchmark families (added
live-quality-evaluator-sanity-v1) with deterministic gating. The remaining-task
qualification ran only preflight-passing candidates; genuine defects found and
repaired: (1) proposition verification status vocabulary (verified vs passed),
(2) critic/fast evaluator `None` numeric dimension scores that errored entire
benchmarks, (3) three live-quality reasoning fixtures that silently errored at
the workflow level so gap/mechanism/model-spec/proposition had never evaluated
a model. With the repairs, mechanism_critique, model_specification_critique and
proposition_critique each qualify nemotron + gemini-2.5-pro (primary + fallback
per task); evidence_extraction (nemotron/deepseek/gemini-pro) and synthesis
(nemotron) remain qualified. Still uncovered: results_critique,
manuscript_critique, gap_analysis, mechanism_generation, model_specification,
proposition_generation, screening — dominant failure reason
below_quality_threshold (model capability), not provider availability.
Phase 7D.4 should build task-aware SHADOW routing (advisory, no production
switching) over the qualified-task set, or continue model search for the
critical uncovered tasks. No role has a qualified primary+fallback; production
activation NOT recommended.