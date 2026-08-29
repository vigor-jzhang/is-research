"""Unit tests for routing policy rank keys.

Routing candidates are ranked by a lexicographic key over evidence fields.
Missing evidence ("unknown") must never outrank a candidate that actually has
evidence, in either direction of each field.
"""

from __future__ import annotations

from research_harness.research.routing.policies import build_rank_key, list_policies


class _Assessment:
    def __init__(self, candidate_id: str, **fields) -> None:
        self.candidate_id = candidate_id
        for name, value in fields.items():
            setattr(self, name, value)


def test_unknown_evidence_sorts_last_for_higher_is_better():
    """A candidate with no benchmark runs must not beat one with evidence.

    ``benchmark_pass_rate`` is ``None`` whenever a candidate produced no
    benchmark runs; mapping that to ``-inf`` (as the old code did for
    descending fields) sorted it ahead of every real value.
    """
    key = build_rank_key([("benchmark_pass_rate", False)])
    unknown = _Assessment("unknown", benchmark_pass_rate=None)
    known = _Assessment("known", benchmark_pass_rate=0.90)
    assert [a.candidate_id for a in sorted([unknown, known], key=key)] == ["known", "unknown"]


def test_unknown_evidence_sorts_last_for_lower_is_better():
    key = build_rank_key([("model_error_rate", True)])
    unknown = _Assessment("unknown", model_error_rate=None)
    known = _Assessment("known", model_error_rate=0.40)
    assert [a.candidate_id for a in sorted([unknown, known], key=key)] == ["known", "unknown"]


def test_higher_is_better_prefers_larger_values():
    key = build_rank_key([("deterministic_pass_rate", False)])
    low = _Assessment("low", deterministic_pass_rate=0.50)
    high = _Assessment("high", deterministic_pass_rate=0.95)
    assert [a.candidate_id for a in sorted([low, high], key=key)] == ["high", "low"]


def test_lower_is_better_prefers_smaller_values():
    key = build_rank_key([("cost_per_successful_case", True)])
    cheap = _Assessment("cheap", cost_per_successful_case=0.01)
    pricey = _Assessment("pricey", cost_per_successful_case=1.0)
    assert [a.candidate_id for a in sorted([pricey, cheap], key=key)] == ["cheap", "pricey"]


def test_candidate_id_is_the_final_tie_break():
    key = build_rank_key([("deterministic_pass_rate", False)])
    b = _Assessment("b", deterministic_pass_rate=0.9)
    a = _Assessment("a", deterministic_pass_rate=0.9)
    assert [c.candidate_id for c in sorted([b, a], key=key)] == ["a", "b"]


def test_every_builtin_policy_places_unknown_evidence_last():
    """Applies to all shipped policies, not just the one field tested above."""
    for spec in list_policies():
        for field, ascending in spec.fields:
            key = build_rank_key([(field, ascending)])
            unknown = _Assessment("unknown", **{field: None})
            known = _Assessment("known", **{field: 0.5})
            order = [a.candidate_id for a in sorted([unknown, known], key=key)]
            assert order == ["known", "unknown"], (
                f"policy {spec.policy_id!r} field {field!r} (ascending={ascending}) "
                f"ranks unknown evidence first"
            )
