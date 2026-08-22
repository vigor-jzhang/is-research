# Numerical Experiments, Robustness & Welfare — Phase 3E

Phase 3E transforms the Phase 3C/3D verified results into deterministic
numerical evidence:

```text
FormalAnalyticalModel + Verified Equilibrium + Verified Propositions
→ numerical scenarios (baseline, low/high probes, 1-D sweeps, grids)
→ robustness checks
→ welfare outcomes
```

No paper drafting, LaTeX generation, publication claims, new gap search, or
new theory generation (Phase 4).

## Schemas (`src/research_harness/research/schemas/numerical.py`)

- `NumericalScenario` — parameter points with feasibility (domain + condition
  validation); `ParameterSweep` — baseline / low_high / sweep_1d / grid with
  `SweepDimension` specs, fixed parameters, total points, optional
  `series_blob_ref`.
- `NumericalResult` — one evaluated point: parameter values, outcomes,
  feasible flag + infeasible reason, scenario/group labels, x-axis fields
  (visualization-ready series).
- `RobustnessCheck` — check_type (parameter_range, assumption_relaxation,
  boundary_condition, alternative_branch, sensitivity, proposition_support),
  outcome (`supported`|`violated`|`not_testable`), admissible points,
  violations, optional `proposition_id`.
- `WelfareAnalysis` — `WelfareMetric`s (name, actor, value, definition),
  total welfare, notes; only model-definable metrics.
- `NumericalExperiment` — aggregate (sweeps, results, robustness, welfare,
  status, summary) with full reproducibility metadata.
- `NumericalExperimentExecution` — engine (`sympy+python`), engine_version,
  seed (0; no randomness), counts, failures.

## Numerical engine

`research.numerical_analysis` — deterministic Python/SymPy/NumPy. The LLM is
never used for numbers (it may only design scenarios in principle; the default
run uses deterministic scenario generation). Every point records `model_id`,
`equilibrium_id`, parameter values, and sampling spec.

## Parameter validation

Before evaluation: parameter domains (R, R_+, R_-, `[lo,hi]`, `> k`) and
equilibrium conditions (`2*b != 0`, etc., parsed from candidate conditions)
are checked; invalid points are persisted as `feasible=False` with an explicit
`infeasible_reason` — never silently evaluated. Outcome domains are checked
too (e.g. `q < 0` violates `R_+`).

## Sweep/scenario design

Default deterministic runs: baseline (defaults: 10 for demand-like parameters,
1 otherwise), per-parameter 1-D sweeps (0.5×–2×, 7 points), per-parameter
domain-edge probes (0 → 1.5×, 4 points; these exercise infeasible recording),
and a 2-parameter grid (4×4) when ≥2 parameters exist.

## Proposition robustness

Every proposition of the model is checked across admissible points:
the derivative sign at each point (where the proposition's own conditions
hold) is compared with the claim. `supported` / `violated` / `not_testable`
are recorded in `RobustnessCheck` artifacts; a numerical violation NEVER
overwrites the symbolic proposition — it is preserved as a robustness result.
This catches conditional propositions whose declared conditions don't actually
guarantee the sign (unit-tested with `dp/db < 0 when a > 0` vs `c < 0`).

## Welfare analysis

Only metrics definable from the model are computed: each actor's payoff
evaluated at the equilibrium point (parameters + equilibrium outcomes
substituted), plus their sum as total welfare. No fabricated welfare formulas
(e.g. consumer surplus is not invented when the model doesn't define it).
For the Cournot baseline: π1 = π2 = 9, total = 18.

## Reproducibility / idempotency

Same inputs → same results: deterministic engine, seed=0, engine version
recorded; identical runs reuse the same execution artifact (idempotency).
Large result tables (> threshold, default 500 rows) are written to the
BlobStore as JSONL and referenced (`series_blob_ref`) instead of uncontrolled
arrays inside SQLite JSON.

## Provenance

```text
NumericalExperiment → NumericalResult / RobustnessCheck / WelfareAnalysis
  → EquilibriumCandidate → FormalAnalyticalModel → SelectedMechanism
RobustnessCheck → Proposition (tested proposition id)
```

Verified after SQLite reopen.

## CLI

```bash
uv run research-agent research numerical run --equilibrium <equilibrium-analysis-id>
uv run research-agent research numerical inspect <experiment-id>
uv run research-agent research numerical results <experiment-id>
uv run research-agent research numerical robustness <experiment-id>
uv run research-agent research numerical welfare <experiment-id>
```

## Configuration

```yaml
research:
  numerical:
    model_role: reasoning
    max_points: 10000
    artifact_point_threshold: 500
```

## Tests

Offline (analytically known Cournot q*=(a−c)/3 and monopoly-with-slope
p*=(ab+c)/2b): `tests/unit/test_numerical_analysis.py` — baseline evaluation,
1-D sweep monotonicity, invalid-domain rejection (a=0/c=0 probes recorded
infeasible), equilibrium-condition enforcement (b=0 violates `2*b != 0`),
proposition supported numerically, proposition violation detection with
hand-persisted c<0 points (symbolic proposition untouched), deterministic
rerun (idempotent, engine/seed recorded), multi-parameter grid, welfare
calculation, robustness parameter-range checks, provenance after reopen.

Integration: `tests/integration/test_phase3e_numerical.py` — full offline
chain (3C equilibrium → 3D statics+propositions → 3E experiment with
baseline/sweeps/probes/grid → robustness → welfare) with provenance after
reopen.

Live (opt-in `live_numerical_analysis`): `tests/live/test_numerical_analysis_live.py` —
deterministic, no network; structural validity only. Live run on the seeded
Cournot game: 39 feasible + 2 infeasible points, 7 robustness checks
(4 proposition-support supported, 1 not_testable), welfare π1=π2=9, total 18.