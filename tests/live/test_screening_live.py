"""Live screening smoke — opt-in, small bounds, requires OPENROUTER_API_KEY."""

import os
import pathlib

import pytest

pytestmark = pytest.mark.live_screening


@pytest.mark.asyncio
async def test_live_screening_small(tmp_path: pathlib.Path):
    # Skip if no key
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        pytest.skip("OPENROUTER_API_KEY not set")

    from research_harness.app.bootstrap import build_runtime
    from research_harness.config.loader import load_config_from_dict
    from research_harness.research.envelope import ArtifactEnvelope
    from research_harness.research.schemas.execution import LiteratureSearchExecution
    from research_harness.research.schemas.identity import PaperIdentity, ResolutionMethod
    from research_harness.research.schemas.paper import PaperRecord
    from research_harness.research.schemas.project import ResearchQuestion

    # Very small config: use sqlite store, screening + model
    cfg = load_config_from_dict(
        {
            "plugins": [
                "model.openrouter",
                "routing.role_router",
                "storage.artifacts_sqlite",
                "literature.screening_protocol_builder",
                "literature.screening_view_builder",
                "literature.title_abstract_screener",
                "literature.screening_orchestrator",
                "autonomy.configurable",
            ],
            "models": {
                "roles": {
                    "fast": {"provider": "openrouter", "model": "openai/gpt-4o-mini"},
                    "reasoning": {"provider": "openrouter", "model": "openai/gpt-4o-mini"},
                }
            },
            "artifacts": {"store": "sqlite", "path": str(tmp_path / "art.db")},
            "literature": {
                "screening": {
                    "protocol_model_role": "reasoning",
                    "screening_model_role": "fast",
                    "max_candidates": 5,
                    "max_model_calls": 5,
                }
            },
            "runtime": {"autonomy": "high"},
        }
    )

    runtime = build_runtime(cfg)
    async with runtime:
        store = runtime.services.require("artifact_store.default")
        rq = ArtifactEnvelope.create(
            payload=ResearchQuestion(
                question="Does algorithmic pricing impact platform competition in digital markets? Provide criteria for literature screening."
            ),
            artifact_type="research_question",
        )
        await store.put(rq)

        # Build protocol via real model (reasoning)
        builder = runtime.services.require("screening_protocol_builder.default")
        try:
            protocol_id = await builder.build(rq.artifact_id)
        except Exception as e:
            pytest.skip(f"protocol builder failed live (likely quota/model): {e}")
        assert protocol_id
        from research_harness.research.schemas.screening_protocol import ScreeningProtocol

        proto = (await store.get(protocol_id)).parse_payload(ScreeningProtocol)
        assert proto.status.value == "approved"
        assert len(proto.inclusion_criteria) >= 1

        # Create ≤3 PaperIdentities manually (no provider search to keep cheap)
        # Use deterministic but plausible titles that model must assess
        papers = [
            PaperRecord(
                title="Algorithmic pricing and platform competition: an empirical study",
                abstract="We analyze pricing algorithms on digital platforms and their effect on competition using game-theoretic model.",
                year=2022,
            ),
            PaperRecord(
                title="Deep learning for image recognition",
                abstract="CNNs achieve high accuracy on ImageNet.",
                year=2021,
            ),
            PaperRecord(title="Ambiguous economics paper", abstract=None, year=2020),
        ]
        id_ids = []
        for p in papers:
            p_env = ArtifactEnvelope.create(
                payload=p, artifact_type="paper_record", producer="live_test"
            )
            await store.put(p_env)
            pi = PaperIdentity(
                member_paper_artifact_ids=[p_env.artifact_id],
                canonical_identifiers=[],
                resolution_method=ResolutionMethod.exact_identifier,
                resolution_evidence=[],
            )
            pi_env = ArtifactEnvelope.create(
                payload=pi, artifact_type="paper_identity", producer="live_test"
            )
            await store.put(pi_env)
            id_ids.append(pi_env.artifact_id)

        exec_rec = LiteratureSearchExecution(
            strategy_artifact_id="s",
            query_artifact_ids=[],
            search_record_artifact_ids=[],
            paper_artifact_ids=[],
            paper_identity_artifact_ids=id_ids,
            counts={},
            provider_failures=[],
        )
        exec_env = ArtifactEnvelope.create(
            payload=exec_rec, artifact_type="literature_search_execution", producer="live_test"
        )
        await store.put(exec_env)

        orchestrator = runtime.services.require("screening_orchestrator.default")
        exec_id = await orchestrator.screen(exec_env.artifact_id, protocol_id)
        assert exec_id
        from research_harness.research.schemas.screening_execution import (
            ScreenedLiteratureSet,
            ScreeningExecution,
        )

        screening_exec = (await store.get(exec_id)).parse_payload(ScreeningExecution)
        assert screening_exec.counts["total_candidates"] == 3
        # At least one decision produced
        assert (
            len(screening_exec.decision_artifact_ids) == 3
            or screening_exec.counts["processed"] >= 2
        )
        # Screened set
        sets = await store.list(artifact_type="screened_literature_set")
        assert len(sets) >= 1
        s = sets[0].parse_payload(ScreenedLiteratureSet)
        total_in_sets = (
            len(s.included_identity_ids)
            + len(s.excluded_identity_ids)
            + len(s.uncertain_identity_ids)
        )
        assert total_in_sets == 3 or total_in_sets >= 2  # allow review etc
        # Verify events persisted? Check that decision artifacts exist
        for did in s.decision_artifact_ids:
            assert await store.exists(did)
        # Provenance check: decisions derived from protocol and view
        for did in s.decision_artifact_ids[:1]:
            parents = await store.get_parents(did)
            assert len(parents) >= 1  # at least provenance exists
            assert any(p.relation.value == "derived_from" for p in parents)
