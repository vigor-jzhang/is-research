"""Regression tests for round 35 — L33 (remaining CLI nits) and L26 (mutable
state behind frozen/shared objects).

L33: `runtime inspect` printed a "Services" heading over ``metadata.provides``
(a static declaration already listed per plugin) instead of the registry; 12
``__import__()`` string hacks stood where a normal import would do; and the
`screening sets list --execution` filter carried a dead ``or`` branch that
re-parsed the payload only to recompute the first comparison — or raise
``ValidationError`` on a payload missing the required key.

L26: ``PolicySpec`` was frozen but held a mutable list/dict, and the module
singletons in ``_POLICIES`` were handed to every caller; readiness shared
mutable ``QualificationCriteria`` instances across runs.

Where driving the CLI is cheap (services section, screening sets list) the test
drives the real command; the ``__import__`` removal is a source-shape test, the
same trade-off round 28 recorded for the store-leak checks.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from research_harness.cli.main import app
from research_harness.research.envelope import ArtifactEnvelope
from research_harness.research.routing.policies import get_policy
from research_harness.research.routing.readiness import criteria_for_role
from research_harness.research.schemas.screening_execution import ScreenedLiteratureSet

MAIN = (
    pathlib.Path(__file__).resolve().parents[2]
    / "src"
    / "research_harness"
    / "cli"
    / "main.py"
)

runner = CliRunner()


def _write_config(tmp_path: pathlib.Path) -> pathlib.Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "plugins:\n"
        "  - storage.artifacts_sqlite\n"
        "artifacts:\n"
        "  store: sqlite\n"
        f"  path: {tmp_path / 'artifacts.db'}\n",
        encoding="utf-8",
    )
    return cfg


# ---------------------------------------------------------------------------
# L33 — runtime inspect "Services" section
# ---------------------------------------------------------------------------


def test_runtime_inspect_services_section_prints_the_registry(
    tmp_path: pathlib.Path,
) -> None:
    """The Services section must report the registry, not re-list
    ``metadata.provides`` under a heading that names something else."""
    cfg = _write_config(tmp_path)
    result = runner.invoke(app, ["runtime", "inspect", "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert "Services" in result.output
    # inspect does not start plugins, so nothing can be registered yet; the
    # old output printed plugin provides here instead of saying so.
    assert "none registered" in result.output
    # the static declaration still lives in the Plugins section
    assert "provides:" in result.output


# ---------------------------------------------------------------------------
# L33 — __import__() string hacks
# ---------------------------------------------------------------------------


def test_no_dunder_import_hacks_in_cli() -> None:
    """Every ``__import__()`` string hack is replaced by a normal import."""
    source = MAIN.read_text(encoding="utf-8")
    assert "__import__" not in source


# ---------------------------------------------------------------------------
# L33 — dead `or` branch in screening sets list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
def test_screening_sets_list_execution_filter_survives_malformed_artifact(
    tmp_path: pathlib.Path,
) -> None:
    """A stored artifact whose payload lacks the required key must be
    filtered out, not crash the listing with ValidationError."""
    import asyncio

    from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
    from research_harness.research.envelope import compute_content_hash

    db = tmp_path / "artifacts.db"
    good = ArtifactEnvelope.create(
        payload=ScreenedLiteratureSet(
            screening_execution_id="exec-11111111",
            screening_protocol_id="proto-22222222",
            included_identity_ids=["p1"],
        ),
        artifact_type="screened_literature_set",
        producer="test",
    )
    malformed_payload = {"unexpected": "x"}
    # Exactly what _row_to_envelope builds for a stored artifact whose payload
    # lacks the required schema fields.
    malformed = ArtifactEnvelope[Any](
        artifact_type="screened_literature_set",
        payload=malformed_payload,  # type: ignore[arg-type]
        content_hash=compute_content_hash(malformed_payload),
        producer="test",
    )

    async def _seed() -> None:
        store = SQLiteArtifactStore(path=db)
        try:
            await store.put(good)
            await store.put(malformed)
        finally:
            await store.close()

    asyncio.run(_seed())

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "plugins:\n  - storage.artifacts_sqlite\n"
        "artifacts:\n  store: sqlite\n"
        f"  path: {db}\n",
        encoding="utf-8",
    )
    from research_harness.cli.main import app

    result = runner.invoke(
        app, ["literature", "screening", "sets", "list", "--execution", "exec-11111111", "--config", str(cfg)]
    )
    assert result.exit_code == 0, result.output
    assert good.artifact_id[:8] in result.output
    assert malformed.artifact_id[:8] not in result.output


# ---------------------------------------------------------------------------
# L26 — PolicySpec mutable fields behind frozen=True
# ---------------------------------------------------------------------------


def test_policy_spec_fields_and_rules_are_immutable() -> None:
    spec = get_policy("quality_first")
    assert isinstance(spec.fields, tuple)
    with pytest.raises((AttributeError, TypeError)):
        spec.fields.append(("nonexistent_field", True))  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        spec.selection_rules["sabotage"] = "x"  # type: ignore[index]


# ---------------------------------------------------------------------------
# L26 — shared QualificationCriteria instances
# ---------------------------------------------------------------------------


def test_shared_role_criteria_are_immutable() -> None:
    """``criteria_for_role`` hands out module-level singletons; mutating one
    must fail loudly instead of re-thresholding every later run."""
    criteria = criteria_for_role("fast")
    snapshot = criteria.model_dump()
    try:
        with pytest.raises(ValidationError):
            criteria.min_repetitions = 1
    finally:
        # On a tree where the model is not frozen the assignment above
        # succeeded; undo it so the shared instance is not poisoned for the
        # rest of the run.
        for key, value in snapshot.items():
            try:
                setattr(criteria, key, value)
            except ValidationError:
                pass
