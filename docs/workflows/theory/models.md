# Formal Analytical Model Specification

model specification transforms the mechanism development `SelectedMechanism` into a structured,
machine-checkable formal analytical model:

```text
SelectedMechanism
→ actors → timing → information structure → decisions
→ primitives → parameters → assumptions → payoffs
→ FormalAnalyticalModel (structurally validated)
→ critique → revised FormalAnalyticalModel V2 (supersedes)
```

No equilibrium, propositions, or numerical experiments yet (equilibrium derivation+).

## Schemas (`src/research_harness/research/schemas/model.py`)

- `FormalAnalyticalModel` — references all components explicitly (no free-form
  equation block): actors, variables, parameters, assumptions, timing,
  information structure, payoffs; status (`draft` → `revised`), revision notes.
- `ModelActor` — actor_id, name, role, `strategic` flag (optimizes own payoff).
- `ModelVariable` — symbol, name, meaning, domain, units, `kind`
  (SymbolKind), `owner_actor_id` (required for decision variables).
- `ModelParameter` — symbol, name, meaning, domain, units.
- `ModelTimingStage` — explicit 0-based `stage_number`, name, description,
  `actor_ids` (who is active; empty = nature).
- `InformationStructure` — `InformationItem` (actor, observed
  `variable_symbols`, `available_at_stage`, public/private visibility) +
  `UncertaintyItem` (random variable, distribution, belief note).
- `ModelAssumption` — statement, `mathematical_form` (Expression),
  `knowledge_basis` (reuses mechanism development enum), `source_ids` (required for
  literature_supported), purpose, restrictiveness (low/medium/high).
- `Expression` — `expression` (SymPy-compatible), `latex`, `symbols_used`
  (declared symbol set).
- `PayoffFunction` — actor_id, objective_type, expression, decision_variables,
  parameters, constraints.
- `ModelSpecificationCritique` — issues with `ModelCritiqueCategory`
  (mechanism_model_mismatch, undefined_concept, inconsistent_timing,
  impossible_information, redundant_assumption, missing_strategic_actor,
  payoff_inconsistency, poor_tractability, unjustified_restriction),
  severity, verdict, recommendations.
- `ModelSpecificationExecution` — operational record of a build run.

## Symbol / variable representation

Taxonomy (`SymbolKind`): `decision_variable`, `state_variable`, `parameter`,
`derived_quantity`, `random_variable`, `private_information`,
`observable_signal`. Every symbol records symbol, name, meaning, domain,
units (if relevant), and owner/actor (if relevant). No undefined symbols may
appear in persisted expressions.

## Timing & information structure

Timing is explicit stages (Stage 0: parameters/types realized → … → final
stage: payoffs realized) with actor_ids per stage; must be sequential 0..N
with unique numbers. Information structure lists who observes what, at which
stage, and with which visibility, plus uncertainty distributions and beliefs;
must be consistent with the mechanism (validated against the symbol table and
actor set).

## Assumptions & grounding

Each assumption carries statement, `mathematical_form`, `knowledge_basis`
(`literature_supported` | `research_inference` | `new_hypothesis` |
`modeling_assumption`), `source_ids`, purpose, restrictiveness.
Literature-supported assumptions require source ids that resolve to the
mechanism's grounded evidence/synthesis artifacts; grounding labels are
retained per assumption on the persisted model.

## Payoff representation

Each actor's objective is explicit: actor_id, objective_type, SymPy-parsable
expression + LaTeX + declared symbols, decision_variables (only symbols the
actor owns), parameters, constraints. Every referenced symbol is validated
against the model symbol table; expression free symbols must be declared in
`symbols_used` and defined in the table.

## Structural validation (deterministic, before persistence)

- all symbols defined (free symbols of parsed expressions ⊆ symbol table)
- declared `symbols_used` ⊆ table and ⊇ free symbols
- no duplicate symbols (variables + parameters)
- domains non-empty; symbol syntax valid
- actor references valid (owners, timing actors, information actors, payoffs)
- decision ownership valid (decision_variable has an owner; payoff lists only
  owned decisions)
- timing consistent (sequential 0..N, no duplicates, actors exist)
- information structure valid (observed symbols defined, stage within timing,
  uncertainty variables are random_variable)
- assumption references valid (literature_supported ⇒ non-empty valid source_ids)

Structurally invalid specifications are rejected entirely (execution records
the failures; nothing is persisted).

## Critic / revision

- `research.model_specification_critic` (role `critic`, configurable) critiques
  the nine dimensions; critique persisted separately with provenance.
- `model revise <model-id>` runs the critic first if absent, then a revision
  call (role `reasoning`) that revalidates the full spec.
- Revision creates `FormalAnalyticalModel` V2 via `supersedes`; V1 is never
  mutated; revision notes preserved on V2. Structurally invalid revisions are
  rejected loudly (no V2 persisted).

## Provenance

```text
FormalAnalyticalModel V2 →(supersedes) V1 → SelectedMechanism
  → MechanismCandidate → ResearchGap → SynthesisStatement → EvidenceItem
ModelSpecificationCritique → model
```

Literature-backed assumptions additionally keep individual grounding edges to
their evidence/synthesis artifacts.

## CLI

```bash
uv run --env-file .env research-agent research model build --mechanism <selected-mechanism-id>
uv run research-agent research model inspect <model-id>
uv run --env-file .env research-agent research model critique <model-id>
uv run --env-file .env research-agent research model revise <model-id>
```

## Configuration

```yaml
research:
  model:
    builder_role: reasoning
    critic_role: critic
    revision_role: reasoning
    max_actors: 8
    max_variables: 40
    max_parameters: 40
    max_assumptions: 20
    max_stages: 20
    max_payoffs: 10
```

## Tests

Offline (fake models): `tests/unit/test_model_builder.py` (valid generation
with full component references, undefined-symbol rejection, expression symbol
not listed in symbols_used, duplicate-symbol rejection, unknown decision
owner, payoff ownership mismatch, timing inconsistencies (duplicate/gap/
unknown actor), information-structure violations (unknown observed symbol,
stage out of range, non-random uncertainty), assumption grounding rejection
(bad/missing literature sources), idempotency + model-role change, critic
output + idempotency + role change, immutable revision with supersedes,
invalid revision rejected, provenance after reopen).

Integration: `tests/integration/test_phase3b_model.py` — full offline chain
SelectedMechanism → model → critique → revised V2, provenance verified after
SQLite reopen.

Live (opt-in `live_model_specification`): `tests/live/test_model_specification_live.py` —
structural validity only; retries once on structurally rejected builds
(stochastic model output); no claim of economic correctness.