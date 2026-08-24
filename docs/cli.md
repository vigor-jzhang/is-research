# CLI Reference

`research-agent` (Typer) is the operator interface; every command composes
the runtime via `app.bootstrap` and delegates business logic to plugins.
Phase-specific docs list each phase's commands; this page is the full
reference.

## Kernel commands

```bash
# Plugins (builtin + external entry_points)
uv run research-agent plugins list [--config configs/example.yaml]
uv run research-agent plugins inspect model.openrouter

# Resolved runtime composition (no secrets)
uv run research-agent runtime inspect --config configs/example.yaml

# Config validation
uv run research-agent config validate configs/example.yaml

# End-to-end agent run (requires OPENROUTER_API_KEY)
uv run --env-file .env research-agent run --config configs/example.yaml --prompt "Use echo to say hello"

# Sessions
uv run research-agent session inspect <session-id>

# Immutable research artifacts (SQLite, no network)
uv run research-agent artifacts list --type paper_record
uv run research-agent artifacts inspect <artifact-id>
uv run research-agent artifacts lineage <artifact-id> --direction ancestors
```

The `run` command demonstrates:

```
config → kernel → plugin manager → JSONL session → simple_tool_loop
     → model_router (role) → OpenRouter provider → echo tool → trajectory events
```

## Literature (Phases 2A–2H)

```bash
uv run research-agent literature sources
uv run --env-file .env research-agent literature search --source crossref --query "algorithmic pricing" --limit 5
uv run --env-file .env research-agent literature search --source semantic_scholar --query "information systems" --limit 5
uv run --env-file .env research-agent literature get "10.1234/abc" --source crossref

# Search strategy & orchestration (Phase 2C, requires OpenRouter for planning)
uv run --env-file .env research-agent literature plan --question <rq_artifact_id> [--research-plan <rp_id>]
uv run --env-file .env research-agent literature execute --strategy <strategy_artifact_id>
uv run --env-file .env research-agent literature discover --question <rq_artifact_id>  # plan+execute
uv run research-agent literature identities list
uv run research-agent literature identities inspect <identity_id>

# Screening (Phase 2D, PaperIdentity level, approval-gated)
uv run --env-file .env research-agent literature screening protocol create --question <rq_id> [--research-plan <rp_id>]
uv run research-agent literature screening protocol inspect <protocol_id>
uv run research-agent literature screening protocol approve <draft_protocol_id>
uv run --env-file .env research-agent literature screening run --search-execution <exec_id> --protocol <approved_protocol_id>
uv run research-agent literature screening decisions list [--execution <screening_exec_id>]
uv run research-agent literature screening decisions inspect <decision_id>
uv run research-agent literature screening review --decision <decision_id> --final include --notes "human override"

# Documents (Phase 2E, acquisition from ScreenedLiteratureSet, no LLM)
uv run research-agent literature documents locate --set <screened_set_id>
uv run research-agent literature documents acquire --set <screened_set_id>
uv run research-agent literature documents run --set <screened_set_id>  # locate+acquire+extract
uv run research-agent literature documents list [--set <id>] [--execution <id>]
uv run research-agent literature documents inspect <full_text_doc_id>
uv run research-agent literature documents import --identity <paper_identity_id> --file paper.pdf
uv run research-agent literature documents text <full_text_doc_id> --page 5

# Evidence extraction (Phase 2F, page-grounded, structured, model-assisted)
uv run --env-file .env research-agent literature evidence run --corpus <full-text-corpus-id>
uv run research-agent literature evidence profiles list
uv run research-agent literature evidence profiles inspect <profile_id>
uv run research-agent literature evidence items list --profile <profile_id>
uv run research-agent literature evidence items inspect <evidence_id>

# Cross-paper synthesis (Phase 2G, evidence-grounded, model-assisted)
uv run --env-file .env research-agent literature synthesis run --corpus <evidence-corpus-id>
uv run research-agent literature synthesis inspect <synthesis_id>
uv run research-agent literature synthesis themes list --synthesis <synthesis_id>
uv run research-agent literature synthesis themes inspect <theme_id>

# Research gap analysis (Phase 2H, evidence-grounded)
uv run --env-file .env research-agent literature gaps run --synthesis <synthesis-id> --corpus <evidence-corpus-id> [--question <rq-id>]
uv run research-agent literature gaps list --analysis <gap-analysis-id>
uv run research-agent literature gaps inspect <gap-id>
uv run research-agent literature gaps analysis inspect <analysis-id>
```

