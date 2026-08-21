"""Ensure live tests are skipped unless explicitly selected with -m live."""

import pytest


def pytest_collection_modifyitems(config, items):  # type: ignore[no-untyped-def]
    # If user explicitly selects live marker, don't skip
    # config.getoption("-m") returns the marker expression
    marker_expr: str = config.getoption("-m", default="") or ""
    run_live = "live" in marker_expr
    if run_live:
        return
    # Otherwise skip any test marked with live
    for item in items:
        if item.get_closest_marker("live"):
            item.add_marker(
                pytest.mark.skip(
                    reason="live test skipped; run with `uv run --env-file .env pytest -m live`"
                )
            )
