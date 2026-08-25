# Architecture

## Overview

Research Harness is a plugin-first system. The kernel is deliberately small and contains no research-domain logic (no papers, no equilibrium, no citations). Everything that provides an agent capability is a plugin that interacts through explicit service contracts and an asynchronous event bus.

```
                     ┌─────────────────────────┐
                     │      CLI / Bootstrap    │
                     │  (application layer)    │
                     └────────────┬────────────┘
                                  │
                    ┌─────────────┼─────────────┐
                    │             │             │
              ┌─────▼─────┐ ┌─────▼─────┐ ┌────▼────┐
              │  Kernel   │ │ Contracts │ │ Plugins │
              │ (generic) │ │ (Protocols)│ │(concrete)│
              └─────┬─────┘ └─────▲─────┘ └────┬────┘
                    │             │            │
              ┌─────▼─────┐       │      ┌─────▼────┐
              │  Service  │◄──────┼──────│ Model    │
              │  Registry │       │      │ Router   │
              └─────┬─────┘       │      └────┬─────┘
                    │             │           │
              ┌─────▼─────┐       │     ┌─────▼────┐
              │ EventBus  │◄──────┼─────┤ OpenRouter│
              │(transport)│       │     └──────────┘
              └─────┬─────┘       │
                    │             │
         ┌──────────▼──────┐      │
         │ Session Plugin  │      │
         │ (persistence)   │      │
         └─────────────────┘      │
                                  │
                        ┌─────────▼────────┐
                        │   Agent Loop     │
                        └────────┬─────────┘
                                 │
                      ┌──────────┼───────────┐
                      │          │           │
                   Tools     Autonomy    Skills
```

Research workflow plugins (literature, modeling, equilibrium, numerical, results, manuscript, publication, novelty) compose on the same kernel — see `docs/research-domain.md` and the per-phase docs (`docs/literature-sources.md`, `docs/screening.md`, `docs/evidence.md`, `docs/synthesis.md`, `docs/gaps.md`, `docs/mechanisms.md`, `docs/models.md`, `docs/equilibrium.md`, `docs/propositions.md`, `docs/numerical.md`, `docs/results.md`, `docs/manuscript.md`, `docs/publication.md`, `docs/novelty.md`).

The composition root is `src/research_harness/app/bootstrap.py` — it knows about both kernel and plugins; the kernel knows only about abstractions.

## Kernel

The kernel (`src/research_harness/kernel/`) owns:

