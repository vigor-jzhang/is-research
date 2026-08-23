# Research Harness

Plugin-first research harness for Information Systems / analytical-modeling research.

> **Everything that provides an agent capability is a plugin.** The kernel is minimal infrastructure (plugin discovery, lifecycle, services, events, configuration) — all model access, routing, tools, loops, sessions, and autonomy are implemented as plugins.

Inspired by DeepSeek Harness, this is an independent Python implementation of a complete autonomous research pipeline (question → planning → literature → gap → theory → model → analysis → verification → critique → manuscript). Phases 2A–4B are implemented: literature search/screening/documents/evidence/synthesis/gaps, mechanism development, analytical modeling, equilibrium derivation, propositions, numerical experiments, results assembly, and evidence-grounded manuscript drafting.

## Philosophy

- Kernel is infrastructure, not an agent
- Plugins communicate via explicit typed service contracts (`model_provider.openrouter`, `model_router.default`, `tool.echo`, `agent_loop.default`, etc.)
- Append-only JSONL session trajectories for reproducibility and future replay/fork/search
- Logical model roles (`fast`, `reasoning`, `critic`, `long_context`) mapped to provider/model via configuration
- Configuration-driven composition; no hard-coded model names in research code
- Asynchronous interfaces for LLM, tools, events, workflows

## Installation

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/vigor-jzhang/is-research.git
cd is-research
uv sync --group dev
```

## Environment Setup

```bash
cp .env.example .env
# edit .env and set:
# OPENROUTER_API_KEY=sk-or-v1-...
```

Use `uv`'s env-file support (canonical):

```bash
uv run --env-file .env research-agent run --prompt "hello"
uv run --env-file .env pytest -m live -v   # live OpenRouter smoke
```

For convenience, the harness also auto-loads `.env` from the project root if present (without overriding existing env vars), so `uv run research-agent run` works locally without the flag. In CI, prefer `uv run --env-file .env`.

Never commit `.env` or API keys. The session event log scrubs sensitive keys and never persists secrets.

## Configuration

See `configs/example.yaml` (full 44-plugin composition):

```yaml
runtime:
  autonomy: high   # high | interactive

plugins:
  - model.openrouter
  - routing.role_router
  - session.jsonl
  - storage.artifacts_sqlite
  - storage.blobs_filesystem
  - literature.crossref
  - literature.semantic_scholar
  - literature.ingestion
  - literature.identity_resolver
  - literature.search_planner
  - literature.search_orchestrator
  - literature.screening_protocol_builder
  - literature.screening_view_builder
  - literature.title_abstract_screener
  - literature.screening_orchestrator
  - literature.evidence_extractor
  - literature.evidence_orchestrator
  - literature.synthesis
  - literature.gap_analyzer
  - research.gap_selection
  - research.mechanism_generator
  - research.mechanism_critic
  - research.model_builder
  - research.model_specification_critic
  - research.equilibrium_deriver
  - research.equilibrium_verifier
  - research.comparative_statics
  - research.proposition_verifier
  - research.proposition_critic
  - research.proposition_generator
  - research.numerical_analysis
  - research.results_assembler
  - research.results_critic
  - research.manuscript_drafter
  - research.manuscript_critic
  - documents.locator.metadata
  - documents.locator.unpaywall
  - documents.fetcher.http
  - documents.extractor.pypdf
  - documents.acquisition_orchestrator
  - autonomy.configurable
  - tool.echo
  - loop.simple_tool_loop

models:
  roles:
    fast: {provider: openrouter, model: "deepseek/deepseek-v4-flash-0731"}
    reasoning: {provider: openrouter, model: "nvidia/nemotron-3-ultra-550b-a55b:free"}
    critic: {provider: openrouter, model: "nvidia/nemotron-3-ultra-550b-a55b:free"}
    long_context: {provider: openrouter, model: "deepseek/deepseek-v4-flash-0731"}

