# Equilibrium Derivation & Symbolic Verification — Phase 3C

Phase 3C transforms the Phase 3B `FormalAnalyticalModel` into an equilibrium:

```text
FormalAnalyticalModel
→ solvability gate
→ optimization problems
→ FOCs / best responses (SymPy)
→ candidate equilibrium (simultaneous or backward induction)
→ deterministic symbolic verification
→ bounded revision loop on failure
```

No propositions, comparative statics, welfare analysis, or numerical
experiments yet (Phase 3D+).

## Solvability gate

Deterministic, before any derivation:

- relevant strategic actors have payoff/objective functions (a strategic actor
  owning decision variables but no payoff fails);
- every decision variable appears in an appropriate optimization problem
  (declared decision variables missing from payoffs fail);
- timing defines the solution order (sequential stages, decision makers appear
  in a stage);
- payoff expressions parse with SymPy.

A model with zero payoffs fails clearly as `not_solvable` (execution records
the reasons; nothing proceeds to equilibrium generation).

## Schemas (`src/research_harness/research/schemas/equilibrium.py`)

- `OptimizationProblem` — actor, decision variables, objective, constraints.
- `FirstOrderCondition` — actor, decision variable, payoff, `foc_expression`
  (∂payoff/∂x), candidate solutions, `applicable` flag (never fabricates an
  FOC when the payoff is independent of the variable).
- `BestResponse` — closed form when the FOC solves uniquely, else
  `implicit=True` (implicit FOC recorded).
- `EquilibriumCandidate` — per-variable `EquilibriumExpression`
  (expression + conditions + solution_method), decision variables, proposed_by
  (`sympy` | `llm`), revision_round, revision_notes.
- `EquilibriumVerification` — status (`verified` | `partially_verified` |
  `failed`), `VerificationCheck`s with symbolic detail, required conditions.
- `EquilibriumAnalysis` — aggregate (candidates, verifications, selected
  candidate, status, solution order/method), evolved via `supersedes`.
- `EquilibriumExecution` — operational record (`solvable`/`not_solvable`/
  `derived`/`partially_derived`/`failed`, counts, failures).

## Derivation strategy

- FOCs and best responses are computed with SymPy (`diff`, `solve`); the LLM
  (`reasoning` role) is used only to propose candidates when SymPy cannot solve
  the system, and never replaces verification.
- Solution order honors timing: actors deciding at the same stage move
  simultaneously (joint FOC system); different stages are solved by backward
  induction (last stage first), with later movers' best responses substituted
  into earlier payoffs before differentiating.
- The deriver and verifier share `research_harness/research/symbolic.py`
  (`game_consistent_focs`, `game_consistent_payoffs`, `decision_stage_plan`)
  so verification always uses the same solution concept as derivation.

## Symbolic verification (deterministic, no LLM)

`research.equilibrium_verifier` performs, for every candidate:

1. symbol validation (all expression symbols defined in the model table);
2. FOC residuals — substitute the candidate into each game-consistent FOC;
   residual must simplify to zero (`simplify(cancel(...)) == 0`);
3. second-order conditions — second derivative of the (game-consistent) payoff
   evaluated at the candidate; constant negative ⇒ pass, symbolic ⇒ recorded
   as a required condition (partial);
4. best-response consistency — candidate equals its closed-form best response
   with other candidates substituted;
5. timing order — for sequential candidates, stage-k expressions depend only
   on earlier-stage variables and parameters;
6. constraints/domains — non-constant denominators ⇒ `denominator != 0`
   conditions recorded.

Status: `verified` (all hard checks pass, SOCs signed), `partially_verified`
(residuals pass but SOC/domain conditions are symbolic and recorded),
`failed` (any residual/structural check fails). An LLM answer is never marked
verified without the symbolic checks passing.

## Equilibrium conditions

Candidates carry explicit conditions: parameter restrictions, interiority,
positivity, `denominator != 0` (auto-extracted from non-constant denominators),
and symbolic SOC inequalities. Conditional solutions are never presented as
globally valid.

## Failed / revised candidates

`candidate → verification failure → bounded revision attempt → re-verification`
with `max_revisions` (default 2, configurable). Each attempt is a distinct
immutable `EquilibriumCandidate` (revision_round incremented, revision_notes
recorded); the loop never runs unbounded.

## Provenance

```text
EquilibriumAnalysis →(supersedes) EquilibriumCandidate
  → BestResponse / FOC / OptimizationProblem → FormalAnalyticalModel
  → SelectedMechanism → ...
EquilibriumVerification → candidate
EquilibriumExecution → model
```

Verified after SQLite reopen.

## CLI

```bash
uv run --env-file .env research-agent research equilibrium derive --model <model-id>
uv run research-agent research equilibrium inspect <analysis-id>
uv run research-agent research equilibrium verify <candidate-id>
```

## Configuration

```yaml
research:
  equilibrium:
    deriver_role: reasoning
    revision_role: reasoning
    max_revisions: 2
    max_llm_calls: 10
```

## Tests

Offline (fake models, deterministic closed forms): `tests/unit/test_equilibrium.py`
covers zero-payoff rejection, strategic actor without payoff, decision variable
outside payoffs, monopoly (`p* = (a + c)/2`, FOC + SOC `-2` + BR consistency),
simultaneous Cournot (`q* = (a - c)/3`), sequential backward induction
(`x* = (a - c)/2`, `y* = (a - c)/4` with timing-order checks), incorrect LLM
candidate rejected, undefined-symbol candidate rejected, parameter
restrictions + partial verification (denominator `2b != 0`, symbolic SOC),
bounded revision (3 candidates max, 2 revisions), idempotency + model-role
change, provenance after reopen.

Integration: `tests/integration/test_phase3c_equilibrium.py` — full offline
chain (leader-follower game, fully symbolic, verified) with provenance after
reopen; asserts the LLM is never invoked for a symbolically solvable game.

Live (opt-in `live_equilibrium`): `tests/live/test_equilibrium_live.py` —
structural derivation + symbolic verification only; skips when no game model
(decision variables in payoffs) exists; no claim of economic validity.