- **plugin abstractions** — `Plugin`, `PluginMetadata`, `PluginContext` (`kernel/plugin.py:35`)
- **dependency resolution** — `PluginManager` validates `requires`/`provides`, detects missing providers and cycles, topologically sorts deterministically (Kahn's algorithm with sorted tie-breaking) (`kernel/manager.py:64`)
- **lifecycle** — `setup` (register services/subscribe) → `start` → `stop` → `teardown`; dependents start after dependencies and stop before them; orphaned services are cleaned via `clear_owner`
- **service registry** — `ServiceRegistry` (`kernel/services.py:18`) with registration, lookup, duplicate detection, ownership tracking
- **event dispatch** — `EventBus` (`kernel/events.py:31`) — in-process async pub/sub; events carry `event_id`, `schema_version`, `event_type`, `timestamp`, `source`, `session_id`, `run_id`, `parent_event_id`, `payload`; wildcard `*` subscription supported; failures are isolated and logged; in-memory `history()` is for tests/debugging — **not** the persistent source of truth
- **runtime** — `Runtime` (`kernel/runtime.py:17`) is a generic holder of `config`, `PluginManager`, `ServiceRegistry`, `EventBus` with `start`/`stop`; it does **not** discover plugins

The kernel imports **zero** concrete plugins and contains no research behavior. Verified by `tests/unit/test_architecture.py:13`.

## Application Bootstrap

The bootstrap layer (`src/research_harness/app/bootstrap.py`) is the composition root:

- Loads YAML config via `config/loader.py`
- Discovers **built-in** plugins from `plugins/registry.py:332` (`BUILTIN_PLUGINS`, 70 plugins)
- Discovers **external** plugins via `importlib.metadata.entry_points(group="research_harness.plugins")` — lazy factories, validated on creation, duplicate IDs rejected, clear errors
- Merges built-in + external deterministically (`get_all_plugin_factories`)
- Builds per-plugin configs from `AppConfig` (e.g., `models.roles` → `routing.role_router`, `session.root` → `session.jsonl`)
- Constructs `ServiceRegistry`, `EventBus`, `PluginManager`, and `Runtime`

```
config (AppConfig)
      │
      ▼
bootstrap.build_runtime()  ──► discovers plugins ──► PluginManager ──► Runtime
```

CLI and tests use `app.bootstrap.build_runtime` / `build_runtime_from_yaml`, never `kernel.runtime` discovery.

## External Plugin Discovery

Any installed Python package can provide plugins:

```toml
# pyproject.toml of external package
[project.entry-points."research_harness.plugins"]
"tool.my_tool" = "my_package.plugin:MyPlugin"
# or factory function
"tool.my_tool" = "my_package.plugin:create_plugin"
```

Contract:

- Entry-point **name** is the plugin id (e.g., `tool.my_tool`) and must equal `plugin.metadata.id`
- Value is either a `Plugin` subclass, a `Plugin` instance, or a callable returning a `Plugin`
- The harness validates the result is a `Plugin` and that `metadata.id` matches the entry-point name; mismatches and load failures raise `PluginError` with the entry-point value in the message
- Duplicate ids (builtin vs external or external vs external) are rejected before registration
- Discovery is lazy per-factory to avoid import-time side effects; `list_available_plugins()` returns sorted `(id, source)` without instantiating unrelated plugins

See `tests/unit/test_external_plugins.py` for realistic mocked entry-point fixtures covering discovery, coexistence, duplicate, malformed, and load-error cases.

## Plugin Contract

Every plugin implements `Plugin` (`kernel/plugin.py:55`) with:

```python
metadata: (
    PluginMetadata  # id, version, plugin_type, description, provides, requires, optional_requires
)


async def setup(ctx: PluginContext): ...
async def start(): ...
async def stop(): ...
async def teardown(): ...
```

Metadata example:

```yaml
id: model.openrouter
version: 0.1.0
plugin_type: model
provides: [model_provider.openrouter]
requires: []
```

Another plugin declares `requires: [model_provider.openrouter]` and the manager guarantees ordering.

`PluginContext` (`kernel/plugin.py:35`) is the only surface plugins receive:

- `ctx.config` — plugin-specific configuration slice
- `ctx.services.require(name)` / `ctx.try_get(name)` / `ctx.register(name, instance)`
- `ctx.events` — subscribe/emit
- `ctx.runtime_meta` — read-only runtime metadata

No global registries, no import side-effects. `tests/unit/test_architecture.py:88` enforces cross-plugin import rules.

## Service Registry

Services are named strings like `model_provider.openrouter`. The registry stores `ServiceEntry(name, instance, owner)` and enforces:

- duplicate detection with owner information
- `require` raises `ServiceError` with available services listed
- `clear_owner` on plugin stop to prevent leaks

Plugins should access peers via `ctx.require` rather than importing implementations:

```python
# BAD
from research_harness.plugins.models.openrouter.plugin import OpenRouterProvider

# GOOD
provider = ctx.require("model_provider.openrouter")
```

Typed `Protocol`s in `src/research_harness/contracts/` define the expected interfaces.

## Event System and Session Persistence

**EventBus is transport; SessionStore is the persistent source of truth.**

```
event producer (loop, provider, tool)
      │
      ▼
   EventBus  ──►  subscribers (logging, diagnostics)
      │
      └──── Session persistence subscriber (session.jsonl)
                   │
                   ▼
              events.jsonl  (append-only, scrubbed)
```

- `Event` (`kernel/events.py:12`) is a Pydantic model with `event_id`, `schema_version`, `event_type`, `timestamp`, `source`, `session_id`, `run_id`, `parent_event_id`, `payload`
- `EventBus` preserves publish order and isolates handler failures; its in-memory `history()` is **not** used for replay — it exists for unit tests and debugging
- `JsonlSessionStore` (`plugins/sessions/jsonl/plugin.py:46`) subscribes to `*` on setup; every event with a `session_id` is appended scrubbed to `.research/sessions/<session_id>/events.jsonl` via `store.append`. The loop only `publish`es — it does not directly write to the store for events.
- Session creation (`store.create_session`) is still direct (the loop needs a session id), but all subsequent trajectory events flow through the bus.
- Replay/resume must read `SessionStore`, never `EventBus` history.

This avoids two competing histories.

## Append-Only Sessions

```
.research/sessions/<session_id>/
  metadata.json
  events.jsonl   # one JSON object per line, append-only
```

Each event is appended with `open(..., "a")`; history is never rewritten. The store scrubs sensitive keys before persisting (see Security). This enables future `resume`/`fork`/`replay` without requiring private model chain-of-thought.

Reproducibility data recorded per event: prompts, visible context, model outputs, tool calls/results, decisions, config refs, model/provider ids, token usage, cost, timestamps, errors.

## Security — Secret Redaction

Defense in depth:

- **Never persisted:** `api_key`, `apikey`, `api-key`, `openrouter_api_key`, `authorization`, `Authorization`, `password`, `token`, `access_token`, `refresh_token`, `secret`, `bearer` (case-insensitive, recursive over dicts and lists) — `plugins/sessions/jsonl/plugin.py:18` (`_SENSITIVE_KEYS`)
- **Provider isolation:** `OpenRouterProvider` owns `OPENROUTER_API_KEY` and `Authorization: Bearer …` headers; it never emits them in `model.requested`/`model.completed` payloads. Verified by `tests/unit/test_session.py:77` (nested scrubbing) and live smoke assertion that `Authorization`/`sk-or-v1` are absent from persisted events.
- **Scrubbing is recursive:** nested `{"headers": {"Authorization": "Bearer …"}}` and lists of dicts are scrubbed.
- **No false confidence:** scrubbing is key-based, not a DLP product; headers are never emitted in the first place.

## Model Abstraction

Research plugins depend on `ModelProvider` (`contracts/model.py:64`), not on OpenRouter:

```python
ModelRequest  # messages, tools, response_schema, temperature, max_tokens, metadata
ModelResponse  # message, tool_calls, finish_reason, usage, model, provider, latency, metadata
```

`ModelCapabilities` declares `tool_calling`, `structured_output`, etc.; the harness can reject incompatible requests early.

## OpenRouter Provider

`OpenRouterPlugin` (`plugins/models/openrouter/plugin.py:282`) owns:

- URL (`https://openrouter.ai/api/v1/chat/completions`) and header auth (`OPENROUTER_API_KEY` from env or plugin config)
- mapping `ModelRequest` ↔ OpenRouter JSON (including `tools`, `response_format` for structured output)
- tool-call parsing (JSON string → dict) and usage extraction
- error normalization → `ModelError` (including upstream `{"error": ...}` envelopes on 200)
- timeout handling and retry for transient transport failures (`Timeout`, `ConnectError`) with bounded retries; 4xx errors are not retried

Research code never sees OpenRouter URLs or auth.

## Model Routing

`RoleRouter` (`plugins/routing/role_router/plugin.py:15`) implements `ModelRouter`:

- configuration `models.roles: { fast: {provider, model}, reasoning: {...}, ...}`
- `resolve(role)` returns mapping
- `complete(role, request)` injects `model` into `request.metadata` and delegates to `model_provider.<provider>`

This separates harness-level role selection from OpenRouter's provider failover. The harness decides the logical role; OpenRouter decides the inference provider for that model.

## Tool Contract

`Tool` (`contracts/tool.py:8`) exposes `name`, `description`, `input_schema` (JSON Schema), `async def execute(arguments) -> Any`. Calls emit trace events. Phase 1 provides `tool.echo` — deterministic, validates `text` argument, returns `{"echoed": text}`.

Custom `.env` loader (`config/dotenv.py`) is a lightweight fallback; canonical is `uv run --env-file .env`.

## Agent Loop

`SimpleToolLoop` (`plugins/loops/simple_tool_loop/plugin.py:17`) is itself a plugin (`agent_loop.default`):

1. create session (if store available)
2. resolve `model_router.default`
3. collect allowed `ToolSpec`s
4. loop: `ModelRequest` → `router.complete` → dispatch tool calls via `tool.<name>` → append tool results → repeat until no tool calls or `max_steps`
5. emit `run.*`, `model.*`, `tool.*` events via `EventBus` (persistence via session subscriber)

`max_steps` is configurable; exceeding it raises `LoopLimitError`.

## Autonomy Policy

`ConfigurableAutonomyPolicy` (`plugins/autonomy/configurable/plugin.py:11`) provides `autonomy_policy.default` with modes `high` (never requires approval) and `interactive` (requires approval for checkpoints like `research_question`, `proposed_mechanism`, `final_contribution_claim`). Research plugins call `requires_approval` / `request_approval`; they never branch on raw config strings.

## Storage and Documents (Phase 2E)

Two stores with distinct duties:

- `ArtifactStore` (`storage.artifacts_sqlite`) — SQLite, small JSON payloads, provenance graph, `ArtifactEnvelope[T]`
- `BlobStore` (`storage.blobs_filesystem`) — filesystem content-addressed by `sha256`, `BlobReference{algorithm,digest,size_bytes,media_type,storage_key}`, layout `.research/blobs/sha256/ab/cd/...`, atomic temp+rename, deduplication

Large PDFs and extracted text **never** go inside `artifacts.payload_json`; they go to `BlobStore`, artifacts hold `BlobReference`.

Document plugins (`plugins/documents/*`) compose:

```
ScreenedLiteratureSet → included PaperIdentities → DocumentLocator(metadata + unpaywall) → DocumentLocation --derived_from--> ProviderRecordSnapshot(unpaywall) → HTTP Fetcher (SSRF/size/PDF validation) → DocumentAcquisition (status) → BlobStore PDF → pypdf Extractor → FullTextDocument (page-level, 1-based) → FullTextCorpus
```

Contracts `contracts/blob.py:BlobStore` and `contracts/document.py:DocumentLocator/Fetcher/Extractor` are provider-neutral; orchestrator `documents.acquisition_orchestrator` enforces budgets, provenance, and corpus creation. No LLM in Phase 2E.

See `docs/documents.md` for full lifecycle, resolution priority, security, and CLI.

## Configuration

`AppConfig` (`config/schema.py:312`) validates YAML via Pydantic v2. `load_config` (`config/loader.py:11`) fails early with readable messages. Secrets are not in YAML; they come from environment. `uv run --env-file .env` is canonical; `config/dotenv.py` provides a fallback auto-load for local DX. See `docs/configuration.md` for the full example composition and secrets; per-phase docs document their own `research.*` / `literature.*` / `documents.*` blocks.

## CLI

`src/research_harness/cli/main.py` uses Typer and composes via `app.bootstrap`. The CLI delegates business logic to plugins/services; a few operator-facing commands (protocol approval, screening review overrides) write artifacts directly, and the inspect/list commands read the SQLite store directly. Command groups:

- `plugins list [--config]` / `plugins inspect <id>` — plugin metadata and source
- `runtime inspect [--config]` — resolved plugin order, services (`service → owner`), model roles (no secrets)
- `config validate <path>`
- `run [--config --prompt --role --max-steps]`
- `session inspect <id>`
- `artifacts list/inspect/lineage`
- `literature` — sources, search, get, plan, execute, discover, identities, screening, documents, evidence, synthesis, gaps (Phases 2A–2H)
- `research` — gap-select, mechanisms, model, equilibrium, comparative-statics, propositions, numerical, results, findings, contributions (Phases 3A–4A)
- `manuscript` — outline, draft, inspect, critique, revise (Phase 4B)
- `publication` — profile-create, format, validate, export, package, inspect (Phase 4C)
- `novelty` — validate, report, gate, revalidate, enrich, inspect (Phases 5A–5D)
- `eval` — run, inspect, list, coverage, readiness (Phase 6A-7A.1 harness: 22 offline benchmarks from novelty-threat to publication-packaging, coverage matrix, deterministic readiness report)

The full command reference (with options and live-test markers) is in `docs/cli.md`.

## Dependency Direction

```
bootstrap (app) ──► kernel, contracts, plugins
kernel ──► contracts (Protocols)
plugins ──► kernel (Plugin, PluginContext), contracts (Protocols), config
CLI ──► bootstrap
```

Rules enforced by `tests/unit/test_architecture.py`:

- `kernel` imports zero `research_harness.plugins.*` or `research_harness.app.*`
- `contracts` imports zero `research_harness.plugins.*`
- No plugin imports another plugin's `plugin.py` implementation — cooperation via `ctx.require`
- No `research_harness.plugins.models.openrouter` in research/other plugins

## Testing

Tests use fake providers/tools and `respx` for OpenRouter; no live API calls in CI. `tests/conftest.py` ensures `pytest` without `-m live` skips live tests. 73 unit, 43 integration, and 16 opt-in live test files cover every phase:

```bash
uv run pytest                # offline: 774 passed, 19 skipped
uv run --env-file .env pytest -m live -v          # OpenRouter live
uv run --env-file .env pytest -m live_novelty_validation -v  # e.g. Phase 5 live
```

Live tests assert structural success with minimal tokens and never log keys.

Coverage: kernel lifecycle, services, events, config, OpenRouter, sessions (including nested secret scrubbing), loops, routing, external discovery, architecture rules, literature phases (2A–2H), research phases (3A–3E), results assembly (4A), manuscript drafting (4B), publication formatting (4C), novelty validation/revalidation/enrichment/pre-acquisition (5A–5D), and evaluation benchmarks (6A–7A.1), plus end-to-end offline integration chains per phase.

## Research Workflows on the Kernel

Phases 2A–5D compose research plugins on the same kernel without modifying it:

- **Phase 2A–2H** (`plugins/literature/*`, `plugins/documents/*`) — search strategy, screening, acquisition, evidence, synthesis, gaps
- **Phase 3A–3E** (`plugins/research/*`) — mechanisms, model, equilibrium, propositions, numerical experiments
- **Phase 4A** — results assembly (findings, contributions, implications, package)
- **Phase 4B** — evidence-grounded manuscript drafting, critique, revision
- **Phase 4C** — publication formatting, citation resolution, bibliography, exports, submission package
- **Phase 5A–5D** (`plugins/research/novelty_validator`) — external novelty validation, incremental revalidation, evidence enrichment, bounded evidence pre-acquisition

Their schemas live under `src/research_harness/research/schemas/`; provenance flows through the immutable SQLite `ArtifactStore` (`derived_from`, `extracted_from`, `generated_from`, `supersedes`). See `docs/research-domain.md` and the per-phase docs for details.
