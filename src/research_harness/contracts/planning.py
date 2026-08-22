"""Search planning and orchestration contracts."""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, Field


class SearchQueryProposal(BaseModel):
    query: str
    purpose: str | None = None
    concepts: list[str] = Field(default_factory=list)
    target_sources: list[str] = Field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None

    model_config = {"extra": "forbid"}


class SearchStrategyProposal(BaseModel):
    objective: str
    concepts: list[str] = Field(default_factory=list)
    queries: list[SearchQueryProposal]

    model_config = {"extra": "forbid"}


class LiteratureSearchPlanner(Protocol):
    """Generates search strategy from ResearchQuestion/Plan via structured output."""

    async def plan(
        self,
        research_question_id: str,
        research_plan_id: str | None = None,
    ) -> tuple[str, list[str]]:
        """Plan strategy.

        Returns (strategy_artifact_id, query_artifact_ids)
        Persists LiteratureQuery and LiteratureSearchStrategy artifacts.
        """
        ...


class LiteratureSearchOrchestrator(Protocol):
    """Executes a strategy across multiple sources."""

    async def execute(self, strategy_artifact_id: str) -> str:
        """Execute strategy, returns LiteratureSearchExecution artifact id."""
        ...
