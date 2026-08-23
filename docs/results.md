# Findings, Contributions & Results Package — Phase 4A

Phase 4A transforms the verified Phase 3 outputs into an immutable
`ResearchResultsPackage`:

```text
ResearchGap
+ SelectedMechanism
+ FormalAnalyticalModel
+ Verified Equilibrium (3C)
+ Verified Propositions (3D)
+ NumericalExperiment (3E)
→ ResearchFinding (grounded statements)
→ ContributionClaim (corpus-bounded)
→ ResearchImplication (epistemic-status separated)
→ ResearchResultsPackage → evidence-grounded manuscript drafting
  (Phase 4B, see docs/manuscript.md)
```

## Schemas (`src/research_harness/research/schemas/results.py`)

- `ResearchFinding` — statement, `finding_type`
  (analytical_result | comparative_static | robustness_result | welfare_result
  | boundary_result), `supporting_proposition_ids`,
  `supporting_comparative_static_ids`, `supporting_numerical_result_ids`,
  `conditions` (preserved from all referenced supports), `confidence`
  (high/medium/low), `knowledge_basis` (reused Phase 3A enum).
- `ContributionClaim` — `gap_id`, `finding_ids`, `claim`,
  `contribution_type` (theoretical | mechanism | analytical |
  empirical_implication | managerial | IS_literature | methodological),
  `advances_literature` (corpus-bounded), `novelty_claim`,
  `novelty_normalized` (sweeping novelty phrases stripped at assembly).
- `ResearchImplication` — `implication_kind` (theory | IS_research |
  management | platform_firm_strategy | policy | future_research) with
  `claim_type` separating the epistemic status: `mathematically_established` |
  `interpretation` | `managerial_implication` | `speculation_future_hypothesis`.
- `ResearchResultsPackage` — research_question_id, gap_id,
  selected_mechanism_id, model_id, equilibrium_analysis_id,
  equilibrium_candidate_id, numerical_experiment_id, finding_ids,
  contribution_claim_ids, implication_ids, limitations. Immutable input to
  Phase 4B.
- `ResultsAssemblyExecution` — operational record (counts, role, timing).
- `ResultsCritique` + `ResultsCritiqueIssue` (7 categories) + verdict
  (approve | revise | reject) — persisted separately; the package stays
  immutable.

## Finding construction

The `reasoning` role receives ONLY verified artifacts: the verified
equilibrium candidate expressions, verified/conditionally verified
propositions (with ids + conditions), verified comparative statics (ids +
signs + conditions), and the numerical results/robustness/welfare of the
experiment. It writes statements that MUST cite at least one verified
proposition, static, or numerical result. Findings never invent math: the
LLM only organizes and interprets verified results. Because the LLM cannot
know finding UUIDs at response time, contributions and implications
reference the findings of the same response POSITIONALLY (`FINDING0`,
`FINDING1`, ...), resolved deterministically to the persisted ids.

Validation failures (below) trigger a bounded corrective retry (max 2):
the rejection reason is appended to the prompt and the full response is
re-issued; persistence happens only after validation succeeds.

## Contribution construction

Each claim links the selected ResearchGap → its finding(s) → why this
advances the reviewed literature (corpus-bounded `advances_literature`).
Sweeping global-novelty phrasing ("first study", "no prior work",
"never been studied", ...) is stripped by regex during assembly and flagged
with `novelty_normalized=True`; a claim that survives normalization is still
caught by the critic if any novelty phrase remains.

## Claim-validation rules (deterministic, before persistence)

- every referenced proposition id must exist and have a `verified` /
  `conditionally_verified` PropositionVerification; `failed` propositions are
  rejected loudly;
- referenced comparative static ids must belong to the model/candidate;
- referenced numerical result ids must belong to the experiment's results;
- `conditions` of a finding must include ALL conditions of every referenced
  proposition and comparative static (conditions are never dropped);
- findings must cite at least one verified support;
- unsupported/unknown ids (or out-of-range `FINDINGk` tokens) are rejected;
- contributions must reference at least one assembled finding and link the
  selected gap; implications must ground in assembled findings.

## Critic behavior (`research.results_critic` → `results_critic.default`)

Role `critic`. Deterministic pre-checks (no LLM) merged with the qualitative
critique:
- **symbolic/numerical contradiction** — a finding citing a proposition whose
  robustness check outcome is `violated` gets a high-severity issue
  (deterministic);
- **weak gap link** — a contribution referencing a different gap than the
  package (deterministic);
- **unsupported novelty claim** — residual "first study" phrasing in a
  contribution's `novelty_claim` (deterministic);
- **overclaiming, causal overstatement, missing conditions, weak IS
  contribution** — assessed by the `critic` role over the full package.

The critique is persisted as a separate `results_critique` artifact
(derived_from the package); the package is never mutated.

## Provenance

```text
ResearchResultsPackage → ContributionClaim → ResearchFinding
  → Proposition / NumericalResult → EquilibriumCandidate → EquilibriumAnalysis
  → FormalAnalyticalModel → SelectedMechanism → ResearchGap → Evidence
```

Every finding carries derived_from edges to its propositions, statics, and
numerical results (plus the candidate); contributions link gap + findings;
implications link findings; the package links contributions, gap, mechanism,
model, and equilibrium analysis. Verified after SQLite reopen.

## CLI

```bash
uv run research-agent research results assemble --numerical <experiment-id>
uv run research-agent research results inspect <package-id>
uv run research-agent research findings list --package <package-id>
uv run research-agent research contributions list --package <package-id>
uv run research-agent research results critique <package-id>
```

## Configuration

```yaml
research:
  results:
    assembler_role: reasoning
    critic_role: critic
    max_findings: 12
    max_contributions: 8
    max_implications: 12
    max_llm_calls: 10
```

## Tests

Unit (`tests/unit/test_results_assembly.py`, 10): valid assembly with
novelty normalization; failed proposition rejected as support; unsupported
IDs rejected; dropped conditions rejected; contribution must link gap +
findings; idempotent assembly; critic surfaces the symbolic/numerical
conflict deterministically; critic merges deterministic + LLM issues;
critic flags residual novelty; provenance after reopen.

Integration (`tests/integration/test_phase4a_results.py`): full offline
chain — model → equilibrium (3C) → statics + propositions (3D) → numerical
experiment (3E) → findings → contributions → implications → package (4A) →
critique, with provenance checks after reopen.

Live (`tests/live/test_results_assembly_live.py`, opt-in
`live_results_assembly`, needs OPENROUTER_API_KEY): assembles a real
package on the completed live Phase 3 chain, verifying structural grounding
only (referenced ids exist/verified, conditions preserved, contribution-gap
link, provenance). Recorded live run: 6 findings, 3 contributions, 6 implications;
implications correctly separated into mathematically established /
managerial / speculation (per-run figures, not asserted by the live test);
the first live attempt was rejected by validation
(ungrounded finding) and corrected via the bounded retry loop.