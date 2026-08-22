"""Research domain package — schemas and provenance."""

from research_harness.research.schemas.claim import ResearchClaim
from research_harness.research.schemas.evidence import EvidenceItem, Locator
from research_harness.research.schemas.execution import LiteratureSearchExecution
from research_harness.research.schemas.identity import PaperIdentity
from research_harness.research.schemas.paper import Author, PaperRecord
from research_harness.research.schemas.project import ResearchPlan, ResearchQuestion
from research_harness.research.schemas.provider_snapshot import ProviderRecordSnapshot
from research_harness.research.schemas.query import LiteratureQuery
from research_harness.research.schemas.search_record import LiteratureSearchRecord
from research_harness.research.schemas.source import SourceRecord
from research_harness.research.schemas.strategy import LiteratureSearchStrategy

__all__ = [
    "Author",
    "EvidenceItem",
    "LiteratureQuery",
    "LiteratureSearchExecution",
    "LiteratureSearchRecord",
    "LiteratureSearchStrategy",
    "Locator",
    "PaperIdentity",
    "PaperRecord",
    "ProviderRecordSnapshot",
    "ResearchClaim",
    "ResearchPlan",
    "ResearchQuestion",
    "SourceRecord",
]
