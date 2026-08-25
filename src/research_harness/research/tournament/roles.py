"""Benchmark -> logical-role mapping for model tournaments (Phase 7B).

The same benchmark can belong to multiple roles; a tournament binds the
candidate model to exactly one role and holds every other role at the
configured defaults, so "which model is best for role X" is answered by
holding the rest constant.
"""

from __future__ import annotations

from research_harness.kernel.errors import ConfigurationError

# role -> benchmarks whose workflow exercises that role with real model calls
ROLE_BENCHMARKS: dict[str, tuple[str, ...]] = {
    "fast": (
        "literature-screening-v1",  # screener decisions use the fast role
    ),
    "reasoning": (
        "evidence-extraction-v1",
        "literature-synthesis-v1",
        "research-gap-analysis-v1",
        "mechanism-development-v1",
        "analytical-model-specification-v1",
        "proposition-correctness-v1",
        "results-assembly-v1",
        "manuscript-grounding-v1",
    ),
    "critic": (
        "mechanism-development-v1",  # mechanism critique pass
        "analytical-model-specification-v1",  # model critique pass
        "proposition-correctness-v1",  # proposition critique pass
        "results-assembly-v1",  # results critique pass
        "manuscript-grounding-v1",  # manuscript critique pass
    ),
}

ROLE_DESCRIPTIONS: dict[str, str] = {
    "fast": "Lightweight structured tasks (screening decisions and similar).",
    "reasoning": "Analytical generation: evidence, synthesis, gaps, mechanisms, "
    "model specification, propositions, results and manuscript drafting.",
    "critic": "Independent critique passes over mechanisms, models, propositions, "
    "results and manuscripts.",
}

SUPPORTED_ROLES = ("fast", "reasoning", "critic")


def validate_role(role: str) -> str:
    if role not in ROLE_BENCHMARKS:
        raise ConfigurationError(
            f"unknown tournament role {role!r}; supported roles: {sorted(ROLE_BENCHMARKS)}"
        )
    return role


def benchmarks_for_role(role: str) -> tuple[str, ...]:
    return ROLE_BENCHMARKS[validate_role(role)]


def role_description(role: str) -> str:
    return ROLE_DESCRIPTIONS[validate_role(role)]
