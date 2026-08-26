"""Application bootstrap — composes kernel + plugins without kernel importing plugins.

This is the composition root. It knows about both kernel and concrete plugins
and is the only place that performs plugin discovery (built-in + external
entry_points).
"""

from __future__ import annotations

import importlib.metadata
import logging
from collections.abc import Callable
from typing import Any

from research_harness.config.loader import load_config
from research_harness.config.schema import AppConfig
from research_harness.kernel.errors import PluginError
from research_harness.kernel.events import EventBus
from research_harness.kernel.manager import PluginManager
from research_harness.kernel.plugin import Plugin
from research_harness.kernel.runtime import Runtime
from research_harness.kernel.services import ServiceRegistry
from research_harness.plugins.registry import BUILTIN_PLUGINS

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "research_harness.plugins"

# ---------------------------------------------------------------------------
# External discovery
# ---------------------------------------------------------------------------


def _load_entry_points() -> list[Any]:
    """Load entry points for the group, handling Python version differences."""
    try:
        # Python 3.10+ supports group argument
        eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)  # type: ignore[call-arg]
        # In newer Python this returns EntryPoints object; in older it returns list
        try:
            return list(eps)
        except TypeError:
            return list(eps)  # type: ignore[arg-type]
    except TypeError:
        # Fallback for older importlib.metadata that returns dict
        try:
            eps_map = importlib.metadata.entry_points()  # type: ignore[call-arg]
            if hasattr(eps_map, "get"):
                return list(eps_map.get(ENTRY_POINT_GROUP, []))  # type: ignore[union-attr]
            # If it's already a dict-like
            return []
        except Exception:
            return []
    except Exception:
        return []


def discover_external_factories() -> dict[str, Callable[[], Plugin]]:
    """Discover external plugins via entry_points.

    Returns a mapping plugin_id -> factory callable that lazily loads and validates
    the plugin. Factories are not invoked until the plugin is actually requested,
    avoiding import-time side effects for unused plugins.

    Validation (instance type, metadata id match) happens when the factory is called.
    """
    factories: dict[str, Callable[[], Plugin]] = {}
    for ep in _load_entry_points():
        plugin_id = ep.name

        def _make_factory(entry_point: Any) -> Callable[[], Plugin]:
            def factory() -> Plugin:
                try:
                    obj = entry_point.load()
                except Exception as e:
                    raise PluginError(
                        f"failed to load external plugin {entry_point.name!r} "
                        f"({entry_point.value}): {e}"
                    ) from e

                # Resolve object to Plugin instance
                plugin: Any = None
                # Case 1: obj is a Plugin instance (has metadata)
                if isinstance(obj, Plugin):
                    plugin = obj
                # Case 2: obj is a Plugin subclass (callable returning instance)
                elif isinstance(obj, type) and issubclass(obj, Plugin):
                    try:
                        plugin = obj()
                    except Exception as e:
                        raise PluginError(
                            f"failed to instantiate external plugin {entry_point.name!r} ({entry_point.value}): {e}"
                        ) from e
                # Case 3: obj is a factory function (callable)
                elif callable(obj):
                    try:
                        result = obj()
                    except Exception as e:
                        raise PluginError(
                            f"failed to call factory for external plugin {entry_point.name!r} ({entry_point.value}): {e}"
                        ) from e
                    if isinstance(result, Plugin):
                        plugin = result
                    else:
                        raise PluginError(
                            f"external plugin factory {entry_point.name!r} ({entry_point.value}) "
                            f"did not return a Plugin instance, got {type(result).__name__}"
                        )
                else:
                    raise PluginError(
                        f"external plugin {entry_point.name!r} ({entry_point.value}) "
                        f"is not a Plugin, Plugin subclass, or factory; got {type(obj).__name__}"
                    )

                # Validate metadata id matches entry point name
                try:
                    meta_id = plugin.metadata.id
                except Exception as e:
                    raise PluginError(
                        f"external plugin {entry_point.name!r} raised when accessing metadata: {e}"
                    ) from e
                if meta_id != entry_point.name:
                    raise PluginError(
                        f"external plugin id mismatch: entry_point name {entry_point.name!r} "
                        f"!= plugin.metadata.id {meta_id!r} ({entry_point.value})"
                    )

                return plugin

            return factory

        if plugin_id in factories:
            raise PluginError(
                f"duplicate external plugin id {plugin_id!r} from entry_points "
                f"(value {ep.value!r}); already provided by another external plugin"
            )
        factories[plugin_id] = _make_factory(ep)
    return factories


