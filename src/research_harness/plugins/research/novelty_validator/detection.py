"""Deterministic novelty-claim detection, risk classification, and base query
generation for Phase 5A.

Layer A guarantees that obvious priority language cannot escape validation.
The risk classifier is purely lexical: an LLM is never asked to decide whether
a phrase containing 'first' is high risk.
"""

from __future__ import annotations

import re
from typing import Any

from research_harness.research.schemas.novelty import (
    ClaimRiskLevel,
    NoveltyClaimType,
)

# (claim_type, risk) for high-risk lexical forms.
_PATTERNS: list[tuple[str, str, str]] = [
    (
        "absolute_priority",
        "critical",
        r"\b(the|our|this)\s+(first|initial)\s+(study|work|paper|analysis|investigation|model|research|attempt|characterization)\b",
    ),
    ("absolute_priority", "critical", r"\bwe\s+are\s+the\s+first\s+to\b"),
    ("absolute_priority", "critical", r"\bwe\s+be(come|ing)\s+the\s+first\b"),
    ("absolute_priority", "critical", r"\bfor\s+the\s+first\s+time\b"),
    ("result_novelty", "high", r"\bshow(ing|s)?\s+for\s+the\s+first\s+time\b"),
    (
        "literature_absence",
        "critical",
        r"\bno\s+prior\s+(study|work|paper|research|analysis|model|literature)\b",
    ),
    (
        "literature_absence",
        "critical",
        r"\bno\s+(study|work|paper|research|analysis|model)\s+has\b",
    ),
    (
        "literature_absence",
        "critical",
        r"\bhas\s+never\s+been\s+(studied|examined|analyzed|investigated|addressed|modeled)\b",
    ),
    (
        "literature_absence",
        "critical",
        r"\b(has|had)\s+not\s+been\s+(studied|examined|analyzed|investigated|addressed|considered|modeled)\b",
    ),
    (
        "literature_absence",
        "critical",
        r"\bnever\s+been\s+(studied|examined|analyzed|investigated)\b",
    ),
    ("literature_absence", "critical", r"\bpreviously\s+unknown\b"),
    ("literature_absence", "critical", r"\bunprecedented\b"),
    ("literature_absence", "critical", r"\bunexplored\b"),
    ("scoped_priority", "high", r"\bto\s+(the\s+best\s+of\s+)?our\s+knowledge\b"),
    ("scoped_priority", "high", r"\bamong\s+the\s+first\b"),
    (
        "mechanism_novelty",
        "high",
        r"\bnovel\s+(mechanism|channel|pathway|driver|force|effect|linkage|tension)\b",
    ),
    ("model_novelty", "high", r"\bnovel\s+(analytical\s+)?(model|framework|game|setup)\b"),
    ("model_novelty", "high", r"\bnew\s+(analytical\s+)?(model|framework|game)\b"),
    ("mechanism_novelty", "high", r"\bintroduce\s+(a\s+)?(new|novel)\s+mechanism\b"),
    (
        "empirical_or_contextual_novelty",
        "medium",
        r"\bfirst\s+to\s+(examine|study|analyze|investigate|quantify|document|apply|estimate)\b",
    ),
    (
        "contribution_difference",
        "medium",
        r"\b(distinct|different|unlike)\s+from\s+(prior|previous|existing)\s+(work|studies|literature|research)\b",
    ),
]

# Contexts where "first"/"new" are technical, not novelty claims.
#
# L12: "time" used to be in this list, which blacklisted the canonical priority
# phrase "for the first time" -- the very phrase the absolute_priority and
# result_novelty patterns below exist to catch. A match always sits inside its
# own blacklist window, so those two patterns could never fire and the
# highest-risk novelty signal was systematically invisible. The technical uses
# ("first-time buyers") are hyphenated adjectival compounds, and they cannot
# match any pattern here: every pattern requires the full "for the first time"
# phrasing. Do not re-add "time".
_BLACKLIST = re.compile(
    r"\bfirst[- ](order|stage|step|period|best|mover|player|derivative|moment|round|stage|difference)\b"
    r"|\bfirst[- ](stage|order)\s+condition\b"
    r"|\bfirst\s+and\s+second\b"
    r"|\bnew[- ](york|jersey|zealand|england|dehli)\b",
    flags=re.IGNORECASE,
)

# Ordering used when overlapping spans must resolve to one classification.
_RISK_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}

_RISK_BY_TYPE: dict[str, ClaimRiskLevel] = {
    "absolute_priority": ClaimRiskLevel.critical,
    "literature_absence": ClaimRiskLevel.critical,
    "scoped_priority": ClaimRiskLevel.high,
    "mechanism_novelty": ClaimRiskLevel.high,
    "model_novelty": ClaimRiskLevel.high,
    "result_novelty": ClaimRiskLevel.high,
    "empirical_or_contextual_novelty": ClaimRiskLevel.medium,
    "contribution_difference": ClaimRiskLevel.medium,
}

