# Propositions, Comparative Statics & Economic Interpretation

proposition development transforms a equilibrium derivation verified equilibrium into comparative statics,
propositions, symbolic verification, and structured economic/IS
interpretation:

```text
Verified Equilibrium
→ comparative statics (∂x*/∂θ, deterministic SymPy)
→ candidate propositions (LLM, role `reasoning`)
→ deterministic symbolic verification
→ critique (role `critic`) + economic interpretation
```

No numerical experiments, parameter sweeps, plots, welfare experiments,
robustness experiments, or paper writing (numerical analysis+).

## Schemas (`src/research_harness/research/schemas/proposition.py`)

- `ComparativeStatic` — outcome variable, parameter, `derivative_expression`
  (∂x*/∂θ), sign (`positive`|`negative`|`zero`|`ambiguous`), conditions,
  interpretation, derived_by (`sympy`).
- `ComparativeStaticsAnalysis` — aggregate (static_ids, status, summary).
- `ComparativeStaticsExecution` — operational record (statics_created,
  failures, role).
- `Proposition` — statement, claim_type (`monotonicity`|`equality`|`threshold`),
  outcome/parameter/expected_sign, mathematical_form, explicit conditions,
  `supporting_static_ids` (the math backing it), status, proposed_by, role.
- `PropositionVerification` — status (`verified`|`conditionally_verified`|
  `failed`), `PropositionCheck`s (equilibrium_consistency, derivative_sign,
  algebraic_relation, condition_requirement, symbol_validation), required
  conditions.
- `PropositionCritique` — issues (overclaiming, interpretation_beyond_support,
  missing_conditions, trivial_proposition, contradicts_assumptions_or_mechanism,
  weak_is_relevance), verdict, recommendations.
- `EconomicInterpretation` — separates `mathematical_result`,
  `economic_interpretation`, `managerial_implication`,
  `is_theoretical_implication`, `consistency_note`.

## Comparative-static derivation

Fully deterministic (SymPy), no LLM: for every equilibrium outcome × model
parameter, `∂x*/∂θ` is computed and simplified. Signs are inferred only when
provable without parameter restrictions (constants, or constant × provably
positive factors); otherwise `ambiguous` with the parameter set recorded.
`∂q/∂a = 1/3` positive; `∂q/∂c = -1/3` negative; unused parameters give
zero; `dp/db = -c/(2b²)` is ambiguous with conditions.

## Sign / condition handling

`∂x*/∂θ > 0 if C holds` is never simplified to a global claim. Ambiguous
statics require the proposition to declare the conditions explicitly; the
verifier records `conditions_required` and the proposition is at most
`conditionally_verified`. Conditional results keep their conditions verbatim.

## Proposition generation

`research.proposition_generator` (role `reasoning`, configurable): the LLM
proposes structured propositions from the verified equilibrium, the verified
comparative statics (IDs shown and authoritative), model assumptions, and the
mechanism. Every proposition must reference supporting statics; the prompt
forbids claiming signs the statics do not support.

## Symbolic verification

`research.proposition_verifier` (no LLM) checks deterministically:

- equilibrium consistency (supporting statics exist, reference the same
  candidate; required for monotonicity claims);
- derivative sign (expected sign vs the verified static's sign; ambiguous
  statics with declared conditions ⇒ `conditionally_verified`);
- algebraic equality (for equality claims, substitute the candidate into
  `lhs = rhs`; difference must simplify to zero);
- condition requirements (missing conditions on ambiguous statics ⇒ failed);
- symbol validation.

LLM-generated propositions are never marked verified without these checks
passing. `threshold` claims are rejected as unsupported by the verifier.

## Interpretation / critic

- `research.proposition_critic` (role `critic`): critiques overclaiming,
  interpretation beyond the math, missing conditions, triviality,
  contradictions with assumptions/mechanism, weak IS relevance; durable
  `PropositionCritique` artifacts.
- Economic interpretations (role `reasoning`) separate the mathematical
  result, economic interpretation, managerial implication, and IS/theoretical
  implication; the prompt forces consistency with the verified result and its
  conditions.

## Provenance

```text
EconomicInterpretation → Proposition → ComparativeStatic
  → EquilibriumCandidate / EquilibriumVerification → FormalAnalyticalModel
  → SelectedMechanism → ResearchGap
```

Verified after SQLite reopen.

## CLI

```bash
uv run research-agent research comparative-statics run --equilibrium <equilibrium-analysis-id>
uv run research-agent research comparative-statics inspect <comparative-statics-analysis-id>
uv run --env-file .env research-agent research propositions generate --analysis <comparative-statics-analysis-id>
uv run research-agent research propositions inspect <proposition-id>
uv run research-agent research propositions verify <proposition-id>
```

## Configuration

```yaml
research:
  proposition:
    generator_role: reasoning
    critic_role: critic
    interpretation_role: reasoning
    max_propositions: 8
    max_llm_calls: 20
```

## Tests

Offline (deterministic closed forms): `tests/unit/test_propositions.py` —
positive/negative/zero statics, ambiguous sign with conditions, verified
proposition, incorrect sign rejected, missing-condition rejected vs
conditionally-verified with conditions, equality claim verified at the
equilibrium, equilibrium-consistency rejection of hallucinated static refs,
full generate→verify→critique→interpret flow, interpretation provenance,
idempotency, model-role change.

Integration: `tests/integration/test_phase3d_propositions.py` — full offline
chain (equilibrium derivation services → statics → 2 propositions → verification → critique
→ interpretation) with provenance after reopen.

Live (opt-in `live_propositions`): `tests/live/test_propositions_live.py` —
structural validity and symbolic grounding only. The recorded live run
produced 5 propositions, all symbolically verified, critiqued, and
interpreted (per-run figure, not asserted by the live test).