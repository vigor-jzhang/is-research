# Model Routing Status

## Current State

Production model routing is currently DISABLED.

The system has completed:
- model tournaments and role leaderboards
- latency/token/cost accounting
- routing policy evaluation
- live-quality qualification infrastructure
- provider/model preflight
- task-specific model qualification
- task-aware shadow routing

The static config-driven role router remains the production execution path.

## Qualified Task Coverage

Currently qualified task-level model coverage includes at least:

- evidence_extraction
- gap_analysis

Other task qualifications may exist in historical qualification stores/runs, but only persisted/current qualified evidence should be trusted for activation decisions.

## Tasks Not Yet Ready for Dynamic Routing

The following tasks currently do not have sufficient qualified production coverage:

- synthesis
- mechanism_generation
- model_specification
- proposition_generation
- results_critique
- manuscript_critique
- screening

Some critic-task qualification results may also need to be re-established in the active qualification store after prior environment/store loss.

## Main Remaining Model Work

Before controlled production activation:

1. Find qualified primary models for every task intended for dynamic routing.
2. Find qualified fallback models for critical tasks.
3. Re-run live qualification with provider-stable endpoints.
4. Persist fresh TaskQualificationMatrix / ProductionQualificationMatrix artifacts.
5. Verify qualification evidence freshness.
6. Run task-aware shadow routing using the final qualification matrix.
7. Only then consider controlled production activation.

Current known model/provider issues include:
- model-quality failures on several generation and screening tasks
- provider/rate-limit/502 instability for some otherwise promising candidates
- no qualified fallback coverage for dynamically routable tasks

Qualification thresholds must NOT be lowered simply to obtain coverage.

## Deferred Phase

Controlled production routing / activation is explicitly deferred.

Phase 7E should eventually implement:

- opt-in task-aware production switching
- hard quality gates
- qualified primary/fallback requirements
- circuit breakers
- provider/model failure fallback
- cost and latency budgets
- observability
- immediate rollback to static configuration

Phase 7E must not be started until explicitly resumed.

## Safety Invariants

These invariants must remain true:

- production routing stays disabled by default
- unqualified models are never dynamically selected
- qualification is task-specific and cannot transfer between tasks
- static configured models remain the fallback execution path
- unsafe_task_qualification_rate must remain 0
- unsafe_task_route_rate must remain 0
- historical qualification/routing decisions remain immutable
- thresholds are not relaxed automatically

## Resume Checklist

When model-routing work resumes:

- review this document
- inspect current provider/model availability
- re-run model preflight
- re-run qualification for uncovered/stale tasks
- inspect TaskQualificationMatrix
- inspect ProductionQualificationMatrix
- confirm primary/fallback coverage
- run shadow-routing validation
- decide whether Phase 7E activation criteria are satisfied

## Project Status

Phase 7 routing infrastructure: substantially complete.

Phase 7 model qualification: incomplete.

Phase 7 production activation: not started / intentionally deferred.

The broader research-agent pipeline remains usable through the static config-driven model roles.