_COMPILED: list[tuple[str, str, re.Pattern[str]]] = [
    (ct, rk, re.compile(p, flags=re.IGNORECASE)) for (ct, rk, p) in _PATTERNS
]

# Known absolute/absence lead-ins removed for conservative rewording.
_ABSOLUTE_LEADIN = re.compile(
    r"^(this|we|our|the)\s+(is|are|was|were)\s+the\s+first\s+(study|work|paper|analysis|investigation|model|research|attempt|characterization)\s+(to\s+)?"
    r"|^we\s+are\s+the\s+first\s+to\s+"
    r"|^no\s+prior\s+(study|work|paper|research|analysis|model|literature)\s+"
    r"|^no\s+(study|work|paper|research|analysis|model)\s+has\s+",
    flags=re.IGNORECASE,
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


normalize = _normalize


def detect_high_risk(text: str) -> list[tuple[str, str, int, int]]:
    """Return [(claim_type, risk, start, end)] of deterministic high-risk
    spans in `text`. Blacklisted technical contexts are skipped."""
    text_n = _normalize(text)
    findings: list[tuple[str, str, int, int]] = []
    for claim_type, risk, pattern in _COMPILED:
        for m in pattern.finditer(text_n):
            start, end = m.start(), m.end()
            if _BLACKLIST.search(text_n[max(0, start - 12) : end + 12]):
                continue
            findings.append((claim_type, risk, start, end))
    # sort by start so overlapping spans are merged in order
    findings.sort(key=lambda f: f[2])
    merged: list[tuple[str, str, int, int]] = []
    for f in findings:
        if merged and f[2] < merged[-1][3]:
            # Overlapping: keep the HIGHER-risk classification. The previous
            # form kept the earlier span's type and risk unconditionally, so a
            # critical span followed by an overlapping medium one was reported
            # as medium -- the opposite of what this comment claimed.
            prev = merged[-1]
            if _RISK_RANK.get(f[1], 0) > _RISK_RANK.get(prev[1], 0):
                merged[-1] = (f[0], f[1], prev[2], max(prev[3], f[3]))
            else:
                merged[-1] = (prev[0], prev[1], prev[2], max(prev[3], f[3]))
        else:
            merged.append(f)
    return merged


def classify_claim_type_risk(claim_type: str) -> tuple[NoveltyClaimType, ClaimRiskLevel]:
    if claim_type not in _RISK_BY_TYPE:
        # model-layer or ordinary positioning language
        return NoveltyClaimType(claim_type), ClaimRiskLevel.low
    return NoveltyClaimType(claim_type), _RISK_BY_TYPE[claim_type]


def contains_high_risk(text: str) -> bool:
    return bool(detect_high_risk(text))


def claim_paragraph(text: str, quote: str) -> int | None:
    """0-based paragraph index of the quote inside `text`, if found."""
    paragraphs = [p for p in text.split("\n") if p.strip()]
    for i, para in enumerate(paragraphs):
        if _normalize(quote) in _normalize(para):
            return i
    return None


def distinctive_fragment(claim_text: str, max_len: int = 90) -> str:
    """First sentence of the claim, bounded, for exact-phrase searching."""
    frag = re.split(r"(?<=[.!?])\s+", claim_text.strip(), maxsplit=1)[0]
    if len(frag) > max_len:
        cut = frag[:max_len]
        frag = cut.rsplit(" ", 1)[0]
    return frag


def deterministic_queries(claim: Any) -> list[tuple[str, str, list[str]]]:
    """Return [(query, query_type, synonyms)] generated deterministically
    from the claim. Always valid even without a model."""
    fragment = distinctive_fragment(claim.claim_text)
    queries: list[tuple[str, str, list[str]]] = [
        (f'"{fragment}"', "exact", []),
    ]
    if claim.scope:
        queries.append((f"{fragment} {claim.scope}", "setting", []))
    if claim.claim_type in (
        "absolute_priority",
        "literature_absence",
        "scoped_priority",
    ):
        queries.append((f"{fragment} prior research", "theory", []))
    else:
        queries.append((f"{fragment} mechanism", "mechanism", []))
    # dedupe normalized
    seen: set[str] = set()
    out: list[tuple[str, str, list[str]]] = []
    for q, qt, syn in queries:
        key = _normalize(q).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((q, qt, syn))
    return out


def conservative_wording(claim: Any) -> str:
    """Deterministic conservative rewording fallback (never modifies the
    manuscript — used only for the recommendation artifact)."""
    text = claim.claim_text.strip()
    if claim.claim_type == "literature_absence":
        return (
            "Within the declared search scope, we did not identify directly conflicting prior work."
        )
    if claim.claim_type in ("absolute_priority", "scoped_priority", "result_novelty"):
        stripped = _ABSOLUTE_LEADIN.sub("", text).strip(" ,.:;")
        base = stripped or "address this question"
        return f"To our knowledge, this study is among the first to {base}."
    return f"To our knowledge, {text}."


def normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", query).strip().lower().rstrip(".").rstrip('"')


def normalize_claim_text(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
