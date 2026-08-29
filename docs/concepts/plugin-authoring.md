# Plugin Authoring Guide

This guide shows how to create a new plugin for the research harness.

## Principles

- A plugin is the only way to add capability; do not patch the kernel.
- Communicate via services and events, not direct imports of other plugins.
- Keep lifecycle deterministic; register services in `setup`, not at import time.
- Never embed secrets or hard-coded model names.

## Minimal Plugin

Create `src/research_harness/plugins/<category>/<name>/plugin.py`:

```python
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata


class MyService:
    async def do_work(self, x: str) -> str:
        return f"processed {x}"


class MyPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="category.my_plugin",  # unique, lower-cased, dot-namespaced
            version="0.1.0",
            plugin_type="my_type",  # e.g. model, tool, session, skill
            description="Does something useful",
            provides=["my_service.default"],  # services this plugin registers
            requires=[],  # services that must exist first
            optional_requires=["tool.echo"],  # ordering-only if present
        )

    async def setup(self, ctx: PluginContext) -> None:
        # ctx.config is the plugin's slice from AppConfig or runtime plugin_configs
        cfg = ctx.config
        svc = MyService()
        ctx.register("my_service.default", svc)
        # optionally subscribe to events
        ctx.subscribe("run.completed", self.on_run_completed)

    async def on_run_completed(self, event):
        # handle events asynchronously
        ...

    async def start(self) -> None:
        # start background tasks if needed
        ...

    async def stop(self) -> None:
        # stop background tasks
        ...

    async def teardown(self) -> None:
        # cleanup; services are auto-cleared by owner
        ...
```

Register it in `src/research_harness/plugins/registry.py`:

```python
def _create_my_plugin() -> Plugin:
    from research_harness.plugins.category.my_plugin.plugin import MyPlugin

    return MyPlugin()


BUILTIN_PLUGINS["category.my_plugin"] = _create_my_plugin
```

And add it to `configs/example.yaml`:

```yaml
plugins:
  - category.my_plugin
```

## Services

- Register with `ctx.register("service.name", instance)` — `owner` is set automatically to your plugin id.
- Consume with `ctx.require("service.name")` (raises `ServiceError` if missing) or `ctx.try_get` for optional.
- Define a `Protocol` in `src/research_harness/contracts/` for the service interface so consumers depend on the protocol, not your class.

Duplicate service registration raises `ServiceError` with owner information.

## Events

Emit:

```python
await ctx.emit("my.event", payload={"x": 1}, session_id=sid, run_id=rid)
```

Subscribe:

```python
ctx.subscribe("my.event", handler)  # handler: async def handler(event: Event)
ctx.subscribe("*", wildcard_handler)
```

Events are Pydantic models with `event_id`, `schema_version`, `event_type`, `timestamp`, `source`, `session_id`, `run_id`, `parent_event_id`, `payload`. The bus preserves order and isolates handler failures.

Persist important events via the session store if you need replay.

## Configuration

If your plugin needs config, add fields to `src/research_harness/config/schema.py` and wire them in `src/research_harness/app/bootstrap.py` (derived configs in `_derived_plugin_configs`). For quick prototyping, use `ctx.config` which already contains the relevant slice (e.g., `{"models": {"roles": {...}}}` for routing, `{"session": {"root": ...}}` for sessions).

Validate early; raise `ConfigurationError` with a clear message.

## Model Providers and Routers

Implement `ModelProvider` (`src/research_harness/contracts/model.py:58`):

```python
async def complete(self, request: ModelRequest) -> ModelResponse: ...
```

Use only `ctx.config` and environment for secrets; scrub secrets from any persisted payloads. Map to your provider's HTTP API with `httpx`; normalize errors to `ModelError`; retry only transient transport failures.

For routing, implement `ModelRouter` (`src/research_harness/contracts/routing.py`) that resolves a logical role to a provider/model and delegates.

Research skills must request a role (`reasoning`, `fast`, `critic`, `long_context`) — never a concrete model slug.

## Tools

Implement `Tool` (`src/research_harness/contracts/tool.py:8`):

```python
name = "my_tool"
description = "..."
input_schema = {"type": "object", "properties": {...}, "required": [...]}


async def execute(self, arguments: dict) -> Any: ...
```

The agent loop will convert your `input_schema` to a `ToolSpec` and dispatch by `tool.<name>`.

## Sessions

Implement `SessionStore` (`src/research_harness/contracts/session.py`). Use append-only writes and scrub sensitive keys before persisting. See `src/research_harness/plugins/sessions/jsonl/plugin.py:21` for example.

## Autonomy

Implement `AutonomyPolicy` (`src/research_harness/contracts/autonomy.py`) with `requires_approval` and `request_approval`. Research code calls the policy; it never checks `if mode == "interactive"`.

## External Plugins

Any installed package can provide plugins via entry points (no marketplace needed):

```toml
# pyproject.toml of external package
[project.entry-points."research_harness.plugins"]
"tool.my_tool" = "my_package.plugin:MyPlugin"
# factory function is also allowed
"tool.my_tool" = "my_package.plugin:create_plugin"
```

Rules:

- Entry-point **name** must equal `plugin.metadata.id` (e.g., `tool.my_tool`)
- Value may be a `Plugin` subclass, a `Plugin` instance, or a callable returning a `Plugin`
- The harness validates the result is a `Plugin` and that the id matches; mismatches raise `PluginError` with the entry-point value in the message
- Duplicate ids (builtin vs external or external vs external) are rejected before registration
- Built-ins remain in `src/research_harness/plugins/registry.py`; externals are discovered by `src/research_harness/app/bootstrap.py` via `importlib.metadata.entry_points(group="research_harness.plugins")`

Example external plugin:

```python
# my_package/plugin.py
from research_harness.kernel.plugin import Plugin, PluginContext, PluginMetadata


class MyToolPlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            id="tool.my_tool",
            version="0.1.0",
            plugin_type="tool",
            description="my external tool",
            provides=["tool.my_tool"],
            requires=[],
        )

    async def setup(self, ctx: PluginContext) -> None:
        ctx.register("tool.my_tool", MyTool())
```

Then:

```yaml
# configs/example.yaml
plugins:
  - tool.my_tool
```

Verify discovery:

```bash
uv run research-agent plugins list   # shows source builtin/external
```

Keep built-ins inside the repo for core platform; split to separate distributions only when needed.

## Testing Your Plugin

- Unit test lifecycle: register, resolve order, start/stop, service cleanup.
- Use `respx` to mock HTTP for provider plugins.
- Assert events are emitted and secrets are not persisted.
- For loops, inject a fake `ModelProvider` and `Tool` (see `tests/unit/test_loop.py`).

## Checklist

- [ ] `metadata` has correct `id`, `provides`, `requires`, no duplicates
- [ ] Services registered in `setup`, not at import time
- [ ] No direct imports of other plugins' implementations
- [ ] Config validated via Pydantic; secrets from env only
- [ ] Events emitted for observable actions
- [ ] Errors are typed (`ModelError`, `ToolError`, etc.) with cause chains
- [ ] Tests pass with `uv run pytest`, `ruff check`, `pyright`