def get_all_plugin_factories() -> dict[str, Callable[[], Plugin]]:
    """Return merged builtin + external factories with duplicate detection.

    Builtins take precedence in detection: if an external id duplicates a builtin,
    an error is raised.
    """
    merged: dict[str, Callable[[], Plugin]] = dict(BUILTIN_PLUGINS)
    externals = discover_external_factories()
    for pid, factory in externals.items():
        if pid in merged:
            # Try to report source of builtin vs external
            raise PluginError(
                f"duplicate plugin id {pid!r}: already provided by built-in plugin; "
                f"external entry_point {pid!r} conflicts (value would shadow built-in)"
            )
        merged[pid] = factory
    return merged


def list_available_plugins() -> list[tuple[str, str]]:
    """Return list of (plugin_id, source) for diagnostics.

    Source is 'builtin' or 'external'.
    List is sorted deterministically.
    """
    builtin_ids = set(BUILTIN_PLUGINS.keys())
    try:
        externals = discover_external_factories()
    except Exception:
        externals = {}
    result: list[tuple[str, str]] = []
    for pid in sorted(builtin_ids):
        result.append((pid, "builtin"))
    for pid in sorted(externals.keys()):
        # Avoid duplicates already reported; but list only externals not shadowing
        if pid not in builtin_ids:
            result.append((pid, "external"))
    # Sort overall for deterministic output (builtin first then external, but sorted)
    result.sort(key=lambda x: x[0])
    return result


def create_plugin(plugin_id: str) -> Plugin:
    """Create a plugin by id from merged factories.

    Validates that the returned object is a Plugin and that its metadata id
    matches the requested id.
    """
    factories = get_all_plugin_factories()
    factory = factories.get(plugin_id)
    if factory is None:
        available = sorted(factories.keys())
        raise PluginError(f"unknown plugin {plugin_id!r}. Available: {available}")
    plugin = factory()
    if not isinstance(plugin, Plugin):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise PluginError(
            f"factory for {plugin_id!r} did not return a Plugin, got {type(plugin).__name__}"
        )
    # Double-check id matches (for builtins, should always match; for externals already validated)
    if plugin.metadata.id != plugin_id:
        raise PluginError(
            f"plugin id mismatch: requested {plugin_id!r} but plugin.metadata.id is {plugin.metadata.id!r}"
        )
    return plugin


# ---------------------------------------------------------------------------
# Runtime construction (composition root)
# ---------------------------------------------------------------------------


