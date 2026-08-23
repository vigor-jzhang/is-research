"""CLI for research harness."""

from __future__ import annotations

import asyncio
import pathlib
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from research_harness.config.dotenv import load_dotenv
from research_harness.config.loader import load_config
from research_harness.kernel.errors import ConfigurationError

# Load .env early so OPENROUTER_API_KEY is available for local runs.
# For uv users, `uv run --env-file .env ...` is the canonical way;
# this fallback allows `uv run research-agent ...` without explicit flag.
load_dotenv()

app = typer.Typer(help="Research Harness - plugin-first research system", no_args_is_help=True)
plugins_app = typer.Typer(help="Plugin commands")
config_app = typer.Typer(help="Config commands")
session_app = typer.Typer(help="Session commands")
runtime_app = typer.Typer(help="Runtime commands")
artifacts_app = typer.Typer(help="Artifact commands")
literature_app = typer.Typer(help="Literature commands")
research_app = typer.Typer(help="Research commands (Phase 3A)")

app.add_typer(plugins_app, name="plugins")
app.add_typer(config_app, name="config")
app.add_typer(session_app, name="session")
app.add_typer(runtime_app, name="runtime")
app.add_typer(artifacts_app, name="artifacts")
app.add_typer(literature_app, name="literature")
app.add_typer(research_app, name="research")
manuscript_app = typer.Typer(help="Manuscript drafting (Phase 4B)")
app.add_typer(manuscript_app, name="manuscript")
publication_app = typer.Typer(help="Publication formatting (Phase 4C)")
app.add_typer(publication_app, name="publication")

console = Console()


# ---------------------------------------------------------------------------
# plugins list / inspect
# ---------------------------------------------------------------------------


@plugins_app.command("list")
def plugins_list(
    config: Annotated[
        pathlib.Path | None, typer.Option(help="Config file to load plugins from")
    ] = None,
) -> None:
    """List available plugins."""
    from research_harness.app.bootstrap import create_plugin, list_available_plugins

    table = Table(title="Plugins")
    table.add_column("ID", style="cyan")
    table.add_column("Type", style="magenta")
    table.add_column("Version", style="green")
    table.add_column("Provides", style="yellow")
    table.add_column("Requires", style="red")
    table.add_column("Source", style="dim")

    if config is not None:
        try:
            cfg = load_config(config)
            console.print(f"[dim]Configured plugins in {config}:[/dim]")
            for pid in cfg.plugins:
                try:
                    plugin = create_plugin(pid)
                    meta = plugin.metadata
                    # Determine source via list_available_plugins
                    source = "builtin"
                    for avail_id, avail_src in list_available_plugins():
                        if avail_id == pid:
                            source = avail_src
                            break
                    table.add_row(
                        meta.id,
                        meta.plugin_type,
                        meta.version,
                        ", ".join(meta.provides),
                        ", ".join(meta.requires) or "-",
                        source,
                    )
                except Exception as e:
                    table.add_row(pid, "unknown", "-", "-", f"error: {e}", "-")
        except ConfigurationError as e:
            console.print(f"[red]Config error: {e}[/red]")
            raise typer.Exit(code=1) from e
    else:
        # List all available (builtin + external)
        for pid, source in list_available_plugins():
            try:
                plugin = create_plugin(pid)
                meta = plugin.metadata
                table.add_row(
                    meta.id,
                    meta.plugin_type,
                    meta.version,
                    ", ".join(meta.provides),
                    ", ".join(meta.requires) or "-",
                    source,
                )
            except Exception as e:
                table.add_row(pid, "error", "-", "-", str(e), source)

    console.print(table)


@plugins_app.command("inspect")
def plugins_inspect(plugin_id: str) -> None:
    """Inspect a single plugin."""
    from research_harness.app.bootstrap import create_plugin, list_available_plugins

    try:
        plugin = create_plugin(plugin_id)
    except Exception as e:
        console.print(f"[red]Unknown plugin {plugin_id!r}: {e}[/red]")
        raise typer.Exit(code=1) from e

    meta = plugin.metadata
    # Determine source
    source = "unknown"
    for avail_id, avail_src in list_available_plugins():
        if avail_id == plugin_id:
            source = avail_src
            break
    console.print(f"[bold cyan]{meta.id}[/bold cyan] v{meta.version} [dim]({source})[/dim]")
    console.print(f"Type: {meta.plugin_type}")
    console.print(f"Description: {meta.description}")
    console.print(f"Provides: {', '.join(meta.provides) or '-'}")
    console.print(f"Requires: {', '.join(meta.requires) or '-'}")
    console.print(f"Optional: {', '.join(meta.optional_requires) or '-'}")
    console.print(f"Source: {source}")


# ---------------------------------------------------------------------------
# config validate
# ---------------------------------------------------------------------------


@config_app.command("validate")
def config_validate(
    path: Annotated[pathlib.Path, typer.Argument(help="Path to YAML config")],
) -> None:
    """Validate a configuration file."""
    try:
        cfg = load_config(path)
        console.print(f"[green]✓ Configuration {path} is valid[/green]")
        console.print(f"  plugins: {cfg.plugins}")
        console.print(f"  autonomy: {cfg.runtime.autonomy}")
        console.print(f"  roles: {list(cfg.models.roles.keys())}")
        console.print(f"  session root: {cfg.session.root}")
        console.print(f"  max_steps: {cfg.loop.max_steps}")
    except ConfigurationError as e:
        console.print(f"[red]✗ Configuration invalid: {e}[/red]")
        raise typer.Exit(code=1) from e
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(code=1) from e


# ---------------------------------------------------------------------------
# runtime inspect
# ---------------------------------------------------------------------------


@runtime_app.command("inspect")
def runtime_inspect(
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect resolved runtime composition without leaking secrets."""
    from research_harness.app.bootstrap import build_runtime, list_available_plugins

    # Determine config path
    if config is not None and not config.exists():
        console.print(f"[yellow]Config {config} not found, using defaults[/yellow]")
        cfg_path = None
    else:
        cfg_path = config

    if cfg_path is not None:
        try:
            cfg = load_config(cfg_path)
        except ConfigurationError as e:
            console.print(f"[red]Config error: {e}[/red]")
            raise typer.Exit(code=1) from e
    else:
        from research_harness.config.schema import AppConfig

        cfg = AppConfig(
            plugins=[
                "model.openrouter",
                "routing.role_router",
                "session.jsonl",
                "autonomy.configurable",
                "tool.echo",
                "loop.simple_tool_loop",
            ],
            models={
                "roles": {
                    "fast": {"provider": "openrouter", "model": "deepseek/deepseek-v4-flash-0731"},
                    "reasoning": {
                        "provider": "openrouter",
                        "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
                    },
                    "critic": {
                        "provider": "openrouter",
                        "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
                    },
                    "long_context": {
                        "provider": "openrouter",
                        "model": "deepseek/deepseek-v4-flash-0731",
                    },
                }
            },  # type: ignore[arg-type]
        )

    # Build runtime but do not start — we want to inspect composition
    try:
        runtime = build_runtime(cfg)
        # Need to resolve order to show it
        order = runtime.plugins.resolve_order()
    except Exception as e:
        console.print(f"[red]Failed to resolve plugins: {e}[/red]")
        raise typer.Exit(code=1) from e

    console.print("[bold]Plugins[/bold]")
    console.print("-------")
    # Map plugin id -> source
    source_map = dict(list_available_plugins())
    for pid in order:
        try:
            plugin = runtime.plugins.get_plugin(pid)
            meta = plugin.metadata
            src = source_map.get(pid, "unknown")
            console.print(
                f"[cyan]{meta.id}[/cyan] [dim]({src})[/dim] v{meta.version} [{meta.plugin_type}]"
            )
            console.print(f"  provides: {', '.join(meta.provides) or '-'}")
            console.print(f"  requires: {', '.join(meta.requires) or '-'}")
        except Exception as e:
            console.print(f"[red]{pid}: error {e}[/red]")

    console.print("\n[bold]Services[/bold]")
    console.print("--------")
    # Show services after setup would have been registered; we need to simulate?
    # For inspect we show what each plugin provides statically from metadata
    for pid in order:
        try:
            plugin = runtime.plugins.get_plugin(pid)
            for svc in plugin.metadata.provides:
                console.print(f"[yellow]{svc}[/yellow] -> {pid}")
        except Exception:
            continue

    console.print("\n[bold]Model Roles[/bold]")
    console.print("-----------")
    for role, rcfg in cfg.models.roles.items():
        # Never print secrets; only provider/model
        console.print(f"[magenta]{role:15}[/magenta] -> {rcfg.provider} / {rcfg.model}")

    console.print(
        f"\n[dim]Session root: {cfg.session.root} | autonomy: {cfg.runtime.autonomy} | max_steps: {cfg.loop.max_steps}[/dim]"
    )


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


@app.command("run")
def run_cmd(
    config: Annotated[pathlib.Path | None, typer.Option(help="Config YAML path")] = pathlib.Path(
        "configs/example.yaml"
    ),
    prompt: Annotated[str | None, typer.Option(help="Prompt to run")] = None,
    prompt_file: Annotated[pathlib.Path | None, typer.Option(help="File containing prompt")] = None,
    role: Annotated[str, typer.Option(help="Model role")] = "fast",
    max_steps: Annotated[int | None, typer.Option(help="Override max_steps")] = None,
) -> None:
    """Run a simple agent loop end-to-end."""
    if prompt is None and prompt_file is None:
        prompt = "Hello, please use the echo tool to echo 'hello world' and then summarize."

    if prompt_file is not None:
        prompt = pathlib.Path(prompt_file).read_text(encoding="utf-8")

    assert prompt is not None

    # Determine config path
    cfg_path = config
    if cfg_path is not None and not cfg_path.exists():
        console.print(f"[yellow]Config {cfg_path} not found, using defaults[/yellow]")
        cfg_path = None

    asyncio.run(_run_async(cfg_path, prompt, role, max_steps))


async def _run_async(
    cfg_path: pathlib.Path | None,
    prompt: str,
    role: str,
    max_steps: int | None,
) -> None:
    from research_harness.app.bootstrap import build_runtime
    from research_harness.config.schema import AppConfig

    # Load config
    if cfg_path is not None:
        try:
            cfg = load_config(cfg_path)
        except ConfigurationError as e:
            console.print(f"[red]Config error: {e}[/red]")
            raise typer.Exit(code=1) from e
    else:
        cfg = AppConfig(
            plugins=[
                "model.openrouter",
                "routing.role_router",
                "session.jsonl",
                "autonomy.configurable",
                "tool.echo",
                "loop.simple_tool_loop",
            ],
            models={
                "roles": {
                    "fast": {"provider": "openrouter", "model": "deepseek/deepseek-v4-flash-0731"},
                    "reasoning": {
                        "provider": "openrouter",
                        "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
                    },
                    "critic": {
                        "provider": "openrouter",
                        "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
                    },
                    "long_context": {
                        "provider": "openrouter",
                        "model": "deepseek/deepseek-v4-flash-0731",
                    },
                }
            },  # type: ignore[arg-type]
        )

    # Override max_steps if requested
    if max_steps is not None:
        cfg.loop.max_steps = max_steps

    console.print(f"[dim]Starting runtime with plugins: {cfg.plugins}[/dim]")
    console.print(f"[dim]Prompt: {prompt[:120]}...[/dim]")
    console.print(f"[dim]Role: {role}[/dim]")

    runtime = build_runtime(cfg)
    try:
        async with runtime:
            # Obtain loop and run
            try:
                loop = runtime.services.require("agent_loop.default")
            except Exception as e:
                console.print(f"[red]Failed to get agent_loop: {e}[/red]")
                raise typer.Exit(code=1) from e

            console.print("[cyan]Running agent loop...[/cyan]")
            result = await loop.run(prompt, role=role)
            console.print(f"[green]✓ Completed in {result.steps} steps[/green]")
            console.print(f"[bold]Output:[/bold] {result.output}")
            if result.session_id:
                console.print(f"[dim]Session: {result.session_id}[/dim]")
                root = cfg.session.root
                sid = result.session_id
                console.print(f"[dim]Events stored under {root}/{sid}/events.jsonl[/dim]")
    except Exception as e:
        # Top-level error handling with cause chain
        console.print(f"[red]Run failed: {e}[/red]")
        import traceback

        # Show traceback in debug; here just short
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        console.print(f"[dim]{tb}[/dim]")
        raise typer.Exit(code=1) from e


# ---------------------------------------------------------------------------
# session commands (stubs for Phase 1)
# ---------------------------------------------------------------------------


@session_app.command("inspect")
def session_inspect(
    session_id: str, root: Annotated[pathlib.Path | None, typer.Option()] = None
) -> None:
    """Inspect a session's events."""
    from pathlib import Path

    r = Path(root) if root else Path(".research/sessions")
    events_path = r / session_id / "events.jsonl"
    meta_path = r / session_id / "metadata.json"
    if not events_path.exists():
        console.print(f"[red]Session {session_id!r} not found at {events_path}[/red]")
        raise typer.Exit(code=1) from None

    if meta_path.exists():
        console.print(f"[dim]Metadata: {meta_path.read_text()}[/dim]")

    lines = events_path.read_text().splitlines()
    console.print(f"[cyan]Session {session_id} has {len(lines)} events[/cyan]")
    for line in lines[:20]:
        # truncate
        console.print(line[:500])
    if len(lines) > 20:
        console.print(f"[dim]... and {len(lines) - 20} more[/dim]")


# ---------------------------------------------------------------------------
# artifacts commands
# ---------------------------------------------------------------------------


def _get_artifact_store(
    config: pathlib.Path | None,
) -> tuple[Any, pathlib.Path]:  # type: ignore[no-untyped-def]
    """Return (store, path) from config or defaults. Does not start runtime."""
    from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore

    if config is not None and config.exists():
        try:
            cfg = load_config(config)
            store_path = pathlib.Path(cfg.artifacts.path)
            return SQLiteArtifactStore(path=store_path), store_path
        except Exception as e:
            console.print(f"[red]Failed to load config {config}: {e}[/red]")
            raise typer.Exit(code=1) from e
    # Default
    default_path = pathlib.Path(".research/artifacts.db")
    return SQLiteArtifactStore(path=default_path), default_path


@artifacts_app.command("list")
def artifacts_list(
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
    artifact_type: Annotated[
        str | None, typer.Option("--type", help="Filter by artifact_type")
    ] = None,
    session: Annotated[str | None, typer.Option("--session", help="Filter by session_id")] = None,
    limit: Annotated[int | None, typer.Option(help="Limit results")] = None,
) -> None:
    """List artifacts."""
    import asyncio

    cfg_path = config if config is not None and config.exists() else None
    store, _ = _get_artifact_store(cfg_path)

    async def _run() -> None:
        artifacts = await store.list(artifact_type=artifact_type, session_id=session, limit=limit)
        if not artifacts:
            console.print("[dim]No artifacts found[/dim]")
            return
        table = Table(title="Artifacts")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Type", style="magenta")
        table.add_column("Created", style="dim")
        table.add_column("Session", style="yellow")
        table.add_column("Hash", style="green")
        for env in artifacts:
            table.add_row(
                env.artifact_id[:8] + "…",
                env.artifact_type,
                env.created_at.isoformat()[:19],
                (env.session_id or "-")[:8],
                env.content_hash[:8] + "…",
            )
            # Show full id on second line for copy
            table.add_row(f"[dim]{env.artifact_id}[/dim]", "", "", "", "")
        console.print(table)
        await store.close()

    asyncio.run(_run())


@artifacts_app.command("inspect")
def artifacts_inspect(
    artifact_id: str,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect an artifact."""
    import asyncio
    import json

    cfg_path = config if config is not None and config.exists() else None
    store, _ = _get_artifact_store(cfg_path)

    async def _run() -> None:
        try:
            env = await store.get(artifact_id)
        except Exception as e:
            console.print(f"[red]Artifact {artifact_id!r} not found: {e}[/red]")
            raise typer.Exit(code=1) from e
        console.print(
            f"[bold cyan]{env.artifact_id}[/bold cyan] [{env.artifact_type}] v{env.schema_version}"
        )
        console.print(f"Created: {env.created_at.isoformat()} producer={env.producer}")
        console.print(f"Session: {env.session_id} Run: {env.run_id}")
        console.print(f"Hash: {env.content_hash}")
        console.print(f"Metadata: {json.dumps(env.metadata, indent=2)}")
        # Payload
        if hasattr(env.payload, "model_dump"):
            payload_str = json.dumps(env.payload.model_dump(mode="json"), indent=2)  # type: ignore[attr-defined]
        else:
            payload_str = json.dumps(env.payload, indent=2)  # type: ignore[arg-type]
        console.print("[bold]Payload:[/bold]")
        console.print(payload_str[:4000])
        # Provenance summary
        parents, children = await store.get_provenance(artifact_id)
        if parents:
            console.print(f"\n[bold]Parents ({len(parents)}):[/bold]")
            for p in parents:
                console.print(f"  {p.relation.value} ← {p.source_artifact_id}")
        if children:
            console.print(f"\n[bold]Children ({len(children)}):[/bold]")
            for c in children:
                console.print(f"  {c.relation.value} → {c.target_artifact_id}")
        await store.close()

    asyncio.run(_run())


@artifacts_app.command("lineage")
def artifacts_lineage(
    artifact_id: str,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
    direction: Annotated[str, typer.Option(help="ancestors or descendants")] = "ancestors",
) -> None:
    """Show lineage for an artifact."""
    import asyncio

    cfg_path = config if config is not None and config.exists() else None
    store, _ = _get_artifact_store(cfg_path)

    async def _run() -> None:
        try:
            # Verify artifact exists
            await store.get(artifact_id)
        except Exception as e:
            console.print(f"[red]Artifact {artifact_id!r} not found: {e}[/red]")
            raise typer.Exit(code=1) from e
        try:
            lineage = await store.get_lineage(artifact_id, direction=direction)
        except Exception as e:
            console.print(f"[red]Failed to get lineage: {e}[/red]")
            raise typer.Exit(code=1) from e
        if not lineage:
            console.print(f"[dim]No {direction} for {artifact_id}[/dim]")
        else:
            console.print(
                f"[bold]{direction.capitalize()} of {artifact_id} ({len(lineage)}):[/bold]"
            )
            for env in lineage:
                console.print(
                    f"  [cyan]{env.artifact_id}[/cyan] [{env.artifact_type}] {env.created_at.isoformat()[:19]}"
                )
                # Show title or statement snippet if available
                try:
                    if hasattr(env.payload, "title"):
                        console.print(f"    title: {env.payload.title}")  # type: ignore[attr-defined]
                    elif hasattr(env.payload, "statement"):
                        stmt = env.payload.statement  # type: ignore[attr-defined]
                        console.print(f"    statement: {stmt[:80]}")
                    elif hasattr(env.payload, "question"):
                        console.print(f"    question: {env.payload.question}")  # type: ignore[attr-defined]
                except Exception:
                    pass
        await store.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# literature commands
# ---------------------------------------------------------------------------


@literature_app.command("sources")
def literature_sources(
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """List configured/available literature providers."""
    # Load config if available
    if config is not None and config.exists():
        try:
            cfg = load_config(config)
            console.print(f"[dim]Config: {config}[/dim]")
            console.print(
                f"  crossref enabled: {cfg.literature.crossref.enabled} (timeout {cfg.literature.crossref.timeout_seconds}s, mailto={cfg.literature.crossref.mailto or 'not set'})"
            )
            console.print(
                f"  semantic_scholar enabled: {cfg.literature.semantic_scholar.enabled} (timeout {cfg.literature.semantic_scholar.timeout_seconds}s, api_key={'set' if __import__('os').getenv('SEMANTIC_SCHOLAR_API_KEY') else 'not set'})"
            )
            console.print(f"  plugins: {cfg.plugins}")
        except Exception as e:
            console.print(f"[red]Failed to load config: {e}[/red]")
    else:
        console.print("[dim]No config found, using defaults[/dim]")

    # List available literature source plugins
    from research_harness.app.bootstrap import list_available_plugins

    table = Table(title="Literature Sources")
    table.add_column("Service", style="cyan")
    table.add_column("Plugin ID", style="magenta")
    table.add_column("Source", style="dim")
    for pid, source in list_available_plugins():
        if pid.startswith("literature."):
            # Find provides
            try:
                from research_harness.app.bootstrap import create_plugin

                plugin = create_plugin(pid)
                provides = ", ".join(plugin.metadata.provides)
            except Exception:
                provides = "-"
            table.add_row(provides, pid, source)
    console.print(table)
    # Also show ingestion
    console.print(
        "[dim]Ingestion service: literature_ingestor.default (requires artifact_store.default)[/dim]"
    )


@literature_app.command("search")
def literature_search(
    query: Annotated[str, typer.Option(help="Search query")],
    source: Annotated[
        str, typer.Option(help="Provider: crossref or semantic_scholar")
    ] = "crossref",
    limit: Annotated[int, typer.Option(help="Max results")] = 10,
    year_from: Annotated[int | None, typer.Option(help="Filter year from")] = None,
    year_to: Annotated[int | None, typer.Option(help="Filter year to")] = None,
    page_token: Annotated[str | None, typer.Option(help="Opaque pagination token")] = None,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Search literature and ingest artifacts."""
    import asyncio

    # Normalize source
    src = source.strip().lower()
    if src not in ("crossref", "semantic_scholar", "semanticscholar"):
        console.print(f"[red]Unknown source {source!r}, use crossref or semantic_scholar[/red]")
        raise typer.Exit(code=1)
    if src == "semanticscholar":
        src = "semantic_scholar"

    async def _run() -> None:
        # Load config and build runtime with required plugins
        cfg_path = config if config is not None and config.exists() else None
        if cfg_path is not None:
            cfg = load_config(cfg_path)
        else:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "literature.crossref",
                        "literature.semantic_scholar",
                        "literature.ingestion",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                    "literature": {
                        "crossref": {"enabled": True, "timeout_seconds": 20},
                        "semantic_scholar": {"enabled": True, "timeout_seconds": 20},
                    },
                }
            )
        # Ensure required plugins are present
        required = ["storage.artifacts_sqlite", "literature.ingestion", f"literature.{src}"]
        for r in required:
            if r not in cfg.plugins:
                cfg.plugins.append(r)

        from research_harness.app.bootstrap import build_runtime
        from research_harness.contracts.literature import LiteratureSearchRequest

        runtime = build_runtime(cfg)
        async with runtime:
            # Resolve source and ingestor
            try:
                src_service = runtime.services.require(f"literature_source.{src}")
            except Exception as e:
                console.print(f"[red]Source {src!r} not available: {e}[/red]")
                raise typer.Exit(code=1) from e
            try:
                ingestor = runtime.services.require("literature_ingestor.default")
            except Exception as e:
                console.print(f"[red]Ingestor not available: {e}[/red]")
                raise typer.Exit(code=1) from e

            req = LiteratureSearchRequest(
                query=query,
                limit=limit,
                year_from=year_from,
                year_to=year_to,
                page_token=page_token,
            )
            console.print(f"[dim]Searching {src} for {query!r} (limit {limit})...[/dim]")
            try:
                search_env, snapshot_envs, paper_envs = await ingestor.ingest_search(
                    src_service, req
                )
            except Exception as e:
                console.print(f"[red]Search failed: {e}[/red]")
                import traceback

                console.print(f"[dim]{traceback.format_exc()}[/dim]")
                raise typer.Exit(code=1) from e

            from research_harness.research.schemas.paper import PaperRecord
            from research_harness.research.schemas.search_record import LiteratureSearchRecord

            console.print(f"[green]✓ Search artifact: {search_env.artifact_id}[/green]")
            search_rec = search_env.parse_payload(LiteratureSearchRecord)
            console.print(
                f"  Provider: {src}  Returned: {len(paper_envs)}  Total estimate: {search_rec.total_estimate}"
            )
            console.print("  Papers:")
            for pe in paper_envs:
                paper = pe.parse_payload(PaperRecord)
                console.print(
                    f"    [cyan]{pe.artifact_id[:8]}[/cyan] {paper.title[:80]}  year={paper.year}  doi={paper.doi or '-'}"
                )

            if snapshot_envs:
                console.print(
                    f"\n[dim]Provider snapshots: {[e.artifact_id[:8] for e in snapshot_envs]}[/dim]"
                )
            if search_rec.pagination.get("next_page_token"):
                console.print(
                    f"[dim]Next page token: {search_rec.pagination['next_page_token']}[/dim]"
                )

    asyncio.run(_run())


