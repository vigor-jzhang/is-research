# Configuration

The full composition lives in `configs/example.yaml` (81 plugins, including the evaluation framework-7A.1 evaluation harness, thirty-one evaluator plugins, the model tournaments `evaluation.model_tournament` plugin, and the shadow routing `routing.policy_router` plugin, the live-quality validation `evaluation.live_quality` plugin, and config-driven `live_quality.candidates` for qualification campaigns (expanded in qualification expansion and calibration: fast 4, reasoning 5, critic 4). `AppConfig`
(`config/schema.py`) validates it via Pydantic v2; `load_config`
(`config/loader.py`) fails early with readable messages. Secrets are never in
YAML — they come from the environment.

## Full example configuration

```yaml
runtime:
  autonomy: high   # high | interactive

plugins:
  - model.openrouter
  - routing.role_router
  - session.jsonl
  - storage.artifacts_sqlite
  - storage.blobs_filesystem
  - literature.crossref
  - literature.semantic_scholar
  - literature.ingestion
  - literature.identity_resolver
  - literature.search_planner
  - literature.search_orchestrator
  - literature.screening_protocol_builder
  - literature.screening_view_builder
  - literature.title_abstract_screener
  - literature.screening_orchestrator
  - literature.evidence_extractor
  - literature.evidence_orchestrator
  - literature.synthesis
  - literature.gap_analyzer
  - research.gap_selection
  - research.mechanism_generator
  - research.mechanism_critic
  - research.model_builder
  - research.model_specification_critic
  - research.equilibrium_deriver
  - research.equilibrium_verifier
  - research.comparative_statics
  - research.proposition_verifier
  - research.proposition_critic
  - research.proposition_generator
  - research.numerical_analysis
  - research.results_assembler
  - research.results_critic
  - research.manuscript_drafter
  - research.manuscript_critic
  - research.publication_formatter
  - research.novelty_validator
  - research.evaluation_harness
  - evaluator.deterministic
  - evaluator.retrieval
  - evaluator.claim_grounding
  - evaluator.citation_correctness
  - evaluator.llm_judge
  - evaluator.screening
  - evaluator.evidence
  - evaluator.gap_analysis
  - evaluator.mechanism
  - evaluator.equilibrium
  - evaluator.numerical
  - evaluator.comparative_statics
  - evaluator.proposition
  - evaluator.results_grounding
  - evaluator.manuscript_grounding
  - evaluator.pipeline_integrity
  - evaluator.synthesis
  - evaluator.model_specification
  - evaluator.document_acquisition
  - evaluator.revalidation
  - evaluator.identity_resolution
  - evaluator.gap_selection
  - evaluator.novelty_revalidation
  - evaluator.publication_packaging
  - documents.locator.metadata
  - documents.locator.unpaywall
  - documents.fetcher.http
  - documents.extractor.pypdf
  - documents.acquisition_orchestrator
  - autonomy.configurable
  - tool.echo
  - loop.simple_tool_loop

models:
  roles:
    fast: {provider: openrouter, model: "deepseek/deepseek-v4-flash-0731"}
    reasoning: {provider: openrouter, model: "nvidia/nemotron-3-ultra-550b-a55b:free"}
    critic: {provider: openrouter, model: "nvidia/nemotron-3-ultra-550b-a55b:free"}
    long_context: {provider: openrouter, model: "deepseek/deepseek-v4-flash-0731"}

session:
  root: ".research/sessions"

artifacts:
  store: sqlite
  path: ".research/artifacts.db"

loop:
  max_steps: 8
```

The `literature:` (search, screening, evidence, synthesis, gaps), `research:`
(mechanism, model, equilibrium, proposition, numerical, results, manuscript,
publication, novelty), `documents:`, and `evaluation:` (evaluator ids,
`judge_role`, `cost_per_million_tokens`) sections configure each capability; every
capability doc under `docs/` documents its own configuration block. The
`evaluation` section is documented in `docs/operations/evaluation.md`.

## Secrets in `.env`

```
OPENROUTER_API_KEY=...
CROSSREF_MAILTO=you@example.com
SEMANTIC_SCHOLAR_API_KEY=...
UNPAYWALL_EMAIL=you@example.com
```

`uv run --env-file .env` is canonical; `config/dotenv.py` also auto-loads
`.env` from the project root (without overriding existing env vars) for local
convenience. Never commit `.env` or API keys — the session event log scrubs
sensitive keys and never persists secrets.

## Validate

```bash
uv run research-agent config validate configs/example.yaml
```