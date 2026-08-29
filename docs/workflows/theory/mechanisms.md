# Mechanism Development — Phase 3A

Phase 3A transforms the frozen Phase 2H `GapAnalysis` into a selected,
critiqued, and revised theoretical mechanism:

```text
GapAnalysis
→ GapSelection (autonomy checkpoint)
→ MechanismCandidate (generation, role `reasoning`)
→ MechanismCritique (independent critic, role `critic`)
→ SelectedMechanism (revision, role `reasoning`)
```

Phase 3A produces a mechanism **representation only**. No utility functions,
equations, optimization problems, equilibrium, propositions, proofs, or
numerical experiments — those begin in Phase 3B.

## Schemas (`src/research_harness/research/schemas/mechanism.py`)

- `GapSelection` — selected gap id, alternative gap ids considered, selection
  rationale, four selection dimension scores (research importance, theoretical
  relevance, analytical-model suitability, tractability), evidence/synthesis
  basis, status (`pending_approval`/`approved`/`rejected`), autonomy mode and
  approval decision, `selected_by` (model | operator | fallback).
- `MechanismCandidate` — name, description, actors, strategic interactions,
  information structure, incentives, causal logic, key assumptions, expected
  outcomes, boundary conditions, `literature_support_ids`, element-level
  `grounding`, deterministic support counts, `analytical_model_potential`,
  `evaluation` (raw dimension scores).
- `MechanismEvaluation` — six raw dimensions kept separate
  (gap_alignment, theoretical_coherence, novelty_within_reviewed_corpus,
  analytical_tractability, managerial_economic_relevance, is_relevance);
  `composite` is the simple mean for transparency.
- `GroundingElement` — one mechanism element + explicit `KnowledgeBasis`:
  `literature_supported` (source_ids required, must resolve to existing
  artifacts), `research_inference`, `new_hypothesis`, `modeling_assumption`.
- `MechanismCritique` — issues with `CritiqueCategory`
  (logical_inconsistency, unsupported_assumption,
  already_explained_by_reviewed_literature, unclear_causal_direction,
  unmodelable_concept, missing_actor_or_incentive, alternative_explanation),
  severity, verdict (keep|revise|reject), revision recommendations.
- `MechanismAnalysis` — durable aggregate (gap_selection_id, candidate_ids,
  critique_ids, selected_mechanism_id, status) evolved immutably via
  `supersedes` (generated → critiqued → selected).
- `MechanismAnalysisExecution` — operational record of a generation run.
- `SelectedMechanism` — the final mechanism after generation + critique +
  revision; references the original candidate and critiques; `revision_notes`
  record what changed. Original candidates are never mutated.

## Gap-selection process

1. Input: `GapAnalysis.ranked_gap_ids` (all gaps loaded with ranking and
   analytical-model opportunity).
2. Selection call (role `reasoning`) returns one gap id + rationale + scores.
   The model is explicitly instructed **not** to assume rank #1; the choice is
   validated against the analyzed gap set. Invalid/unknown ids and model
   failures fall back deterministically to the top-ranked gap (recorded).
   `--gap <id>` overrides selection by an operator (no model call).
3. Autonomy checkpoint `research_gap` via `autonomy_policy.default`:
   interactive mode requests approval (recorded on the GapSelection: required,
   decided_by, reason); high-autonomy mode continues while recording the
   auto-approval. Rejected selections are persisted with `status=rejected`.
4. The selected `ResearchGap` is superseded by a `status=selected` artifact
   (immutability preserved).
5. Idempotent: same analysis + autonomy mode reuses the existing selection.

## Mechanism representation & grounding discipline

- Structured candidates via strict JSON schema (role `reasoning`).
- Every element is labeled with its knowledge basis. Novel hypotheses are
  never presented as established facts; `literature_supported` elements must
  cite synthesis-statement / evidence artifact ids from the gap's context.
- Grounding validation: hallucinated ids in `literature_support_ids` or in
  `literature_supported` source_ids reject the candidate (recorded in
  `candidates_rejected` + execution failures); valid candidates still persist.
- Deterministic support counts: `literature_support_papers` (distinct paper ids
  across referenced statements/evidence) and `literature_support_evidence_items`.
- Evaluation: model supplies raw 0..1 dimension scores; composite is computed
  deterministically; raw dimensions remain on the artifact.

## Critic process

- Independent plugin `research.mechanism_critic` with role `critic`
  (configurable `critic_role`).
- Identifies logical inconsistencies, unsupported assumptions, mechanisms
  already explained by the reviewed literature, unclear causal direction,
  unmodelable concepts, missing actors/incentives, and alternative
  explanations; returns verdict + revision recommendations; preserved as a
  durable `MechanismCritique` artifact with provenance to the candidate.

## Selection / revision

- `research-agent research mechanisms select <candidate-id>`: runs the critic
  first if no critique exists, then a revision call (role `reasoning`) that
  takes candidate + critique and returns the revised mechanism.
- If the revision model is unavailable, the candidate is selected unchanged
  with an explicit revision note (documented fallback).
- The `MechanismAnalysis` aggregate is evolved via `supersedes`:
  generated → critiqued → selected, preserving the full chain
  `candidate → critique → revision → SelectedMechanism`.

## Provenance

```text
SelectedMechanism → MechanismCandidate → ResearchGap(selected)
  → SynthesisStatement → EvidenceItem
SelectedMechanism → MechanismCritique → MechanismCandidate
MechanismAnalysis → (supersedes chain) → candidate_ids / critique_ids / selected_mechanism_id
```

Literature-backed elements carry provenance edges to their statement/evidence
artifacts; novel inference is visible through `KnowledgeBasis` labels.

## CLI

```bash
uv run --env-file .env research-agent research gap-select --analysis <gap-analysis-id> [--gap <gap-id>]
uv run --env-file .env research-agent research mechanisms generate --selection <gap-selection-id>
uv run research-agent research mechanisms inspect <candidate-id>
uv run --env-file .env research-agent research mechanisms critique <candidate-id>
uv run --env-file .env research-agent research mechanisms select <candidate-id>
```

## Configuration

```yaml
research:
  mechanism:
    generator_role: reasoning
    critic_role: critic
    revision_role: reasoning
    max_candidates: 5
    max_model_calls: 20
```

## Tests

Offline (fake models): `tests/unit/test_gap_selection.py` (model picks beyond
rank #1, invalid model proposal falls back, interactive approval recorded,
rejection records rejected, operator override, deterministic fallback on model
failure, idempotency, provenance + gap marked selected) and
`tests/unit/test_mechanism.py` (multiple grounded candidates with element
labels, unsupported evidence rejection, unknown grounding source rejection,
approved-selection requirement, idempotency + model-role change, critic output,
revision preserving the original, fallback when revision model fails,
provenance chain to evidence).

Integration: `tests/integration/test_phase3a_mechanisms.py` — full offline
chain GapAnalysis → GapSelection (interactive) → 3 candidates → critique →
SelectedMechanism, with provenance verified after SQLite reopen.

Live (opt-in `live_mechanism`): `tests/live/test_mechanism_live.py` —
structural grounding + provenance only, reuses prior artifacts idempotently.