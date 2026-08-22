"""Document contracts — locator, fetcher, extractor, orchestrator."""

from __future__ import annotations

from typing import Protocol


class DocumentLocator(Protocol):
    """Provider-neutral resolver for DocumentLocation candidates."""

    @property
    def resolver_id(self) -> str: ...

    async def resolve(self, paper_identity_id: str) -> list[str]:
        """Return list of DocumentLocation artifact_ids for the identity."""
        ...


class DocumentFetcher(Protocol):
    """Generic HTTP fetcher for document bytes."""

    async def fetch(self, document_location_id: str) -> str:
        """Fetch location, store blob, return DocumentAcquisition artifact_id."""
        ...


class DocumentExtractor(Protocol):
    """Extract structured text from stored PDF bytes."""

    @property
    def extractor_id(self) -> str: ...

    @property
    def extractor_version(self) -> str: ...

    async def extract(self, acquisition_id: str) -> str:
        """Extract text, return FullTextDocument artifact_id."""
        ...


class DocumentAcquisitionOrchestrator(Protocol):
    async def run(self, screened_literature_set_id: str) -> str:
        """Process included identities, return DocumentAcquisitionExecution id."""
        ...

    async def import_local(self, paper_identity_id: str, file_path: str) -> str:
        """Import user-provided file, return DocumentAcquisition id."""
        ...