@literature_app.command("get")
def literature_get(
    identifier: Annotated[
        str,
        typer.Argument(
            help="DOI or paperId (for crossref use DOI, for semantic_scholar use paperId or DOI:10...)"
        ),
    ],
    source: Annotated[
        str, typer.Option(help="Provider: crossref or semantic_scholar")
    ] = "crossref",
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Retrieve a single paper by identifier."""
    import asyncio

    src = source.strip().lower()
    if src not in ("crossref", "semantic_scholar", "semanticscholar"):
        console.print(f"[red]Unknown source {source!r}[/red]")
        raise typer.Exit(code=1)
    if src == "semanticscholar":
        src = "semantic_scholar"

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        if cfg_path is not None:
            cfg = load_config(cfg_path)
        else:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "literature.crossref",
                        "literature.semantic_scholar",
                        "literature.ingestion",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        for r in [f"literature.{src}", "storage.artifacts_sqlite", "literature.ingestion"]:
            if r not in cfg.plugins:
                cfg.plugins.append(r)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            src_service = runtime.services.require(f"literature_source.{src}")
            ingestor = runtime.services.require("literature_ingestor.default")
            console.print(f"[dim]Fetching {identifier!r} from {src}...[/dim]")
            try:
                snapshot_env, paper_env = await ingestor.ingest_get(src_service, identifier)
            except Exception as e:
                console.print(f"[red]Get failed: {e}[/red]")
                import traceback

                console.print(f"[dim]{traceback.format_exc()}[/dim]")
                raise typer.Exit(code=1) from e

            from research_harness.research.schemas.paper import PaperRecord

            paper = paper_env.parse_payload(PaperRecord)
            console.print(f"[green]✓ Paper artifact: {paper_env.artifact_id}[/green]")
            console.print(f"  Title: {paper.title}")
            console.print(f"  Year: {paper.year}  Venue: {paper.venue}")
            console.print(f"  DOI: {paper.doi}")
            console.print(f"  Authors: {', '.join(a.name for a in paper.authors[:3])}")
            console.print(f"  Snapshot: {snapshot_env.artifact_id}")

    asyncio.run(_run())


@literature_app.command("plan")
def literature_plan(
    question: Annotated[str, typer.Option(help="ResearchQuestion artifact id")],
    research_plan: Annotated[
        str | None, typer.Option("--research-plan", help="ResearchPlan artifact id")
    ] = None,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Plan literature search strategy from a ResearchQuestion."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "literature.search_planner",
                        "model.openrouter",
                        "routing.role_router",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                    "literature": {"planning": {"model_role": "fast", "max_queries": 8}},
                }
            )
        # Ensure planner dependencies
        for req in ["storage.artifacts_sqlite", "literature.search_planner"]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)
        # Also need model router if planner uses it
        if "model.openrouter" not in cfg.plugins and "routing.role_router" not in cfg.plugins:
            cfg.plugins.extend(["model.openrouter", "routing.role_router"])

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            planner = runtime.services.require("literature_search_planner.default")
            console.print(f"[dim]Planning search for question {question[:8]}...[/dim]")
            try:
                strat_id, query_ids = await planner.plan(question, research_plan)
            except Exception as e:
                console.print(f"[red]Planning failed: {e}[/red]")
                import traceback

                console.print(f"[dim]{traceback.format_exc()}[/dim]")
                raise typer.Exit(code=1) from e

            console.print(f"[green]✓ Strategy artifact: {strat_id}[/green]")
            console.print(f"  Queries ({len(query_ids)}):")
            from research_harness.research.schemas.query import LiteratureQuery

            for qid in query_ids:
                q_env = await runtime.services.require("artifact_store.default").get(qid)
                q = q_env.parse_payload(LiteratureQuery)
                console.print(
                    f"    [cyan]{qid[:8]}[/cyan] {q.query}  sources={q.target_sources}  purpose={q.purpose or '-'}"
                )

            # Also show strategy details
            strat_env = await runtime.services.require("artifact_store.default").get(strat_id)
            from research_harness.research.schemas.strategy import LiteratureSearchStrategy

            strat = strat_env.parse_payload(LiteratureSearchStrategy)
            console.print(f"  Objective: {strat.objective}")
            console.print(f"  Source names: {strat.source_names}")

    asyncio.run(_run())


@literature_app.command("execute")
def literature_execute(
    strategy: Annotated[str, typer.Option(help="LiteratureSearchStrategy artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Execute a search strategy across providers."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite", "literature.search_orchestrator"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        for req in ["storage.artifacts_sqlite", "literature.search_orchestrator"]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)
        # Also need sources and ingestor and resolver
        for req in [
            "literature.crossref",
            "literature.semantic_scholar",
            "literature.ingestion",
            "literature.identity_resolver",
        ]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            orchestrator = runtime.services.require("literature_search_orchestrator.default")
            console.print(f"[dim]Executing strategy {strategy[:8]}...[/dim]")
            try:
                exec_id = await orchestrator.execute(strategy)
            except Exception as e:
                console.print(f"[red]Execution failed: {e}[/red]")
                import traceback

                console.print(f"[dim]{traceback.format_exc()}[/dim]")
                raise typer.Exit(code=1) from e

            console.print(f"[green]✓ Execution artifact: {exec_id}[/green]")
            store = runtime.services.require("artifact_store.default")
            exec_env = await store.get(exec_id)
            from research_harness.research.schemas.execution import LiteratureSearchExecution

            rec = exec_env.parse_payload(LiteratureSearchExecution)
            console.print(f"  Queries executed: {rec.counts.get('queries_executed')}")
            console.print(
                f"  Provider searches: attempted={rec.counts.get('provider_searches_attempted')} succeeded={rec.counts.get('provider_searches_succeeded')} failed={rec.counts.get('provider_searches_failed')}"
            )
            console.print(
                f"  Raw papers: {rec.counts.get('raw_paper_records')}  Unique identities: {rec.counts.get('unique_paper_identities')}  Duplicates collapsed: {rec.counts.get('duplicate_records_collapsed')}"
            )
            if rec.provider_failures:
                console.print(f"  Failures: {rec.provider_failures}")
            console.print(f"  Paper identities: {rec.paper_identity_artifact_ids[:3]}")

    asyncio.run(_run())


@literature_app.command("discover")
def literature_discover(
    question: Annotated[str, typer.Option(help="ResearchQuestion artifact id")],
    research_plan: Annotated[
        str | None, typer.Option("--research-plan", help="ResearchPlan artifact id")
    ] = None,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Convenience: plan + execute from a ResearchQuestion."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "literature.search_planner",
                        "literature.search_orchestrator",
                        "literature.crossref",
                        "literature.semantic_scholar",
                        "literature.ingestion",
                        "literature.identity_resolver",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        for req in [
            "storage.artifacts_sqlite",
            "literature.search_planner",
            "literature.search_orchestrator",
            "literature.crossref",
            "literature.semantic_scholar",
            "literature.ingestion",
            "literature.identity_resolver",
        ]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)
        # Need model router for planner
        if "model.openrouter" not in cfg.plugins:
            cfg.plugins.extend(["model.openrouter", "routing.role_router"])

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            planner = runtime.services.require("literature_search_planner.default")
            orchestrator = runtime.services.require("literature_search_orchestrator.default")
            console.print(f"[dim]Planning for question {question[:8]}...[/dim]")
            strat_id, _ = await planner.plan(question, research_plan)
            console.print(f"[green]✓ Strategy: {strat_id}[/green]")
            console.print("[dim]Executing strategy...[/dim]")
            exec_id = await orchestrator.execute(strat_id)
            console.print(f"[green]✓ Execution: {exec_id}[/green]")
            store = runtime.services.require("artifact_store.default")
            exec_env = await store.get(exec_id)
            from research_harness.research.schemas.execution import LiteratureSearchExecution

            rec = exec_env.parse_payload(LiteratureSearchExecution)
            console.print(
                f"  Raw papers: {rec.counts.get('raw_paper_records')}  Unique: {rec.counts.get('unique_paper_identities')}"
            )

    asyncio.run(_run())


# Identities sub-commands (group)
identities_app = typer.Typer(help="Paper identity commands")
literature_app.add_typer(identities_app, name="identities")


@identities_app.command("list")
def identities_list(
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
    limit: Annotated[int | None, typer.Option(help="Limit")] = None,
) -> None:
    """List paper identities."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore

        # Use store directly to avoid needing full runtime
        if cfg_path is not None and cfg_path.exists():
            cfg = load_config(cfg_path)
            path = cfg.artifacts.path
        else:
            path = ".research/artifacts.db"
        store = SQLiteArtifactStore(path=path)
        identities = await store.list(artifact_type="paper_identity", limit=limit)
        if not identities:
            console.print("[dim]No paper identities found[/dim]")
            await store.close()
            return
        table = Table(title="Paper Identities")
        table.add_column("ID", style="cyan")
        table.add_column("Members", style="magenta")
        table.add_column("Method", style="yellow")
        table.add_column("DOI", style="green")
        for env in identities:
            from research_harness.research.schemas.identity import PaperIdentity

            ident = env.parse_payload(PaperIdentity)
            doi = next((e.value for e in ident.canonical_identifiers if e.scheme == "doi"), "-")
            table.add_row(
                env.artifact_id[:8],
                str(len(ident.member_paper_artifact_ids)),
                ident.resolution_method.value,
                doi,
            )
            table.add_row(f"[dim]{env.artifact_id}[/dim]", "", "", "")
        console.print(table)
        await store.close()

    asyncio.run(_run())


@identities_app.command("inspect")
def identities_inspect(
    identity_id: str,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect a paper identity."""
    import asyncio
    import json

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore

        if cfg_path is not None and cfg_path.exists():
            cfg = load_config(cfg_path)
            path = cfg.artifacts.path
        else:
            path = ".research/artifacts.db"
        store = SQLiteArtifactStore(path=path)
        try:
            env = await store.get(identity_id)
        except Exception as e:
            console.print(f"[red]Identity {identity_id!r} not found: {e}[/red]")
            raise typer.Exit(code=1) from e
        from research_harness.research.schemas.identity import PaperIdentity

        ident = env.parse_payload(PaperIdentity)
        console.print(f"[bold cyan]{env.artifact_id}[/bold cyan] [{env.artifact_type}]")
        console.print(
            f"Members ({len(ident.member_paper_artifact_ids)}): {ident.member_paper_artifact_ids}"
        )
        console.print(f"Method: {ident.resolution_method.value}  Confidence: {ident.confidence}")
        console.print(
            f"Canonical identifiers: {[f'{e.scheme}:{e.value}' for e in ident.canonical_identifiers]}"
        )
        console.print(
            f"Evidence: {json.dumps([e.model_dump() for e in ident.resolution_evidence], indent=2)}"
        )
        # Show member titles
        for pid in ident.member_paper_artifact_ids[:3]:
            try:
                p_env = await store.get(pid)
                from research_harness.research.schemas.paper import PaperRecord

                paper = p_env.parse_payload(PaperRecord)
                console.print(f"  [dim]{pid[:8]}[/dim] {paper.title}  doi={paper.doi}")
            except Exception:
                pass
        # Check if superseded
        children_links = await store.get_children(identity_id)
        superseded_by = [
            link.target_artifact_id
            for link in children_links
            if link.relation.value == "supersedes"
        ]
        if superseded_by:
            console.print(f"[yellow]Superseded by: {superseded_by}[/yellow]")
        await store.close()

    asyncio.run(_run())


# Screening sub-commands
screening_app = typer.Typer(help="Screening commands")
literature_app.add_typer(screening_app, name="screening")

screening_protocol_app = typer.Typer(help="Screening protocol commands")
screening_app.add_typer(screening_protocol_app, name="protocol")

screening_decisions_app = typer.Typer(help="Screening decisions")
screening_app.add_typer(screening_decisions_app, name="decisions")

screening_sets_app = typer.Typer(help="Screened literature sets")
screening_app.add_typer(screening_sets_app, name="sets")

evidence_app = typer.Typer(help="Evidence extraction (Phase 2F)")
literature_app.add_typer(evidence_app, name="evidence")

synthesis_app = typer.Typer(help="Cross-paper synthesis (Phase 2G)")
literature_app.add_typer(synthesis_app, name="synthesis")

synthesis_themes_app = typer.Typer(help="Synthesis themes")
synthesis_app.add_typer(synthesis_themes_app, name="themes")

gaps_app = typer.Typer(help="Research gap analysis (Phase 2H)")
literature_app.add_typer(gaps_app, name="gaps")

gaps_analysis_app = typer.Typer(help="Gap analysis artifacts")
gaps_app.add_typer(gaps_analysis_app, name="analysis")

evidence_profiles_app = typer.Typer(help="Paper research profiles")
evidence_app.add_typer(evidence_profiles_app, name="profiles")

evidence_items_app = typer.Typer(help="Evidence items")
evidence_app.add_typer(evidence_items_app, name="items")


@screening_protocol_app.command("create")
def screening_protocol_create(
    question: Annotated[str, typer.Option(help="ResearchQuestion artifact id")],
    research_plan: Annotated[
        str | None, typer.Option("--research-plan", help="ResearchPlan artifact id")
    ] = None,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Create a screening protocol (model-assisted, approval-gated)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "literature.screening_protocol_builder",
                        "model.openrouter",
                        "routing.role_router",
                        "autonomy.configurable",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        for req in ["storage.artifacts_sqlite", "literature.screening_protocol_builder"]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)
        if "model.openrouter" not in cfg.plugins:
            cfg.plugins.extend(["model.openrouter", "routing.role_router", "autonomy.configurable"])

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            builder = runtime.services.require("screening_protocol_builder.default")
            try:
                proto_id = await builder.build(question, research_plan)
            except Exception as e:
                console.print(f"[red]Protocol creation failed: {e}[/red]")
                import traceback

                console.print(f"[dim]{traceback.format_exc()}[/dim]")
                raise typer.Exit(code=1) from e
            console.print(f"[green]✓ Protocol artifact: {proto_id}[/green]")
            # Inspect
            store = runtime.services.require("artifact_store.default")
            env = await store.get(proto_id)
            from research_harness.research.schemas.screening_protocol import ScreeningProtocol

            proto = env.parse_payload(ScreeningProtocol)
            console.print(f"  Objective: {proto.objective}")
            console.print(f"  Status: {proto.status.value}")
            console.print(
                f"  Inclusion ({len(proto.inclusion_criteria)}): {[c.criterion_id for c in proto.inclusion_criteria]}"
            )
            console.print(
                f"  Exclusion ({len(proto.exclusion_criteria)}): {[c.criterion_id for c in proto.exclusion_criteria]}"
            )

    asyncio.run(_run())


@screening_protocol_app.command("inspect")
def screening_protocol_inspect(
    protocol_id: str,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect a screening protocol."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore

        if cfg_path is not None and cfg_path.exists():
            cfg = load_config(cfg_path)
            path = cfg.artifacts.path
        else:
            path = ".research/artifacts.db"
        store = SQLiteArtifactStore(path=path)
        try:
            env = await store.get(protocol_id)
        except Exception as e:
            console.print(f"[red]Protocol {protocol_id!r} not found: {e}[/red]")
            raise typer.Exit(code=1) from e
        from research_harness.research.schemas.screening_protocol import ScreeningProtocol

        proto = env.parse_payload(ScreeningProtocol)
        console.print(
            f"[bold cyan]{env.artifact_id}[/bold cyan] [{env.artifact_type}] {env.created_at.isoformat()}"
        )
        console.print(f"Objective: {proto.objective}")
        console.print(f"Status: {proto.status.value}")
        console.print(f"Inclusion ({len(proto.inclusion_criteria)}):")
        for c in proto.inclusion_criteria:
            console.print(
                f"  [green]{c.criterion_id}[/green] {c.description} (required={c.required})"
            )
        console.print(f"Exclusion ({len(proto.exclusion_criteria)}):")
        for c in proto.exclusion_criteria:
            console.print(f"  [red]{c.criterion_id}[/red] {c.description}")
        console.print(f"Decision rules: {proto.decision_rules}")
        # Check if superseded
        children = await store.get_children(protocol_id)
        superseded = [c.target_artifact_id for c in children if c.relation.value == "supersedes"]
        if superseded:
            console.print(f"[yellow]Superseded by: {superseded}[/yellow]")
        await store.close()

    asyncio.run(_run())


@screening_protocol_app.command("approve")
def screening_protocol_approve(
    protocol_id: str,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Approve a screening protocol (for interactive flow)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
        from research_harness.research.provenance.relations import (
            ProvenanceLink,
            ProvenanceRelation,
        )

        if cfg_path is not None and cfg_path.exists():
            cfg = load_config(cfg_path)
            path = cfg.artifacts.path
        else:
            path = ".research/artifacts.db"
        store = SQLiteArtifactStore(path=path)
        try:
            env = await store.get(protocol_id)
        except Exception as e:
            console.print(f"[red]Protocol {protocol_id!r} not found: {e}[/red]")
            raise typer.Exit(code=1) from e
        from research_harness.research.schemas.screening_protocol import (
            ProtocolStatus,
            ScreeningProtocol,
        )

        proto = env.parse_payload(ScreeningProtocol)
        if proto.status == ProtocolStatus.approved:
            console.print(f"[green]Protocol {protocol_id} already approved[/green]")
            await store.close()
            return
        # Create approved version that supersedes draft
        approved = proto.model_copy(update={"status": ProtocolStatus.approved})
        from research_harness.research.envelope import ArtifactEnvelope

        new_env = ArtifactEnvelope.create(
            payload=approved, artifact_type="screening_protocol", producer="cli.approve"
        )
        await store.put(new_env)
        await store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.supersedes,
                source_artifact_id=protocol_id,
                target_artifact_id=new_env.artifact_id,
                producer="cli.approve",
            )
        )
        console.print(
            f"[green]✓ Approved protocol: {new_env.artifact_id} supersedes {protocol_id}[/green]"
        )
        await store.close()

    asyncio.run(_run())


@screening_app.command("run")
def screening_run(
    search_execution: Annotated[
        str, typer.Option("--search-execution", help="LiteratureSearchExecution artifact id")
    ],
    protocol: Annotated[
        str, typer.Option("--protocol", help="Approved ScreeningProtocol artifact id")
    ],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Execute screening over a search execution."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "literature.screening_view_builder",
                        "literature.title_abstract_screener",
                        "literature.screening_orchestrator",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        for req in [
            "storage.artifacts_sqlite",
            "literature.screening_view_builder",
            "literature.title_abstract_screener",
            "literature.screening_orchestrator",
        ]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)
        if "model.openrouter" not in cfg.plugins:
            cfg.plugins.extend(["model.openrouter", "routing.role_router", "autonomy.configurable"])

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            orchestrator = runtime.services.require("screening_orchestrator.default")
            try:
                exec_id = await orchestrator.screen(search_execution, protocol)
            except Exception as e:
                console.print(f"[red]Screening failed: {e}[/red]")
                import traceback

                console.print(f"[dim]{traceback.format_exc()}[/dim]")
                raise typer.Exit(code=1) from e
            console.print(f"[green]✓ Screening execution: {exec_id}[/green]")
            store = runtime.services.require("artifact_store.default")
            exec_env = await store.get(exec_id)
            from research_harness.research.schemas.screening_execution import ScreeningExecution

            rec = exec_env.parse_payload(ScreeningExecution)
            console.print(
                f"  Total: {rec.counts.get('total_candidates')}  Included: {rec.counts.get('included')}  Excluded: {rec.counts.get('excluded')}  Uncertain: {rec.counts.get('uncertain')}  Failed: {rec.counts.get('failed')}  Reused: {rec.counts.get('reused')}"
            )
            # Find screened set for this execution
            all_sets = await store.list(artifact_type="screened_literature_set")
            for s_env in all_sets:
                from research_harness.research.schemas.screening_execution import (
                    ScreenedLiteratureSet,
                )

                s = s_env.parse_payload(ScreenedLiteratureSet)
                if s.screening_execution_id == exec_id:
                    console.print(
                        f"  Screened set: {s_env.artifact_id}  Included: {len(s.included_identity_ids)}  Excluded: {len(s.excluded_identity_ids)}  Uncertain: {len(s.uncertain_identity_ids)}"
                    )
                    break

    asyncio.run(_run())


@screening_decisions_app.command("list")
def screening_decisions_list(
    execution: Annotated[
        str | None, typer.Option("--execution", help="ScreeningExecution artifact id")
    ] = None,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """List screening decisions."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore

        if cfg_path is not None and cfg_path.exists():
            cfg = load_config(cfg_path)
            path = cfg.artifacts.path
        else:
            path = ".research/artifacts.db"
        store = SQLiteArtifactStore(path=path)
        # If execution filter, get decision ids from execution
        filter_ids: set[str] | None = None
        if execution:
            try:
                exec_env = await store.get(execution)
                from research_harness.research.schemas.screening_execution import ScreeningExecution

                rec = exec_env.parse_payload(ScreeningExecution)
                filter_ids = set(rec.decision_artifact_ids)
            except Exception as e:
                console.print(f"[red]Execution {execution!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
        decisions = await store.list(artifact_type="screening_decision")
        if filter_ids is not None:
            decisions = [d for d in decisions if d.artifact_id in filter_ids]
        if not decisions:
            console.print("[dim]No decisions found[/dim]")
            await store.close()
            return
        table = Table(title="Screening Decisions")
        table.add_column("ID", style="cyan")
        table.add_column("PaperIdentity", style="magenta")
        table.add_column("Decision", style="yellow")
        table.add_column("Confidence", style="green")
        table.add_column("Protocol", style="dim")
        for env in decisions:
            from research_harness.research.schemas.screening_decision import ScreeningDecision

            dec = env.parse_payload(ScreeningDecision)
            table.add_row(
                env.artifact_id[:8],
                dec.paper_identity_id[:8],
                dec.decision.value,
                str(dec.confidence),
                dec.screening_protocol_id[:8],
            )
            table.add_row(f"[dim]{env.artifact_id}[/dim]", "", "", "", "")
        console.print(table)
        await store.close()

    asyncio.run(_run())


@screening_decisions_app.command("inspect")
def screening_decisions_inspect(
    decision_id: str,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect a screening decision."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore

        if cfg_path is not None and cfg_path.exists():
            cfg = load_config(cfg_path)
            path = cfg.artifacts.path
        else:
            path = ".research/artifacts.db"
        store = SQLiteArtifactStore(path=path)
        try:
            env = await store.get(decision_id)
        except Exception as e:
            console.print(f"[red]Decision {decision_id!r} not found: {e}[/red]")
            raise typer.Exit(code=1) from e
        from research_harness.research.schemas.screening_decision import ScreeningDecision

        dec = env.parse_payload(ScreeningDecision)
        console.print(f"[bold cyan]{env.artifact_id}[/bold cyan] [{env.artifact_type}]")
        console.print(f"PaperIdentity: {dec.paper_identity_id}")
        console.print(f"View: {dec.screening_view_id}  Protocol: {dec.screening_protocol_id}")
        console.print(
            f"Decision: {dec.decision.value}  Confidence: {dec.confidence}  Sufficiency: {dec.information_sufficiency.value}"
        )
        console.print(f"Matched inclusion: {dec.matched_inclusion_criteria}")
        console.print(f"Matched exclusion: {dec.matched_exclusion_criteria}")
        console.print(f"Reason codes: {dec.reason_codes}")
        console.print(f"Rationale: {dec.rationale_summary}")
        # Try to find review
        reviews = await store.list(artifact_type="screening_review")
        for r_env in reviews:
            from research_harness.research.schemas.screening_review import ScreeningReview

            rev = r_env.parse_payload(ScreeningReview)
            if rev.screening_decision_id == decision_id:
                console.print(
                    f"[yellow]Review: {r_env.artifact_id} original={rev.original_decision} final={rev.final_decision} reviewer={rev.reviewer_type.value}[/yellow]"
                )
        await store.close()

    asyncio.run(_run())


@screening_sets_app.command("list")
def screening_sets_list(
    execution: Annotated[
        str | None, typer.Option("--execution", help="Filter by ScreeningExecution id")
    ] = None,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """List screened literature sets."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore

        if cfg_path is not None and cfg_path.exists():
            cfg = load_config(cfg_path)
            path = cfg.artifacts.path
        else:
            path = ".research/artifacts.db"
        store = SQLiteArtifactStore(path=path)
        sets = await store.list(artifact_type="screened_literature_set")
        if execution:
            sets = [
                s
                for s in sets
                if s.payload.get("screening_execution_id") == execution
                or s.parse_payload(
                    __import__(
                        "research_harness.research.schemas.screening_execution",
                        fromlist=["ScreenedLiteratureSet"],
                    ).ScreenedLiteratureSet
                ).screening_execution_id
                == execution
            ]  # type: ignore[attr-defined]
        if not sets:
            console.print("[dim]No screened sets found[/dim]")
            await store.close()
            return
        table = Table(title="Screened Literature Sets")
        table.add_column("ID", style="cyan")
        table.add_column("Execution", style="magenta")
        table.add_column("Protocol", style="yellow")
        table.add_column("Included", style="green")
        table.add_column("Excluded", style="red")
        table.add_column("Uncertain", style="dim")
        for env in sets:
            from research_harness.research.schemas.screening_execution import ScreenedLiteratureSet

            s = env.parse_payload(ScreenedLiteratureSet)
            table.add_row(
                env.artifact_id[:8],
                s.screening_execution_id[:8],
                s.screening_protocol_id[:8],
                str(len(s.included_identity_ids)),
                str(len(s.excluded_identity_ids)),
                str(len(s.uncertain_identity_ids)),
            )
            table.add_row(f"[dim]{env.artifact_id}[/dim]", "", "", "", "", "")
        console.print(table)
        await store.close()

    asyncio.run(_run())


@screening_sets_app.command("inspect")
def screening_sets_inspect(
    set_id: str,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect a screened literature set."""
    import asyncio
    import json

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore

        if cfg_path is not None and cfg_path.exists():
            cfg = load_config(cfg_path)
            path = cfg.artifacts.path
        else:
            path = ".research/artifacts.db"
        store = SQLiteArtifactStore(path=path)
        try:
            env = await store.get(set_id)
        except Exception as e:
            console.print(f"[red]Set {set_id!r} not found: {e}[/red]")
            raise typer.Exit(code=1) from e
        from research_harness.research.schemas.screening_execution import ScreenedLiteratureSet

        s = env.parse_payload(ScreenedLiteratureSet)
        console.print(f"[bold cyan]{env.artifact_id}[/bold cyan] [{env.artifact_type}]")
        console.print(f"Execution: {s.screening_execution_id}")
        console.print(f"Protocol: {s.screening_protocol_id}")
        console.print(f"Included ({len(s.included_identity_ids)}): {s.included_identity_ids[:5]}")
        console.print(f"Excluded ({len(s.excluded_identity_ids)}): {s.excluded_identity_ids[:5]}")
        console.print(
            f"Uncertain ({len(s.uncertain_identity_ids)}): {s.uncertain_identity_ids[:5]}"
        )
        console.print(f"Decisions ({len(s.decision_artifact_ids)}): {s.decision_artifact_ids[:5]}")
        console.print(f"Created: {s.created_at.isoformat()}")
        if s.metadata:
            console.print(f"Metadata: {json.dumps(s.metadata, indent=2)}")
        # Provenance
        parents, _children = await store.get_provenance(set_id)
        if parents:
            console.print(f"\n[bold]Parents ({len(parents)}):[/bold]")
            for p in parents:
                console.print(f"  {p.relation.value} ← {p.source_artifact_id}")
        await store.close()

    asyncio.run(_run())


@screening_app.command("review")
def screening_review(
    decision: Annotated[str, typer.Option(help="ScreeningDecision artifact id")],
    final: Annotated[str, typer.Option(help="Final disposition: include, exclude, uncertain")],
    notes: Annotated[str | None, typer.Option(help="Review notes")] = None,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Review (override) a screening decision."""
    import asyncio

    if final not in ("include", "exclude", "uncertain"):
        console.print(f"[red]Invalid final {final!r}, must be include/exclude/uncertain[/red]")
        raise typer.Exit(code=1)

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
        from research_harness.research.schemas.screening_review import ReviewerType, ScreeningReview

        if cfg_path is not None and cfg_path.exists():
            cfg = load_config(cfg_path)
            path = cfg.artifacts.path
        else:
            path = ".research/artifacts.db"
        store = SQLiteArtifactStore(path=path)
        try:
            dec_env = await store.get(decision)
        except Exception as e:
            console.print(f"[red]Decision {decision!r} not found: {e}[/red]")
            raise typer.Exit(code=1) from e
        from research_harness.research.schemas.screening_decision import ScreeningDecision

        dec = dec_env.parse_payload(ScreeningDecision)
        review = ScreeningReview(
            screening_decision_id=decision,
            review_reason="human_override",
            original_decision=dec.decision.value,
            final_decision=final,
            reviewer_type=ReviewerType.human,
            notes=notes,
        )
        from research_harness.research.envelope import ArtifactEnvelope
        from research_harness.research.provenance.relations import (
            ProvenanceLink,
            ProvenanceRelation,
        )

        env = ArtifactEnvelope.create(
            payload=review, artifact_type="screening_review", producer="cli.review"
        )
        await store.put(env)
        await store.add_provenance(
            ProvenanceLink(
                relation=ProvenanceRelation.derived_from,
                source_artifact_id=decision,
                target_artifact_id=env.artifact_id,
                producer="cli.review",
            )
        )
        console.print(
            f"[green]✓ Review artifact: {env.artifact_id} final={final} (original {dec.decision.value} preserved)[/green]"
        )
        await store.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# documents commands
# ---------------------------------------------------------------------------

documents_app = typer.Typer(help="Document acquisition and extraction")
literature_app.add_typer(documents_app, name="documents")


@documents_app.command("locate")
def documents_locate(
    set_id: Annotated[str, typer.Option("--set", help="ScreenedLiteratureSet artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Locate candidate document URLs for a screened set."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg_obj = load_config(cfg_path) if cfg_path is not None else None
        if cfg_obj is None:
            from research_harness.config.loader import load_config_from_dict

            cfg_obj = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "storage.blobs_filesystem",
                        "documents.locator.metadata",
                        "documents.locator.unpaywall",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                    "documents": {"blob_root": ".research/blobs"},
                }
            )
        for req in [
            "storage.artifacts_sqlite",
            "storage.blobs_filesystem",
            "documents.locator.metadata",
            "documents.locator.unpaywall",
        ]:
            if req not in cfg_obj.plugins:
                cfg_obj.plugins.append(req)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg_obj)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            # Load screened set
            try:
                s_env = await store.get(set_id)
            except Exception as e:
                console.print(f"[red]Screened set {set_id!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
            from research_harness.research.schemas.screening_execution import ScreenedLiteratureSet

            screened_set = s_env.parse_payload(ScreenedLiteratureSet)
            total = len(screened_set.included_identity_ids)
            console.print(f"[dim]Resolving {total} included identities...[/dim]")
            # Try both locators if available
            meta_loc = runtime.services.get("document_locator.metadata")
            unpaywall_loc = runtime.services.get("document_locator.unpaywall")

            found = 0
            for pi_id in screened_set.included_identity_ids[:10]:
                locs: list[str] = []
                if meta_loc:
                    try:
                        locs.extend(await meta_loc.resolve(pi_id))
                    except Exception as e:
                        console.print(
                            f"[yellow]metadata locator failed for {pi_id[:8]}: {e}[/yellow]"
                        )
                if unpaywall_loc:
                    try:
                        locs.extend(await unpaywall_loc.resolve(pi_id))
                    except Exception as e:
                        console.print(
                            f"[yellow]unpaywall locator failed for {pi_id[:8]}: {e}[/yellow]"
                        )
                if locs:
                    found += 1
                    console.print(f"[green]✓ {pi_id[:8]} -> {len(locs)} location(s)[/green]")
                else:
                    console.print(f"[dim]  {pi_id[:8]} no location[/dim]")
            console.print(f"[bold]Found locations for {found}/{total} included[/bold]")

    asyncio.run(_run())


@documents_app.command("acquire")
def documents_acquire(
    set_id: Annotated[str, typer.Option("--set", help="ScreenedLiteratureSet artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Acquire documents for a screened set (fetch + extract)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg_obj = load_config(cfg_path) if cfg_path is not None else None
        if cfg_obj is None:
            from research_harness.config.loader import load_config_from_dict

            cfg_obj = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "storage.blobs_filesystem",
                        "documents.locator.metadata",
                        "documents.locator.unpaywall",
                        "documents.fetcher.http",
                        "documents.extractor.pypdf",
                        "documents.acquisition_orchestrator",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                    "documents": {"blob_root": ".research/blobs"},
                }
            )
        for req in [
            "storage.artifacts_sqlite",
            "storage.blobs_filesystem",
            "documents.locator.metadata",
            "documents.locator.unpaywall",
            "documents.fetcher.http",
            "documents.extractor.pypdf",
            "documents.acquisition_orchestrator",
        ]:
            if req not in cfg_obj.plugins:
                cfg_obj.plugins.append(req)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg_obj)
        async with runtime:
            orchestrator = runtime.services.require("document_acquisition_orchestrator.default")
            try:
                exec_id = await orchestrator.run(set_id)
            except Exception as e:
                console.print(f"[red]Acquisition failed: {e}[/red]")
                import traceback

                console.print(f"[dim]{traceback.format_exc()}[/dim]")
                raise typer.Exit(code=1) from e
            console.print(f"[green]✓ Document acquisition execution: {exec_id}[/green]")
            store = runtime.services.require("artifact_store.default")
            exec_env = await store.get(exec_id)
            from research_harness.research.schemas.full_text import DocumentAcquisitionExecution

            rec = exec_env.parse_payload(DocumentAcquisitionExecution)
            console.print(
                f"  Total included: {rec.counts.get('total_included')}  Downloaded: {rec.counts.get('downloaded')}  No location: {rec.counts.get('no_location')}  Failed: {rec.counts.get('failed')}"
            )
            console.print(
                f"  Text extracted: {rec.counts.get('text_extracted')}  Insufficient: {rec.counts.get('insufficient_text')}"
            )
            # Find corpus
            corpora = await store.list(artifact_type="full_text_corpus")
            for c_env in corpora:
                from research_harness.research.schemas.full_text import FullTextCorpus

                c = c_env.parse_payload(FullTextCorpus)
                if c.document_acquisition_execution_id == exec_id:
                    console.print(
                        f"  Corpus: {c_env.artifact_id}  Available docs: {len(c.available_document_ids)}"
                    )
                    break

    asyncio.run(_run())


# Convenience alias `run`
@documents_app.command("run")
def documents_run(
    set_id: Annotated[str, typer.Option("--set", help="ScreenedLiteratureSet artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """End-to-end: locate + acquire + extract for a screened set."""
    # Same as acquire (orchestrator does both)
    documents_acquire(set_id, config)


@documents_app.command("list")
def documents_list(
    set_id: Annotated[
        str | None, typer.Option("--set", help="Filter by ScreenedLiteratureSet id")
    ] = None,
    execution: Annotated[
        str | None, typer.Option("--execution", help="Filter by DocumentAcquisitionExecution id")
    ] = None,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """List FullTextDocuments."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore

        if cfg_path is not None and cfg_path.exists():
            cfg_obj = load_config(cfg_path)
            path = cfg_obj.artifacts.path
        else:
            path = ".research/artifacts.db"
        store = SQLiteArtifactStore(path=path)
        docs = await store.list(artifact_type="full_text_document")
        # Filter by set/execution via corpus
        if set_id or execution:
            # Load corpus to filter
            corpora = await store.list(artifact_type="full_text_corpus")
            allowed_ids: set[str] | None = None
            for c_env in corpora:
                from research_harness.research.schemas.full_text import FullTextCorpus

                c = c_env.parse_payload(FullTextCorpus)
                if set_id and c.screened_literature_set_id != set_id:
                    continue
                if execution and c.document_acquisition_execution_id != execution:
                    continue
                if allowed_ids is None:
                    allowed_ids = set()
                allowed_ids.update(c.available_document_ids)
            if allowed_ids is not None:
                docs = [d for d in docs if d.artifact_id in allowed_ids]
            elif set_id or execution:
                docs = []
        if not docs:
            console.print("[dim]No documents found[/dim]")
            await store.close()
            return
        table = Table(title="FullTextDocuments")
        table.add_column("ID", style="cyan")
        table.add_column("PaperIdentity", style="magenta")
        table.add_column("Pages", style="yellow")
        table.add_column("Status", style="green")
        table.add_column("Chars", style="dim")
        for env in docs:
            from research_harness.research.schemas.full_text import FullTextDocument

            d = env.parse_payload(FullTextDocument)
            table.add_row(
                env.artifact_id[:8],
                d.paper_identity_id[:8],
                str(d.page_count),
                d.text_status.value,
                str(d.character_count),
            )
            table.add_row(f"[dim]{env.artifact_id}[/dim]", "", "", "", "")
        console.print(table)
        await store.close()

    asyncio.run(_run())


@documents_app.command("inspect")
def documents_inspect(
    doc_id: str,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect a FullTextDocument."""
    import asyncio
    import json

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore

        if cfg_path is not None and cfg_path.exists():
            cfg_obj = load_config(cfg_path)
            path = cfg_obj.artifacts.path
            blob_root = cfg_obj.documents.blob_root
        else:
            path = ".research/artifacts.db"
            blob_root = ".research/blobs"
        store = SQLiteArtifactStore(path=path)
        try:
            env = await store.get(doc_id)
        except Exception as e:
            console.print(f"[red]Document {doc_id!r} not found: {e}[/red]")
            raise typer.Exit(code=1) from e
        from research_harness.research.schemas.full_text import FullTextDocument

        doc = env.parse_payload(FullTextDocument)
        console.print(f"[bold cyan]{env.artifact_id}[/bold cyan] [{env.artifact_type}]")
        console.print(f"PaperIdentity: {doc.paper_identity_id}")
        console.print(f"Acquisition: {doc.document_acquisition_id}")
        console.print(f"Extractor: {doc.extractor} v{doc.extractor_version}")
        console.print(
            f"Pages: {doc.page_count} with_text {doc.pages_with_text} chars {doc.character_count} status {doc.text_status.value}"
        )
        console.print(
            f"Source blob: {doc.source_blob.storage_key} sha256 {doc.source_blob.digest[:12]}..."
        )
        if doc.text_blob:
            console.print(
                f"Text blob: {doc.text_blob.storage_key} sha256 {doc.text_blob.digest[:12]}... size {doc.text_blob.size_bytes}"
            )
        console.print(f"Quality: {json.dumps(doc.quality_metrics, indent=2)}")
        # Try to show acquisition
        try:
            acq_env = await store.get(doc.document_acquisition_id)
            from research_harness.research.schemas.document_acquisition import DocumentAcquisition

            acq = acq_env.parse_payload(DocumentAcquisition)
            console.print(
                f"Acquisition status: {acq.status.value} http {acq.http_status} final_url {acq.final_url}"
            )
        except Exception:
            pass
        # Provenance
        parents, _children = await store.get_provenance(doc_id)
        if parents:
            console.print(f"\n[bold]Parents ({len(parents)}):[/bold]")
            for p in parents:
                console.print(f"  {p.relation.value} ← {p.source_artifact_id}")
        # Try to load one page of text via blob store
        if doc.text_blob:
            try:
                from research_harness.plugins.storage.blobs_filesystem.plugin import (
                    FilesystemBlobStore,
                )

                blobs = FilesystemBlobStore(root=blob_root)
                data = await blobs.get_bytes(doc.text_blob)  # type: ignore[arg-type]
                # Show first page snippet
                j = json.loads(data.decode("utf-8"))
                if j.get("pages"):
                    first = j["pages"][0]
                    snippet = first.get("text", "")[:500]
                    console.print(
                        f"\n[bold]First page snippet (page {first.get('page')}):[/bold]\n{snippet[:500]}"
                    )
            except Exception as e:
                console.print(f"[yellow]Could not load text blob: {e}[/yellow]")
        await store.close()

    asyncio.run(_run())


@documents_app.command("import")
def documents_import(
    identity: Annotated[str, typer.Option("--identity", help="PaperIdentity artifact id")],
    file: Annotated[pathlib.Path, typer.Option("--file", help="Local PDF file path")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Import a user-provided local PDF for a PaperIdentity."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg_obj = load_config(cfg_path) if cfg_path is not None else None
        if cfg_obj is None:
            from research_harness.config.loader import load_config_from_dict

            cfg_obj = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "storage.blobs_filesystem",
                        "documents.acquisition_orchestrator",
                        "documents.extractor.pypdf",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                    "documents": {"blob_root": ".research/blobs"},
                }
            )
        for req in [
            "storage.artifacts_sqlite",
            "storage.blobs_filesystem",
            "documents.acquisition_orchestrator",
            "documents.extractor.pypdf",
        ]:
            if req not in cfg_obj.plugins:
                cfg_obj.plugins.append(req)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg_obj)
        async with runtime:
            svc = runtime.services.require("document_acquisition_orchestrator.default")
            try:
                acq_id = await svc.import_local(identity, str(file))
            except Exception as e:
                console.print(f"[red]Import failed: {e}[/red]")
                import traceback

                console.print(f"[dim]{traceback.format_exc()}[/dim]")
                raise typer.Exit(code=1) from e
            console.print(f"[green]✓ Imported acquisition: {acq_id}[/green]")
            # Try to find extracted doc
            store = runtime.services.require("artifact_store.default")
            # Give extractor a moment? It runs synchronously in import_local
            docs = await store.list(artifact_type="full_text_document")
            for d_env in docs:
                from research_harness.research.schemas.full_text import FullTextDocument

                d = d_env.parse_payload(FullTextDocument)
                if d.document_acquisition_id == acq_id:
                    console.print(
                        f"  FullTextDocument: {d_env.artifact_id} pages {d.page_count} status {d.text_status.value}"
                    )
                    break

    asyncio.run(_run())


@documents_app.command("text")
def documents_text(
    doc_id: str,
    page: Annotated[int | None, typer.Option(help="Page number (1-based)")] = None,
    limit: Annotated[int, typer.Option(help="Max characters to show")] = 4000,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Show extracted page text for a FullTextDocument."""
    import asyncio
    import json

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore

        if cfg_path is not None and cfg_path.exists():
            cfg_obj = load_config(cfg_path)
            art_path = cfg_obj.artifacts.path
            blob_root = cfg_obj.documents.blob_root
        else:
            art_path = ".research/artifacts.db"
            blob_root = ".research/blobs"
        store = SQLiteArtifactStore(path=art_path)
        try:
            env = await store.get(doc_id)
        except Exception as e:
            console.print(f"[red]Document {doc_id!r} not found: {e}[/red]")
            raise typer.Exit(code=1) from e
        from research_harness.research.schemas.full_text import FullTextDocument

        doc = env.parse_payload(FullTextDocument)
        if not doc.text_blob:
            console.print(f"[yellow]No text blob (status {doc.text_status.value})[/yellow]")
            await store.close()
            return
        from research_harness.plugins.storage.blobs_filesystem.plugin import FilesystemBlobStore

        blobs = FilesystemBlobStore(root=blob_root)
        try:
            data = await blobs.get_bytes(doc.text_blob)  # type: ignore[arg-type]
        except Exception as e:
            console.print(f"[red]Failed to load text blob: {e}[/red]")
            raise typer.Exit(code=1) from e
        j = json.loads(data.decode("utf-8"))
        pages = j.get("pages", [])
        if page is not None:
            found = next((p for p in pages if p.get("page") == page), None)
            if not found:
                console.print(f"[red]Page {page} not found (has {len(pages)} pages)[/red]")
                raise typer.Exit(code=1)
            text = found.get("text", "")
            console.print(f"[bold]Page {page} ({len(text)} chars):[/bold]")
            console.print(text[:limit])
            if len(text) > limit:
                console.print(f"[dim]... truncated, {len(text) - limit} more chars[/dim]")
        else:
            # Show overview
            console.print(
                f"[bold]Document {doc_id} has {len(pages)} pages, status {doc.text_status.value}[/bold]"
            )
            for p in pages[:5]:
                snippet = p.get("text", "")[:200].replace("\n", " ")
                console.print(f"  Page {p.get('page')}: {snippet[:200]}")
            if len(pages) > 5:
                console.print(f"[dim]... and {len(pages) - 5} more pages (use --page)[/dim]")
        await store.close()

    asyncio.run(_run())


@evidence_app.command("run")
def evidence_run(
    corpus: Annotated[str, typer.Option("--corpus", help="FullTextCorpus artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Extract structured evidence from a FullTextCorpus (Phase 2F)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "storage.blobs_filesystem",
                        "literature.evidence_extractor",
                        "literature.evidence_orchestrator",
                        "model.openrouter",
                        "routing.role_router",
                        "autonomy.configurable",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                    "documents": {"blob_root": ".research/blobs"},
                }
            )
        for req in [
            "storage.artifacts_sqlite",
            "storage.blobs_filesystem",
            "literature.evidence_extractor",
            "literature.evidence_orchestrator",
        ]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("evidence_orchestrator.default")
            try:
                exec_id = await svc.run(corpus)
            except Exception as e:
                console.print(f"[red]Evidence extraction failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            console.print(f"[green]✓ Evidence extraction execution: {exec_id}[/green]")
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.evidence_extraction import (
                EvidenceExtractionExecution,
            )

            rec = (await store.get(exec_id)).parse_payload(EvidenceExtractionExecution)
            console.print(
                f"  docs {rec.documents_attempted} attempted / {rec.documents_completed} completed, "
                f"chunks {rec.chunks_processed}/{rec.chunks_failed} failed, "
                f"evidence {rec.evidence_items_created}, profiles {rec.profiles_created}"
            )

    asyncio.run(_run())


@evidence_profiles_app.command("list")
def evidence_profiles_list(
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """List PaperResearchProfiles."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            envs = await store.list(artifact_type="paper_research_profile")
            from research_harness.research.schemas.research_profile import PaperResearchProfile

            for env in envs:
                p = env.parse_payload(PaperResearchProfile)
                console.print(
                    f"{env.artifact_id}  {p.paper_identity_id[:8]}  "
                    f"evidence {len(p.evidence_item_ids)}  created {p.created_at.isoformat()}"
                )
            console.print(f"[bold]{len(envs)} profile(s)[/bold]")

    asyncio.run(_run())


@evidence_profiles_app.command("inspect")
def evidence_profiles_inspect(
    profile_id: Annotated[str, typer.Argument(help="PaperResearchProfile artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect a PaperResearchProfile."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            try:
                env = await store.get(profile_id)
            except Exception as e:
                console.print(f"[red]Profile {profile_id!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
            from research_harness.research.schemas.research_profile import PaperResearchProfile

            p = env.parse_payload(PaperResearchProfile)
            console.print(
                f"[bold]Profile {profile_id}[/bold] paper {p.paper_identity_id} "
                f"doc {p.full_text_document_id} evidence {len(p.evidence_item_ids)}"
            )
            for field_name in [
                "research_question",
                "research_context",
                "theories",
                "constructs",
                "mechanisms",
                "assumptions",
                "methodology",
                "data",
                "sample",
                "variables",
                "main_findings",
                "results",
                "boundary_conditions",
                "limitations",
                "future_research",
            ]:
                claims = getattr(p, field_name, [])
                if claims:
                    console.print(f"[bold]{field_name}:[/bold]")
                    for c in claims[:10]:
                        tag = " [dim](inference)[/dim]" if c.inference else ""
                        console.print(f"  - {c.text[:300]}{tag} ev:{len(c.evidence_item_ids)}")

    asyncio.run(_run())


@evidence_items_app.command("list")
def evidence_items_list(
    profile: Annotated[
        str | None, typer.Option("--profile", help="PaperResearchProfile id")
    ] = None,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """List EvidenceItems (optionally filtered by profile)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.evidence import EvidenceItem
            from research_harness.research.schemas.research_profile import PaperResearchProfile

            wanted: set[str] | None = None
            if profile:
                try:
                    p_env = await store.get(profile)
                    p = p_env.parse_payload(PaperResearchProfile)
                    wanted = set(p.evidence_item_ids)
                except Exception as e:
                    console.print(f"[red]Profile {profile!r} not found: {e}[/red]")
                    raise typer.Exit(code=1) from e
            envs = await store.list(artifact_type="evidence_item")
            shown = 0
            for env in envs:
                if wanted is not None and env.artifact_id not in wanted:
                    continue
                ev = env.parse_payload(EvidenceItem)
                pages = ",".join(
                    str(p) for p in (ev.locator.pages if ev.locator and ev.locator.pages else [])
                )
                console.print(
                    f"{env.artifact_id}  {ev.category.value if ev.category else '?'}  "
                    f"pages[{pages}]  conf {ev.confidence}  {ev.statement[:80]}"
                )
                shown += 1
            console.print(f"[bold]{shown} evidence item(s)[/bold]")

    asyncio.run(_run())


@evidence_items_app.command("inspect")
def evidence_items_inspect(
    evidence_id: Annotated[str, typer.Argument(help="EvidenceItem artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect an EvidenceItem."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            try:
                env = await store.get(evidence_id)
            except Exception as e:
                console.print(f"[red]Evidence {evidence_id!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
            from research_harness.research.schemas.evidence import EvidenceItem

            ev = env.parse_payload(EvidenceItem)
            console.print(f"[bold]Evidence {evidence_id}[/bold]")
            console.print(f"  category: {ev.category.value if ev.category else 'None'}")
            console.print(f"  statement: {ev.statement}")
            console.print(
                f"  locator: pages={ev.locator.pages if ev.locator else None} "
                f"page={ev.locator.page if ev.locator else None}"
            )
            console.print(f"  source_artifact_id: {ev.source_artifact_id}")
            console.print(f"  extraction_method: {ev.extraction_method}")
            console.print(f"  confidence: {ev.confidence}")
            parents = await store.get_parents(env.artifact_id)
            for p in parents:
                console.print(f"  provenance: {p.relation.value} <- {p.source_artifact_id}")

    asyncio.run(_run())


@synthesis_app.command("run")
def synthesis_run(
    corpus: Annotated[str, typer.Option("--corpus", help="EvidenceCorpus artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Run cross-paper synthesis over an EvidenceCorpus (Phase 2G)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "literature.synthesis",
                        "model.openrouter",
                        "routing.role_router",
                        "autonomy.configurable",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        for req in ["storage.artifacts_sqlite", "literature.synthesis"]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("literature_synthesizer.default")
            try:
                exec_id = await svc.run(corpus)
            except Exception as e:
                console.print(f"[red]Synthesis failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            console.print(f"[green]✓ Synthesis execution: {exec_id}[/green]")
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.synthesis import (
                LiteratureSynthesis,
                SynthesisExecution,
            )

            rec = (await store.get(exec_id)).parse_payload(SynthesisExecution)
            console.print(
                f"  profiles {rec.profiles_processed}, evidence {rec.evidence_items_processed}, "
                f"batches {rec.batches_processed}/{rec.batches_failed} failed, "
                f"themes {rec.themes_created}, statements {rec.statements_created}, "
                f"rejected {rec.statements_rejected}"
            )
            synths = await store.list(artifact_type="literature_synthesis")
            for s_env in synths:
                s = s_env.parse_payload(LiteratureSynthesis)
                if s.evidence_corpus_id == corpus:
                    console.print(f"  LiteratureSynthesis: {s_env.artifact_id}")

    asyncio.run(_run())


@synthesis_app.command("inspect")
def synthesis_inspect(
    synthesis_id: Annotated[str, typer.Argument(help="LiteratureSynthesis artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect a LiteratureSynthesis."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            try:
                env = await store.get(synthesis_id)
            except Exception as e:
                console.print(f"[red]Synthesis {synthesis_id!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
            from research_harness.research.schemas.synthesis import LiteratureSynthesis

            s = env.parse_payload(LiteratureSynthesis)
            console.print(f"[bold]Synthesis {synthesis_id}[/bold] corpus {s.evidence_corpus_id}")
            console.print(
                f"  themes {len(s.theme_ids)} statements {len(s.statement_ids)} counts {s.counts}"
            )
            for tid in s.theme_ids:
                t_env = await store.get(tid)
                from research_harness.research.schemas.synthesis import SynthesisTheme

                t = t_env.parse_payload(SynthesisTheme)
                console.print(f"[bold]Theme: {t.title}[/bold] ({t.dimension or '?'})")
                console.print(
                    f"  evidence {len(t.evidence_item_ids)} papers {len(t.paper_identity_ids)}"
                )
                for stmt in t.statements[:8]:
                    console.print(
                        f"  - [{stmt.type.value}] ({stmt.support_type.value}, "
                        f"supporting {stmt.papers_supporting}p/{stmt.evidence_items_supporting}e"
                        + (
                            f", conflicting {stmt.papers_conflicting}p/{stmt.evidence_items_conflicting}e)"
                            if stmt.evidence_items_conflicting
                            else ")"
                        )
                        + f" conf {stmt.confidence}: {stmt.statement[:200]}"
                    )
                if len(t.statements) > 8:
                    console.print(f"  [dim]... and {len(t.statements) - 8} more[/dim]")

    asyncio.run(_run())


@synthesis_themes_app.command("list")
def synthesis_themes_list(
    synthesis: Annotated[str, typer.Option("--synthesis", help="LiteratureSynthesis id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """List themes of a LiteratureSynthesis."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            try:
                s_env = await store.get(synthesis)
            except Exception as e:
                console.print(f"[red]Synthesis {synthesis!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
            from research_harness.research.schemas.synthesis import (
                LiteratureSynthesis,
                SynthesisTheme,
            )

            s = s_env.parse_payload(LiteratureSynthesis)
            for tid in s.theme_ids:
                t = (await store.get(tid)).parse_payload(SynthesisTheme)
                console.print(
                    f"{tid}  {t.title[:70]}  ({t.dimension or '?'})  "
                    f"statements {len(t.statements)} papers {len(t.paper_identity_ids)}"
                )
            console.print(f"[bold]{len(s.theme_ids)} theme(s)[/bold]")

    asyncio.run(_run())


@synthesis_themes_app.command("inspect")
def synthesis_themes_inspect(
    theme_id: Annotated[str, typer.Argument(help="SynthesisTheme artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect a SynthesisTheme."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            try:
                t_env = await store.get(theme_id)
            except Exception as e:
                console.print(f"[red]Theme {theme_id!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
            from research_harness.research.schemas.synthesis import SynthesisTheme

            t = t_env.parse_payload(SynthesisTheme)
            console.print(f"[bold]Theme {t.title}[/bold] ({t.dimension or '?'})")
            console.print(f"  papers: {', '.join(t.paper_identity_ids)}")
            console.print(f"  evidence: {', '.join(t.evidence_item_ids)}")
            for stmt in t.statements:
                console.print(f"[bold]- [{stmt.type.value}] ({stmt.support_type.value})[/bold]")
                console.print(f"  {stmt.statement}")
                console.print(
                    f"  supporting: {stmt.papers_supporting} papers, "
                    f"{stmt.evidence_items_supporting} evidence items "
                    f"(conflicting: {stmt.papers_conflicting}/{stmt.evidence_items_conflicting})"
                )
                console.print(
                    f"  supporting_evidence_ids: {', '.join(stmt.supporting_evidence_ids[:8])}"
                )
            parents = await store.get_parents(t_env.artifact_id)
            for p in parents:
                console.print(f"  provenance: {p.relation.value} <- {p.source_artifact_id}")

    asyncio.run(_run())


@gaps_app.command("run")
def gaps_run(
    synthesis: Annotated[str, typer.Option("--synthesis", help="LiteratureSynthesis artifact id")],
    corpus: Annotated[str, typer.Option("--corpus", help="EvidenceCorpus artifact id")],
    question: Annotated[
        str | None, typer.Option("--question", help="ResearchQuestion artifact id (optional)")
    ] = None,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Run evidence-grounded research gap analysis (Phase 2H)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "literature.gap_analyzer",
                        "model.openrouter",
                        "routing.role_router",
                        "autonomy.configurable",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        for req in ["storage.artifacts_sqlite", "literature.gap_analyzer"]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("gap_analyzer.default")
            try:
                exec_id = await svc.run(synthesis, corpus, research_question_id=question)
            except Exception as e:
                console.print(f"[red]Gap analysis failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            console.print(f"[green]✓ Gap analysis execution: {exec_id}[/green]")
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.gap import (
                GapAnalysis,
                GapAnalysisExecution,
            )

            rec = (await store.get(exec_id)).parse_payload(GapAnalysisExecution)
            console.print(
                f"  themes {rec.themes_processed}, statements {rec.statements_processed}, "
                f"gaps {rec.gaps_created}, rejected {rec.gaps_rejected}"
            )
            analyses = await store.list(artifact_type="gap_analysis")
            for a_env in analyses:
                a = a_env.parse_payload(GapAnalysis)
                if a.literature_synthesis_id == synthesis:
                    console.print(f"  GapAnalysis: {a_env.artifact_id}")
                    for gid in a.ranked_gap_ids[:5]:
                        g = (await store.get(gid)).parse_payload(
                            __import__(
                                "research_harness.research.schemas.gap", fromlist=["ResearchGap"]
                            ).ResearchGap
                        )
                        console.print(
                            f"    [{g.strength.value}] {g.title[:70]} ({g.gap_type.value}) "
                            f"papers {g.supporting_papers} ev {g.supporting_evidence_items}"
                        )

    asyncio.run(_run())


@gaps_app.command("list")
def gaps_list(
    analysis: Annotated[str, typer.Option("--analysis", help="GapAnalysis artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """List research gaps of a GapAnalysis."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            try:
                a_env = await store.get(analysis)
            except Exception as e:
                console.print(f"[red]Analysis {analysis!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
            from research_harness.research.schemas.gap import GapAnalysis, ResearchGap

            a = a_env.parse_payload(GapAnalysis)
            for gid in a.ranked_gap_ids:
                g = (await store.get(gid)).parse_payload(ResearchGap)
                console.print(
                    f"{gid}  [{g.strength.value}] {g.gap_type.value}  "
                    f"papers {g.supporting_papers} ev {g.supporting_evidence_items}  "
                    f"rank {g.ranking.composite if g.ranking else 0}  {g.title[:70]}"
                )
            console.print(f"[bold]{len(a.gap_ids)} gap(s)[/bold]")

    asyncio.run(_run())


@gaps_app.command("inspect")
def gaps_inspect(
    gap_id: Annotated[str, typer.Argument(help="ResearchGap artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect a ResearchGap."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            try:
                g_env = await store.get(gap_id)
            except Exception as e:
                console.print(f"[red]Gap {gap_id!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
            from research_harness.research.schemas.gap import ResearchGap

            g = g_env.parse_payload(ResearchGap)
            console.print(f"[bold]Gap {gap_id}[/bold]")
            console.print(f"  title: {g.title}")
            console.print(f"  type: {g.gap_type.value}  strength: {g.strength.value}")
            console.print(f"  description: {g.description}")
            if g.why_it_matters:
                console.print(f"  why_it_matters: {g.why_it_matters}")
            console.print(
                f"  supporting: {g.supporting_papers} papers, {g.supporting_evidence_items} evidence items; "
                f"contradicting: {g.contradicting_papers} papers"
            )
            console.print(
                f"  synthesis statements: {', '.join(g.supporting_synthesis_statement_ids[:8])}"
            )
            if g.ranking:
                console.print(
                    f"  ranking: evidence {g.ranking.evidence_strength}, importance {g.ranking.research_importance}, "
                    f"theory {g.ranking.theoretical_relevance}, model {g.ranking.analytical_model_potential}, "
                    f"tractability {g.ranking.tractability} -> composite {g.ranking.composite}"
                )
            if g.analytical_model_opportunity:
                console.print(
                    f"  model opportunity: suitable={g.analytical_model_opportunity.suitable} "
                    f"domains={g.analytical_model_opportunity.domains}"
                )
            parents = await store.get_parents(g_env.artifact_id)
            for p in parents:
                console.print(f"  provenance: {p.relation.value} <- {p.source_artifact_id}")

    asyncio.run(_run())


@gaps_analysis_app.command("inspect")
def gaps_analysis_inspect(
    analysis_id: Annotated[str, typer.Argument(help="GapAnalysis artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect a GapAnalysis."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            try:
                a_env = await store.get(analysis_id)
            except Exception as e:
                console.print(f"[red]Analysis {analysis_id!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
            from research_harness.research.schemas.gap import GapAnalysis, ResearchGap

            a = a_env.parse_payload(GapAnalysis)
            console.print(f"[bold]GapAnalysis {analysis_id}[/bold]")
            console.print(
                f"  synthesis: {a.literature_synthesis_id}  corpus: {a.evidence_corpus_id}"
            )
            console.print(f"  gaps: {len(a.gap_ids)}  ranked: {len(a.ranked_gap_ids)}")
            console.print(f"  summary: {a.summary}")
            if a.coverage_limitations:
                console.print(
                    f"  coverage limitations: {len(a.coverage_limitations)} doc(s) without evidence"
                )
            for gid in a.ranked_gap_ids[:10]:
                g = (await store.get(gid)).parse_payload(ResearchGap)
                console.print(
                    f"  [{g.strength.value}] {g.title[:80]} ({g.gap_type.value}) rank {g.ranking.composite if g.ranking else 0}"
                )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Phase 3A: research commands (gap selection + mechanism development)
# ---------------------------------------------------------------------------

mechanisms_app = typer.Typer(help="Mechanism development (Phase 3A)")
research_app.add_typer(mechanisms_app, name="mechanisms")
model_app = typer.Typer(help="Formal analytical model (Phase 3B)")
research_app.add_typer(model_app, name="model")
equilibrium_app = typer.Typer(help="Equilibrium derivation (Phase 3C)")
research_app.add_typer(equilibrium_app, name="equilibrium")
comparative_statics_app = typer.Typer(help="Comparative statics (Phase 3D)")
research_app.add_typer(comparative_statics_app, name="comparative-statics")
propositions_app = typer.Typer(help="Propositions (Phase 3D)")
research_app.add_typer(propositions_app, name="propositions")
numerical_app = typer.Typer(help="Numerical experiments (Phase 3E)")
research_app.add_typer(numerical_app, name="numerical")
results_app = typer.Typer(help="Results assembly (Phase 4A)")
research_app.add_typer(results_app, name="results")
findings_app = typer.Typer(help="Research findings (Phase 4A)")
research_app.add_typer(findings_app, name="findings")
contributions_app = typer.Typer(help="Contribution claims (Phase 4A)")
research_app.add_typer(contributions_app, name="contributions")


@research_app.command("gap-select")
def research_gap_select(
    analysis: Annotated[str, typer.Option("--analysis", help="GapAnalysis artifact id")],
    gap: Annotated[
        str | None, typer.Option("--gap", help="Explicitly select a ResearchGap id (optional)")
    ] = None,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Select a research gap from a GapAnalysis (Phase 3A)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "research.gap_selection",
                        "model.openrouter",
                        "routing.role_router",
                        "autonomy.configurable",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        for req in ["storage.artifacts_sqlite", "research.gap_selection"]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("gap_selection.default")
            try:
                sel_id = await svc.select(analysis, selected_gap_id=gap)
            except Exception as e:
                console.print(f"[red]Gap selection failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.mechanism import GapSelection

            sel = (await store.get(sel_id)).parse_payload(GapSelection)
            console.print(f"[green]✓ GapSelection: {sel_id}[/green]")
            console.print(f"  selected: {sel.selected_gap_id}  status: {sel.status.value}")
            console.print(f"  selected_by: {sel.selected_by}  autonomy: {sel.autonomy_mode}")
            console.print(
                f"  approval_required: {sel.approval_required}  decided_by: {sel.approval_decided_by}"
            )
            console.print(f"  rationale: {sel.selection_rationale}")
            if sel.alternative_gap_ids:
                console.print(f"  alternatives: {len(sel.alternative_gap_ids)}")
            for gid in sel.alternative_gap_ids:
                console.print(f"    - {gid}")

    asyncio.run(_run())


@mechanisms_app.command("generate")
def mechanisms_generate(
    selection: Annotated[str, typer.Option("--selection", help="GapSelection artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Generate mechanism candidates for a selected gap (Phase 3A)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "research.mechanism_generator",
                        "model.openrouter",
                        "routing.role_router",
                        "autonomy.configurable",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        for req in ["storage.artifacts_sqlite", "research.mechanism_generator"]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("mechanism_generator.default")
            try:
                exec_id = await svc.generate(selection)
            except Exception as e:
                console.print(f"[red]Mechanism generation failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.mechanism import (
                MechanismAnalysis,
                MechanismAnalysisExecution,
            )

            rec = (await store.get(exec_id)).parse_payload(MechanismAnalysisExecution)
            console.print(f"[green]✓ Generation execution: {exec_id}[/green]")
            console.print(
                f"  candidates {rec.candidates_created}, rejected {rec.candidates_rejected}"
            )
            for env in reversed(await store.list(artifact_type="mechanism_analysis")):
                a = env.parse_payload(MechanismAnalysis)
                if a.gap_selection_id == selection:
                    console.print(f"  MechanismAnalysis: {env.artifact_id} ({a.status.value})")
                    for cid in a.candidate_ids:
                        c = (await store.get(cid)).parse_payload(
                            __import__(
                                "research_harness.research.schemas.mechanism",
                                fromlist=["MechanismCandidate"],
                            ).MechanismCandidate
                        )
                        comp = c.evaluation.composite if c.evaluation else 0.0
                        console.print(
                            f"    [{comp:.3f}] {c.name[:70]} papers {c.literature_support_papers} "
                            f"ev {c.literature_support_evidence_items}"
                        )
                    break

    asyncio.run(_run())


@mechanisms_app.command("inspect")
def mechanisms_inspect(
    candidate_id: Annotated[str, typer.Argument(help="MechanismCandidate artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect a mechanism candidate (Phase 3A)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            try:
                c_env = await store.get(candidate_id)
            except Exception as e:
                console.print(f"[red]Candidate {candidate_id!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
            from research_harness.research.schemas.mechanism import MechanismCandidate

            c = c_env.parse_payload(MechanismCandidate)
            console.print(f"[bold]MechanismCandidate {candidate_id}[/bold]")
            console.print(f"  name: {c.name}  status: {c.status.value}")
            console.print(f"  gap: {c.gap_id}")
            console.print(f"  description: {c.description}")
            console.print(f"  actors: {', '.join(c.actors)}")
            console.print(f"  causal logic: {c.causal_logic}")
            console.print(
                f"  literature support: {c.literature_support_papers} papers, {c.literature_support_evidence_items} evidence items"
            )
            if c.grounding:
                console.print("  grounding:")
                for g in c.grounding:
                    console.print(
                        f"    - {g.element[:90]} [{g.basis.value}]"
                        + (f" (sources: {len(g.source_ids)})" if g.source_ids else "")
                    )
            if c.evaluation:
                console.print(
                    f"  evaluation: gap_align {c.evaluation.gap_alignment}, theory {c.evaluation.theoretical_coherence}, "
                    f"novelty {c.evaluation.novelty_within_reviewed_corpus}, tractability {c.evaluation.analytical_tractability}, "
                    f"mgr/econ {c.evaluation.managerial_economic_relevance}, IS {c.evaluation.is_relevance} "
                    f"-> composite {c.evaluation.composite}"
                )
            parents = await store.get_parents(c_env.artifact_id)
            for p in parents:
                console.print(f"  provenance: {p.relation.value} <- {p.source_artifact_id}")

    asyncio.run(_run())


@mechanisms_app.command("critique")
def mechanisms_critique(
    candidate_id: Annotated[str, typer.Argument(help="MechanismCandidate artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Critique a mechanism candidate with the independent critic (Phase 3A)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "research.mechanism_critic",
                        "model.openrouter",
                        "routing.role_router",
                        "autonomy.configurable",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        for req in ["storage.artifacts_sqlite", "research.mechanism_critic"]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("mechanism_critic.default")
            try:
                crit_id = await svc.critique(candidate_id)
            except Exception as e:
                console.print(f"[red]Critique failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.mechanism import MechanismCritique

            crit = (await store.get(crit_id)).parse_payload(MechanismCritique)
            console.print(f"[green]✓ MechanismCritique: {crit_id}[/green]")
            console.print(f"  verdict: {crit.verdict.value}")
            console.print(f"  overall: {crit.overall_assessment}")
            for i in crit.issues:
                console.print(f"  [{i.severity.value}] {i.category.value}: {i.description[:140]}")

    asyncio.run(_run())


@mechanisms_app.command("select")
def mechanisms_select(
    candidate_id: Annotated[str, typer.Argument(help="MechanismCandidate artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Select/revise a mechanism candidate into the SelectedMechanism (Phase 3A)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "research.mechanism_critic",
                        "model.openrouter",
                        "routing.role_router",
                        "autonomy.configurable",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        for req in ["storage.artifacts_sqlite", "research.mechanism_critic"]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("mechanism_critic.default")
            try:
                sm_id = await svc.select(candidate_id)
            except Exception as e:
                console.print(f"[red]Mechanism selection failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.mechanism import SelectedMechanism

            sm = (await store.get(sm_id)).parse_payload(SelectedMechanism)
            console.print(f"[green]✓ SelectedMechanism: {sm_id}[/green]")
            console.print(f"  name: {sm.name}")
            console.print(f"  candidate: {sm.mechanism_candidate_id}")
            console.print(f"  critiques incorporated: {len(sm.critique_ids)}")
            if sm.revision_notes:
                console.print(f"  revision notes: {len(sm.revision_notes)}")
            console.print(f"  causal logic: {sm.causal_logic[:200]}")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Phase 3B: formal analytical model commands
# ---------------------------------------------------------------------------


@model_app.command("build")
def model_build(
    mechanism: Annotated[str, typer.Option("--mechanism", help="SelectedMechanism artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Build a formal analytical model specification (Phase 3B)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "research.model_builder",
                        "model.openrouter",
                        "routing.role_router",
                        "autonomy.configurable",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        for req in ["storage.artifacts_sqlite", "research.model_builder"]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("analytical_model_builder.default")
            try:
                exec_id = await svc.build(mechanism)
            except Exception as e:
                console.print(f"[red]Model build failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.model import (
                FormalAnalyticalModel,
                ModelSpecificationExecution,
            )

            rec = (await store.get(exec_id)).parse_payload(ModelSpecificationExecution)
            if rec.rejected:
                console.print("[red]Model specification rejected by structural validation[/red]")
                for f in rec.failures:
                    console.print(f"  [red]{f.get('error', '')}[/red]")
                raise typer.Exit(code=1)
            console.print(f"[green]✓ Model build execution: {exec_id}[/green]")
            for env in reversed(await store.list(artifact_type="formal_analytical_model")):
                m = env.parse_payload(FormalAnalyticalModel)
                if m.selected_mechanism_id == mechanism:
                    console.print(f"  Model: {env.artifact_id} ({m.status.value})")
                    console.print(f"  {m.title}")
                    console.print(
                        f"  actors {len(m.actors)}, variables {len(m.variables)}, "
                        f"parameters {len(m.parameters)}, assumptions {len(m.assumptions)}, "
                        f"stages {len(m.timing)}, payoffs {len(m.payoffs)}"
                    )
                    break

    asyncio.run(_run())


@model_app.command("inspect")
def model_inspect(
    model_id: Annotated[str, typer.Argument(help="FormalAnalyticalModel artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect a formal analytical model (Phase 3B)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            try:
                m_env = await store.get(model_id)
            except Exception as e:
                console.print(f"[red]Model {model_id!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
            from research_harness.research.schemas.model import FormalAnalyticalModel

            m = m_env.parse_payload(FormalAnalyticalModel)
            console.print(f"[bold]FormalAnalyticalModel {model_id}[/bold]")
            console.print(f"  {m.title}  ({m.status.value})")
            console.print(f"  mechanism: {m.selected_mechanism_id}")
            console.print(f"  game_type: {m.game_type or '?'}")
            console.print(f"  actors: {', '.join(a.actor_id for a in m.actors)}")
            console.print("  variables:")
            for v in m.variables:
                console.print(
                    f"    {v.symbol} [{v.kind.value}] owner={v.owner_actor_id or '-'} "
                    f"domain={v.domain}: {v.meaning[:70]}"
                )
            console.print(f"  parameters: {', '.join(p.symbol for p in m.parameters)}")
            console.print("  timing:")
            for t in m.timing:
                console.print(
                    f"    Stage {t.stage_number} {t.name}: {t.description[:80]} actors={t.actor_ids}"
                )
            console.print("  payoffs:")
            for p in m.payoffs:
                console.print(
                    f"    {p.actor_id} ({p.objective_type}): {p.expression.expression} "
                    f"decisions={p.decision_variables}"
                )
            console.print("  assumptions:")
            for a in m.assumptions:
                console.print(
                    f"    [{a.knowledge_basis.value}] {a.statement[:90]} "
                    f"(restrictiveness {a.restrictiveness})"
                )
            parents = await store.get_parents(m_env.artifact_id)
            for p in parents:
                console.print(f"  provenance: {p.relation.value} <- {p.source_artifact_id}")

    asyncio.run(_run())


@model_app.command("critique")
def model_critique(
    model_id: Annotated[str, typer.Argument(help="FormalAnalyticalModel artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Critique a formal analytical model (Phase 3B)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "research.model_builder",
                        "research.model_specification_critic",
                        "model.openrouter",
                        "routing.role_router",
                        "autonomy.configurable",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        for req in [
            "storage.artifacts_sqlite",
            "research.model_builder",
            "research.model_specification_critic",
        ]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("model_specification_critic.default")
            try:
                crit_id = await svc.critique(model_id)
            except Exception as e:
                console.print(f"[red]Model critique failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.model import ModelSpecificationCritique

            crit = (await store.get(crit_id)).parse_payload(ModelSpecificationCritique)
            console.print(f"[green]✓ ModelSpecificationCritique: {crit_id}[/green]")
            console.print(f"  verdict: {crit.verdict}")
            console.print(f"  overall: {crit.overall_assessment}")
            for i in crit.issues:
                console.print(f"  [{i.severity}] {i.category.value}: {i.description[:140]}")

    asyncio.run(_run())


@model_app.command("revise")
def model_revise(
    model_id: Annotated[str, typer.Argument(help="FormalAnalyticalModel artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Revise a formal analytical model in response to critique (Phase 3B)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "research.model_builder",
                        "research.model_specification_critic",
                        "model.openrouter",
                        "routing.role_router",
                        "autonomy.configurable",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        for req in [
            "storage.artifacts_sqlite",
            "research.model_builder",
            "research.model_specification_critic",
        ]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("model_specification_critic.default")
            try:
                v2_id = await svc.revise(model_id)
            except Exception as e:
                console.print(f"[red]Model revision failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.model import FormalAnalyticalModel

            v2 = (await store.get(v2_id)).parse_payload(FormalAnalyticalModel)
            console.print(f"[green]✓ Revised FormalAnalyticalModel: {v2_id}[/green]")
            console.print(f"  {v2.title}  status={v2.status.value}")
            console.print(f"  revision notes: {len(v2.revision_notes)}")
            for note in v2.revision_notes[:8]:
                console.print(f"    - {note}")
            console.print(
                f"  actors {len(v2.actors)}, variables {len(v2.variables)}, "
                f"assumptions {len(v2.assumptions)}, stages {len(v2.timing)}, "
                f"payoffs {len(v2.payoffs)}"
            )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Phase 3C: equilibrium derivation commands
# ---------------------------------------------------------------------------


@equilibrium_app.command("derive")
def equilibrium_derive(
    model: Annotated[str, typer.Option("--model", help="FormalAnalyticalModel artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Derive + symbolically verify equilibrium (Phase 3C)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "research.equilibrium_verifier",
                        "research.equilibrium_deriver",
                        "model.openrouter",
                        "routing.role_router",
                        "autonomy.configurable",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        for req in [
            "storage.artifacts_sqlite",
            "research.equilibrium_verifier",
            "research.equilibrium_deriver",
        ]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("equilibrium_deriver.default")
            try:
                exec_id = await svc.derive(model)
            except Exception as e:
                console.print(f"[red]Equilibrium derivation failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.equilibrium import (
                EquilibriumAnalysis,
                EquilibriumExecution,
                EquilibriumVerification,
            )

            rec = (await store.get(exec_id)).parse_payload(EquilibriumExecution)
            if rec.status.value == "not_solvable":
                console.print("[red]Model not solvable:[/red]")
                for f in rec.failures:
                    console.print(f"  [red]{f.get('error', '')}[/red]")
                raise typer.Exit(code=1)
            console.print(f"[green]✓ Equilibrium execution: {exec_id}[/green]")
            console.print(
                f"  status {rec.status.value}  problems {rec.optimization_problems_created} "
                f"focs {rec.focs_created} brs {rec.best_responses_created} "
                f"candidates {rec.candidates_created}  verification {rec.verification_status.value}"
            )
            analyses = await store.list(artifact_type="equilibrium_analysis")
            for a_env in reversed(analyses):
                a = a_env.parse_payload(EquilibriumAnalysis)
                if a.model_id == model:
                    console.print(f"  EquilibriumAnalysis: {a_env.artifact_id} ({a.status.value})")
                    console.print(
                        f"  solution order: {' -> '.join(a.solution_order) or 'n/a'}  method: {a.solution_method}"
                    )
                    for cid in a.candidate_ids:
                        c = (await store.get(cid)).parse_payload(
                            __import__(
                                "research_harness.research.schemas.equilibrium",
                                fromlist=["EquilibriumCandidate"],
                            ).EquilibriumCandidate
                        )
                        line = ", ".join(
                            f"{e.variable} = {e.expression.expression}" for e in c.expressions
                        )
                        console.print(f"    [{c.proposed_by}] {c.solution_method}: {line[:110]}")
                    if a.selected_candidate_id:
                        for vid in a.verification_ids:
                            v = (await store.get(vid)).parse_payload(EquilibriumVerification)
                            if v.candidate_id == a.selected_candidate_id:
                                console.print(
                                    f"  selected {a.selected_candidate_id[:8]} [{v.status.value}]"
                                )
                                for chk in v.checks:
                                    console.print(
                                        f"    {'✓' if chk.passed else '✗'} {chk.check_type.value}: {chk.detail[:100]}"
                                    )
                    break

    asyncio.run(_run())


@equilibrium_app.command("inspect")
def equilibrium_inspect(
    analysis_id: Annotated[str, typer.Argument(help="EquilibriumAnalysis artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect an EquilibriumAnalysis (Phase 3C)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            try:
                a_env = await store.get(analysis_id)
            except Exception as e:
                console.print(f"[red]Analysis {analysis_id!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
            from research_harness.research.schemas.equilibrium import (
                EquilibriumAnalysis,
                EquilibriumCandidate,
                EquilibriumVerification,
            )

            a = a_env.parse_payload(EquilibriumAnalysis)
            console.print(f"[bold]EquilibriumAnalysis {analysis_id}[/bold]")
            console.print(f"  model: {a.model_id}")
            console.print(f"  status: {a.status.value}  method: {a.solution_method}")
            console.print(f"  solution order: {' -> '.join(a.solution_order) or 'n/a'}")
            console.print(f"  revisions used: {a.revision_rounds}")
            for cid in a.candidate_ids:
                c = (await store.get(cid)).parse_payload(EquilibriumCandidate)
                console.print(f"  candidate {cid} [{c.proposed_by}] round {c.revision_round}:")
                for e in c.expressions:
                    console.print(
                        f"    {e.variable} = {e.expression.expression}  "
                        f"(conditions: {'; '.join(e.conditions) or '-'})"
                    )
            for vid in a.verification_ids:
                v = (await store.get(vid)).parse_payload(EquilibriumVerification)
                tag = (
                    "[green]✓[/green]"
                    if v.status.value == "verified"
                    else "[yellow]~[/yellow]"
                    if v.status.value == "partially_verified"
                    else "[red]✗[/red]"
                )
                console.print(f"  verification {vid} {tag} {v.status.value}")
                for chk in v.checks:
                    console.print(
                        f"    {'✓' if chk.passed else '✗'} {chk.check_type.value}: {chk.detail[:110]}"
                    )
            if a.verification_ids:
                parents = await store.get_parents(a_env.artifact_id)
                for p in parents:
                    console.print(f"  provenance: {p.relation.value} <- {p.source_artifact_id}")

    asyncio.run(_run())


@equilibrium_app.command("verify")
def equilibrium_verify(
    candidate_id: Annotated[str, typer.Argument(help="EquilibriumCandidate artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Symbolically verify an equilibrium candidate (Phase 3C)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite", "research.equilibrium_verifier"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        for req in ["storage.artifacts_sqlite", "research.equilibrium_verifier"]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("equilibrium_verifier.default")
            try:
                v_id = await svc.verify(candidate_id)
            except Exception as e:
                console.print(f"[red]Verification failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.equilibrium import EquilibriumVerification

            v = (await store.get(v_id)).parse_payload(EquilibriumVerification)
            tag = (
                "[green]✓[/green]"
                if v.status.value == "verified"
                else "[yellow]~[/yellow]"
                if v.status.value == "partially_verified"
                else "[red]✗[/red]"
            )
            console.print(
                f"[green]✓ EquilibriumVerification: {v_id}[/green] {tag} {v.status.value}"
            )
            for chk in v.checks:
                console.print(
                    f"  {'✓' if chk.passed else '✗'} {chk.check_type.value}: {chk.detail[:110]}"
                )
            if v.conditions_required:
                console.print(f"  conditions: {'; '.join(v.conditions_required)}")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Phase 3D: comparative statics + propositions commands
# ---------------------------------------------------------------------------


@comparative_statics_app.command("run")
def comparative_statics_run(
    equilibrium: Annotated[
        str, typer.Option("--equilibrium", help="EquilibriumAnalysis artifact id")
    ],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Derive comparative statics of a verified equilibrium (Phase 3D)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite", "research.comparative_statics"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("comparative_statics.default")
            try:
                exec_id = await svc.run(equilibrium)
            except Exception as e:
                console.print(f"[red]Comparative statics failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.proposition import (
                ComparativeStatic,
                ComparativeStaticsAnalysis,
                ComparativeStaticsExecution,
            )

            rec = (await store.get(exec_id)).parse_payload(ComparativeStaticsExecution)
            console.print(f"[green]✓ Comparative statics execution: {exec_id}[/green]")
            console.print(f"  statics: {rec.statics_created}")
            for env in reversed(await store.list(artifact_type="comparative_statics_analysis")):
                a = env.parse_payload(ComparativeStaticsAnalysis)
                if a.equilibrium_candidate_id == rec.equilibrium_candidate_id:
                    console.print(f"  ComparativeStaticsAnalysis: {env.artifact_id}")
                    for sid in a.static_ids:
                        s = (await store.get(sid)).parse_payload(ComparativeStatic)
                        console.print(
                            f"    d{s.outcome_variable}/d{s.parameter} = "
                            f"{s.derivative_expression.expression}  [{s.sign.value}]"
                            + (f"  ({'; '.join(s.conditions)})" if s.conditions else "")
                        )
                    break

    asyncio.run(_run())


@comparative_statics_app.command("inspect")
def comparative_statics_inspect(
    analysis_id: Annotated[str, typer.Argument(help="ComparativeStaticsAnalysis artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect a ComparativeStaticsAnalysis (Phase 3D)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            try:
                a_env = await store.get(analysis_id)
            except Exception as e:
                console.print(f"[red]Analysis {analysis_id!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
            from research_harness.research.schemas.proposition import (
                ComparativeStatic,
                ComparativeStaticsAnalysis,
            )

            a = a_env.parse_payload(ComparativeStaticsAnalysis)
            console.print(f"[bold]ComparativeStaticsAnalysis {analysis_id}[/bold]")
            console.print(f"  model: {a.model_id}")
            console.print(f"  equilibrium candidate: {a.equilibrium_candidate_id}")
            console.print(f"  summary: {a.summary}")
            for sid in a.static_ids:
                s = (await store.get(sid)).parse_payload(ComparativeStatic)
                tag = (
                    "[green]✓[/green]"
                    if s.sign.value == "positive"
                    else "[red]✓[/red]"
                    if s.sign.value == "negative"
                    else "[yellow]0[/yellow]"
                    if s.sign.value == "zero"
                    else "[yellow]~[/yellow]"
                )
                console.print(
                    f"  {tag} d{s.outcome_variable}/d{s.parameter} = "
                    f"{s.derivative_expression.expression}  [{s.sign.value}]"
                    + (f"  conditions: {'; '.join(s.conditions)}" if s.conditions else "")
                )
            parents = await store.get_parents(a_env.artifact_id)
            for p in parents:
                console.print(f"  provenance: {p.relation.value} <- {p.source_artifact_id}")

    asyncio.run(_run())


@propositions_app.command("generate")
def propositions_generate(
    analysis: Annotated[
        str,
        typer.Option("--analysis", help="ComparativeStaticsAnalysis artifact id (Phase 3D)"),
    ],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Generate, verify, critique, and interpret propositions (Phase 3D)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": [
                        "storage.artifacts_sqlite",
                        "research.proposition_verifier",
                        "research.proposition_critic",
                        "research.proposition_generator",
                        "model.openrouter",
                        "routing.role_router",
                        "autonomy.configurable",
                    ],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        for req in [
            "storage.artifacts_sqlite",
            "research.proposition_verifier",
            "research.proposition_critic",
            "research.proposition_generator",
        ]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("proposition_generator.default")
            try:
                prop_ids = await svc.generate(analysis)
            except Exception as e:
                console.print(f"[red]Proposition generation failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.proposition import (
                EconomicInterpretation,
                Proposition,
                PropositionCritique,
                PropositionVerification,
            )

            console.print(f"[green]✓ Propositions: {len(prop_ids)}[/green]")
            for pid in prop_ids:
                p = (await store.get(pid)).parse_payload(Proposition)
                v = next(
                    (
                        env.parse_payload(PropositionVerification)
                        for env in await store.list(artifact_type="proposition_verification")
                        if env.parse_payload(PropositionVerification).proposition_id == pid
                    ),
                    None,
                )
                tag = (
                    "[green]✓[/green]"
                    if v and v.status.value == "verified"
                    else "[yellow]~[/yellow]"
                    if v and v.status.value == "conditionally_verified"
                    else "[red]✗[/red]"
                )
                console.print(f"  {tag} {pid[:8]} [{p.claim_type.value}] {p.statement[:90]}")
                crits = [
                    env.parse_payload(PropositionCritique)
                    for env in await store.list(artifact_type="proposition_critique")
                    if env.parse_payload(PropositionCritique).proposition_id == pid
                ]
                if crits:
                    console.print(f"     critique verdict: {crits[0].verdict.value}")
                interps = [
                    env.parse_payload(EconomicInterpretation)
                    for env in await store.list(artifact_type="economic_interpretation")
                    if env.parse_payload(EconomicInterpretation).proposition_id == pid
                ]
                if interps:
                    console.print(
                        f"     interpretation: {interps[0].economic_interpretation[:100]}"
                    )

    asyncio.run(_run())


@propositions_app.command("inspect")
def propositions_inspect(
    proposition_id: Annotated[str, typer.Argument(help="Proposition artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect a proposition with its verification/critique/interpretation."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            try:
                p_env = await store.get(proposition_id)
            except Exception as e:
                console.print(f"[red]Proposition {proposition_id!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
            from research_harness.research.schemas.proposition import (
                EconomicInterpretation,
                Proposition,
                PropositionCritique,
                PropositionVerification,
            )

            p = p_env.parse_payload(Proposition)
            console.print(f"[bold]Proposition {proposition_id}[/bold]")
            console.print(f"  {p.statement}")
            console.print(
                f"  claim_type: {p.claim_type.value}  outcome: {p.outcome_variable or '-'}  "
                f"parameter: {p.parameter or '-'}  expected_sign: {p.expected_sign or '-'}"
            )
            console.print(f"  conditions: {'; '.join(p.conditions) or '-'}")
            console.print(f"  supporting statics: {p.supporting_static_ids}")
            for env in await store.list(artifact_type="proposition_verification"):
                v = env.parse_payload(PropositionVerification)
                if v.proposition_id == proposition_id:
                    tag = (
                        "[green]✓[/green]"
                        if v.status.value == "verified"
                        else "[yellow]~[/yellow]"
                        if v.status.value == "conditionally_verified"
                        else "[red]✗[/red]"
                    )
                    console.print(f"  verification {tag} {v.status.value}")
                    for chk in v.checks:
                        console.print(
                            f"    {'✓' if chk.passed else '✗'} {chk.check_type.value}: {chk.detail[:110]}"
                        )
            for env in await store.list(artifact_type="proposition_critique"):
                c = env.parse_payload(PropositionCritique)
                if c.proposition_id == proposition_id:
                    console.print(f"  critique verdict: {c.verdict.value}")
                    for i in c.issues:
                        console.print(
                            f"    [{i.severity}] {i.category.value}: {i.description[:110]}"
                        )
            for env in await store.list(artifact_type="economic_interpretation"):
                i = env.parse_payload(EconomicInterpretation)
                if i.proposition_id == proposition_id:
                    console.print(f"  interpretation ({i.model_role}):")
                    console.print(f"    math: {i.mathematical_result[:110]}")
                    console.print(f"    economic: {i.economic_interpretation[:110]}")
                    console.print(f"    managerial: {i.managerial_implication[:110]}")
                    console.print(f"    IS: {i.is_theoretical_implication[:110]}")

    asyncio.run(_run())


@propositions_app.command("verify")
def propositions_verify(
    proposition_id: Annotated[str, typer.Argument(help="Proposition artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Symbolically verify a proposition (Phase 3D)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite", "research.proposition_verifier"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        for req in ["storage.artifacts_sqlite", "research.proposition_verifier"]:
            if req not in cfg.plugins:
                cfg.plugins.append(req)

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("proposition_verifier.default")
            try:
                v_id = await svc.verify(proposition_id)
            except Exception as e:
                console.print(f"[red]Verification failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.proposition import PropositionVerification

            v = (await store.get(v_id)).parse_payload(PropositionVerification)
            tag = (
                "[green]✓[/green]"
                if v.status.value == "verified"
                else "[yellow]~[/yellow]"
                if v.status.value == "conditionally_verified"
                else "[red]✗[/red]"
            )
            console.print(
                f"[green]✓ PropositionVerification: {v_id}[/green] {tag} {v.status.value}"
            )
            for chk in v.checks:
                console.print(
                    f"  {'✓' if chk.passed else '✗'} {chk.check_type.value}: {chk.detail[:110]}"
                )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Phase 3E: numerical experiment commands
# ---------------------------------------------------------------------------


@numerical_app.command("run")
def numerical_run(
    equilibrium: Annotated[
        str, typer.Option("--equilibrium", help="EquilibriumAnalysis artifact id")
    ],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Run deterministic numerical experiments on a verified equilibrium (Phase 3E)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite", "research.numerical_analysis"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("numerical_analysis.default")
            try:
                exec_id = await svc.run(equilibrium)
            except Exception as e:
                console.print(f"[red]Numerical run failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.numerical import (
                NumericalExperiment,
                NumericalExperimentExecution,
            )

            rec = (await store.get(exec_id)).parse_payload(NumericalExperimentExecution)
            console.print(f"[green]✓ Numerical execution: {exec_id}[/green]")
            console.print(f"  engine {rec.engine} {rec.engine_version}  seed {rec.seed}")
            console.print(
                f"  sweeps {rec.sweeps_created}  results {rec.results_created}  "
                f"infeasible {rec.results_infeasible}  robustness {rec.robustness_created}  "
                f"welfare {rec.welfare_created}"
            )
            for env in reversed(await store.list(artifact_type="numerical_experiment")):
                exp = env.parse_payload(NumericalExperiment)
                if exp.equilibrium_candidate_id == rec.equilibrium_candidate_id:
                    console.print(f"  NumericalExperiment: {env.artifact_id}")
                    console.print(f"  {exp.summary}")
                    break

    asyncio.run(_run())


@numerical_app.command("inspect")
def numerical_inspect(
    experiment_id: Annotated[str, typer.Argument(help="NumericalExperiment artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect a NumericalExperiment (Phase 3E)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            try:
                exp_env = await store.get(experiment_id)
            except Exception as e:
                console.print(f"[red]Experiment {experiment_id!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
            from research_harness.research.schemas.numerical import (
                NumericalExperiment,
                NumericalResult,
                ParameterSweep,
            )

            exp = exp_env.parse_payload(NumericalExperiment)
            console.print(f"[bold]NumericalExperiment {experiment_id}[/bold]")
            console.print(f"  model: {exp.model_id}  equilibrium: {exp.equilibrium_candidate_id}")
            console.print(f"  status: {exp.status}  summary: {exp.summary}")
            for sid in exp.sweeps:
                s = (await store.get(sid)).parse_payload(ParameterSweep)
                console.print(
                    f"  sweep {s.name} [{s.kind.value}]: {len(s.dimensions)} dim(s), "
                    f"{s.total_points} points"
                )
            for rid in exp.results[:8]:
                r = (await store.get(rid)).parse_payload(NumericalResult)
                console.print(
                    f"  [{r.scenario} {r.group or ''}] {r.parameter_values} -> {r.outcomes}"
                )
            if len(exp.results) > 8:
                console.print(f"  ... {len(exp.results) - 8} more results")
            for cid in exp.robustness:
                c = (await store.get(cid)).parse_payload(
                    __import__(
                        "research_harness.research.schemas.numerical",
                        fromlist=["RobustnessCheck"],
                    ).RobustnessCheck
                )
                console.print(f"  robustness [{c.outcome.value}] {c.description[:70]}")
            for wid in exp.welfare:
                w = (await store.get(wid)).parse_payload(
                    __import__(
                        "research_harness.research.schemas.numerical",
                        fromlist=["WelfareAnalysis"],
                    ).WelfareAnalysis
                )
                console.print(f"  welfare total {w.total_welfare}")
            parents = await store.get_parents(exp_env.artifact_id)
            for p in parents:
                console.print(f"  provenance: {p.relation.value} <- {p.source_artifact_id}")

    asyncio.run(_run())


@numerical_app.command("results")
def numerical_results(
    experiment_id: Annotated[str, typer.Argument(help="NumericalExperiment artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """List all NumericalResult series rows (visualization-ready)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            exp = (await store.get(experiment_id)).parse_payload(
                __import__(
                    "research_harness.research.schemas.numerical",
                    fromlist=["NumericalExperiment"],
                ).NumericalExperiment
            )
            from research_harness.research.schemas.numerical import NumericalResult

            header = "feasible | x_param | x_value | parameters -> outcomes"
            console.print(f"[bold]{header}[/bold]")
            for rid in exp.results:
                r = (await store.get(rid)).parse_payload(NumericalResult)
                console.print(
                    f"{'✓' if r.feasible else '✗'} {r.x_parameter or '-'} | {r.x_value or '-'} | "
                    f"{r.parameter_values} -> {r.outcomes}"
                    + (f"  [invalid: {r.infeasible_reason}]" if not r.feasible else "")
                )
            if exp.metadata.get("series_blob_ref"):
                console.print(
                    f"[bold]full series table in blob: {exp.metadata['series_blob_ref']['digest'][:16]}...[/bold]"
                )

    asyncio.run(_run())


@numerical_app.command("robustness")
def numerical_robustness(
    experiment_id: Annotated[str, typer.Argument(help="NumericalExperiment artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Show robustness checks of a NumericalExperiment (Phase 3E)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            exp = (await store.get(experiment_id)).parse_payload(
                __import__(
                    "research_harness.research.schemas.numerical",
                    fromlist=["NumericalExperiment"],
                ).NumericalExperiment
            )
            from research_harness.research.schemas.numerical import RobustnessCheck

            for cid in exp.robustness:
                c = (await store.get(cid)).parse_payload(RobustnessCheck)
                tag = (
                    "[green]✓[/green]"
                    if c.outcome.value == "supported"
                    else "[yellow]~[/yellow]"
                    if c.outcome.value == "not_testable"
                    else "[red]✗[/red]"
                )
                console.print(f"  {tag} [{c.outcome.value}] {c.check_type.value}: {c.description}")
                console.print(f"      {c.conclusion}  (admissible: {c.admissible_points})")
                for v in c.violations[:5]:
                    console.print(f"      violation at {v.parameter_values}: {v.detail}")

    asyncio.run(_run())


@numerical_app.command("welfare")
def numerical_welfare(
    experiment_id: Annotated[str, typer.Argument(help="NumericalExperiment artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Show welfare analysis of a NumericalExperiment (Phase 3E)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            exp = (await store.get(experiment_id)).parse_payload(
                __import__(
                    "research_harness.research.schemas.numerical",
                    fromlist=["NumericalExperiment"],
                ).NumericalExperiment
            )
            from research_harness.research.schemas.numerical import WelfareAnalysis

            for wid in exp.welfare:
                w = (await store.get(wid)).parse_payload(WelfareAnalysis)
                console.print(f"[bold]WelfareAnalysis {wid}[/bold]")
                console.print(f"  scenario: {w.scenario}  parameters: {w.parameter_values}")
                for m in w.metrics:
                    console.print(f"  {m.name}: {m.value}")
                console.print(f"  total welfare: {w.total_welfare}")
                for n in w.notes:
                    console.print(f"  note: {n}")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Phase 4A: results assembly, findings, contributions, critique
# ---------------------------------------------------------------------------


@results_app.command("assemble")
def results_assemble(
    numerical: Annotated[str, typer.Option("--numerical", help="NumericalExperiment artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Assemble findings, contributions, implications into a package (Phase 4A)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite", "research.results_assembler"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("results_assembler.default")
            try:
                exec_id = await svc.assemble(numerical)
            except Exception as e:
                console.print(f"[red]Results assembly failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.results import (
                ResearchResultsPackage,
                ResultsAssemblyExecution,
            )

            rec = (await store.get(exec_id)).parse_payload(ResultsAssemblyExecution)
            console.print(f"[green]✓ ResultsAssemblyExecution: {exec_id}[/green]")
            console.print(
                f"  findings {rec.findings_created}  contributions {rec.contributions_created}  "
                f"implications {rec.implications_created}  novelty normalized "
                f"{rec.novelty_claims_normalized}"
            )
            for env in reversed(await store.list(artifact_type="results_package")):
                pkg = env.parse_payload(ResearchResultsPackage)
                if pkg.numerical_experiment_id == numerical:
                    console.print(f"  ResearchResultsPackage: {env.artifact_id}")
                    console.print(f"  {pkg.summary}")
                    break

    asyncio.run(_run())


@results_app.command("inspect")
def results_inspect(
    package_id: Annotated[str, typer.Argument(help="ResearchResultsPackage artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect a ResearchResultsPackage (Phase 4A)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            try:
                p_env = await store.get(package_id)
            except Exception as e:
                console.print(f"[red]Package {package_id!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
            from research_harness.research.schemas.results import (
                ContributionClaim,
                ResearchFinding,
                ResearchImplication,
                ResearchResultsPackage,
            )

            pkg = p_env.parse_payload(ResearchResultsPackage)
            console.print(f"[bold]ResearchResultsPackage {package_id}[/bold]")
            console.print(f"  gap: {pkg.gap_id}")
            console.print(f"  mechanism: {pkg.selected_mechanism_id}")
            console.print(f"  model: {pkg.model_id}  equilibrium: {pkg.equilibrium_analysis_id}")
            console.print(f"  status: {pkg.status}  summary: {pkg.summary}")
            for fid in pkg.finding_ids:
                f = (await store.get(fid)).parse_payload(ResearchFinding)
                console.print(f"  finding [{f.finding_type.value}] {f.statement[:120]}")
            for cid in pkg.contribution_claim_ids:
                c = (await store.get(cid)).parse_payload(ContributionClaim)
                console.print(f"  contribution [{c.contribution_type.value}] {c.claim[:120]}")
            for iid in pkg.implication_ids:
                i = (await store.get(iid)).parse_payload(ResearchImplication)
                console.print(
                    f"  implication [{i.implication_kind.value}/{i.claim_type.value}] {i.text[:120]}"
                )
            if pkg.limitations:
                console.print("  limitations:")
                for lim in pkg.limitations:
                    console.print(f"    - {lim}")

    asyncio.run(_run())


@results_app.command("critique")
def results_critique(
    package_id: Annotated[str, typer.Argument(help="ResearchResultsPackage artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Critique a ResearchResultsPackage (Phase 4A)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite", "research.results_critic"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("results_critic.default")
            try:
                crit_id = await svc.critique(package_id)
            except Exception as e:
                console.print(f"[red]Results critique failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.results import ResultsCritique

            c = (await store.get(crit_id)).parse_payload(ResultsCritique)
            console.print(f"[green]✓ ResultsCritique: {crit_id}[/green]")
            console.print(f"  verdict: {c.verdict.value}")
            console.print(f"  assessment: {c.overall_assessment}")
            for issue in c.issues:
                console.print(
                    f"  [{issue.severity}] {issue.category.value}: {issue.description[:140]}"
                )
            for r in c.recommendations:
                console.print(f"  rec: {r}")

    asyncio.run(_run())


@findings_app.command("list")
def findings_list(
    package: Annotated[str, typer.Option("--package", help="ResearchResultsPackage id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """List findings of a package (Phase 4A)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            pkg = (await store.get(package)).parse_payload(
                __import__(
                    "research_harness.research.schemas.results",
                    fromlist=["ResearchResultsPackage"],
                ).ResearchResultsPackage
            )
            from research_harness.research.schemas.results import ResearchFinding

            for fid in pkg.finding_ids:
                f = (await store.get(fid)).parse_payload(ResearchFinding)
                console.print(f"[bold]{fid}[/bold] [{f.finding_type.value}/{f.confidence.value}]")
                console.print(f"  {f.statement}")
                if f.conditions:
                    console.print(f"  conditions: {'; '.join(f.conditions)}")
                console.print(f"  supports: props {f.supporting_proposition_ids}")
                console.print(f"            statics {f.supporting_comparative_static_ids}")
                console.print(f"            results {len(f.supporting_numerical_result_ids)} ids")

    asyncio.run(_run())


@contributions_app.command("list")
def contributions_list(
    package: Annotated[str, typer.Option("--package", help="ResearchResultsPackage id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """List contribution claims of a package (Phase 4A)."""
    import asyncio

    async def _run() -> None:
        cfg_path = config if config is not None and config.exists() else None
        cfg = load_config(cfg_path) if cfg_path is not None else None
        if cfg is None:
            from research_harness.config.loader import load_config_from_dict

            cfg = load_config_from_dict(
                {
                    "plugins": ["storage.artifacts_sqlite"],
                    "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
                }
            )
        if "storage.artifacts_sqlite" not in cfg.plugins:
            cfg.plugins.append("storage.artifacts_sqlite")

        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            pkg = (await store.get(package)).parse_payload(
                __import__(
                    "research_harness.research.schemas.results",
                    fromlist=["ResearchResultsPackage"],
                ).ResearchResultsPackage
            )
            from research_harness.research.schemas.results import ContributionClaim

            for cid in pkg.contribution_claim_ids:
                c = (await store.get(cid)).parse_payload(ContributionClaim)
                console.print(f"[bold]{cid}[/bold] [{c.contribution_type.value}]")
                console.print(f"  claim: {c.claim}")
                console.print(f"  advances: {c.advances_literature[:200]}")
                console.print(
                    f"  findings: {c.finding_ids}"
                    + ("  [normalized novelty]" if c.novelty_normalized else "")
                )

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Phase 4B: manuscript outline, drafting, inspection, critique, revision
# ---------------------------------------------------------------------------


def _manuscript_config(config: pathlib.Path | None, extra_plugins: list[str]) -> Any:
    cfg_path = config if config is not None and config.exists() else None
    cfg = load_config(cfg_path) if cfg_path is not None else None
    if cfg is None:
        from research_harness.config.loader import load_config_from_dict

        cfg = load_config_from_dict(
            {
                "plugins": ["storage.artifacts_sqlite", *extra_plugins],
                "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
            }
        )
    if "storage.artifacts_sqlite" not in cfg.plugins:
        cfg.plugins.append("storage.artifacts_sqlite")
    return cfg


@manuscript_app.command("outline")
def manuscript_outline(
    results: Annotated[str, typer.Option("--results", help="ResearchResultsPackage artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Build a deterministic manuscript outline over a results package (Phase 4B)."""
    import asyncio

    async def _run() -> None:
        cfg = _manuscript_config(config, ["research.manuscript_drafter"])
        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("manuscript_drafter.default")
            try:
                outline_id = await svc.outline(results)
            except Exception as e:
                console.print(f"[red]Outline failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.manuscript import ManuscriptOutline

            outline = (await store.get(outline_id)).parse_payload(ManuscriptOutline)
            console.print(f"[green]✓ ManuscriptOutline: {outline_id}[/green]")
            console.print(f"  title: {outline.title}")
            for spec_ in outline.section_specs:
                console.print(
                    f"  {spec_.section_id.value:<22} {spec_.title} "
                    f"[{len(spec_.artifact_ids)} artifacts]"
                )

    asyncio.run(_run())


@manuscript_app.command("draft")
def manuscript_draft(
    outline: Annotated[str, typer.Option("--outline", help="ManuscriptOutline artifact id")],
    sections: Annotated[
        str | None,
        typer.Option(
            "--sections",
            help="Comma-separated section ids to draft (default: all sections)",
        ),
    ] = None,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Draft the manuscript section by section (Phase 4B)."""
    import asyncio

    async def _run() -> None:
        cfg = _manuscript_config(config, ["research.manuscript_drafter"])
        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("manuscript_drafter.default")
            wanted = [s.strip() for s in sections.split(",") if s.strip()] if sections else None
            try:
                exec_id = await svc.draft(outline, wanted)
            except Exception as e:
                console.print(f"[red]Drafting failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.manuscript import (
                ManuscriptDraft,
                ManuscriptDraftExecution,
            )

            rec = (await store.get(exec_id)).parse_payload(ManuscriptDraftExecution)
            console.print(f"[green]✓ ManuscriptDraftExecution: {exec_id}[/green]")
            console.print(
                f"  sections created {rec.sections_created}  claims {rec.claims_created}  "
                f"citations {rec.citations_created}  novelty normalized {rec.novelty_claims_normalized}"
            )
            if rec.draft_id:
                draft = (await store.get(rec.draft_id)).parse_payload(ManuscriptDraft)
                console.print(f"  ManuscriptDraft v{draft.version}: {rec.draft_id}")
                console.print(f"  {draft.summary}")

    asyncio.run(_run())


@manuscript_app.command("inspect")
def manuscript_inspect(
    draft_id: Annotated[str, typer.Argument(help="ManuscriptDraft artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect a ManuscriptDraft (Phase 4B)."""
    import asyncio

    async def _run() -> None:
        cfg = _manuscript_config(config, [])
        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            try:
                d_env = await store.get(draft_id)
            except Exception as e:
                console.print(f"[red]Draft {draft_id!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
            from research_harness.research.schemas.manuscript import (
                ManuscriptDraft,
                ManuscriptSection,
            )

            draft = d_env.parse_payload(ManuscriptDraft)
            console.print(f"[bold]ManuscriptDraft v{draft.version} {draft_id}[/bold]")
            console.print(f"  title: {draft.title}  status: {draft.status.value}")
            console.print(f"  supersedes: {draft.supersedes or '-'}")
            console.print(f"  {draft.summary}")
            for sid in draft.section_ids:
                s = (await store.get(sid)).parse_payload(ManuscriptSection)
                console.print(f"  [{s.section_id.value}] {s.title}")
                console.print(f"      claims: {len(s.claims)}  citations: {len(s.citations)}")
                console.print(f"      {s.body[:160].replace(chr(10), ' ')}...")

    asyncio.run(_run())


@manuscript_app.command("critique")
def manuscript_critique(
    draft_id: Annotated[str, typer.Argument(help="ManuscriptDraft artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Critique a ManuscriptDraft (Phase 4B)."""
    import asyncio

    async def _run() -> None:
        cfg = _manuscript_config(config, ["research.manuscript_critic"])
        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("manuscript_critic.default")
            try:
                crit_id = await svc.critique(draft_id)
            except Exception as e:
                console.print(f"[red]Critique failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.manuscript import ManuscriptCritique

            c = (await store.get(crit_id)).parse_payload(ManuscriptCritique)
            console.print(f"[green]✓ ManuscriptCritique: {crit_id}[/green]")
            console.print(f"  verdict: {c.verdict.value}")
            console.print(f"  assessment: {c.overall_assessment}")
            for issue in c.issues:
                console.print(
                    f"  [{issue.severity}] {issue.category.value}"
                    + (f" @ {issue.location}" if issue.location else "")
                    + f": {issue.description[:130]}"
                )
            for r in c.recommendations:
                console.print(f"  rec: {r}")

    asyncio.run(_run())


@manuscript_app.command("revise")
def manuscript_revise(
    draft_id: Annotated[str, typer.Argument(help="ManuscriptDraft artifact id (V1)")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Revise a ManuscriptDraft into a superseding version (Phase 4B)."""
    import asyncio

    async def _run() -> None:
        cfg = _manuscript_config(config, ["research.manuscript_drafter"])
        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("manuscript_drafter.default")
            try:
                exec_id = await svc.revise(draft_id)
            except Exception as e:
                console.print(f"[red]Revision failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.manuscript import (
                ManuscriptDraft,
                ManuscriptDraftExecution,
            )

            rec = (await store.get(exec_id)).parse_payload(ManuscriptDraftExecution)
            console.print(f"[green]✓ Revision execution: {exec_id}[/green]")
            draft = (await store.get(rec.draft_id)).parse_payload(ManuscriptDraft)
            console.print(f"  ManuscriptDraft v{draft.version}: {rec.draft_id}")
            console.print(f"  supersedes: {draft.supersedes}")
            console.print(f"  {draft.summary}")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Phase 4C: publication formatting, validation, export, packaging
# ---------------------------------------------------------------------------


def _publication_config(config: pathlib.Path | None, extra_plugins: list[str]) -> Any:
    cfg_path = config if config is not None and config.exists() else None
    cfg = load_config(cfg_path) if cfg_path is not None else None
    if cfg is None:
        from research_harness.config.loader import load_config_from_dict

        cfg = load_config_from_dict(
            {
                "plugins": [
                    "storage.artifacts_sqlite",
                    "storage.blobs_filesystem",
                    *extra_plugins,
                ],
                "artifacts": {"store": "sqlite", "path": ".research/artifacts.db"},
            }
        )
    for pid in ("storage.artifacts_sqlite", "storage.blobs_filesystem"):
        if pid not in cfg.plugins:
            cfg.plugins.append(pid)
    return cfg


@publication_app.command("profile-create")
def publication_profile_create(
    name: Annotated[str, typer.Option("--name", help="Journal / style name")],
    style: Annotated[
        str, typer.Option("--style", help="citation style: author_year | apa")
    ] = "author_year",
    anonymous: Annotated[bool, typer.Option("--anonymous", help="Anonymous-review mode")] = False,
    total_word_limit: Annotated[
        int | None, typer.Option("--total-word-limit", help="Total word limit")
    ] = None,
    abstract_max_words: Annotated[int, typer.Option("--abstract-max-words")] = 250,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Create a PublicationProfile (Phase 4C)."""
    import asyncio

    async def _run() -> None:
        cfg = _publication_config(config, ["research.publication_formatter"])
        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("publication_formatter.default")
            profile_id = await svc.create_profile(
                name=name,
                citation_style=style,
                total_word_limit=total_word_limit,
                abstract_max_words=abstract_max_words,
                anonymous_review=anonymous,
            )
            console.print(f"[green]✓ PublicationProfile: {profile_id}[/green]")
            console.print(f"  {name}  style {style}  anonymous {anonymous}")

    asyncio.run(_run())


@publication_app.command("format")
def publication_format(
    draft: Annotated[str, typer.Option("--draft", help="ManuscriptDraft artifact id")],
    profile: Annotated[str, typer.Option("--profile", help="PublicationProfile artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Format a manuscript draft per a publication profile (Phase 4C)."""
    import asyncio

    async def _run() -> None:
        cfg = _publication_config(config, ["research.publication_formatter"])
        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("publication_formatter.default")
            try:
                m_id = await svc.format(draft, profile)
            except Exception as e:
                console.print(f"[red]Formatting failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.publication import FormattedManuscript

            fm = (await store.get(m_id)).parse_payload(FormattedManuscript)
            console.print(f"[green]✓ FormattedManuscript: {m_id}[/green]")
            console.print(
                f"  sections {len(fm.sections)}  total words {fm.total_word_count}  "
                f"citations {len(fm.citation_map)}"
            )
            console.print(f"  front matter: {fm.front_matter.generated_by}")

    asyncio.run(_run())


@publication_app.command("validate")
def publication_validate(
    manuscript: Annotated[str, typer.Argument(help="FormattedManuscript artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Validate a FormattedManuscript deterministically (Phase 4C)."""
    import asyncio

    async def _run() -> None:
        cfg = _publication_config(config, ["research.publication_formatter"])
        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("publication_formatter.default")
            leaf, passed = await svc.validate(manuscript)
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.publication import FormattedManuscript

            fm = (await store.get(leaf)).parse_payload(FormattedManuscript)
            if passed:
                console.print(f"[green]✓ FormattedManuscript validated: {leaf}[/green]")
            else:
                console.print(f"[red]✗ FormattedManuscript FAILED validation: {leaf}[/red]")
            for issue in fm.validation_issues:
                console.print(f"  [{issue.severity}] {issue.check}: {issue.detail[:130]}")

    asyncio.run(_run())


@publication_app.command("export")
def publication_export(
    manuscript: Annotated[
        str, typer.Option("--manuscript", help="FormattedManuscript artifact id")
    ],
    format: Annotated[str, typer.Option("--format", help="markdown | latex | docx | pdf")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Export a FormattedManuscript deterministically via the BlobStore (Phase 4C)."""
    import asyncio

    async def _run() -> None:
        cfg = _publication_config(config, ["research.publication_formatter"])
        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("publication_formatter.default")
            try:
                er_id = await svc.export(manuscript, format)
            except Exception as e:
                console.print(f"[red]Export failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.publication import ExportRecord

            er = (await store.get(er_id)).parse_payload(ExportRecord)
            console.print(f"[green]✓ ExportRecord: {er_id}[/green]")
            console.print(
                f"  {er.format} via {er.renderer} {er.renderer_version}  "
                f"{er.size_bytes} bytes  sha256 {er.content_hash[:16]}…"
            )
            console.print(f"  blob: {er.blob_ref['storage_key']}")

    asyncio.run(_run())


@publication_app.command("package")
def publication_package(
    manuscript: Annotated[
        str, typer.Option("--manuscript", help="FormattedManuscript artifact id")
    ],
    cover_letter: Annotated[
        bool, typer.Option("--cover-letter", help="Generate a cover letter")
    ] = False,
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Assemble the SubmissionPackage (all formats; ready only when validated)."""
    import asyncio

    async def _run() -> None:
        cfg = _publication_config(config, ["research.publication_formatter"])
        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            svc = runtime.services.require("publication_formatter.default")
            try:
                pkg_id = await svc.package(manuscript, with_cover_letter=cover_letter)
            except Exception as e:
                console.print(f"[red]Packaging failed: {e}[/red]")
                raise typer.Exit(code=1) from e
            store = runtime.services.require("artifact_store.default")
            from research_harness.research.schemas.publication import SubmissionPackage

            pkg = (await store.get(pkg_id)).parse_payload(SubmissionPackage)
            tag = "✓" if pkg.status.value == "ready" else "✗"
            console.print(f"[{tag}] SubmissionPackage: {pkg_id}  status {pkg.status.value}")
            console.print(f"  {pkg.summary}")
            for er in pkg.export_records:
                console.print(
                    f"  export {er.format}: {er.content_hash[:16]}… ({er.size_bytes} bytes)"
                )

    asyncio.run(_run())


@publication_app.command("inspect")
def publication_inspect(
    package_id: Annotated[str, typer.Argument(help="SubmissionPackage artifact id")],
    config: Annotated[pathlib.Path | None, typer.Option(help="Config file path")] = pathlib.Path(
        "configs/example.yaml"
    ),
) -> None:
    """Inspect a SubmissionPackage (Phase 4C)."""
    import asyncio

    async def _run() -> None:
        cfg = _publication_config(config, [])
        from research_harness.app.bootstrap import build_runtime

        runtime = build_runtime(cfg)
        async with runtime:
            store = runtime.services.require("artifact_store.default")
            try:
                p_env = await store.get(package_id)
            except Exception as e:
                console.print(f"[red]Package {package_id!r} not found: {e}[/red]")
                raise typer.Exit(code=1) from e
            from research_harness.research.schemas.publication import (
                CoverLetter,
                FormattedManuscript,
                SubmissionPackage,
            )

            pkg = p_env.parse_payload(SubmissionPackage)
            console.print(f"[bold]SubmissionPackage {package_id}[/bold]")
            console.print(f"  status: {pkg.status.value}  summary: {pkg.summary}")
            fm = (await store.get(pkg.formatted_manuscript_id)).parse_payload(FormattedManuscript)
            console.print(f"  manuscript: {pkg.formatted_manuscript_id} (draft {pkg.draft_id})")
            console.print(f"    title: {fm.front_matter.title}")
            console.print(f"    sections: {len(fm.sections)}  words: {fm.total_word_count}")
            for er in pkg.export_records:
                console.print(
                    f"  export {er.format}: blob {er.blob_ref['storage_key']}  "
                    f"sha256 {er.content_hash[:16]}…"
                )
            if pkg.cover_letter_id:
                cl = (await store.get(pkg.cover_letter_id)).parse_payload(CoverLetter)
                console.print(f"  cover letter: {pkg.cover_letter_id}  anonymous {cl.anonymous}")

    asyncio.run(_run())


if __name__ == "__main__":
    app()
