"""Pydantic schema for runtime configuration."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class RuntimeSection(BaseModel):
    autonomy: str = Field(default="high", description="Autonomy mode: high or interactive")

    @field_validator("autonomy")
    @classmethod
    def validate_autonomy(cls, v: str) -> str:
        if v not in ("high", "interactive"):
            raise ValueError(f"autonomy must be 'high' or 'interactive', got {v!r}")
        return v


class ModelRoleConfig(BaseModel):
    provider: str = Field(description="Provider id, e.g. openrouter")
    model: str = Field(description="Model slug")

    model_config = {"extra": "forbid"}


class ModelsConfig(BaseModel):
    roles: dict[str, ModelRoleConfig] = Field(
        default_factory=dict, description="Logical role -> provider/model"
    )

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, v: dict[str, ModelRoleConfig]) -> dict[str, ModelRoleConfig]:
        if not v:
            return v
        for role in v:
            if not role:
                raise ValueError("role name must be non-empty")
        return v


class SessionConfig(BaseModel):
    root: str = Field(default=".research/sessions", description="Session storage root dir")

    model_config = {"extra": "forbid"}


class LoopConfig(BaseModel):
    max_steps: int = Field(default=8, ge=1, le=100, description="Max agent loop steps")

    model_config = {"extra": "forbid"}


class ArtifactsConfig(BaseModel):
    store: str = Field(default="sqlite", description="Artifact store backend")
    path: str = Field(default=".research/artifacts.db", description="SQLite path")

    @field_validator("store")
    @classmethod
    def validate_store(cls, v: str) -> str:
        if v not in ("sqlite",):
            raise ValueError(f"artifacts.store must be 'sqlite', got {v!r}")
        return v

    model_config = {"extra": "forbid"}


class CrossrefConfig(BaseModel):
    enabled: bool = Field(default=True)
    timeout_seconds: float = Field(default=20.0, ge=1, le=120)
    mailto: str | None = Field(default=None, description="Contact email for polite pool")

    model_config = {"extra": "forbid"}


class SemanticScholarConfig(BaseModel):
    enabled: bool = Field(default=True)
    timeout_seconds: float = Field(default=20.0, ge=1, le=120)

    model_config = {"extra": "forbid"}


class LiteraturePlanningConfig(BaseModel):
    model_role: str = Field(default="fast", description="Model role for planning")
    max_queries: int = Field(default=8, ge=1, le=20)
    max_sources_per_query: int = Field(default=2, ge=1, le=5)

    model_config = {"extra": "forbid"}


class LiteratureOrchestrationConfig(BaseModel):
    max_queries: int = Field(default=8, ge=1, le=20)
    max_results_per_query_per_source: int = Field(default=50, ge=1, le=200)
    max_total_provider_requests: int = Field(default=50, ge=1, le=500)
    max_total_papers: int = Field(default=500, ge=1, le=5000)

    model_config = {"extra": "forbid"}


class LiteratureScreeningConfig(BaseModel):
    protocol_model_role: str = Field(default="reasoning")
    screening_model_role: str = Field(default="fast")
    max_candidates: int = Field(default=500, ge=1, le=5000)
    max_model_calls: int = Field(default=500, ge=1, le=5000)
    max_inclusion_criteria: int = Field(default=12, ge=1, le=20)
    max_exclusion_criteria: int = Field(default=12, ge=1, le=20)
    review_uncertain: bool = Field(default=True)
    review_low_confidence_below: float | None = Field(default=0.65, ge=0.0, le=1.0)

    model_config = {"extra": "forbid"}


class LiteratureEvidenceConfig(BaseModel):
    model_role: str = Field(default="reasoning", description="Logical model role for extraction")
    pages_per_chunk: int = Field(default=4, ge=1, le=50, description="Pages per extraction chunk")
    max_chunks_per_document: int = Field(default=50, ge=1, le=500)
    max_model_calls: int = Field(default=500, ge=1, le=5000)

    model_config = {"extra": "forbid"}


class LiteratureSynthesisConfig(BaseModel):
    model_role: str = Field(default="reasoning", description="Logical model role for synthesis")
    batch_profiles: int = Field(default=3, ge=1, le=20, description="Profiles per model batch")
    max_evidence_per_profile: int = Field(default=12, ge=1, le=100)
    max_batches: int = Field(default=20, ge=1, le=100)
    max_model_calls: int = Field(default=100, ge=1, le=1000)

    model_config = {"extra": "forbid"}


class LiteratureGapConfig(BaseModel):
    model_role: str = Field(default="reasoning", description="Logical model role for gap analysis")
    max_statements: int = Field(
        default=200, ge=1, le=1000, description="Max synthesis statements to analyze"
    )
    max_gaps: int = Field(default=50, ge=1, le=200)
    max_model_calls: int = Field(default=20, ge=1, le=100)

    model_config = {"extra": "forbid"}


class ResearchMechanismConfig(BaseModel):
    generator_role: str = Field(
        default="reasoning", description="Logical model role for mechanism generation/revision"
    )
    critic_role: str = Field(
        default="critic", description="Logical model role for mechanism critique"
    )
    revision_role: str = Field(
        default="reasoning", description="Logical model role for mechanism revision"
    )
    max_candidates: int = Field(default=5, ge=1, le=20)
    max_model_calls: int = Field(default=20, ge=1, le=100)

    model_config = {"extra": "forbid"}


class ResearchNumericalConfig(BaseModel):
    model_role: str = Field(
        default="reasoning",
        description="Logical model role for numerical runs (metadata only; numbers are deterministic)",
    )
    max_points: int = Field(default=10000, ge=100, le=1000000)
    artifact_point_threshold: int = Field(default=500, ge=10, le=100000)

    model_config = {"extra": "forbid"}


class ResearchPropositionConfig(BaseModel):
    generator_role: str = Field(
        default="reasoning", description="Logical model role for proposition generation"
    )
    critic_role: str = Field(
        default="critic", description="Logical model role for proposition critique"
    )
    interpretation_role: str = Field(
        default="reasoning", description="Logical model role for economic interpretation"
    )
    max_propositions: int = Field(default=8, ge=1, le=50)
    max_llm_calls: int = Field(default=20, ge=1, le=100)

    model_config = {"extra": "forbid"}


class ResearchEquilibriumConfig(BaseModel):
    deriver_role: str = Field(
        default="reasoning", description="Logical model role for equilibrium derivation"
    )
    revision_role: str = Field(
        default="reasoning", description="Logical model role for candidate revision"
    )
    max_revisions: int = Field(
        default=2, ge=0, le=10, description="Bounded revision attempts per candidate"
    )
    max_llm_calls: int = Field(default=10, ge=1, le=100)

    model_config = {"extra": "forbid"}


class ResearchModelConfig(BaseModel):
    builder_role: str = Field(
        default="reasoning", description="Logical model role for model building"
    )
    critic_role: str = Field(default="critic", description="Logical model role for model critique")
    revision_role: str = Field(
        default="reasoning", description="Logical model role for model revision"
    )
    max_actors: int = Field(default=8, ge=1, le=30)
    max_variables: int = Field(default=40, ge=1, le=200)
    max_parameters: int = Field(default=40, ge=1, le=200)
    max_assumptions: int = Field(default=20, ge=1, le=100)
    max_stages: int = Field(default=20, ge=1, le=100)
    max_payoffs: int = Field(default=10, ge=1, le=50)

    model_config = {"extra": "forbid"}


class ResearchResultsConfig(BaseModel):
    assembler_role: str = Field(
        default="reasoning", description="Logical model role for results assembly"
    )
    critic_role: str = Field(
        default="critic", description="Logical model role for results critique"
    )
    max_findings: int = Field(default=12, ge=1, le=50)
    max_contributions: int = Field(default=8, ge=1, le=30)
    max_implications: int = Field(default=12, ge=1, le=50)
    max_llm_calls: int = Field(default=10, ge=1, le=100)

    model_config = {"extra": "forbid"}


class ResearchManuscriptConfig(BaseModel):
    drafter_role: str = Field(
        default="reasoning", description="Logical model role for manuscript drafting"
    )
    critic_role: str = Field(
        default="critic", description="Logical model role for manuscript critique"
    )
    max_llm_calls: int = Field(default=100, ge=1, le=1000)

    model_config = {"extra": "forbid"}


class ResearchPublicationConfig(BaseModel):
    formatter_role: str = Field(
        default="reasoning", description="Logical model role for front matter / cover letter"
    )
    max_llm_calls: int = Field(default=10, ge=1, le=100)

    model_config = {"extra": "forbid"}


class ResearchConfig(BaseModel):
    mechanism: ResearchMechanismConfig = Field(default_factory=ResearchMechanismConfig)
    model: ResearchModelConfig = Field(default_factory=ResearchModelConfig)
    equilibrium: ResearchEquilibriumConfig = Field(default_factory=ResearchEquilibriumConfig)
    proposition: ResearchPropositionConfig = Field(default_factory=ResearchPropositionConfig)
    numerical: ResearchNumericalConfig = Field(default_factory=ResearchNumericalConfig)
    results: ResearchResultsConfig = Field(default_factory=ResearchResultsConfig)
    manuscript: ResearchManuscriptConfig = Field(default_factory=ResearchManuscriptConfig)
    publication: ResearchPublicationConfig = Field(default_factory=ResearchPublicationConfig)

    model_config = {"extra": "forbid"}


class LiteratureConfig(BaseModel):
    crossref: CrossrefConfig = Field(default_factory=CrossrefConfig)
    semantic_scholar: SemanticScholarConfig = Field(default_factory=SemanticScholarConfig)
    planning: LiteraturePlanningConfig = Field(default_factory=LiteraturePlanningConfig)
    orchestration: LiteratureOrchestrationConfig = Field(
        default_factory=LiteratureOrchestrationConfig
    )
    screening: LiteratureScreeningConfig = Field(default_factory=LiteratureScreeningConfig)
    evidence: LiteratureEvidenceConfig = Field(default_factory=LiteratureEvidenceConfig)
    synthesis: LiteratureSynthesisConfig = Field(default_factory=LiteratureSynthesisConfig)
    gap: LiteratureGapConfig = Field(default_factory=LiteratureGapConfig)

    model_config = {"extra": "forbid"}


class DocumentsDownloadConfig(BaseModel):
    timeout_seconds: float = Field(default=30.0, ge=1, le=300)
    max_redirects: int = Field(default=5, ge=0, le=10)
    max_bytes: int = Field(default=52428800, ge=1024, le=536870912)

    model_config = {"extra": "forbid"}


class DocumentsLocationConfig(BaseModel):
    use_metadata: bool = Field(default=True)
    use_unpaywall: bool = Field(default=True)

    model_config = {"extra": "forbid"}


class DocumentsAcquisitionConfig(BaseModel):
    max_locations_per_paper: int = Field(default=5, ge=1, le=20)

    model_config = {"extra": "forbid"}


class DocumentsExtractionConfig(BaseModel):
    extractor: str = Field(default="pypdf")

    model_config = {"extra": "forbid"}


class DocumentsConfig(BaseModel):
    blob_root: str = Field(default=".research/blobs")
    location: DocumentsLocationConfig = Field(default_factory=DocumentsLocationConfig)
    download: DocumentsDownloadConfig = Field(default_factory=DocumentsDownloadConfig)
    acquisition: DocumentsAcquisitionConfig = Field(default_factory=DocumentsAcquisitionConfig)
    extraction: DocumentsExtractionConfig = Field(default_factory=DocumentsExtractionConfig)

    model_config = {"extra": "forbid"}


class AppConfig(BaseModel):
    runtime: RuntimeSection = Field(default_factory=RuntimeSection)
    plugins: list[str] = Field(default_factory=list, description="List of plugin ids to load")
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    loop: LoopConfig = Field(default_factory=LoopConfig)
    artifacts: ArtifactsConfig = Field(default_factory=ArtifactsConfig)
    literature: LiteratureConfig = Field(default_factory=LiteratureConfig)
    documents: DocumentsConfig = Field(default_factory=DocumentsConfig)
    research: ResearchConfig = Field(default_factory=ResearchConfig)

    @field_validator("plugins")
    @classmethod
    def validate_plugins(cls, v: list[str]) -> list[str]:
        if len(v) != len(set(v)):
            raise ValueError(f"duplicate plugin ids in config: {v}")
        return v

    model_config = {"extra": "forbid"}

    def plugin_config(self, plugin_id: str) -> dict[str, Any]:
        """Return dict config for a given plugin id (derived)."""
        mapping: dict[str, dict[str, Any]] = {
            "routing.role_router": {"models": self.models.model_dump()},
            "session.jsonl": {"session": self.session.model_dump()},
            "loop.simple_tool_loop": {"loop": self.loop.model_dump()},
            "autonomy.configurable": {"autonomy": self.runtime.autonomy},
            "storage.artifacts_sqlite": {"artifacts": self.artifacts.model_dump()},
            "storage.blobs_filesystem": {"documents": self.documents.model_dump()},
            "literature.crossref": {"literature": self.literature.model_dump()},
            "literature.semantic_scholar": {"literature": self.literature.model_dump()},
            "literature.ingestion": {},
            "literature.identity_resolver": {},
            "literature.search_planner": {"literature": self.literature.model_dump()},
            "literature.search_orchestrator": {"literature": self.literature.model_dump()},
            "literature.screening_protocol_builder": {"literature": self.literature.model_dump()},
            "literature.screening_view_builder": {},
            "literature.title_abstract_screener": {"literature": self.literature.model_dump()},
            "literature.screening_orchestrator": {"literature": self.literature.model_dump()},
            "literature.evidence_extractor": {"literature": self.literature.model_dump()},
            "literature.evidence_orchestrator": {"literature": self.literature.model_dump()},
            "literature.synthesis": {"literature": self.literature.model_dump()},
            "literature.gap_analyzer": {"literature": self.literature.model_dump()},
            "research.gap_selection": {
                "research": self.research.model_dump(),
                "autonomy_mode": self.runtime.autonomy,
            },
            "research.mechanism_generator": {"research": self.research.model_dump()},
            "research.mechanism_critic": {"research": self.research.model_dump()},
            "research.model_builder": {"research": self.research.model_dump()},
            "research.model_specification_critic": {"research": self.research.model_dump()},
            "research.equilibrium_deriver": {"research": self.research.model_dump()},
            "research.equilibrium_verifier": {},
            "research.comparative_statics": {},
            "research.proposition_verifier": {},
            "research.proposition_critic": {"research": self.research.model_dump()},
            "research.proposition_generator": {"research": self.research.model_dump()},
            "research.numerical_analysis": {"research": self.research.model_dump()},
            "documents.locator.metadata": {"documents": self.documents.model_dump()},
            "documents.locator.unpaywall": {"documents": self.documents.model_dump()},
            "documents.fetcher.http": {"documents": self.documents.model_dump()},
            "documents.extractor.pypdf": {"documents": self.documents.model_dump()},
            "documents.acquisition_orchestrator": {"documents": self.documents.model_dump()},
        }
        return mapping.get(plugin_id, {})
