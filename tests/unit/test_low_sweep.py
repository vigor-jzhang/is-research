"""Regression tests for the Low-tier sweep (round 25).

One batch covering the cheap real defects in the Low tier: L3, L8, L9, L11,
L17, L22, L24, L25, L27, L28, L29, L30, L31, L32, L34, L35. Each is a small
behavioural defect rather than a preference — see §9.7 for the five findings
that were closed as accepted instead.
"""

from __future__ import annotations

import pathlib

import pytest

# ---------------------------------------------------------------------------
# L24 — plugin version regex
# ---------------------------------------------------------------------------


def test_version_regex_rejects_injection():
    """L24: the trailing `.*` accepted anything after the three numbers."""
    from research_harness.kernel.plugin import _VERSION_RE

    assert _VERSION_RE.match("0.1.0")
    assert _VERSION_RE.match("1.2.3")
    assert _VERSION_RE.match("1.2.3-beta.1")
    assert _VERSION_RE.match("1.2.3+build.7")
    assert not _VERSION_RE.match("1.2.3; DROP TABLE"), "injection still validates"
    assert not _VERSION_RE.match("1.2.3 rm -rf /")


# ---------------------------------------------------------------------------
# L25 — __bool__ must agree with __len__
# ---------------------------------------------------------------------------


def test_empty_registry_is_falsy():
    """L25: `__bool__` returned True while `__len__` was 0."""
    from research_harness.kernel.services import ServiceRegistry

    registry = ServiceRegistry()
    assert len(registry) == 0
    assert not registry, "an empty registry claimed to be truthy"
    registry.register("a.service", object(), owner="test")
    assert registry, "a non-empty registry claimed to be falsy"


# ---------------------------------------------------------------------------
# L30 — rate fields must be bounded
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "deterministic_pass_rate",
        "benchmark_pass_rate",
        "case_pass_rate",
        "case_error_rate",
        "repetition_failure_rate",
        "structured_output_success_rate",
        "model_error_rate",
        "retry_rate",
    ],
)
def test_rate_field_rejects_a_value_above_one(field: str):
    """L30: a rate of 5.0 validated and beat every threshold."""
    import pydantic

    from research_harness.research.schemas.tournament import (
        TournamentModelConfig,
        TournamentModelResult,
    )

    base = {
        "candidate_id": "m",
        "config": TournamentModelConfig(
            candidate_id="m", provider="openrouter", requested_model="m"
        ),
        "role": "reasoning",
    }
    TournamentModelResult(**{**base, field: 0.5})
    with pytest.raises(pydantic.ValidationError):
        TournamentModelResult(**{**base, field: 5.0})
    with pytest.raises(pydantic.ValidationError):
        TournamentModelResult(**{**base, field: -0.1})


# ---------------------------------------------------------------------------
# L31 — latent 0.0 timeout and a zero-duration campaign
# ---------------------------------------------------------------------------


def test_model_preflight_timeout_default_is_not_zero():
    """L31: 0.0 is a live timeout, not "unset"."""
    from research_harness.research.schemas.qualification import ModelPreflight

    pf = ModelPreflight(
        candidate_id="m", provider="openrouter", requested_model="m",
        status="available",
    )
    assert pf.timeout_seconds > 0.0, "a preflight built without a timeout would fail instantly"


def test_campaign_completed_at_defaults_to_none():
    """L31: both timestamps defaulted to now, reporting a zero duration."""
    from research_harness.research.routing.readiness import criteria_for_role
    from research_harness.research.schemas.qualification import (
        QualificationCampaign,
        RoleQualificationSummary,
    )

    campaign = QualificationCampaign(
        role="reasoning",
        benchmark_id="b",
        summary=RoleQualificationSummary(
            role="reasoning",
            benchmark_id="b",
            status="not_qualified",
            primary=None,
            fallback=None,
            qualified_models=[],
            criteria=criteria_for_role("reasoning"),
        ),
        criteria=criteria_for_role("reasoning"),
    )
    assert campaign.completed_at is None, "an unfinished campaign claimed a completion time"


