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


def _create_gap_selection() -> Plugin:
    from research_harness.plugins.research.gap_selection.plugin import GapSelectionPlugin

    return GapSelectionPlugin()


def _create_mechanism_generator() -> Plugin:
    from research_harness.plugins.research.mechanism_generator.plugin import (
        MechanismGeneratorPlugin,
    )

    return MechanismGeneratorPlugin()


def _create_mechanism_critic() -> Plugin:
    from research_harness.plugins.research.mechanism_critic.plugin import (
        MechanismCriticPlugin,
    )

    return MechanismCriticPlugin()


def _create_model_builder() -> Plugin:
    from research_harness.plugins.research.model_builder.plugin import ModelBuilderPlugin

    return ModelBuilderPlugin()


def _create_model_specification_critic() -> Plugin:
    from research_harness.plugins.research.model_specification_critic.plugin import (
        ModelSpecificationCriticPlugin,
    )

    return ModelSpecificationCriticPlugin()


def _create_equilibrium_deriver() -> Plugin:
    from research_harness.plugins.research.equilibrium_deriver.plugin import (
        EquilibriumDeriverPlugin,
    )

    return EquilibriumDeriverPlugin()


def _create_equilibrium_verifier() -> Plugin:
    from research_harness.plugins.research.equilibrium_verifier.plugin import (
        EquilibriumVerifierPlugin,
    )

    return EquilibriumVerifierPlugin()


def _create_comparative_statics() -> Plugin:
    from research_harness.plugins.research.comparative_statics.plugin import (
        ComparativeStaticsPlugin,
    )

    return ComparativeStaticsPlugin()


def _create_proposition_verifier() -> Plugin:
    from research_harness.plugins.research.proposition_verifier.plugin import (
        PropositionVerifierPlugin,
    )

    return PropositionVerifierPlugin()


def _create_proposition_critic() -> Plugin:
    from research_harness.plugins.research.proposition_critic.plugin import (
        PropositionCriticPlugin,
    )

    return PropositionCriticPlugin()


def _create_proposition_generator() -> Plugin:
    from research_harness.plugins.research.proposition_generator.plugin import (
        PropositionGeneratorPlugin,
    )

    return PropositionGeneratorPlugin()


def _create_numerical_analysis() -> Plugin:
    from research_harness.plugins.research.numerical_analysis.plugin import (
        NumericalAnalysisPlugin,
    )

    return NumericalAnalysisPlugin()


def _create_results_assembler() -> Plugin:
    from research_harness.plugins.research.results_assembler.plugin import (
        ResultsAssemblerPlugin,
    )

    return ResultsAssemblerPlugin()


def _create_results_critic() -> Plugin:
    from research_harness.plugins.research.results_critic.plugin import ResultsCriticPlugin

    return ResultsCriticPlugin()


def _create_manuscript_drafter() -> Plugin:
    from research_harness.plugins.research.manuscript_drafter.plugin import (
        ManuscriptDrafterPlugin,
    )

    return ManuscriptDrafterPlugin()


def _create_manuscript_critic() -> Plugin:
    from research_harness.plugins.research.manuscript_critic.plugin import (
        ManuscriptCriticPlugin,
    )

    return ManuscriptCriticPlugin()


def _create_publication_formatter() -> Plugin:
    from research_harness.plugins.research.publication_formatter.plugin import (
        PublicationFormatterPlugin,
    )

    return PublicationFormatterPlugin()


def _create_novelty_validator() -> Plugin:
    from research_harness.plugins.research.novelty_validator.plugin import (
        NoveltyValidatorPlugin,
    )

    return NoveltyValidatorPlugin()


def _create_evaluation_harness() -> Plugin:
    from research_harness.plugins.research.evaluation_harness.plugin import (
        EvaluationHarnessPlugin,
    )

    return EvaluationHarnessPlugin()


def _create_evaluator_deterministic() -> Plugin:
    from research_harness.plugins.research.evaluator_deterministic.plugin import (
        DeterministicEvaluatorPlugin,
    )

    return DeterministicEvaluatorPlugin()


def _create_evaluator_retrieval() -> Plugin:
    from research_harness.plugins.research.evaluator_retrieval.plugin import (
        RetrievalEvaluatorPlugin,
    )

    return RetrievalEvaluatorPlugin()


def _create_evaluator_claim_grounding() -> Plugin:
    from research_harness.plugins.research.evaluator_claim_grounding.plugin import (
        ClaimGroundingEvaluatorPlugin,
    )

    return ClaimGroundingEvaluatorPlugin()


def _create_evaluator_citation_correctness() -> Plugin:
    from research_harness.plugins.research.evaluator_citation_correctness.plugin import (
        CitationCorrectnessEvaluatorPlugin,
    )

    return CitationCorrectnessEvaluatorPlugin()


def _create_evaluator_llm_judge() -> Plugin:
    from research_harness.plugins.research.evaluator_llm_judge.plugin import (
        LlmJudgeEvaluatorPlugin,
    )

    return LlmJudgeEvaluatorPlugin()


