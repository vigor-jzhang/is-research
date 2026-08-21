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

loop:
  max_steps: 8
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
uv run --env-file .env pytest -m live -v   # opt-in live OpenRouter smoke (requires key)
```

OpenRouter tests use `respx` to mock `https://openrouter.ai/api/v1/chat/completions`.

The live smoke test `tests/live/test_openrouter_live.py` verifies:
`config → bootstrap → role_router → OpenRouter → real model → session`
and asserts structural success (output, model metadata, usage, session events) with minimal tokens. It skips cleanly when `OPENROUTER_API_KEY` is absent and never logs the key.

Optional ad-hoc live run:

```bash
uv run --env-file .env research-agent run --prompt "echo hello"
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
  contracts/     # typed Protocols for model, routing, tool, loop, session, autonomy
  config/        # Pydantic YAML schema + loader + dotenv helper
  app/
    bootstrap.py # composition root: discovers builtin+external plugins, builds Runtime
  plugins/
    models/openrouter
    routing/role_router
    tools/echo
    loops/simple_tool_loop
    sessions/jsonl
    autonomy/configurable
    registry.py  # builtin registry only
  cli/           # Typer CLI (delegates to bootstrap)
configs/example.yaml
docs/
  architecture.md
  plugin-authoring.md
tests/
  unit/          # architecture, external plugins, session, etc.
  live/          # opt-in live smoke (pytest -m live)
```

## Architecture

See `docs/architecture.md` for kernel, service registry, event bus, sessions, model abstraction, and ASCII diagram.

## Creating a Plugin

See `docs/plugin-authoring.md`.

## Roadmap

Phase 1 (this release): harness foundation only — no literature search, no analytical modeling, no web UI. See `docs/architecture.md` for the 7-phase roadmap.

## License

MIT
