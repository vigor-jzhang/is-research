# Research Harness

Plugin-first research harness for Information Systems / analytical-modeling research.

> **Everything that provides an agent capability is a plugin.** The kernel is minimal infrastructure (plugin discovery, lifecycle, services, events, configuration) — all model access, routing, tools, loops, sessions, and autonomy are implemented as plugins.

Inspired by DeepSeek Harness, this is an independent Python implementation of a complete autonomous research pipeline: question → planning → literature → gap → theory → model → analysis → verification → critique → manuscript → publication → novelty validation. Phases 2A–5D are implemented (literature through bounded evidence pre-acquisition); see the [Roadmap](#roadmap).

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

Configuration lives in `configs/example.yaml` (45 plugins; see
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
```

The complete command reference (including kernel commands, `run` demo,
per-phase options, and all live-test markers) is in
[docs/cli.md](docs/cli.md).

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

All gates must pass: `494 passed, 19 skipped` (offline), ruff/format/pyright
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
    documents/   # locator_metadata/unpaywall, fetcher_http, extractor_pypdf,
                 # acquisition_orchestrator
  cli/           # Typer CLI (delegates to bootstrap)
configs/example.yaml
docs/            # per-phase + architecture/configuration/cli/plugin-authoring docs
tests/           # unit/ 50, integration/ 21, live/ 16 (opt-in markers)
```

## Documentation

- **Architecture** — kernel, services, events, sessions, model abstraction, testing: [docs/architecture.md](docs/architecture.md)
- **Configuration** — full example composition, roles, secrets: [docs/configuration.md](docs/configuration.md)
- **CLI reference** — every command + live markers: [docs/cli.md](docs/cli.md)
- **Research domain** — artifacts, provenance, identity: [docs/research-domain.md](docs/research-domain.md)
- **Creating a plugin** — [docs/plugin-authoring.md](docs/plugin-authoring.md)
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
- **Post-Phase-5 (not implemented)** — automatic journal submission, peer-review response generation, open-access full-text prioritization

Each phase has a per-phase doc under `docs/`; nothing beyond the implemented phases is claimed.

## License

MIT