session:
  root: ".research/sessions"

artifacts:
  store: sqlite
  path: ".research/artifacts.db"

loop:
  max_steps: 8
```

The `literature:` (search, screening, evidence, synthesis, gaps), `research:`
(mechanism, model, equilibrium, proposition, numerical, results, manuscript),
and `documents:` sections configure each phase; see the per-phase docs below.

Secrets in `.env`:

```
OPENROUTER_API_KEY=...
CROSSREF_MAILTO=you@example.com
SEMANTIC_SCHOLAR_API_KEY=...
UNPAYWALL_EMAIL=you@example.com
```

Validate:

```bash
uv run research-agent config validate configs/example.yaml
```

## CLI

```bash
# List available plugins (builtin + external entry_points)
uv run research-agent plugins list
uv run research-agent plugins list --config configs/example.yaml
uv run research-agent plugins inspect model.openrouter

# Inspect resolved runtime composition (no secrets)
uv run research-agent runtime inspect --config configs/example.yaml

# Validate config
uv run research-agent config validate configs/example.yaml

# Run end-to-end (requires OPENROUTER_API_KEY)
uv run --env-file .env research-agent run --config configs/example.yaml --prompt "Use echo to say hello"

# Inspect a session
uv run research-agent session inspect <session-id>

# Research artifacts (SQLite, no network)
uv run research-agent artifacts list --type paper_record
uv run research-agent artifacts inspect <artifact-id>
uv run research-agent artifacts lineage <artifact-id> --direction ancestors

# Literature (provider-neutral, ingestion via artifacts)
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

# Publication formatting (Phase 4C, citations + exports + submission package)
uv run research-agent publication profile-create --name "MIS Quarterly (generic)"
uv run research-agent publication format --draft <draft-id> --profile <profile-id>
uv run research-agent publication validate <manuscript-id>
uv run research-agent publication export --manuscript <manuscript-id> --format latex
uv run research-agent publication package --manuscript <manuscript-id> [--cover-letter]
uv run research-agent publication inspect <package-id>
```

The `run` command demonstrates:

```
config → kernel → plugin manager → JSONL session → simple_tool_loop
     → model_router (role) → OpenRouter provider → echo tool → trajectory events
```

## Testing

```bash
uv run pytest
# mocked HTTP, no real API calls — live tests are skipped
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
```

Provider tests use `respx` to mock `https://api.crossref.org/works`, `https://api.semanticscholar.org/graph/v1`, and `https://openrouter.ai/api/v1/chat/completions`.

Live smoke tests (`tests/live/`, one per marker) verify structural success end to end with minimal tokens and skip cleanly when keys or prior artifacts are absent, never logging credentials: `test_openrouter_live`, `test_literature_live`, `test_screening_live`, `test_documents_live`, `test_evidence_live`, `test_synthesis_live`, `test_gap_analysis_live`, `test_mechanism_live`, `test_model_specification_live`, `test_equilibrium_live`, `test_propositions_live`, `test_numerical_analysis_live`, `test_results_assembly_live`, `test_manuscript_live`.

All live tests assert structural success with minimal tokens and skip cleanly when keys are absent, never logging credentials.

Optional ad-hoc live runs:

```bash
uv run --env-file .env research-agent run --prompt "echo hello"
uv run --env-file .env research-agent literature search --source crossref --query "test" --limit 2
```

## Quality Gates

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

All gates must pass: `414 passed, 17 skipped` (offline), ruff/format/pyright clean, `config validate` passes, live tests green on demand.

## Project Structure