def _derived_plugin_configs(
    config: AppConfig, plugin_configs: dict[str, dict[str, Any]] | None
) -> dict[str, dict[str, Any]]:
    """Build per-plugin config from AppConfig + overrides."""
    pc: dict[str, dict[str, Any]] = {}
    if plugin_configs:
        pc.update(plugin_configs)

    derived: dict[str, dict[str, Any]] = {
        "routing.role_router": {"models": config.models.model_dump()},
        "routing.policy_router": {"models": config.models.model_dump()},
        "evaluation.live_quality": {"models": config.models.model_dump()},
        "session.jsonl": {"session": config.session.model_dump()},
        "loop.simple_tool_loop": {"loop": config.loop.model_dump()},
        "autonomy.configurable": {"autonomy": config.runtime.autonomy},
        "storage.artifacts_sqlite": {"artifacts": config.artifacts.model_dump()},
        "storage.blobs_filesystem": {"documents": config.documents.model_dump()},
        "literature.crossref": {"literature": config.literature.model_dump()},
        "literature.semantic_scholar": {"literature": config.literature.model_dump()},
        "literature.ingestion": {},
        "literature.identity_resolver": {},
        "literature.search_planner": {"literature": config.literature.model_dump()},
        "literature.search_orchestrator": {"literature": config.literature.model_dump()},
        "literature.screening_protocol_builder": {"literature": config.literature.model_dump()},
        "literature.screening_view_builder": {},
        "literature.title_abstract_screener": {"literature": config.literature.model_dump()},
        "literature.screening_orchestrator": {"literature": config.literature.model_dump()},
        "literature.evidence_extractor": {"literature": config.literature.model_dump()},
        "literature.evidence_orchestrator": {"literature": config.literature.model_dump()},
        "literature.synthesis": {"literature": config.literature.model_dump()},
        "literature.gap_analyzer": {"literature": config.literature.model_dump()},
        "research.gap_selection": {
            "research": config.research.model_dump(),
            "autonomy_mode": config.runtime.autonomy,
        },
        "research.mechanism_generator": {"research": config.research.model_dump()},
        "research.mechanism_critic": {"research": config.research.model_dump()},
        "research.model_builder": {"research": config.research.model_dump()},
        "research.model_specification_critic": {"research": config.research.model_dump()},
        "research.equilibrium_deriver": {"research": config.research.model_dump()},
        "research.equilibrium_verifier": {},
        "research.comparative_statics": {},
        "research.proposition_verifier": {},
        "research.proposition_critic": {"research": config.research.model_dump()},
        "research.proposition_generator": {"research": config.research.model_dump()},
        "research.numerical_analysis": {"research": config.research.model_dump()},
        "research.results_assembler": {"research": config.research.model_dump()},
        "research.results_critic": {"research": config.research.model_dump()},
        "research.manuscript_drafter": {"research": config.research.model_dump()},
        "research.manuscript_critic": {"research": config.research.model_dump()},
        "research.publication_formatter": {"research": config.research.model_dump()},
        "research.novelty_validator": {"research": config.research.model_dump()},
        "research.evaluation_harness": {"evaluation": config.evaluation.model_dump()},
        "evaluation.model_tournament": {"evaluation": config.evaluation.model_dump()},
        "evaluator.deterministic": {"evaluation": config.evaluation.model_dump()},
        "evaluator.retrieval": {"evaluation": config.evaluation.model_dump()},
        "evaluator.claim_grounding": {"evaluation": config.evaluation.model_dump()},
        "evaluator.citation_correctness": {"evaluation": config.evaluation.model_dump()},
        "evaluator.llm_judge": {"evaluation": config.evaluation.model_dump()},
        "evaluator.screening": {"evaluation": config.evaluation.model_dump()},
        "evaluator.evidence": {"evaluation": config.evaluation.model_dump()},
        "evaluator.gap_analysis": {"evaluation": config.evaluation.model_dump()},
        "evaluator.mechanism": {"evaluation": config.evaluation.model_dump()},
        "evaluator.equilibrium": {"evaluation": config.evaluation.model_dump()},
        "evaluator.numerical": {"evaluation": config.evaluation.model_dump()},
        "evaluator.comparative_statics": {"evaluation": config.evaluation.model_dump()},
        "evaluator.proposition": {"evaluation": config.evaluation.model_dump()},
        "evaluator.results_grounding": {"evaluation": config.evaluation.model_dump()},
        "evaluator.manuscript_grounding": {"evaluation": config.evaluation.model_dump()},
        "evaluator.pipeline_integrity": {"evaluation": config.evaluation.model_dump()},
        "documents.locator.metadata": {"documents": config.documents.model_dump()},
        "documents.locator.unpaywall": {"documents": config.documents.model_dump()},
        "documents.fetcher.http": {"documents": config.documents.model_dump()},
        "documents.extractor.pypdf": {"documents": config.documents.model_dump()},
        "documents.acquisition_orchestrator": {"documents": config.documents.model_dump()},
    }
    for pid, cfg in derived.items():
        if pid not in pc:
            pc[pid] = cfg
        else:
            pc[pid] = {**cfg, **pc[pid]}
    return pc


def build_runtime(
    config: AppConfig,
    extra_plugins: list[Plugin] | None = None,
    plugin_configs: dict[str, dict[str, Any]] | None = None,
) -> Runtime:
    """Build a Runtime from a validated AppConfig.

    Discovers built-in + external plugins, instantiates those listed in
    config.plugins, and wires the kernel components.
    """
    services = ServiceRegistry()
    events = EventBus()
    pc = _derived_plugin_configs(config, plugin_configs)

    manager = PluginManager(
        services=services,
        events=events,
        plugin_configs=pc,
        runtime_meta={"config_path": None},
    )

    # Instantiate plugins listed in config via merged discovery
    for pid in config.plugins:
        plugin = create_plugin(pid)
        manager.register(plugin)

    # Extra plugins for testing (inject fakes directly, bypass discovery)
    if extra_plugins:
        for p in extra_plugins:
            manager.register(p)

    return Runtime(config=config, plugin_manager=manager, services=services, events=events)


def build_runtime_from_yaml(
    path: str,
    extra_plugins: list[Plugin] | None = None,
    plugin_configs: dict[str, dict[str, Any]] | None = None,
) -> Runtime:
    """Load YAML config and build runtime."""
    config = load_config(path)
    return build_runtime(config, extra_plugins=extra_plugins, plugin_configs=plugin_configs)
