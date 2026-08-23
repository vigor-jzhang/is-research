"""Citation styles for Phase 4C — replaceable behind a Protocol so APA or
journal-specific styles can be added later without touching the formatter.

Rendering only uses fields that exist in the canonical metadata; missing
fields are omitted, never invented.
"""

from __future__ import annotations

from typing import Protocol

from research_harness.research.schemas.publication import CitationStyleName


class CitationStyle(Protocol):
    """A citation style renders inline citations and bibliography entries."""

    name: CitationStyleName

    def inline(
        self,
        authors: list[str],
        year: int | None,
        page_locator: str | None,
        title: str,
    ) -> str: ...

    def bibliography_entry(
        self,
        authors: list[str],
        year: int | None,
        title: str,
        venue: str | None,
        doi: str | None,
    ) -> str: ...


def _author_display(authors: list[str]) -> str:
    if not authors:
        return ""
    if len(authors) == 1:
        return authors[0]
    if len(authors) == 2:
        return f"{authors[0]} and {authors[1]}"
    return f"{authors[0]} et al."


class AuthorYearStyle:
    """Generic author-year style (default)."""

    name: CitationStyleName = CitationStyleName.author_year

    def inline(
        self,
        authors: list[str],
        year: int | None,
        page_locator: str | None,
        title: str,
    ) -> str:
        parts: list[str] = []
        if authors:
            parts.append(_author_display(authors))
        elif title:
            parts.append(f'"{title}"')
        if year is not None:
            parts.append(str(year))
        if not parts:
            return "(anonymous)"
        text = ", ".join(parts)
        if page_locator:
            text += f", {page_locator}"
        return f"({text})"

    def bibliography_entry(
        self,
        authors: list[str],
        year: int | None,
        title: str,
        venue: str | None,
        doi: str | None,
    ) -> str:
        pieces: list[str] = []
        author_part = ""
        if authors:
            author_part = "; ".join(authors)
            if year is not None:
                author_part += f" ({year})"
        elif year is not None:
            author_part = str(year)
        if author_part:
            pieces.append(author_part)
        pieces.append(title)
        if venue:
            pieces.append(venue)
        if doi:
            pieces.append(f"doi: {doi}")
        return ". ".join(pieces) + "."


class ApaStyle:
    """APA-like rendering (basic; extended APA rules are out of scope)."""

    name: CitationStyleName = CitationStyleName.apa

    def inline(
        self,
        authors: list[str],
        year: int | None,
        page_locator: str | None,
        title: str,
    ) -> str:
        parts: list[str] = []
        if authors:
            parts.append(_author_display(authors))
        elif title:
            parts.append(f'"{title}"')
        if year is not None:
            parts.append(str(year))
        if not parts:
            return "(anonymous)"
        text = ", ".join(parts)
        if page_locator:
            text += f", p. {page_locator}"
        return f"({text})"

    def bibliography_entry(
        self,
        authors: list[str],
        year: int | None,
        title: str,
        venue: str | None,
        doi: str | None,
    ) -> str:
        pieces: list[str] = []
        if authors:
            pieces.append(", ".join(authors))
        if year is not None:
            pieces.append(f"({year})")
        pieces.append(title)
        if venue:
            pieces.append(venue)
        if doi:
            pieces.append(f"https://doi.org/{doi}")
        return ". ".join(pieces) + "."


_STYLES: dict[CitationStyleName, CitationStyle] = {
    CitationStyleName.author_year: AuthorYearStyle(),
    CitationStyleName.apa: ApaStyle(),
}


def get_style(name: CitationStyleName) -> CitationStyle:
    return _STYLES[name]


def available_styles() -> list[CitationStyleName]:
    return list(_STYLES)
