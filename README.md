# Research Harness

A plugin-first research harness for business information systems analytical-modeling research. It provides an auditable workflow from literature discovery through evidence-grounded theory development, publication packaging, novelty validation, and model evaluation.

## Start here

1. Install the project and create a local environment.

   ```bash
   uv sync --all-groups
   cp .env.example .env
   ```

2. Validate the configured runtime.

   ```bash
   uv run research-agent config validate configs/example.yaml
   uv run research-agent runtime inspect --config configs/example.yaml
   ```

3. Run offline tests.

   ```bash
   uv run pytest -m "not live"
   ```

Provider-backed commands require credentials in `.env`; see [Configuration](docs/configuration.md).

## Documentation

Start with the [documentation index](docs/index.md). Common entry points:

- [Architecture](docs/architecture.md) — kernel, plugins, storage, and events.
- [Configuration](docs/configuration.md) — YAML configuration, secrets, and validation.
- [CLI reference](docs/cli.md) — complete command reference.
- [Research workflow](docs/index.md#research-workflow) — phase-by-phase guides.
- [Evaluation and model operations](docs/index.md#evaluation-and-model-operations) — benchmarks, routing, and qualification.

## Workflow at a glance

```text
Literature → screening → documents → evidence → synthesis → gaps
    → mechanisms → analytical model → equilibrium → propositions → numerics
    → results → manuscript → publication → novelty validation
```

Each phase writes immutable artifacts with provenance. The SQLite artifact store is authoritative for research objects; JSONL sessions capture the runtime trajectory.

## Repository layout

```text
src/research_harness/  Application, kernel, contracts, plugins, and research schemas
configs/               Example runtime and tournament configurations
docs/                  Task-oriented documentation (start at docs/index.md)
tests/                 Unit, integration, and opt-in live tests
```

## Development

```bash
uv run ruff check src tests
uv run pytest -m "not live"
```

Live tests are opt-in and may call external providers; see [CLI testing guidance](docs/cli.md#testing-and-live-markers).

## Scope and safeguards

The harness preserves provenance and exposes uncertainty; it does not establish publication-ready correctness automatically. Review generated outputs, check source permissions, and treat novelty validation as decision support rather than legal advice.
