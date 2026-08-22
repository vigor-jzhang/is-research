"""Screening contracts — view builder, screener, orchestrator."""

from __future__ import annotations

from typing import Protocol


class ScreeningViewBuilder(Protocol):
    async def build(self, paper_identity_id: str) -> str:
        """Build PaperScreeningView for a PaperIdentity, returns view artifact id."""
        ...


class TitleAbstractScreener(Protocol):
    async def screen(self, screening_view_id: str, protocol_id: str) -> str:
        """Screen a view against a protocol, returns ScreeningDecision artifact id."""
        ...


class ScreeningOrchestrator(Protocol):
    async def screen(self, search_execution_id: str, protocol_id: str) -> str:
        """Screen all current PaperIdentities from a search execution, returns ScreeningExecution artifact id."""
        ...


class ScreeningProtocolBuilder(Protocol):
    async def build(self, research_question_id: str, research_plan_id: str | None = None) -> str:
        """Build a ScreeningProtocol, returns protocol artifact id (draft, may need approval)."""
        ...
