"""Ensure live tests are skipped unless explicitly selected.

The OpenRouter plugin loads `.env` at import time, so OPENROUTER_API_KEY /
UNPAYWALL_EMAIL are present in every test process. Without this hook the
optional live tests would run during the normal offline suite.
"""

import pytest


def pytest_collection_modifyitems(config, items):  # type: ignore[no-untyped-def]
    marker_expr: str = config.getoption("-m", default="") or ""
    # Run live if the user explicitly requested any live marker
    if any(token in marker_expr for token in ("live",)):
        return
    for item in items:
        live_marker = next(
            (m for m in item.iter_markers() if m.name == "live" or m.name.startswith("live_")),
            None,
        )
        if live_marker is not None:
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        f"live test ({live_marker.name}) skipped; run with "
                        f"`uv run --env-file .env pytest -m {live_marker.name}`"
                    )
                )
            )