# ---------------------------------------------------------------------------
# L35 — UnicodeDecodeError must not escape raw
# ---------------------------------------------------------------------------


def test_config_loader_reports_a_bad_encoding(tmp_path: pathlib.Path):
    """L35: UnicodeDecodeError is a ValueError, not an OSError."""
    from research_harness.config.loader import load_config
    from research_harness.kernel.errors import ConfigurationError

    bad = tmp_path / "cfg.yaml"
    bad.write_bytes(b"models:\n  x: \xff\xfe\n")
    with pytest.raises(ConfigurationError):
        load_config(bad)


def test_dotenv_skips_a_bad_encoding(tmp_path: pathlib.Path):
    """L35: a non-UTF-8 .env escaped raw instead of being skipped."""
    from research_harness.config.dotenv import _load_file

    bad = tmp_path / ".env"
    bad.write_bytes(b"KEY=\xff\xfe\n")
    _load_file(bad)  # must not raise


# ---------------------------------------------------------------------------
# L27 — wrong-role criteria must not be silently relabelled
# ---------------------------------------------------------------------------


def test_criteria_for_the_wrong_role_raises():
    """L27: it used to relabel them, judging one role on another's thresholds."""
    from research_harness.research.routing.qualification import _check_criteria_role
    from research_harness.research.routing.readiness import criteria_for_role

    critic = criteria_for_role("critic")
    assert critic.role == "critic"
    _check_criteria_role(critic, "critic")  # same role is fine
    with pytest.raises(ValueError, match="not 'reasoning'"):
        _check_criteria_role(critic, "reasoning")


# ---------------------------------------------------------------------------
# L28 — probes must not square the retry count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_structured_probe_attempts_retries_plus_one():
    """L28: the inner retry nested inside the outer loop gave (retries+1)**2."""
    from research_harness.contracts.common import Usage
    from research_harness.contracts.model import Message, ModelResponse
    from research_harness.research.routing.preflight import run_candidate_preflight
    from research_harness.research.schemas.tournament import TournamentModelConfig

    structured_calls = {"n": 0}

    class _Flaky:
        """Reachable, but every schema-carrying (structured) call fails."""

        async def complete(self, request):  # noqa: ANN201
            if request.response_schema is None:
                return ModelResponse(
                    message=Message(role="assistant", content="ok"),
                    model="m",
                    provider="openrouter",
                    usage=Usage(prompt_tokens=1, completion_tokens=1),
                    latency_ms=1.0,
                )
            structured_calls["n"] += 1
            raise RuntimeError("rate limit 429")

    await run_candidate_preflight(
        role="reasoning",
        candidate=TournamentModelConfig(
            candidate_id="m", provider="openrouter", requested_model="m"
        ),
        service_lookup=lambda name: _Flaky(),
        base_router=None,
        timeout_seconds=5.0,
        retries=2,
    )
    assert structured_calls["n"] == 3, (
        f"the structured probe made {structured_calls['n']} attempts; "
        f"retries+1 is 3, but nesting the inner retry gives (retries+1)**2 = 9"
    )


# ---------------------------------------------------------------------------
# L29 — percentile must range-check q
# ---------------------------------------------------------------------------


def test_percentile_rejects_an_out_of_range_q():
    """L29: q was never checked, so q=5 extrapolated past the sample."""
    from research_harness.research.tournament.accounting import percentile

    assert percentile([1.0, 2.0, 3.0], 0.5) == 2.0
    with pytest.raises(ValueError, match="must be in"):
        percentile([1.0, 2.0, 3.0], 5.0)
    with pytest.raises(ValueError, match="must be in"):
        percentile([1.0, 2.0, 3.0], -0.5)


# ---------------------------------------------------------------------------
# L11 — normalization must not empty a claim
# ---------------------------------------------------------------------------


