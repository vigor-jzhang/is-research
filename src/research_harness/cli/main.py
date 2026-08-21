"""CLI for research harness."""

from __future__ import annotations

import asyncio
import pathlib
from typing import Annotated

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

app.add_typer(plugins_app, name="plugins")
app.add_typer(config_app, name="config")
app.add_typer(session_app, name="session")
app.add_typer(runtime_app, name="runtime")

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


if __name__ == "__main__":
    app()
