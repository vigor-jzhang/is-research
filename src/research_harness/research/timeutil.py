"""Time helpers for evidence age / staleness checks.

Every age and staleness path in the routing code subtracts a timestamp that may
have come from a parsed payload, where Pydantic has not applied any timezone
normalisation. Subtracting an aware datetime from a naive one raises TypeError,
and a timestamp in the future yields a negative age that silently passes every
"is this too old?" check. Both are handled here so the callers cannot get it
wrong.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def aware_utc(value: Any) -> datetime | None:
    """Coerce `value` to a timezone-aware UTC datetime, or None if impossible.

    A naive datetime is assumed to be UTC, which is what the storage layer
    writes. Returns None for anything that is not a datetime, so callers can
    treat "no usable timestamp" explicitly rather than crashing.
    """
    if value is None or not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def age_seconds(created_at: Any, *, now: datetime | None = None) -> float | None:
    """Seconds since `created_at`, never negative. None if it cannot be read.

    Clamping at zero matters: a clock skew or backdated artifact used to produce
    a negative age, which passed every freshness gate.
    """
    stamp = aware_utc(created_at)
    if stamp is None:
        return None
    reference = now if now is not None else datetime.now(UTC)
    return max(0.0, (reference - stamp).total_seconds())
