"""Model usage / cost / latency accounting for tournaments (Phase 7B).

Pure functions with no plugin dependencies. Cost is never invented:
- provider-returned usage cost wins when present (cost_source = "provider"),
- otherwise cost is calculated from explicitly configured pricing when both
  input/output rates are present (cost_source = "pricing"),
- otherwise cost stays None (cost_source = None).

Latency is aggregated only over successful candidate calls so fixture/setup
time never masquerades as model latency.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from statistics import fmean
from typing import Any

from research_harness.research.schemas.tournament import (
    BenchmarkRunRef,
    ModelCallRecord,
    TournamentModelConfig,
    TournamentPricing,
)


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile; None for empty input."""
    vals = sorted(values)
    if not vals:
        return None
    if len(vals) == 1:
        return float(vals[0])
    k = (len(vals) - 1) * q
    f = int(k)
    c = min(f + 1, len(vals) - 1)
    return vals[f] + (vals[c] - vals[f]) * (k - f)


def call_cost(
    usage: object,
    pricing: TournamentPricing | None,
) -> tuple[float | None, str | None]:
    """Return (cost, source) for one call's usage."""
    if usage is None:
        return None, None
    provider_cost = getattr(usage, "cost", None)
    if provider_cost is not None:
        return float(provider_cost), "provider"
    if (
        pricing is not None
        and pricing.input_per_million is not None
        and pricing.output_per_million is not None
    ):
        prompt = int(getattr(usage, "prompt_tokens", None) or 0)
        completion = int(getattr(usage, "completion_tokens", None) or 0)
        calc = (
            prompt * pricing.input_per_million + completion * pricing.output_per_million
        ) / 1_000_000
        return calc, "pricing"
    return None, None


def aggregate_calls(calls: list[ModelCallRecord]) -> dict[str, Any]:
    """Aggregate per-call records into raw dimension values (null when
    unknown/absent)."""
    out: dict[str, Any] = {
        "latency_ms_mean": None,
        "latency_ms_p50": None,
        "latency_ms_p95": None,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "estimated_cost": None,
        "structured_output_success_rate": None,
        "model_error_rate": None,
        "retry_rate": None,
        "failure_counts": {},
        "resolved_model": None,
    }
    if not calls:
        return out

    total = len(calls)
    latencies = [c.latency_ms for c in calls if c.status == "success" and c.latency_ms is not None]
    if latencies:
        out["latency_ms_mean"] = fmean(latencies)
        out["latency_ms_p50"] = percentile(latencies, 0.5)
        out["latency_ms_p95"] = percentile(latencies, 0.95)

    if any(c.prompt_tokens is not None for c in calls):
        out["input_tokens"] = sum(int(c.prompt_tokens or 0) for c in calls)
    if any(c.completion_tokens is not None for c in calls):
        out["output_tokens"] = sum(int(c.completion_tokens or 0) for c in calls)
    totals = [c.total_tokens for c in calls if c.total_tokens is not None]
    if totals:
        out["total_tokens"] = sum(int(t) for t in totals)
    elif out["input_tokens"] is not None and out["output_tokens"] is not None:
        out["total_tokens"] = out["input_tokens"] + out["output_tokens"]

    costs: list[float] = []
    for c in calls:
        if c.status not in ("success", "structured_output_failure"):
            continue
        value = c.provider_cost if c.provider_cost is not None else c.calculated_cost
        if value is not None:
            costs.append(float(value))
    completed = sum(1 for c in calls if c.status in ("success", "structured_output_failure"))
    if costs and len(costs) == completed:
        out["estimated_cost"] = sum(costs)

    structured_calls = [c for c in calls if c.structured]
    if structured_calls:
        ok = sum(1 for c in structured_calls if c.status == "success")
        out["structured_output_success_rate"] = ok / len(structured_calls)

    errors = sum(1 for c in calls if c.status == "error")
    out["model_error_rate"] = errors / total
    retries = sum(int(c.retries or 0) for c in calls)
    out["retry_rate"] = retries / total

    counts: Counter[str] = Counter()
    for c in calls:
        if c.failure is not None:
            counts[c.failure.value] += 1
    out["failure_counts"] = dict(counts)

    resolved = [c.model for c in calls if c.model]
    if resolved:
        # ``max(set(...))`` ties on string hash order, which is randomised per
        # interpreter process, so the same input could yield a different
        # resolved model between runs. Break ties by name to stay reproducible.
        model_counts = Counter(resolved)
        out["resolved_model"] = min(model_counts, key=lambda m: (-model_counts[m], m))

    return out


def aggregate_run_results(
    runs: list[BenchmarkRunRef],
    calls_metrics: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate EvaluationRun-level outcomes into pass rates."""
    total_cases = sum(int(r.cases_total or 0) for r in runs)
    passed_cases = sum(int(r.cases_passed or 0) for r in runs)
    failed_cases = sum(int(r.cases_failed or 0) for r in runs)
    error_cases = sum(int(r.cases_error or 0) for r in runs)
    passed_benchmarks = sum(1 for r in runs if r.report_status == "passed")
    total_benchmarks = len(runs)

    case_pass_rate = passed_cases / total_cases if total_cases else None
    deterministic_pass_rate = (
        passed_cases / (passed_cases + failed_cases) if (passed_cases + failed_cases) else None
    )
    benchmark_pass_rate = passed_benchmarks / total_benchmarks if total_benchmarks else None

    estimated_cost = calls_metrics.get("estimated_cost")
    cost_per_successful_case = (
        estimated_cost / passed_cases if estimated_cost is not None and passed_cases else None
    )
    cost_per_successful_benchmark = (
        estimated_cost / passed_benchmarks
        if estimated_cost is not None and passed_benchmarks
        else None
    )

    return {
        "case_pass_rate": case_pass_rate,
        "deterministic_pass_rate": deterministic_pass_rate,
        "benchmark_pass_rate": benchmark_pass_rate,
        "cost_per_successful_case": cost_per_successful_case,
        "cost_per_successful_benchmark": cost_per_successful_benchmark,
        "error_cases": error_cases,
    }


def effective_pricing(
    candidate: TournamentModelConfig, provider_cfg: dict[str, Any] | None
) -> TournamentPricing | None:
    """Configured pricing for a candidate: plan-level wins, else the provider
    section (both rates required). Never guesses."""
    if (
        candidate.pricing is not None
        and candidate.pricing.input_per_million is not None
        and candidate.pricing.output_per_million is not None
    ):
        return candidate.pricing
    if provider_cfg is not None:
        rates = provider_cfg.get("pricing") or {}
        if (
            isinstance(rates, dict)
            and rates.get("input_per_million") is not None
            and rates.get("output_per_million") is not None
        ):
            return TournamentPricing(
                source=rates.get("source"),
                version=rates.get("version"),
                input_per_million=float(rates["input_per_million"]),
                output_per_million=float(rates["output_per_million"]),
            )
    return None