def test_normalize_novelty_never_empties_a_claim():
    """L11: stripping the whole claim left an empty ContributionClaim."""
    from research_harness.plugins.research.results_assembler.plugin import (
        ResultsAssemblerService,
    )

    svc = ResultsAssemblerService.__new__(ResultsAssemblerService)

    def _has_content(text: str) -> bool:
        import re

        return bool(re.search(r"[A-Za-z]{4,}", text))

    # A claim consisting ONLY of sweeping novelty language: stripping leaves
    # either nothing ("first study") or degenerate residue ("the").
    for claim in ("first study", "the first study"):
        normalized, changed = svc._normalize_novelty(claim)
        assert normalized.strip(), f"{claim!r} normalized to an empty string"
        assert _has_content(normalized), f"{claim!r} normalized to {normalized!r}"
        assert normalized == claim and changed is False


def test_normalize_novelty_still_strips_when_something_remains():
    from research_harness.plugins.research.results_assembler.plugin import (
        ResultsAssemblerService,
    )

    svc = ResultsAssemblerService.__new__(ResultsAssemblerService)
    normalized, changed = svc._normalize_novelty(
        "For the first time, we show that prices fall with entry."
    )
    assert changed is True
    assert normalized.strip()
    assert "first" not in normalized.lower()


# ---------------------------------------------------------------------------
# L9 — only advertise claim types the verifier can check
# ---------------------------------------------------------------------------


def test_advertised_claim_types_are_all_verifiable():
    """L9: `threshold` was offered but has no verifier implementation."""
    from research_harness.plugins.research.proposition_generator.plugin import (
        _VERIFIABLE_CLAIM_TYPES,
    )
    from research_harness.research.schemas.proposition import PropositionClaimType

    assert "threshold" not in _VERIFIABLE_CLAIM_TYPES
    assert set(_VERIFIABLE_CLAIM_TYPES) <= set(PropositionClaimType.values())
    # The verifier supports exactly these.
    assert set(_VERIFIABLE_CLAIM_TYPES) == {"monotonicity", "equality"}


# ---------------------------------------------------------------------------
# L8 — _latest_verification must take the newest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_latest_verification_returns_the_newest(tmp_path: pathlib.Path):
    """L8: it returned the first match, and store.list is created_at ASC."""
    from research_harness.plugins.research.proposition_generator.plugin import (
        PropositionGeneratorService,
    )
    from research_harness.plugins.storage.artifacts_sqlite.plugin import (
        SQLiteArtifactStore,
    )
    from research_harness.research.envelope import ArtifactEnvelope
    from research_harness.research.schemas.proposition import (
        PropositionVerification,
        PropositionVerificationStatus,
    )

    store = SQLiteArtifactStore(path=tmp_path / "a.db")
    svc = PropositionGeneratorService.__new__(PropositionGeneratorService)
    svc._store = store

    def _ver(status: str) -> ArtifactEnvelope:
        return ArtifactEnvelope.create(
            payload=PropositionVerification(
                proposition_id="p1",
                model_id="m1",
                status=PropositionVerificationStatus(status),
                checks=[],
            ),
            artifact_type="proposition_verification",
            producer="test",
        )

    old = _ver("failed")
    await store.put(old)
    new = _ver("verified")
    await store.put(new)

    got = await svc._latest_verification("p1")
    assert got is not None
    assert got.status.value == "verified", (
        "acted on the oldest verification, not the newest"
    )
    await store.close()


# ---------------------------------------------------------------------------
# L34 — an unconfigured pricing table is not a rate of zero
# ---------------------------------------------------------------------------


def test_cost_table_is_none_when_unconfigured():
    """L34: it fell back to 0.0/0.0, so an unconfigured run reported $0.00."""
    from research_harness.plugins.research.evaluation_harness.plugin import (
        EvaluationHarnessService,
    )

    svc = EvaluationHarnessService(
        artifact_store=None,
        ingestor=None,
        identity_resolver=None,
        evaluators={},
    )
    assert svc._cost_per_million is None

    configured = EvaluationHarnessService(
        artifact_store=None,
        ingestor=None,
        identity_resolver=None,
        evaluators={},
        cost_per_million_tokens={"prompt": 1.0, "completion": 2.0},
    )
    assert configured._cost_per_million == {"prompt": 1.0, "completion": 2.0}
