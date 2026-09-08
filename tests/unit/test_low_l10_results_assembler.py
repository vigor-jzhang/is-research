"""Regression tests for round 37 — L10 (results_assembler).

Two halves:

1. On a rejected assembly attempt, findings persisted before the rejection
   stayed in the append-only store, and the retry wrote a second, parallel
   batch; the package referenced only the new one, orphaning the first.
   Validation now runs over the whole response before the first store write,
   so a rejected attempt leaves nothing behind.

2. The required-conditions check was a substring test, so a finding carrying
   "not (a > 0)" satisfied the required condition "a > 0". The comparison is
   now exact up to whitespace.

Reuses the Phase 4A fixture chain from `test_results_assembly` (the tests
package is importable: `tests/unit/__init__.py` exists).
"""

from __future__ import annotations

import json
import pathlib

import pytest

from research_harness.contracts.model import Message, ModelResponse
from research_harness.plugins.research.results_assembler.plugin import ResultsAssemblerService
from research_harness.plugins.storage.artifacts_sqlite.plugin import SQLiteArtifactStore
from research_harness.research.schemas.results import ResearchResultsPackage
from tests.unit.test_results_assembly import FakeRouter, assembly_response, build_chain


@pytest.mark.asyncio
async def test_retry_does_not_orphan_findings_from_a_rejected_attempt(
    tmp_path: pathlib.Path,
) -> None:
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)

    rejected = assembly_response(ids)
    # a positional token beyond the number of findings this response declares:
    # rejected by validation — pre-fix, only AFTER the findings were persisted
    rejected["contributions"][0]["finding_ids"] = ["FINDING7"]
    good = assembly_response(ids)
    router = FakeRouter([rejected, good])
    svc = ResultsAssemblerService(model_router=router, artifact_store=store)

    exec_id = await svc.assemble(ids["experiment"])
    assert exec_id

    findings = await store.list(artifact_type="research_finding")
    assert len(findings) == 1, (
        "a rejected attempt must not leave its findings batch in the store"
    )
    pkg_env = next(env for env in await store.list(artifact_type="results_package"))
    pkg = pkg_env.parse_payload(ResearchResultsPackage)
    assert pkg.finding_ids == [findings[0].artifact_id]


@pytest.mark.asyncio
async def test_negated_condition_does_not_satisfy_a_required_condition(
    tmp_path: pathlib.Path,
) -> None:
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)

    resp = assembly_response(ids)
    resp["findings"][0]["conditions"] = ["not (a > 0)"]

    class OneShotRouter:
        async def complete(self, role, request):
            return ModelResponse(
                message=Message(role="assistant", content=json.dumps(resp)),
                tool_calls=[],
                finish_reason="stop",
                model="fake",
            )

    svc = ResultsAssemblerService(
        model_router=OneShotRouter(), artifact_store=store, max_llm_calls=1
    )
    with pytest.raises(ValueError, match="drops required conditions"):
        await svc.assemble(ids["experiment"])


@pytest.mark.asyncio
async def test_whitespace_variant_condition_is_still_accepted(
    tmp_path: pathlib.Path,
) -> None:
    """Guard: the exact match is whitespace-insensitive, so "a>0" carries the
    required "a > 0" — the fix must not make condition matching stricter than
    whitespace."""
    store = SQLiteArtifactStore(path=tmp_path / "art.db")
    ids = await build_chain(store)

    resp = assembly_response(ids)
    resp["findings"][0]["conditions"] = ["a>0"]

    class OneShotRouter:
        async def complete(self, role, request):
            return ModelResponse(
                message=Message(role="assistant", content=json.dumps(resp)),
                tool_calls=[],
                finish_reason="stop",
                model="fake",
            )

    svc = ResultsAssemblerService(model_router=OneShotRouter(), artifact_store=store)
    exec_id = await svc.assemble(ids["experiment"])
    assert exec_id