def _create_evaluator_screening() -> Plugin:
    from research_harness.plugins.research.evaluator_screening.plugin import (
        ScreeningEvaluatorPlugin,
    )

    return ScreeningEvaluatorPlugin()


def _create_evaluator_evidence() -> Plugin:
    from research_harness.plugins.research.evaluator_evidence.plugin import (
        EvidenceEvaluatorPlugin,
    )

    return EvidenceEvaluatorPlugin()


def _create_evaluator_gap_analysis() -> Plugin:
    from research_harness.plugins.research.evaluator_gap_analysis.plugin import (
        GapAnalysisEvaluatorPlugin,
    )

    return GapAnalysisEvaluatorPlugin()


def _create_evaluator_mechanism() -> Plugin:
    from research_harness.plugins.research.evaluator_mechanism.plugin import (
        MechanismEvaluatorPlugin,
    )

    return MechanismEvaluatorPlugin()


def _create_evaluator_equilibrium() -> Plugin:
    from research_harness.plugins.research.evaluator_equilibrium.plugin import (
        EquilibriumEvaluatorPlugin,
    )

    return EquilibriumEvaluatorPlugin()


def _create_evaluator_numerical() -> Plugin:
    from research_harness.plugins.research.evaluator_numerical.plugin import (
        NumericalEvaluatorPlugin,
    )

    return NumericalEvaluatorPlugin()


def _create_evaluator_comparative_statics() -> Plugin:
    from research_harness.plugins.research.evaluator_comparative_statics.plugin import (
        ComparativeStaticsEvaluatorPlugin,
    )

    return ComparativeStaticsEvaluatorPlugin()


def _create_evaluator_proposition() -> Plugin:
    from research_harness.plugins.research.evaluator_proposition.plugin import (
        PropositionEvaluatorPlugin,
    )

    return PropositionEvaluatorPlugin()


def _create_evaluator_results_grounding() -> Plugin:
    from research_harness.plugins.research.evaluator_results_grounding.plugin import (
        ResultsGroundingEvaluatorPlugin,
    )

    return ResultsGroundingEvaluatorPlugin()


def _create_evaluator_manuscript_grounding() -> Plugin:
    from research_harness.plugins.research.evaluator_manuscript_grounding.plugin import (
        ManuscriptGroundingEvaluatorPlugin,
    )

    return ManuscriptGroundingEvaluatorPlugin()


def _create_evaluator_pipeline_integrity() -> Plugin:
    from research_harness.plugins.research.evaluator_pipeline_integrity.plugin import (
        PipelineIntegrityEvaluatorPlugin,
    )

    return PipelineIntegrityEvaluatorPlugin()


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
    "research.gap_selection": _create_gap_selection,
    "research.mechanism_generator": _create_mechanism_generator,
    "research.mechanism_critic": _create_mechanism_critic,
    "research.model_builder": _create_model_builder,
    "research.model_specification_critic": _create_model_specification_critic,
    "research.equilibrium_deriver": _create_equilibrium_deriver,
    "research.equilibrium_verifier": _create_equilibrium_verifier,
    "research.comparative_statics": _create_comparative_statics,
    "research.proposition_verifier": _create_proposition_verifier,
    "research.proposition_critic": _create_proposition_critic,
    "research.proposition_generator": _create_proposition_generator,
    "research.numerical_analysis": _create_numerical_analysis,
    "research.results_assembler": _create_results_assembler,
    "research.results_critic": _create_results_critic,
    "research.manuscript_drafter": _create_manuscript_drafter,
    "research.manuscript_critic": _create_manuscript_critic,
    "research.publication_formatter": _create_publication_formatter,
    "research.novelty_validator": _create_novelty_validator,
    "research.evaluation_harness": _create_evaluation_harness,
    "evaluator.deterministic": _create_evaluator_deterministic,
    "evaluator.retrieval": _create_evaluator_retrieval,
    "evaluator.claim_grounding": _create_evaluator_claim_grounding,
    "evaluator.citation_correctness": _create_evaluator_citation_correctness,
    "evaluator.llm_judge": _create_evaluator_llm_judge,
    "evaluator.screening": _create_evaluator_screening,
    "evaluator.evidence": _create_evaluator_evidence,
    "evaluator.gap_analysis": _create_evaluator_gap_analysis,
    "evaluator.mechanism": _create_evaluator_mechanism,
    "evaluator.equilibrium": _create_evaluator_equilibrium,
    "evaluator.numerical": _create_evaluator_numerical,
    "evaluator.comparative_statics": _create_evaluator_comparative_statics,
    "evaluator.proposition": _create_evaluator_proposition,
    "evaluator.results_grounding": _create_evaluator_results_grounding,
    "evaluator.manuscript_grounding": _create_evaluator_manuscript_grounding,
    "evaluator.pipeline_integrity": _create_evaluator_pipeline_integrity,
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
