# Research Harness

Plugin-first research harness for Information Systems / analytical-modeling research.

> **Everything that provides an agent capability is a plugin.** The kernel is minimal infrastructure (plugin discovery, lifecycle, services, events, configuration) — all model access, routing, tools, loops, sessions, and autonomy are implemented as plugins.

Inspired by DeepSeek Harness, this is an independent Python implementation designed for a complete autonomous research pipeline (question → planning → literature → gap → theory → model → analysis → verification → critique → paper) while keeping Phase 1 focused on a clean, composable foundation.

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

See `configs/example.yaml`:

```yaml
runtime:
  autonomy: high   # high | interactive

plugins:
  - model.openrouter
  - routing.role_router
  - session.jsonl
  - storage.artifacts_sqlite
  - literature.crossref
  - literature.semantic_scholar
  - literature.ingestion
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

literature:
  crossref:
    enabled: true
    timeout_seconds: 20
  semantic_scholar:
    enabled: true
    timeout_seconds: 20

loop:
  max_steps: 8
```

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
uv run --env-file .env research-agent literature get --source crossref --id "10.1234/abc"

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
uv run --env-file .env pytest -m "live or live_literature or live_screening or live_documents" -v
```

Provider tests use `respx` to mock `https://api.crossref.org/works`, `https://api.semanticscholar.org/graph/v1`, and `https://openrouter.ai/api/v1/chat/completions`.

- The OpenRouter live smoke `tests/live/test_openrouter_live.py` verifies `config → bootstrap → role_router → OpenRouter → real model → session`
- Literature live tests `tests/live/test_literature_live.py` verify small Crossref/Semantic Scholar lookups/searches

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

All must pass before Phase 1 is considered complete.

## Project Structure

```
src/research_harness/
  kernel/        # plugin, manager, services, events, runtime (generic, no plugin discovery)
  contracts/     # typed Protocols: model, routing, tool, loop, session, autonomy, artifact, literature
  config/        # Pydantic YAML schema + loader + dotenv helper
  app/bootstrap.py # composition root: builtin+external plugins, builds Runtime
  research/
    schemas/     # PaperRecord, EvidenceItem, ResearchClaim, etc. + ProviderRecordSnapshot
    envelope.py  # ArtifactEnvelope[T] + content hashing
    provenance/  # ProvenanceLink
  plugins/
    models/openrouter
    routing/role_router
    tools/echo
    loops/simple_tool_loop
    sessions/jsonl
    storage/
      artifacts_sqlite  # generic SQLite ArtifactStore
      blobs_filesystem  # content-addressed BlobStore (sha256)
    literature/
      crossref (client, mapper)
      semantic_scholar (client, mapper)
      ingestion
      identity_resolver
      search_planner (model-assisted)
      search_orchestrator
      screening_* (protocol, view, screener, orchestrator)
    documents/
      locator_metadata / locator_unpaywall
      fetcher_http (SSRF/size/PDF validation)
      extractor_pypdf (page-level, 1-based)
      acquisition_orchestrator (ScreenedSet → Corpus)
  cli/           # Typer CLI (delegates to bootstrap)
configs/example.yaml
docs/
  architecture.md
  plugin-authoring.md
  research-domain.md
  literature-sources.md
tests/
  unit/          # architecture, external plugins, envelope, mappers, providers, ingestion
  integration/   # e2e + literature ingestion
  live/          # opt-in live smoke (pytest -m live / live_literature)
```

## Architecture

See `docs/architecture.md` for kernel, service registry, event bus, sessions, model abstraction, and ASCII diagram.

## Research Domain

See `docs/research-domain.md` for artifact envelopes, content hashing, provenance (`Paper → Evidence → Claim`), session vs artifact persistence, and external identifier handling.

## Creating a Plugin

See `docs/plugin-authoring.md`.

## Roadmap

Phase 1 (this release): harness foundation only — no literature search, no analytical modeling, no web UI. See `docs/architecture.md` for the 7-phase roadmap.

## License

MIT