```
src/research_harness/
  kernel/        # plugin, manager, services, events, runtime (generic, no plugin discovery)
  contracts/     # typed Protocols: model, routing, tool, loop, session, autonomy,
                 # artifact, blob, literature, document, screening
  config/        # Pydantic YAML schema + loader + dotenv helper
  app/bootstrap.py # composition root: builtin+external plugins, builds Runtime
  research/
    schemas/     # domain artifacts: paper, evidence, synthesis, gap, mechanism,
                 # model, equilibrium, proposition, numerical, results, manuscript, ...
    envelope.py  # ArtifactEnvelope[T] + content hashing
    provenance/  # ProvenanceLink (derived_from, extracted_from, generated_from, supersedes)
    symbolic.py  # shared SymPy helpers (game-consistent FOCs/payoffs, stage plans)
  plugins/
    models/openrouter
    routing/role_router
    tools/echo
    loops/simple_tool_loop
    sessions/jsonl
    autonomy/configurable
    storage/
      artifacts_sqlite  # generic SQLite ArtifactStore (immutable)
      blobs_filesystem  # content-addressed BlobStore (sha256)
    literature/
      crossref (client, mapper)
      semantic_scholar (client, mapper)
      ingestion
      identity_resolver
      search_planner (model-assisted)
      search_orchestrator
      screening_* (protocol, view, screener, orchestrator)
      evidence_extractor / evidence_orchestrator
      synthesis
      gap_analyzer
    research/
      gap_selection, mechanism_generator, mechanism_critic   # Phase 3A
      model_builder, model_specification_critic             # Phase 3B
      equilibrium_deriver, equilibrium_verifier             # Phase 3C
      comparative_statics, proposition_verifier/critic/generator  # Phase 3D
      numerical_analysis                                    # Phase 3E
      results_assembler, results_critic                     # Phase 4A
      manuscript_drafter, manuscript_critic                 # Phase 4B
      publication_formatter                                 # Phase 4C
    documents/
      locator_metadata / locator_unpaywall
      fetcher_http (SSRF/size/PDF validation)
      extractor_pypdf (page-level, 1-based)
      acquisition_orchestrator (ScreenedSet → Corpus)
  cli/           # Typer CLI (delegates to bootstrap)
configs/example.yaml
docs/
  architecture.md, plugin-authoring.md, research-domain.md
  literature-sources.md, search-strategy.md, screening.md, documents.md,
  evidence.md, synthesis.md, gaps.md
  mechanisms.md, models.md, equilibrium.md, propositions.md, numerical.md,
  results.md, manuscript.md, publication.md
tests/
  unit/          # 46 files, all phases (kernel, plugins, schemas, services)
  integration/   # 17 files, end-to-end offline chains (incl. phase3a-e, phase4a-c)
  live/          # 15 opt-in live smokes (pytest -m <marker>, needs .env keys)
```

## Architecture

See `docs/architecture.md` for kernel, service registry, event bus, sessions, model abstraction, and ASCII diagram.

## Research Domain

See `docs/research-domain.md` for artifact envelopes, content hashing, provenance (`Paper → Evidence → Claim`), session vs artifact persistence, and external identifier handling.

## Creating a Plugin

See `docs/plugin-authoring.md`.

## Roadmap

Implemented phases:

- **Phase 1** — plugin-first kernel: services, events, sessions, autonomy, CLI
- **Phase 2A–2H** — literature: sources, search strategy & orchestration,
  screening, document acquisition, evidence extraction, synthesis, gap analysis
- **Phase 3A–3E** — theory: gap selection, mechanism development, formal
  analytical model, symbolic equilibrium derivation & verification,
  propositions & comparative statics, numerical experiments & welfare
- **Phase 4A** — findings, contribution claims, implications, results package
- **Phase 4B** — evidence-grounded manuscript drafting, critique, revision
- **Phase 4C** — publication formatting: citation resolution, bibliography,
  Markdown/LaTeX/DOCX/PDF exports, submission package
- **Post-Phase-4 (not implemented)** — automatic journal submission,
  peer-review response generation, external novelty validation

Each phase has a per-phase doc under `docs/` and a completion report in the
phase's doc; nothing beyond the implemented phases is claimed.

## License

MIT
