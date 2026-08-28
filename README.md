# Research Harness

Plugin-first research harness for Information Systems / analytical-modeling research.

> **Everything that provides an agent capability is a plugin.** The kernel is minimal infrastructure (plugin discovery, lifecycle, services, events, configuration) — all model access, routing, tools, loops, sessions, and autonomy are implemented as plugins.

Inspired by DeepSeek Harness, this is an independent Python implementation of a complete autonomous research pipeline: question → planning → literature → gap → theory → model → analysis → verification → critique → manuscript → publication → novelty validation → evaluation. Phases 2A–6A are implemented (literature through the evaluation harness); see the [Roadmap](#roadmap).

## Quick Start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/vigor-jzhang/is-research.git
cd is-research
uv sync --group dev
cp .env.example .env   # set OPENROUTER_API_KEY (see docs/configuration.md for all secrets)
```

Run the demo agent loop, or the full research pipeline:

```bash
uv run --env-file .env research-agent run --prompt "Use echo to say hello"   # kernel demo
uv run research-agent config validate configs/example.yaml                   # validate composition
```

## How to Use It

Configuration lives in `configs/example.yaml` (80 plugins; see
[docs/configuration.md](docs/configuration.md)). The research pipeline is
driven by one CLI command per stage; every command writes immutable,
provenance-linked artifacts to `.research/artifacts.db`:

```bash
# Literature (Phases 2A–2H): search → screen → documents → evidence → synthesis → gaps
uv run --env-file .env research-agent literature search --source semantic_scholar --query "information systems" --limit 5
uv run --env-file .env research-agent literature discover --question <rq_id>     # plan + execute
uv run --env-file .env research-agent literature screening run --search-execution <exec_id> --protocol <protocol_id>
uv run --env-file .env research-agent literature documents run --set <screened_set_id>
uv run --env-file .env research-agent literature evidence run --corpus <corpus_id>
uv run --env-file .env research-agent literature gaps run --synthesis <synthesis_id> --corpus <corpus_id>

# Theory (Phases 3A–3E): gap → mechanism → model → equilibrium → propositions → numerics
uv run --env-file .env research-agent research mechanisms generate --selection <gap_selection_id>
uv run --env-file .env research-agent research model build --mechanism <mechanism_id>
uv run --env-file .env research-agent research equilibrium derive --model <model_id>
uv run --env-file .env research-agent research propositions generate --analysis <cs_analysis_id>
uv run research-agent research numerical run --equilibrium <equilibrium_id>

# Results → manuscript → publication (Phases 4A–4C)
uv run research-agent research results assemble --numerical <experiment_id>
uv run research-agent manuscript outline --results <package_id> && uv run research-agent manuscript draft --outline <outline_id>
uv run research-agent publication format --draft <draft_id> --profile <profile_id>
uv run research-agent publication package --manuscript <manuscript_id>

# Submission-risk gate (Phases 5A–5D): external novelty validation
uv run research-agent novelty validate <submission_package_id>     # report + readiness gate
uv run research-agent novelty inspect <report-or-gate-id>         # incl. staleness

# Evaluation (Phase 6A-7A.1): offline benchmarks over production workflows
uv run research-agent eval run novelty-threat-v1           # 7 cases: novelty + false-clear
uv run research-agent eval run literature-retrieval-v1      # 6 cases: precision@k, recall@k, MRR
uv run research-agent eval run citation-correctness-v1      # 10 cases: citations, bibliography, invented fields
uv run research-agent eval run literature-screening-v1        # 9 cases: screening + review gates + failure handling
uv run research-agent eval run evidence-extraction-v1         # 9 cases: evidence grounding, locators, categories
uv run research-agent eval run research-gap-analysis-v1        # 10 cases: gap types, grounding, ranking, hallucination
uv run research-agent eval run mechanism-development-v1         # 10 cases: mechanism validity, critic, revision
uv run research-agent eval run equilibrium-correctness-v1       # 10 cases: closed-form equilibria, symbolic verification
uv run research-agent eval run numerical-analysis-v1             # 9 cases: sweeps, feasibility, robustness, welfare
uv run research-agent eval run comparative-statics-v1           # 8 cases: closed-form derivatives, signs, conditions
uv run research-agent eval run proposition-correctness-v1        # 10 cases: monotonicity, equality, threshold, rejection
uv run research-agent eval run results-assembly-v1               # 10 cases: findings, contributions, implications, novelty
uv run research-agent eval run manuscript-grounding-v1           # 11 cases: claims, citations, critique, revision
uv run research-agent eval run research-pipeline-e2e-v1           # 1 case: the full production chain end to end
uv run research-agent eval run literature-synthesis-v1            # 8 cases: consensus, contradiction, mixed, patterns, rejection
uv run research-agent eval run analytical-model-specification-v1  # 9 cases: symbol/timing/ownership validation, critic
uv run research-agent eval run document-acquisition-v1            # 8 cases: OA PDFs, fallback, HTML-as-PDF, oversized, dedup
uv run research-agent eval run incremental-revalidation-v1        # 7 cases: recompute-on-change, deterministic reuse
uv run research-agent eval run literature-ingestion-identity-v1   # 8 cases: DOI/identifier dedup, supersession, partial ingestion
uv run research-agent eval run gap-selection-v1                   # 8 cases: selection, fallback, autonomy, operator override
uv run research-agent eval run novelty-revalidation-v1            # 7 cases: re-trigger on new literature, stale-reuse detection
uv run research-agent eval run publication-packaging-v1           # 8 cases: citations, exports, anonymization, ready gating
uv run research-agent eval coverage                                 # coverage matrix: capability -> benchmark -> evaluator
uv run research-agent eval readiness                               # deterministic evaluation-readiness report
uv run research-agent eval inspect <run-id>                 # per-evaluator detail
uv run research-agent eval list

# Model tournaments + role leaderboards (Phase 7B): compare models per role
uv run research-agent evaluation tournament run --plan configs/tournament/example-reasoning.yaml
uv run research-agent evaluation tournament inspect <tournament-run-id>
uv run research-agent evaluation leaderboard show --role reasoning   # latest leaderboard for a role
uv run research-agent evaluation leaderboard list
uv run research-agent evaluation leaderboard inspect <leaderboard-id>

# Policy-constrained model routing (Phase 7C, shadow mode): decision support only
uv run research-agent routing decide --role reasoning --policy quality_first
uv run research-agent routing shadow --role reasoning            # would-switch vs current config
uv run research-agent routing inspect <decision-id>
uv run research-agent routing policies list

# Live-quality validation + production-routing readiness (Phase 7D.0)
uv run research-agent evaluation live-quality run --role reasoning --repetitions 3
uv run research-agent evaluation live-quality inspect <run-id>
uv run research-agent routing readiness --role reasoning          # ready / not_ready (never auto-enables routing)
uv run research-agent eval run production-routing-readiness-v1    # offline readiness-gate benchmark

# Live-model qualification campaigns (Phase 7D.1)
uv run research-agent routing qualify --role reasoning --repetitions 3   # real campaign (config-driven candidates)
uv run research-agent routing qualification inspect <campaign-id>
uv run research-agent routing qualification summary                      # per-role primary/fallback/status
uv run research-agent eval run model-qualification-policy-v1             # offline qualification benchmark

# Qualification expansion + benchmark calibration (Phase 7D.2)
uv run research-agent eval calibration                                   # audit live-quality benchmarks (references, schemas, leakage, grounding, provider assumptions)
uv run research-agent routing qualify --role fast --repetitions 3        # expanded config-driven candidate pools
uv run research-agent routing qualification matrix                       # ProductionQualificationMatrix (activation input for Phase 7D)
uv run research-agent eval run model-qualification-policy-v1             # 16 offline cases incl. defect-exclusion/stability/eligibility

# Strong-model expansion + task-specific qualification (Phase 7D.3)
uv run research-agent routing qualify --role reasoning --repetitions 3   # expanded pools incl. paid models (8 reasoning / 6 critic / 6 fast)
uv run research-agent routing qualify-task --role reasoning --task evidence_extraction
uv run research-agent routing qualification tasks --role reasoning       # TaskQualificationMatrix (task-by-task coverage)
uv run research-agent routing capability-profile <model-id>              # ModelCapabilityProfile across roles
uv run research-agent eval run task-specific-model-qualification-v1      # 10 offline task-qualification cases

# Focused model qualification (Phase 7D.3A)
uv run research-agent routing qualify --role critic --repetitions 5      # gemini-2.5-pro + strong candidates at 5 reps (repaired fixtures)
uv run research-agent routing qualify --role reasoning --repetitions 3   # corrected evidence grounding (scalar-reference evaluator fix)
uv run research-agent routing qualification tasks --role reasoning       # evidence_extraction now qualified (nemotron/deepseek/gemini-pro)

# Remaining-task qualification + provider preflight (Phase 7D.3B)
uv run research-agent routing preflight --role reasoning                 # lightweight capability probes (available/temporarily_unavailable/capability_mismatch/provider_error)
uv run research-agent routing preflight --model <model>                  # probe one candidate
uv run research-agent routing qualify --role critic --repetitions 5 --candidates "<slug1>,<slug2>"  # preflight-passing candidates only
uv run research-agent routing qualification remaining                    # RemainingTaskCoverage (qualified primary/fallback, provider_unavailable counts)
uv run research-agent routing qualification tasks --role critic          # mechanism/model/proposition critique now qualified
uv run research-agent eval run live-quality-evaluator-sanity-v1          # offline evaluator sanity audit (13 cases)

# Targeted qualification of remaining tasks (Phase 7D.3C)
uv run research-agent routing preflight --config configs/example.yaml     # preflight role + per-task pools (stronger/paid models)
uv run research-agent routing qualify --role critic --repetitions 5 --tasks results_critique,manuscript_critique --candidates "<slug1>,<slug2>"
uv run research-agent routing qualification remaining                    # results_critique now covered (gemini primary)
```

The complete command reference (including kernel commands, `run` demo,
per-phase options, and all live-test markers) is in
[docs/cli.md](docs/cli.md). The evaluation harness (Phase 6A-7A.1), model
tournaments (Phase 7B), and policy routing (Phase 7C) are documented in
[docs/evaluation.md](docs/evaluation.md).

## Testing

```bash
uv run pytest                    # offline, mocked HTTP, live tests skipped
uv run --env-file .env pytest -m live -v   # OpenRouter live smoke
```

Live markers cover every phase (e.g. `live_novelty_validation`); see
[docs/cli.md](docs/cli.md#testing-and-live-markers) for the full list.

## Quality Gates

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

All gates must pass: `861 passed, 21 skipped` (offline), ruff/format/pyright
clean, `config validate` passes, live tests green on demand.

## Project Structure

```
src/research_harness/
  kernel/        # plugin, manager, services, events, runtime
  contracts/     # typed Protocols: model, routing, tool, loop, session, autonomy,
                 # artifact, blob, literature, document, screening
  config/        # Pydantic YAML schema + loader + dotenv helper
  app/bootstrap.py # composition root: builtin+external plugins, builds Runtime
  research/
    schemas/     # domain artifacts: paper, evidence, mechanism, model,
                 # equilibrium, proposition, numerical, results, manuscript,
                 # publication, novelty, ...
    envelope.py  # ArtifactEnvelope[T] + content hashing
    provenance/  # ProvenanceLink (derived_from, extracted_from, generated_from, supersedes)
    symbolic.py  # shared SymPy helpers
  plugins/
    models/openrouter
    routing/role_router, tools/echo, loops/simple_tool_loop
    sessions/jsonl, autonomy/configurable
    storage/     # artifacts_sqlite (immutable), blobs_filesystem (sha256)
    literature/  # crossref, semantic_scholar, ingestion, identity_resolver,
                 # search_planner, search_orchestrator, screening_*,
                 # evidence_extractor/orchestrator, synthesis, gap_analyzer
    research/    # Phase 3A-3E (mechanisms → numerical_analysis)
                 # Phase 4A-4C (results → publication_formatter)
                 # Phase 5A-5D (novelty_validator)
                 # Phase 6A-7A.1 (evaluation_harness + evaluator.*)
                 # Phase 7B (evaluation_model_tournament: role tournaments + leaderboards)
                 # Phase 7C (routing/policy_router shadow routing + evaluator.model_routing)
    documents/   # locator_metadata/unpaywall, fetcher_http, extractor_pypdf,
                 # acquisition_orchestrator
  cli/           # Typer CLI (delegates to bootstrap)
configs/example.yaml
docs/            # per-phase + architecture/configuration/cli/plugin-authoring docs
tests/           # unit/ 80, integration/ 48, live/ 18 (opt-in markers)
```

## Documentation

- **Architecture** — kernel, services, events, sessions, model abstraction, testing: [docs/architecture.md](docs/architecture.md)
- **Configuration** — full example composition, roles, secrets: [docs/configuration.md](docs/configuration.md)
- **CLI reference** — every command + live markers: [docs/cli.md](docs/cli.md)
- **Research domain** — artifacts, provenance, identity: [docs/research-domain.md](docs/research-domain.md)
- **Creating a plugin** — [docs/plugin-authoring.md](docs/plugin-authoring.md)
- **Evaluation** — benchmarks, evaluators, metrics, provenance: [docs/evaluation.md](docs/evaluation.md)
- **Per-phase docs** — `literature-sources.md`, `search-strategy.md`, `screening.md`, `documents.md`, `evidence.md`, `synthesis.md`, `gaps.md`, `mechanisms.md`, `models.md`, `equilibrium.md`, `propositions.md`, `numerical.md`, `results.md`, `manuscript.md`, `publication.md`, `novelty.md`

## Roadmap

- **Phase 1** — plugin-first kernel: services, events, sessions, autonomy, CLI
- **Phase 2A–2H** — literature: search, screening, documents, evidence, synthesis, gaps
- **Phase 3A–3E** — theory: mechanism → model → equilibrium → propositions → numerics
- **Phase 4A** — findings, contributions, implications, results package
- **Phase 4B** — evidence-grounded manuscript drafting, critique, revision
- **Phase 4C** — publication formatting, bibliography, exports, submission package
- **Phase 5A** — external novelty validation + SubmissionReadinessGate
- **Phase 5B** — incremental revalidation + staleness tracking
- **Phase 5C** — evidence enrichment for sparse candidates
- **Phase 5D** — bounded evidence pre-acquisition before assessment
- **Phase 6A** — evaluation harness: benchmarks, evaluators (deterministic + model-assisted), aggregate reports
- **Phase 6B** — retrieval + citation benchmarks over the real search orchestrator and Phase 4C formatter
- **Phase 6C** — screening + evidence-extraction benchmarks over the real Phase 2D/2F pipelines
- **Phase 6D** — gap-analysis + mechanism-development benchmarks over the real Phase 2H/3A pipelines
- **Phase 6E** — equilibrium + numerical benchmarks over the real Phase 3C/3E pipelines (SymPy closed forms, deterministic tolerances)
- **Phase 6F** — comparative-statics + proposition-correctness benchmarks over the real Phase 3D pipelines (scripted proposition responses, symbolic sign/condition preservation)
- **Phase 6G** — results-assembly + manuscript-grounding benchmarks over the real Phase 4A/4B pipelines (grounded claims, citations, critique/revision)
- **Phase 6H** — end-to-end pipeline benchmark (retrieval → … → citation formatting), coverage matrix, deterministic readiness report
- **Phase 7A** — evaluation gap closure: synthesis, analytical-model specification, document acquisition, and incremental-revalidation benchmarks (deterministic evaluators + coverage/readiness update)
- **Phase 7A.1** — final evaluation gap closure: ingestion/identity resolution, gap selection, novelty revalidation, and publication/submission-packaging benchmarks (deterministic evaluators + coverage/readiness update)
- **Phase 7B** — model tournaments + role leaderboards: reproducible per-role model comparison over the frozen benchmarks (correctness-first lexicographic ranking, reliability/latency/token/cost accounting, offline + opt-in live tournaments)
- **Phase 7C** — policy-constrained model routing (shadow): evidence-enrichment benchmark closes the 5C-5D gap; routing policies (quality_first/balanced/cost_constrained/latency_constrained) select from persisted leaderboards with capability/quality/reliability gates, fallbacks, stale-evidence handling, role isolation; shadow mode never switches production models
- **Phase 7D.0** — live-quality model validation + routing readiness: real-model live-quality benchmarks (reasoning/critic/fast) with structural references and deterministic evaluators, configurable repetitions with variance, role-specific production-qualification gates (live_quality_evidence required), production-routing-readiness benchmark, and per-role ready/not_ready verdicts — no automatic routing activation
- **Phase 7D.1** — live-model qualification campaigns: config-driven candidate sets per role, >=3 repetitions each over the live-quality benchmarks, structured rejection kinds, primary/fallback selection among qualified models only, offline model-qualification-policy benchmark (unsafe_model_qualification_rate = 0), per-role qualified status — production switching stays off
- **Phase 7D.2** — qualification expansion & benchmark calibration: expanded candidate pools (fast 4, reasoning 5, critic 4), model-independent calibration audit of every live-quality task (reference validity, achievable schema, no leakage/impossible-evidence/provider assumptions), structured failure attribution (model/grounding/instruction/provider/timeout/rate-limit/benchmark-defect/evaluator-defect/infrastructure), per-task diagnostics (evidence extraction dominates reasoning grounding failures), stability (stable/borderline/unstable) with unstable candidates never eligible, ProductionQualificationMatrix (activation input for Phase 7D), and 6 new offline qualification cases (defect exclusion, extra repetitions, unstable rejection, primary+fallback, role-partial matrix) — thresholds never loosened
- **Phase 7D.3** — strong-model expansion & task-specific qualification: expanded pools incl. paid models (reasoning 8, critic 6, fast 6), task-level qualification reusing the exact role thresholds (qualified_for_task without role qualification), TaskQualificationMatrix + TaskQualificationResult + ModelCapabilityProfile, per-task ranking (qualified only), detailed evidence-extraction diagnostics (hallucinated IDs / page locators / unsupported claims / invalid categories / missing evidence / malformed output), and task-specific-model-qualification-v1 (10 offline cases, unsafe_task_qualification_rate = 0). Calibration audit extended to validate critic fixtures against their schemas — this exposed and repaired three genuine fixture defects (results_critique finding_type, results_package status, manuscript citation evidence_item_id) that had silently errored 2 of 5 critic tasks and inflated the critic det rate since 7D.0
- **Phase 7D.3A** — focused model qualification: evidence-ID interface audit concluded the interface is correct (the model returns semantic candidates; artifact IDs are assigned deterministically by production code) but found a genuine evaluator bug — the reasoning evaluator iterated scalar reference fields (source_artifact_id, gap_id, model_id, ...) character-by-character, inflating critical grounding failures for every model since 7D.0. Fixed (scalar fields = single references). With the fix, evidence_extraction now qualifies for nemotron, deepseek, and gemini-2.5-pro (det 1.0); nemotron also qualifies for synthesis. Critic requalified at 5 reps on repaired fixtures: no_qualified_model (gemini-2.5-pro det 0.40; claude/gpt-4o provider-failed). Fast: no qualified model. No role has a qualified primary+fallback
- **Phase 7D.3B** — remaining-task qualification + provider preflight: added lightweight capability preflight (`routing preflight`; classifies available/temporarily_unavailable/capability_mismatch/provider_error, never interprets provider-unavailable as incapability), config-driven per-task candidate pools, task-specific diagnostics (gap/mechanism/model-spec/proposition buckets, critic defect metrics, screening per-class accuracy), the offline `live-quality-evaluator-sanity-v1` audit (13 cases: known-good passes / known-bad fails / scalar-vs-list ids / provider-not-success), and `RemainingTaskCoverage`. Genuine repairs: (1) the proposition verifier emits status `verified` (enum vocabulary) but the evaluator required the literal `passed` — repaired so correct models are not grounded; (2) the critic evaluator emitted `severity_accuracy: None` (and the fast evaluator per-class accuracies `None` when a class had no cases), which made the ENTIRE critic/fast benchmarks error instead of evaluating — repaired to emit 0.0; (3) three live-quality reasoning fixtures errored at the workflow level (empty `supporting_evidence_ids` violating the SynthesisStatement schema; payoff `expression` as a dict; `variable` vs `label`; `symbolic` method not in the enum), so gap/mechanism/model-spec/proposition cases had silently evaluated ZERO models — repaired. Results (preflight-passing candidates only, fresh store): mechanism_critique, model_specification_critique, and proposition_critique each now qualify nemotron + gemini-2.5-pro (det 1.0) giving per-task primary+fallback; results_critique, manuscript_critique, gap_analysis, mechanism_generation, model_specification, proposition_generation, and screening remain uncovered (dominant reason below_quality_threshold, i.e. model capability). No role has a qualified primary+fallback
- **Phase 7D.3C** — targeted qualification of remaining tasks: expanded config-driven per-task pools with stronger/paid models (qwen3-32b, llama-3.3-70b-instruct, gpt-4o-mini, gemini-2.5-flash) and preflighted every candidate (available-only enter campaigns). Production prompt/schema audit: mechanism_generator prompt already lists the allowed domains (models still output out-of-taxonomy domains → capability); model_builder and proposition_generator inject the required artifact ids themselves (grounding refs correct); the gap workflow's stable-id reuse is a genuine benchmark defect (fixture artifacts reused across repetitions are not "produced by this run"), documented but not safely repairable without changing offline gap semantics. Results: **results_critique now covered** (gemini-2.5-pro primary, det 1.0 at 5 reps); mechanism/model-specification/proposition critique have primary+fallback (llama-3.3-70b-instruct + gemini-2.5-pro); evidence_extraction and synthesis additionally covered by qwen3-32b and llama-3.3-70b-instruct. Still uncovered: gap_analysis, mechanism_generation, model_specification, proposition_generation, manuscript_critique, screening. Offline policy tests extended (task-specific pools, stronger-task-pool model covers a role-default failure, primary-without-fallback, prompt-repair-never-weakened-gates, coverage calculation); task-specific-model-qualification-v1 17/17 and live-quality-evaluator-sanity-v1 14/14 with unsafe rate 0. Task-aware readiness: 6 covered tasks (3 ready_with_fallback, 3 ready_without_fallback); not enough coverage for full task-aware shadow routing yet
- **Post-Phase-5 (not implemented)** — automatic journal submission, peer-review response generation, open-access full-text prioritization
- **Post-Phase-6 (not implemented)** — leaderboards, live benchmark corpora, publication-quality scoring (see the readiness report's uncovered capabilities)
- **Post-Phase-7A.1 (not implemented)** — live provider-connector/publisher-endpoint coverage, advisory LLM-quality judging
- **Post-Phase-7D (not implemented)** — controlled production routing activation, contextual bandits, online learning, self-modifying routing policy, automatic cheapest-model selection, shared leaderboard service

Each phase has a per-phase doc under `docs/`; nothing beyond the implemented phases is claimed.

## License

MIT