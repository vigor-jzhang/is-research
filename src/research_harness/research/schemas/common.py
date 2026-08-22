"""Common research types — ExternalIdentifier and DOI normalization."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

# Supported identifier schemes — keep minimal for Phase 2A
ALLOWED_SCHEMES = {"doi", "semantic_scholar", "crossref", "arxiv", "openalex", "pmid", "url"}


class ExternalIdentifier(BaseModel):
    """Typed external identifier.

    scheme: normalized lower-cased scheme name (e.g., doi, arxiv)
    value: normalized value (for doi, canonical bare DOI)
    """

    scheme: str = Field(description="Identifier scheme, e.g., doi, arxiv")
    value: str = Field(description="Identifier value")

    @field_validator("scheme")
    @classmethod
    def validate_scheme(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("scheme must be non-empty")
        # Allow any scheme but lower-case it; keep allowed set for guidance
        return v

    @field_validator("value")
    @classmethod
    def validate_value(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("value must be non-empty")
        return v

    def normalized(self) -> ExternalIdentifier:
        """Return normalized form (especially for DOI)."""
        if self.scheme == "doi":
            return ExternalIdentifier(scheme="doi", value=normalize_doi(self.value))
        return ExternalIdentifier(scheme=self.scheme.lower(), value=self.value.strip())

    model_config = {"extra": "forbid"}


# DOI normalization: handle common prefixes and canonical bare form
_DOI_PREFIXES = [
    r"^https?://doi\.org/",
    r"^https?://dx\.doi\.org/",
    r"^doi:",
]

_DOI_RE = re.compile(r"^10\.\d{4,9}/.+$", re.IGNORECASE)


def normalize_doi(raw: str) -> str:
    """Normalize DOI to canonical bare form `10.xxxx/yyyy`.

    Handles:
      10.xxxx/yyyy
      https://doi.org/10.xxxx/yyyy
      http://dx.doi.org/10.xxxx/yyyy
      doi:10.xxxx/yyyy
    Returns lower-cased? DOI is case-insensitive but we preserve case for suffix
    except we lower the prefix? Best to lower entire? For stability, lower-case.
    Spec says handle obvious equivalents — we normalize to lower-case bare DOI.
    No network calls.
    """
    s = raw.strip()
    # Remove known prefixes case-insensitively
    for pat in _DOI_PREFIXES:
        s = re.sub(pat, "", s, flags=re.IGNORECASE)
    s = s.strip()
    # Lower-case for canonical form (DOI is case-insensitive)
    s = s.lower()
    # Basic validation — if it doesn't look like DOI, return lower-cased stripped as is
    # but we still return it; caller can validate via regex if needed
    return s


def is_valid_doi(value: str) -> bool:
    return bool(_DOI_RE.match(value.strip()))