## Theory (Phases 3A–3E)

```bash
# Mechanism development (Phase 3A, gap selection -> candidate -> critique -> selection)
uv run --env-file .env research-agent research gap-select --analysis <gap-analysis-id> [--gap <gap-id>]
uv run --env-file .env research-agent research mechanisms generate --selection <gap-selection-id>
uv run research-agent research mechanisms inspect <candidate-id>
uv run --env-file .env research-agent research mechanisms critique <candidate-id>
uv run --env-file .env research-agent research mechanisms select <candidate-id>

# Formal analytical model (Phase 3B, structured specification)
uv run --env-file .env research-agent research model build --mechanism <selected-mechanism-id>
uv run research-agent research model inspect <model-id>
uv run --env-file .env research-agent research model critique <model-id>
uv run --env-file .env research-agent research model revise <model-id>

# Equilibrium derivation (Phase 3C, symbolic)
uv run --env-file .env research-agent research equilibrium derive --model <model-id>
uv run research-agent research equilibrium inspect <analysis-id>
uv run research-agent research equilibrium verify <candidate-id>

# Propositions (Phase 3D, comparative statics + economic interpretation)
uv run research-agent research comparative-statics run --equilibrium <equilibrium-analysis-id>
uv run research-agent research comparative-statics inspect <comparative-statics-analysis-id>
uv run --env-file .env research-agent research propositions generate --analysis <comparative-statics-analysis-id>
uv run research-agent research propositions inspect <proposition-id>
uv run research-agent research propositions verify <proposition-id>

# Numerical experiments (Phase 3E, deterministic + welfare)
uv run research-agent research numerical run --equilibrium <equilibrium-analysis-id>
uv run research-agent research numerical inspect <experiment-id>
uv run research-agent research numerical results <experiment-id>
uv run research-agent research numerical robustness <experiment-id>
uv run research-agent research numerical welfare <experiment-id>
```

## Results & manuscript (Phases 4A–4B)

```bash
# Results assembly (Phase 4A, findings + contributions + package)
uv run research-agent research results assemble --numerical <experiment-id>
uv run research-agent research results inspect <package-id>
uv run research-agent research findings list --package <package-id>
uv run research-agent research contributions list --package <package-id>
uv run research-agent research results critique <package-id>

# Manuscript drafting (Phase 4B, structured + evidence-grounded)
uv run research-agent manuscript outline --results <package-id>
uv run research-agent manuscript draft --outline <outline-id>
uv run research-agent manuscript inspect <draft-id>
uv run research-agent manuscript critique <draft-id>
uv run research-agent manuscript revise <draft-id>
```

## Publication (Phase 4C)

```bash
uv run research-agent publication profile-create --name "MIS Quarterly (generic)"
uv run research-agent publication format --draft <draft-id> --profile <profile-id>
uv run research-agent publication validate <manuscript-id>
uv run research-agent publication export --manuscript <manuscript-id> --format latex
uv run research-agent publication package --manuscript <manuscript-id> [--cover-letter]
uv run research-agent publication inspect <package-id>
```

## Novelty validation (Phases 5A–5D)

```bash
# Full external validation (with automatic evidence pre-acquisition/enrichment)
uv run research-agent novelty validate <submission-package-id> [--as-of 2026-08-23]
#   [--max-results N] [--max-claims N] [--offline] [--report-only]

# Incremental revalidation after a manuscript supersession
uv run research-agent novelty revalidate <previous-report-id> <new-package-id> [--force-all]

# Evidence enrichment for a sparse candidate (Phase 5C)
uv run research-agent novelty enrich <candidate-assessment-id> [--offline]

# Reassess (supersedes the previous report) / gate from latest report
uv run research-agent novelty report <package-id>
uv run research-agent novelty gate <package-id> [--report <report-id>]

# Inspect a report or gate (includes staleness)
uv run research-agent novelty inspect <report-or-gate-id>
```

