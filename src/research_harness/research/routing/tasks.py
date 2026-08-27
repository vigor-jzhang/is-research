"""Canonical research-task registry for task-specific qualification (Phase 7D.3).

Maps the live-quality benchmark case ids to canonical production tasks per role,
so task-level qualification speaks the same language as the production
pipelines (evidence extraction, synthesis, gap analysis, mechanism generation,
model specification, proposition generation; mechanism/model/proposition/
results/manuscript critique; screening). No thresholds live here.
"""

from __future__ import annotations

# canonical task id -> human label
TASK_LABELS: dict[str, str] = {
    # reasoning
    "evidence_extraction": "evidence extraction",
    "synthesis": "literature synthesis",
    "gap_analysis": "research gap analysis",
    "mechanism_generation": "mechanism development",
    "model_specification": "analytical model specification",
    "proposition_generation": "proposition generation",
    # critic
    "mechanism_critique": "mechanism critique",
    "model_specification_critique": "model-specification critique",
    "proposition_critique": "proposition critique",
    "results_critique": "results critique",
    "manuscript_critique": "manuscript critique",
    # fast
    "screening": "literature screening",
}

TASKS_BY_ROLE: dict[str, list[str]] = {
    "reasoning": [
        "evidence_extraction",
        "synthesis",
        "gap_analysis",
        "mechanism_generation",
        "model_specification",
        "proposition_generation",
    ],
    "critic": [
        "mechanism_critique",
        "model_specification_critique",
        "proposition_critique",
        "results_critique",
        "manuscript_critique",
    ],
    "fast": ["screening"],
}

# live-quality benchmark case id -> canonical task id
CASE_TASK_MAP: dict[str, str] = {
    "lq-evidence-extraction": "evidence_extraction",
    "lq-literature-synthesis": "synthesis",
    "lq-gap-analysis": "gap_analysis",
    "lq-mechanism-development": "mechanism_generation",
    "lq-model-specification": "model_specification",
    "lq-proposition-generation": "proposition_generation",
    "lq-mechanism-critique": "mechanism_critique",
    "lq-model-critique": "model_specification_critique",
    "lq-proposition-critique": "proposition_critique",
    "lq-results-critique": "results_critique",
    "lq-manuscript-critique": "manuscript_critique",
    "lq-fast-screening-clear": "screening",
    "lq-fast-screening-uncertain": "screening",
}

BENCHMARK_BY_ROLE: dict[str, str] = {
    "fast": "live-quality-fast-v1",
    "reasoning": "live-quality-reasoning-v1",
    "critic": "live-quality-critic-v1",
}


def canonical_task(task_or_case: str) -> str:
    """Return the canonical task id for a case id (or pass-through for a task id)."""
    if task_or_case in TASK_LABELS:
        return task_or_case
    return CASE_TASK_MAP.get(task_or_case, task_or_case)


def tasks_for_role(role: str) -> list[str]:
    from research_harness.research.routing.roles import validate_role

    validate_role(role)
    return list(TASKS_BY_ROLE.get(role, []))


def case_tasks_for_role(role: str) -> list[str]:
    """Case ids that belong to a role's canonical tasks."""
    canonical = set(tasks_for_role(role))
    return [case_id for case_id, task in CASE_TASK_MAP.items() if task in canonical]
