"""Registry of built-in plugins."""

from __future__ import annotations

from collections.abc import Callable

from research_harness.kernel.plugin import Plugin


def _create_openrouter() -> Plugin:
    from research_harness.plugins.models.openrouter.plugin import OpenRouterPlugin

    return OpenRouterPlugin()


def _create_role_router() -> Plugin:
    from research_harness.plugins.routing.role_router.plugin import RoleRouterPlugin

    return RoleRouterPlugin()


def _create_echo() -> Plugin:
    from research_harness.plugins.tools.echo.plugin import EchoToolPlugin

    return EchoToolPlugin()


def _create_loop() -> Plugin:
    from research_harness.plugins.loops.simple_tool_loop.plugin import SimpleToolLoopPlugin

    return SimpleToolLoopPlugin()


def _create_session() -> Plugin:
    from research_harness.plugins.sessions.jsonl.plugin import JsonlSessionPlugin

    return JsonlSessionPlugin()


def _create_autonomy() -> Plugin:
    from research_harness.plugins.autonomy.configurable.plugin import ConfigurableAutonomyPlugin

    return ConfigurableAutonomyPlugin()


def _create_artifacts_sqlite() -> Plugin:
    from research_harness.plugins.storage.artifacts_sqlite.plugin import ArtifactsSqlitePlugin

    return ArtifactsSqlitePlugin()


def _create_crossref() -> Plugin:
    from research_harness.plugins.literature.crossref.plugin import CrossrefPlugin

    return CrossrefPlugin()


def _create_semantic_scholar() -> Plugin:
    from research_harness.plugins.literature.semantic_scholar.plugin import SemanticScholarPlugin

    return SemanticScholarPlugin()


def _create_ingestion() -> Plugin:
    from research_harness.plugins.literature.ingestion.plugin import LiteratureIngestionPlugin

    return LiteratureIngestionPlugin()


def _create_identity_resolver() -> Plugin:
    from research_harness.plugins.literature.identity_resolver.plugin import (
        PaperIdentityResolverPlugin,
    )

    return PaperIdentityResolverPlugin()


def _create_search_planner() -> Plugin:
    from research_harness.plugins.literature.search_planner.plugin import (
        LiteratureSearchPlannerPlugin,
    )

    return LiteratureSearchPlannerPlugin()


def _create_search_orchestrator() -> Plugin:
    from research_harness.plugins.literature.search_orchestrator.plugin import (
        LiteratureSearchOrchestratorPlugin,
    )

    return LiteratureSearchOrchestratorPlugin()


def _create_screening_protocol_builder() -> Plugin:
    from research_harness.plugins.literature.screening_protocol_builder.plugin import (
        ScreeningProtocolBuilderPlugin,
    )

    return ScreeningProtocolBuilderPlugin()


def _create_screening_view_builder() -> Plugin:
    from research_harness.plugins.literature.screening_view_builder.plugin import (
        ScreeningViewBuilderPlugin,
    )

    return ScreeningViewBuilderPlugin()


def _create_title_abstract_screener() -> Plugin:
    from research_harness.plugins.literature.title_abstract_screener.plugin import (
        TitleAbstractScreenerPlugin,
    )

    return TitleAbstractScreenerPlugin()


def _create_screening_orchestrator() -> Plugin:
    from research_harness.plugins.literature.screening_orchestrator.plugin import (
        ScreeningOrchestratorPlugin,
    )

    return ScreeningOrchestratorPlugin()


def _create_evidence_extractor() -> Plugin:
    from research_harness.plugins.literature.evidence_extractor.plugin import (
        EvidenceExtractorPlugin,
    )

    return EvidenceExtractorPlugin()


def _create_evidence_orchestrator() -> Plugin:
    from research_harness.plugins.literature.evidence_orchestrator.plugin import (
        EvidenceOrchestratorPlugin,
    )

    return EvidenceOrchestratorPlugin()


def _create_synthesis() -> Plugin:
    from research_harness.plugins.literature.synthesis.plugin import (
        LiteratureSynthesisPlugin,
    )

    return LiteratureSynthesisPlugin()


def _create_gap_analyzer() -> Plugin:
    from research_harness.plugins.literature.gap_analyzer.plugin import GapAnalyzerPlugin

    return GapAnalyzerPlugin()


def _create_blobs_filesystem() -> Plugin:
    from research_harness.plugins.storage.blobs_filesystem.plugin import BlobsFilesystemPlugin

    return BlobsFilesystemPlugin()


def _create_locator_metadata() -> Plugin:
    from research_harness.plugins.documents.locator_metadata.plugin import MetadataLocatorPlugin

    return MetadataLocatorPlugin()


def _create_locator_unpaywall() -> Plugin:
    from research_harness.plugins.documents.locator_unpaywall.plugin import UnpaywallLocatorPlugin

    return UnpaywallLocatorPlugin()


def _create_fetcher_http() -> Plugin:
    from research_harness.plugins.documents.fetcher_http.plugin import HttpFetcherPlugin

    return HttpFetcherPlugin()


def _create_extractor_pypdf() -> Plugin:
    from research_harness.plugins.documents.extractor_pypdf.plugin import PypdfExtractorPlugin

    return PypdfExtractorPlugin()


def _create_acquisition_orchestrator() -> Plugin:
    from research_harness.plugins.documents.acquisition_orchestrator.plugin import (
        DocumentAcquisitionOrchestratorPlugin,
    )

    return DocumentAcquisitionOrchestratorPlugin()


BUILTIN_PLUGINS: dict[str, Callable[[], Plugin]] = {
    "model.openrouter": _create_openrouter,
    "routing.role_router": _create_role_router,
    "tool.echo": _create_echo,
    "loop.simple_tool_loop": _create_loop,
    "session.jsonl": _create_session,
    "autonomy.configurable": _create_autonomy,
    "storage.artifacts_sqlite": _create_artifacts_sqlite,
    "literature.crossref": _create_crossref,
    "literature.semantic_scholar": _create_semantic_scholar,
    "literature.ingestion": _create_ingestion,
    "literature.identity_resolver": _create_identity_resolver,
    "literature.search_planner": _create_search_planner,
    "literature.search_orchestrator": _create_search_orchestrator,
    "literature.screening_protocol_builder": _create_screening_protocol_builder,
    "literature.screening_view_builder": _create_screening_view_builder,
    "literature.title_abstract_screener": _create_title_abstract_screener,
    "literature.screening_orchestrator": _create_screening_orchestrator,
    "literature.evidence_extractor": _create_evidence_extractor,
    "literature.evidence_orchestrator": _create_evidence_orchestrator,
    "literature.synthesis": _create_synthesis,
    "literature.gap_analyzer": _create_gap_analyzer,
    "storage.blobs_filesystem": _create_blobs_filesystem,
    "documents.locator.metadata": _create_locator_metadata,
    "documents.locator.unpaywall": _create_locator_unpaywall,
    "documents.fetcher.http": _create_fetcher_http,
    "documents.extractor.pypdf": _create_extractor_pypdf,
    "documents.acquisition_orchestrator": _create_acquisition_orchestrator,
}


def create_plugin(plugin_id: str) -> Plugin:
    """Create a built-in plugin by id.

    For merged builtin+external discovery, use research_harness.app.bootstrap.create_plugin.
    """
    factory = BUILTIN_PLUGINS.get(plugin_id)
    if factory is None:
        available = sorted(BUILTIN_PLUGINS.keys())
        raise ValueError(f"unknown plugin {plugin_id!r}. Available built-ins: {available}")
    return factory()


def list_builtin_ids() -> list[str]:
    return sorted(BUILTIN_PLUGINS.keys())