## Evaluation harness (Phase 6A–6E)

```bash
# Run a benchmark end-to-end (registers the immutable benchmark, runs each
# case over the production workflow with deterministic fixtures, executes
# evaluators, persists + prints the aggregated report). Fully offline.
uv run research-agent eval run novelty-threat-v1           # 7 cases: novelty + false-clear
uv run research-agent eval run literature-retrieval-v1     # 6 cases: precision@k, recall@k, F1@k, MRR
uv run research-agent eval run citation-correctness-v1     # 10 cases: citation/bibliography correctness
uv run research-agent eval run literature-screening-v1       # 9 cases: screening, review gates, failure handling
uv run research-agent eval run evidence-extraction-v1        # 9 cases: evidence grounding, locators, categories
uv run research-agent eval run research-gap-analysis-v1        # 10 cases: gap types, grounding, ranking, hallucination
uv run research-agent eval run mechanism-development-v1         # 10 cases: mechanism validity, critic, revision
uv run research-agent eval run equilibrium-correctness-v1       # 10 cases: closed-form equilibria, symbolic verification
uv run research-agent eval run numerical-analysis-v1             # 9 cases: sweeps, feasibility, robustness, welfare
uv run research-agent eval run <benchmark> [--evaluators evaluator.deterministic,...]

# Inspect an evaluation run or report (per-evaluator detail)
uv run research-agent eval inspect <run-or-report-id>

# List recent evaluation runs
uv run research-agent eval list [--limit 20]
```

See `docs/evaluation.md` for schemas, the evaluator contract, benchmark
architecture, metrics, and the known-expected metrics of each benchmark.

## Testing and live markers

Offline: `uv run pytest` — mocked HTTP via `respx`
(`https://api.crossref.org/works`, `https://api.semanticscholar.org/graph/v1`,
`https://openrouter.ai/api/v1/chat/completions`); live tests are skipped
without `-m live*`.

Opt-in live markers (`tests/live/`, one file per marker, skip cleanly when
keys or prior artifacts are absent, never log credentials):

```bash
uv run --env-file .env pytest -m live -v                 # OpenRouter live
uv run --env-file .env pytest -m live_literature -v      # Crossref/Semantic Scholar live
uv run --env-file .env pytest -m live_screening -v       # Screening live (OpenRouter)
uv run --env-file .env pytest -m live_documents -v       # Document live (Unpaywall, needs UNPAYWALL_EMAIL)
uv run --env-file .env pytest -m live_evidence -v        # Evidence extraction live
uv run --env-file .env pytest -m live_synthesis -v       # Synthesis live
uv run --env-file .env pytest -m live_gap_analysis -v    # Gap analysis live
uv run --env-file .env pytest -m live_mechanism -v       # Mechanism development live
uv run --env-file .env pytest -m live_model_specification -v  # Model specification live
uv run --env-file .env pytest -m live_equilibrium -v       # Equilibrium derivation live
uv run --env-file .env pytest -m live_propositions -v    # Propositions live
uv run --env-file .env pytest -m live_numerical_analysis -v  # Numerical analysis live
uv run --env-file .env pytest -m live_results_assembly -v  # Results assembly live
uv run --env-file .env pytest -m live_manuscript -v  # Manuscript drafting live
uv run --env-file .env pytest -m live_publication -v  # Publication formatting live
uv run --env-file .env pytest -m live_novelty_validation -v  # Novelty validation live
```

Optional ad-hoc live runs:

```bash
uv run --env-file .env research-agent run --prompt "echo hello"
uv run --env-file .env research-agent literature search --source crossref --query "test" --limit 2
```