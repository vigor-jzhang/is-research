"""Research schemas."""

from research_harness.research.schemas.blob import BlobReference
from research_harness.research.schemas.claim import ClaimType, ResearchClaim
from research_harness.research.schemas.common import ExternalIdentifier, normalize_doi
from research_harness.research.schemas.document_acquisition import (
    AcquisitionStatus,
    DocumentAcquisition,
)
from research_harness.research.schemas.document_location import (
    AccessType,
    DocumentLocation,
    HostType,
    VersionType,
)
from research_harness.research.schemas.evidence import EvidenceItem, Locator
from research_harness.research.schemas.execution import LiteratureSearchExecution
from research_harness.research.schemas.full_text import (
    DocumentAcquisitionExecution,
    FullTextCorpus,
    FullTextDocument,
    TextStatus,
)
from research_harness.research.schemas.identity import (
    IdentityEvidence,
    PaperIdentity,
    ResolutionMethod,
)
from research_harness.research.schemas.paper import Author, PaperRecord
from research_harness.research.schemas.project import ResearchPlan, ResearchQuestion
from research_harness.research.schemas.provider_snapshot import ProviderRecordSnapshot
from research_harness.research.schemas.query import LiteratureQuery
from research_harness.research.schemas.screening_decision import (
    InformationSufficiency,
    ScreeningDecision,
    ScreeningDecisionEnum,
)
from research_harness.research.schemas.screening_execution import (
    ScreenedLiteratureSet,
    ScreeningExecution,
)
from research_harness.research.schemas.screening_protocol import (
    ProtocolStatus,
    ScreeningCriterion,
    ScreeningProtocol,
)
from research_harness.research.schemas.screening_review import ReviewerType, ScreeningReview
from research_harness.research.schemas.screening_view import FieldSource, PaperScreeningView
from research_harness.research.schemas.search_record import LiteratureSearchRecord
from research_harness.research.schemas.source import SourceRecord, SourceType
from research_harness.research.schemas.strategy import LiteratureSearchStrategy

__all__ = [
    "AccessType",
    "AcquisitionStatus",
    "Author",
    "BlobReference",
    "ClaimType",
    "DocumentAcquisition",
    "DocumentAcquisitionExecution",
    "DocumentLocation",
    "EvidenceItem",
    "ExternalIdentifier",
    "FieldSource",
    "FullTextCorpus",
    "FullTextDocument",
    "HostType",
    "IdentityEvidence",
    "InformationSufficiency",
    "LiteratureQuery",
    "LiteratureSearchExecution",
    "LiteratureSearchRecord",
    "LiteratureSearchStrategy",
    "Locator",
    "PaperIdentity",
    "PaperRecord",
    "PaperScreeningView",
    "ProtocolStatus",
    "ProviderRecordSnapshot",
    "ResearchClaim",
    "ResearchPlan",
    "ResearchQuestion",
    "ResolutionMethod",
    "ScreenedLiteratureSet",
    "ScreeningCriterion",
    "ScreeningDecision",
    "ScreeningDecisionEnum",
    "ScreeningExecution",
    "ScreeningProtocol",
    "ScreeningReview",
    "ReviewerType",
    "SourceRecord",
    "SourceType",
    "TextStatus",
    "VersionType",
    "normalize_doi",
]
